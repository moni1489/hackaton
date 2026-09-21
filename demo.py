#!/usr/bin/env python3
"""Управление демонстрацией САМ ВКО одной командой. Только стандартная библиотека.

  python demo.py install            зависимости backend и frontend
  python demo.py prepare            .env с секретами, отдельная демо-БД, учётка ведущего, запись сценария, сборка
  python demo.py up [--public]      ВСЁ СРАЗУ: подготовка + сервер + сессия + QR-код в терминале
  python demo.py session            новая сессия на запущенном сервере (ссылка и QR)
  python demo.py reset              сбросить сценарий последней сессии
  python demo.py check              проверка готовности (файлы, модели, сервер, вход)
  python demo.py test               тесты backend (с Redis в docker, если он есть) + сборка/линт frontend
  python demo.py load -n 50         нагрузочная проверка: 50 одновременных зрителей

Подробно: docs/demo/RUNBOOK.md
"""
import argparse
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND, FRONTEND = ROOT / "backend", ROOT / "frontend"
ENV_FILE = BACKEND / ".env"
DEFAULT_SECRET = "change-me-in-production-vko-2026"
TEST_REDIS = "redis://127.0.0.1:6390/0"
# Демо живёт в отдельной БД: только учётная запись ведущего и журнал аудита, ни реальных данных,
# ни учётных записей проекта с известными паролями. Основная БД проекта не затрагивается.
DEMO_DB = "sqlite:///demo.sqlite"


def say(text: str = "") -> None:
    print(text, flush=True)


def run(cmd: list[str], cwd: Path = ROOT, env: dict | None = None, check: bool = True) -> int:
    say(f"$ {' '.join(cmd)}")
    code = subprocess.call(cmd, cwd=cwd, env={**os.environ, **(env or {})})
    if check and code:
        sys.exit(f"Команда завершилась с кодом {code}")
    return code


def npm() -> str:
    path = shutil.which("npm")
    if not path:
        sys.exit("Не найден npm (Node.js 20+). Без него нельзя собрать страницы зрителя и ведущего.")
    return path


def port_busy(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1" if host in ("0.0.0.0", "") else host, port)) == 0


def lan_ip() -> str:
    """Адрес компьютера в локальной сети — работает без интернета (пакеты не отправляются)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


# --- .env ---------------------------------------------------------------------------------------

def read_env() -> dict:
    out = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                key, value = line.split("=", 1)
                out[key.strip()] = value.strip()
    return out


def ensure_env() -> dict:
    """Дописывает в backend/.env недостающие секреты. Существующие строки не трогает."""
    current = read_env()
    add = {}
    if current.get("JWT_SECRET", DEFAULT_SECRET) == DEFAULT_SECRET and "JWT_SECRET" not in os.environ:
        if "JWT_SECRET" not in current:
            add["JWT_SECRET"] = secrets.token_urlsafe(48)
    if not current.get("DEMO_OPERATOR_PASSWORD") and not os.environ.get("DEMO_OPERATOR_PASSWORD"):
        add["DEMO_OPERATOR_PASSWORD"] = secrets.token_urlsafe(12)
    if "DEMO_OPERATOR_EMAIL" not in current:
        add["DEMO_OPERATOR_EMAIL"] = "demo.operator@example.local"
    if add:
        had_content = ENV_FILE.exists() and ENV_FILE.stat().st_size > 0
        with ENV_FILE.open("a", encoding="utf-8") as fh:
            if had_content:
                fh.write("\n")
            fh.write("# добавлено demo.py prepare\n")
            for key, value in add.items():
                fh.write(f"{key}={value}\n")
        try:
            ENV_FILE.chmod(0o600)
        except OSError:
            pass
        say(f"backend/.env: добавлено {', '.join(add)}")
    return {**read_env(), **{k: v for k, v in os.environ.items() if k.startswith(("DEMO_", "JWT_"))}}


def creds() -> tuple[str, str]:
    env = {**read_env(), **os.environ}
    return env.get("DEMO_OPERATOR_EMAIL", "demo.operator@example.local"), env.get("DEMO_OPERATOR_PASSWORD", "")


# --- install / prepare ----------------------------------------------------------------------------

def cmd_install(_args) -> None:
    run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], cwd=BACKEND)
    run([npm(), "install"], cwd=FRONTEND)
    say("\nГотово. Дальше: python demo.py up")


def backend_env(args=None) -> None:
    """Готовит процесс к импорту backend: .env читается из backend/, БД — отдельная демо-БД."""
    if not (args and getattr(args, "main_db", False)):
        os.environ.setdefault("DATABASE_URL", DEMO_DB)
    sys.path.insert(0, str(BACKEND))
    os.chdir(BACKEND)


def prepare_db(args=None) -> None:
    backend_env(args)
    from app.database import SessionLocal, migrate_schema
    from app.models import User
    from app.security import hash_password, verify_password
    from app.services.lines import ensure_defaults

    for column in migrate_schema():
        say(f"  миграция: + {column}")
    email, password = creds()
    with SessionLocal() as db:
        ensure_defaults(db)
        user = db.query(User).filter(User.email == email.lower()).first()
        if user is None:
            db.add(User(email=email.lower(), full_name="Ведущий демонстрации", role="operator",
                        password_hash=hash_password(password)))
            say(f"  создана учётная запись ведущего {email} (роль operator)")
        elif user.role not in ("operator", "admin") or not user.is_active \
                or not verify_password(password, user.password_hash):
            user.role = user.role if user.role in ("operator", "admin") else "operator"
            user.password_hash, user.is_active = hash_password(password), True
            say(f"  учётная запись {email} приведена к паролю из backend/.env")
        db.commit()


def prepare_replay(args=None) -> None:
    backend_env(args)
    from app.services.demo import scenario
    public = FRONTEND / "public"
    frames = scenario.replay_frames()
    (public / "demo-replay.json").write_text(json.dumps(frames, ensure_ascii=False, separators=(",", ":")),
                                             encoding="utf-8")
    scn = scenario.compute()
    decision = {"kind": "confirm", "cause": scn["ml"]["cause"]}
    (public / "demo-report.pdf").write_bytes(scenario.report_pdf(scn, decision))
    say(f"  запись сценария: {len(frames)} кадров → frontend/public/demo-replay.json, demo-report.pdf")
    ml = scn["ml"]
    say(f"  расчёт моделей: «{ml['cause_label']}», уверенность {round(ml['confidence'] * 100)}%, "
        f"источник: {ml['source']} {ml['model_version']}")
    if ml["cause"] != "provider_node":
        sys.exit("Модели дали неожиданный вердикт для демо-сценария — см. docs/demo/REHEARSAL_CHECKLIST.md")


def build_frontend() -> None:
    if not (FRONTEND / "node_modules").exists():
        run([npm(), "install"], cwd=FRONTEND)
    run([npm(), "run", "build"], cwd=FRONTEND, env={"VITE_API_URL": ""})   # пусто = тот же адрес


def cmd_prepare(args) -> None:
    say("== 1/4 секреты и учётная запись ведущего")
    ensure_env()
    say("== 2/4 демо-база данных (backend/demo.sqlite)")
    prepare_db(args)
    say("== 3/4 запись сценария для резервного режима")
    prepare_replay(args)
    say("== 4/4 сборка фронтенда (тот же адрес для API)")
    build_frontend()
    email, password = creds()
    say(f"\nГотово. Вход ведущего: {email} / {password}  (хранится в backend/.env)")


# --- Запуск -----------------------------------------------------------------------------------------

def http(method: str, url: str, body: dict | None = None, token: str | None = None, timeout: float = 10):
    request = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read()).get("detail", "")
        except (ValueError, AttributeError):
            pass
        raise SystemExit(f"{method} {url} → {exc.code} {detail}") from None


def operator_token(base: str) -> str:
    email, password = creds()
    if not password:
        sys.exit("Пароль ведущего не задан. Выполните: python demo.py prepare")
    return http("POST", f"{base}/api/auth/login", {"email": email, "password": password})["access_token"]


def show_link(link: dict) -> None:
    say(f"\nСсылка для зрителей (действует до {link['expires_at']}):\n  {link['viewer_url']}\n")
    try:
        import segno
        segno.make(link["viewer_url"], error="m").terminal(compact=True, border=1)
    except ImportError:
        say("(QR в терминале недоступен: pip install segno; QR есть в панели ведущего)")


def wait_ready(base: str, timeout: float = 90) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if http("GET", f"{base}/api/demo/health", timeout=2).get("ok"):
                return
        except SystemExit:
            pass
        except OSError:
            pass
        time.sleep(0.5)
    sys.exit("Сервер не ответил вовремя. Смотрите вывод выше.")


def new_session(base: str, public_url: str | None = None) -> dict:
    token = operator_token(base)
    body = {"title": "Демонстрация САМ ВКО"}
    if public_url:
        body["public_url"] = public_url
    return http("POST", f"{base}/api/demo/sessions", body, token, timeout=60)


def cmd_up(args) -> None:
    if port_busy(args.host, args.port):
        sys.exit(f"Порт {args.port} занят другим приложением. Выберите другой: python demo.py up --port {args.port + 10}")
    env = ensure_env()
    if not (FRONTEND / "dist" / "index.html").exists() or not (FRONTEND / "public" / "demo-replay.json").exists():
        say("Первый запуск: подготовка…")
        cmd_prepare(args)
    else:
        prepare_db(args)
    host_ip = lan_ip()
    public_url = args.public_url or env.get("DEMO_PUBLIC_URL") or f"http://{host_ip}:{args.port}"
    server_env = {"DEMO_MODE": "true", "DEMO_PUBLIC": "true" if args.public else "false",
                  "EXTERNAL_POLL_ENABLED": "false", "DEMO_PUBLIC_URL": public_url}
    if not args.main_db:
        server_env["DATABASE_URL"] = os.environ.get("DATABASE_URL", DEMO_DB)
    if args.redis:
        server_env["REDIS_URL"] = args.redis
    if args.workers > 1 and not args.redis and "REDIS_URL" not in env:
        sys.exit("Несколько процессов требуют общего Redis: добавьте --redis redis://хост:6379/0")
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", args.host, "--port", str(args.port),
           "--workers", str(args.workers), "--no-server-header", "--log-level", "warning"]
    say(f"\nСервер: {'ПУБЛИЧНЫЙ режим' if args.public else 'режим демо'}, порт {args.port}, процессов {args.workers}")
    proc = subprocess.Popen(cmd, cwd=BACKEND, env={**os.environ, **server_env})
    local = f"http://127.0.0.1:{args.port}"
    try:
        wait_ready(local)
        say(f"Панель ведущего:  {public_url}/demo")
        if not args.no_session:
            data = new_session(local, public_url)
            say(f"Сессия создана: {data['session_id']}")
            show_link(data["link"])
        say("Остановить: Ctrl+C. Резервный режим без сервера: /live/replay\n")
        proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT if os.name != "nt" else signal.CTRL_BREAK_EVENT)
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()


def cmd_session(args) -> None:
    base = args.url.rstrip("/")
    data = new_session(base, args.public_url)
    say(f"Сессия: {data['session_id']}")
    show_link(data["link"])


def cmd_reset(args) -> None:
    base = args.url.rstrip("/")
    token = operator_token(base)
    sid = args.session
    if not sid:
        sessions = http("GET", f"{base}/api/demo/sessions", token=token)
        if not sessions:
            sys.exit("Активных сессий нет.")
        sid = sorted(sessions, key=lambda s: s["created_at"])[-1]["id"]
    state = http("POST", f"{base}/api/demo/sessions/{sid}/control", {"action": "reset"}, token)
    say(f"Сессия {sid} сброшена: этап «{state['view']['stage']['label']}», запуск №{state['view']['session']['run']}")


# --- check / test / load ---------------------------------------------------------------------------------

def cmd_check(args) -> None:
    bad = []

    def line(ok: bool, text: str, fatal: bool = True) -> None:
        say(f"  [{'OK' if ok else 'НЕТ'}] {text}")
        if not ok and fatal:
            bad.append(text)

    say("Файлы и настройки")
    env = read_env()
    line((FRONTEND / "dist" / "index.html").exists(), "frontend/dist собран (python demo.py prepare)")
    line((FRONTEND / "public" / "demo-replay.json").exists(), "запись сценария для резервного режима")
    line((FRONTEND / "public" / "demo-report.pdf").exists(), "PDF для резервного режима")
    line(bool(env.get("DEMO_OPERATOR_PASSWORD")), "пароль ведущего в backend/.env")
    line(env.get("JWT_SECRET", DEFAULT_SECRET) != DEFAULT_SECRET, "JWT_SECRET не по умолчанию", fatal=args.public)
    say("Модели и сценарий (локальный расчёт)")
    try:
        backend_env(args)
        from app.services.demo import scenario
        scn = scenario.compute()
        ml = scn["ml"]
        line(ml["source"] == "model", f"вердикт считает обученная модель ({ml['model_version']})")
        line(ml["cause"] == "provider_node", f"результат сценария: {ml['cause_label']}, {round(ml['confidence'] * 100)}%")
        line(ml["data_quality"]["level"] == 2, f"данных достаточно: {ml['data_quality']['label']}")
        line(bool(ml["forecast"]), "прогноз риска SLA рассчитан")
    except Exception as exc:  # noqa: BLE001
        line(False, f"расчёт сценария: {exc}")
    say(f"Сервер {args.url}")
    base = args.url.rstrip("/")
    try:
        health = http("GET", f"{base}/api/demo/health", timeout=3)
        line(True, f"отвечает; хранилище: {health['store']}; сессий: {health['sessions']}")
        line(all(health["models"].values()), "модели загружены", fatal=True)
        try:
            operator_token(base)
            line(True, "вход ведущего работает")
        except SystemExit as exc:
            line(False, f"вход ведущего: {exc}")
        say(f"  адрес для зрителей: http://{lan_ip()}:{urllib.parse.urlsplit(base).port or 8000}")
    except (SystemExit, OSError):
        line(False, "сервер не запущен (python demo.py up) — это нормально до старта", fatal=False)
    say("\n" + ("ГОТОВО К ПОКАЗУ" if not bad else f"НЕ ГОТОВО: {len(bad)} замечаний"))
    sys.exit(1 if bad else 0)


def docker_redis() -> bool:
    """Поднимает Redis для тестов, если docker есть, а Redis на 6390 ещё нет."""
    import redis  # noqa: PLC0415
    try:
        redis.Redis.from_url(TEST_REDIS, socket_connect_timeout=0.5).ping()
        return True
    except Exception:  # noqa: BLE001
        pass
    if not shutil.which("docker"):
        return False
    say("Поднимаю Redis для тестов (docker, порт 6390)…")
    if subprocess.call(["docker", "run", "-d", "--rm", "--name", "demo-test-redis", "-p", "6390:6379",
                        "redis:7-alpine"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL):
        return False
    time.sleep(1.5)
    return True


def cmd_test(args) -> None:
    have_redis = docker_redis()
    say("Redis для тестов: " + ("есть" if have_redis else "нет — тесты Redis будут пропущены"))
    env = {"DEMO_TEST_REDIS_URL": TEST_REDIS}
    run([sys.executable, "-m", "unittest", "tests.test_demo", "-v"], cwd=BACKEND, env=env)
    if not args.quick:
        say("\nСуществующие тесты проекта (регрессия)")
        run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=BACKEND, env=env)
        say("\nFrontend: линт и сборка")
        run([npm(), "run", "lint"], cwd=FRONTEND, check=False)
        build_frontend()


def cmd_load(args) -> None:
    run([sys.executable, "demo_load.py", "--url", args.url, "--viewers", str(args.viewers),
         "--rounds", str(args.rounds)], cwd=BACKEND)


def main() -> None:
    parser = argparse.ArgumentParser(description="Управление демонстрацией САМ ВКО",
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install", help="установить зависимости").set_defaults(fn=cmd_install)

    p = sub.add_parser("prepare", help="подготовить демонстрацию")
    p.add_argument("--main-db", action="store_true", help="использовать основную БД из окружения/.env вместо demo.sqlite")
    p.set_defaults(fn=cmd_prepare, public=False)

    p = sub.add_parser("up", help="запустить всё и создать сессию")
    p.add_argument("--public", action="store_true", help="публичный режим: только демо-API и вход оператора")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--redis", help="redis://хост:порт/0 — общее состояние для нескольких процессов")
    p.add_argument("--public-url", help="адрес для QR (по умолчанию адрес компьютера в сети)")
    p.add_argument("--no-session", action="store_true", help="не создавать сессию автоматически")
    p.add_argument("--main-db", action="store_true", help="использовать основную БД из окружения/.env вместо demo.sqlite")
    p.set_defaults(fn=cmd_up)

    p = sub.add_parser("session", help="создать сессию на запущенном сервере")
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--public-url")
    p.set_defaults(fn=cmd_session)

    p = sub.add_parser("reset", help="сбросить сценарий")
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--session")
    p.set_defaults(fn=cmd_reset)

    p = sub.add_parser("check", help="проверка готовности")
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--public", action="store_true")
    p.set_defaults(fn=cmd_check, main_db=False)

    p = sub.add_parser("test", help="запустить тесты")
    p.add_argument("--quick", action="store_true", help="только тесты демонстрации")
    p.set_defaults(fn=cmd_test)

    p = sub.add_parser("load", help="нагрузочная проверка зрителей")
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("-n", "--viewers", type=int, default=50)
    p.add_argument("--rounds", type=int, default=3, help="сколько переключений этапов измерять")
    p.set_defaults(fn=cmd_load)

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
