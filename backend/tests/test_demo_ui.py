"""Браузерная проверка демонстрации: два зрителя (телефон и ноутбук) следуют за ведущим,
экран не выходит за ширину, зритель ничего не может нажать, токен не остаётся в адресе,
резервное воспроизведение работает при «зависшей» сети.

Запуск:  cd backend && python -m unittest tests.test_demo_ui -v
Нужны: собранный frontend/dist (python demo.py prepare), пакет playwright и Chromium
(DEMO_TEST_CHROMIUM=путь или установленный Playwright). Иначе тесты пропускаются.
Сервер — настоящее приложение (app.main) в отдельном процессе на временной БД.
"""
import glob
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DIST = BACKEND.parent / "frontend" / "dist"
EMAIL, PASSWORD = "ui.operator@example.local", secrets.token_urlsafe(12)
BANNER = "ДЕМОНСТРАЦИОННЫЕ СИНТЕТИЧЕСКИЕ ДАННЫЕ"

try:
    from playwright.sync_api import sync_playwright
except ImportError:              # pragma: no cover
    sync_playwright = None

PROC = None
BASE = ""
PW = None
BROWSER = None


def _chromium() -> str | None:
    if os.environ.get("DEMO_TEST_CHROMIUM"):
        return os.environ["DEMO_TEST_CHROMIUM"]
    found = sorted(glob.glob(str(Path.home() / ".cache/ms-playwright/chromium-*/chrome-linux*/chrome")))
    return found[-1] if found else None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def setUpModule():
    global PROC, BASE, PW, BROWSER
    if sync_playwright is None:
        raise unittest.SkipTest("playwright не установлен (pip install playwright)")
    if not (DIST / "index.html").exists():
        raise unittest.SkipTest("frontend/dist не собран (python demo.py prepare)")
    tmp = tempfile.mkdtemp()
    env = {**os.environ, "DEMO_MODE": "true", "DEMO_PUBLIC": "false", "DATABASE_URL": f"sqlite:///{tmp}/ui.db",
           "JWT_SECRET": secrets.token_urlsafe(32), "REDIS_URL": "redis://127.0.0.1:1/0",
           "EXTERNAL_POLL_ENABLED": "false", "DEMO_RATE_LIMIT_OPERATOR": "100000"}
    seed = ("from app.database import SessionLocal, migrate_schema\n"
            "from app.models import User\nfrom app.security import hash_password\n"
            "migrate_schema()\nwith SessionLocal() as db:\n"
            f"    db.add(User(email='{EMAIL}', full_name='UI', role='operator', password_hash=hash_password('{PASSWORD}')))\n"
            "    db.commit()\n")
    subprocess.run([sys.executable, "-c", seed], cwd=BACKEND, env=env, check=True, capture_output=True)
    port = _free_port()
    PROC = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port),
                             "--log-level", "error"], cwd=BACKEND, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    BASE = f"http://127.0.0.1:{port}"
    import httpx
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            if httpx.get(f"{BASE}/api/demo/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.3)
    else:
        PROC.kill()
        raise RuntimeError("сервер не поднялся")
    PW = sync_playwright().start()
    try:
        BROWSER = PW.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
    except Exception as exc:  # noqa: BLE001
        PROC.kill()
        PW.stop()
        raise unittest.SkipTest(f"Chromium недоступен: {exc}")


def tearDownModule():
    if BROWSER:
        BROWSER.close()
    if PW:
        PW.stop()
    if PROC:
        PROC.terminate()
        PROC.wait(10)


def overflow(page) -> int:
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def operator_creates_session(context):
    """Оператор входит в обычное приложение и на вкладке «Демонстрация» жмёт одну кнопку."""
    page = context.new_page()
    page.goto(f"{BASE}/demo")
    page.fill("#email", EMAIL)
    page.fill("#password", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_selector("text=Демонстрация для зала")
    page.click("text=▶ Запустить демо")
    page.wait_for_selector(".dc-qr svg")
    return page, page.input_value("input[aria-label='Ссылка для зрителей']")


class Browsers(unittest.TestCase):
    def test_operator_login_rejects_bad_password(self):
        ctx = BROWSER.new_context()
        page = ctx.new_page()
        page.goto(f"{BASE}/demo")
        page.wait_for_selector("text=Вход в систему")
        page.fill("#email", EMAIL)
        page.fill("#password", "wrong-password")
        page.click("button[type=submit]")
        page.wait_for_selector("text=Неверный логин или пароль")
        ctx.close()

    def test_two_browsers_follow_operator_fit_screen_and_stay_read_only(self):
        op_ctx = BROWSER.new_context(viewport={"width": 1440, "height": 950})
        op, url = operator_creates_session(op_ctx)
        phone = BROWSER.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        laptop = BROWSER.new_context(viewport={"width": 1366, "height": 768})
        tiny = BROWSER.new_context(viewport={"width": 360, "height": 640}, is_mobile=True)
        viewers = [ctx.new_page() for ctx in (phone, laptop, tiny)]
        for page in viewers:
            page.goto(url)
            page.wait_for_selector(".st[data-stage='waiting']")
            self.assertNotIn("#t=", page.url)                         # токен убран из адресной строки
        self.assertNotIn(url.split("#t=")[1], viewers[0].content())   # и не выводится на экран

        def everyone_on(stage, limit_ms=2000):
            started = time.time()
            for page in viewers:
                page.wait_for_selector(f".st[data-stage='{stage}']", timeout=limit_ms)
            self.assertLess(time.time() - started, limit_ms / 1000)
            for page in viewers:
                self.assertLessEqual(overflow(page), 0, f"{stage}: горизонтальная прокрутка")
                self.assertGreaterEqual(page.locator(f"text={BANNER}").count(), 1, f"{stage}: нет маркировки")
                self.assertEqual(page.locator("input, select, textarea").count(), 0)   # зритель ничего не вводит

        op.click("text=▶ Запуск")
        everyone_on("normal")
        for page in viewers:
            self.assertGreaterEqual(page.locator("text=Последний замер").count(), 1)
        for click, stage in (("Следующий этап →", "incident_started"), ("Следующий этап →", "diagnostics"),
                             ("Следующий этап →", "ml_result"), ("Следующий этап →", "operator_review")):
            op.click(f"text={click}")
            everyone_on(stage)
        for page in viewers:
            self.assertGreaterEqual(page.locator("text=Ожидает решения оператора").count(), 1)
            self.assertGreaterEqual(page.locator("text=Узел провайдера в районе").count(), 1)
            self.assertEqual(page.locator("button").count(), 0)        # до акта у зрителя нет ни одной кнопки
        self.assertTrue(op.locator("text=Следующий этап →").is_disabled())   # человек не обходится

        viewers[0].reload()                                            # перезагрузка не теряет этап
        viewers[0].wait_for_selector(".st[data-stage='operator_review']", timeout=5000)

        op.click("text=Подтвердить вердикт")
        everyone_on("operator_confirmed")
        op.click("text=Сформировать акт")
        everyone_on("report_ready")
        with viewers[0].expect_download() as download:
            viewers[0].click("text=Скачать PDF-акт SLA")
        self.assertTrue(Path(download.value.path()).read_bytes().startswith(b"%PDF"))
        self.assertEqual(viewers[0].locator("button").count(), 1)      # единственная кнопка зрителя — скачать акт

        op.once("dialog", lambda dialog: dialog.accept())
        op.click("text=↺ Сброс")
        everyone_on("reset")
        for ctx in (op_ctx, phone, laptop, tiny):
            ctx.close()

    def test_late_joiner_sees_current_stage_at_once(self):
        ctx = BROWSER.new_context(viewport={"width": 1280, "height": 900})
        op, url = operator_creates_session(ctx)
        op.click("text=▶ Запуск")
        op.click("text=Следующий этап →")
        late = BROWSER.new_context(viewport={"width": 390, "height": 844}, is_mobile=True).new_page()
        late.goto(url)
        late.wait_for_selector(".st[data-stage='incident_started']", timeout=3000)
        self.assertGreaterEqual(late.locator("text=затронутым школам (4 из 12)").count(), 1)
        ctx.close()

    def test_replay_works_when_external_network_hangs(self):
        """Wi-Fi есть, интернета нет: запросы наружу «висят». Резервный режим всё равно открывается."""
        ctx = BROWSER.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
        page = ctx.new_page()
        external = []
        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(BASE)
                   else external.append(route.request.url))          # не отвечаем и не отменяем: «чёрная дыра»
        started = time.time()
        page.goto(f"{BASE}/live/replay", wait_until="domcontentloaded")
        page.wait_for_selector(f"text={BANNER}", timeout=8000)
        self.assertLess(time.time() - started, 8)
        self.assertGreaterEqual(page.locator("text=ВОСПРОИЗВЕДЕНИЕ ЗАПИСИ").count(), 1)
        seen = []
        for _ in range(13):
            stage = page.locator(".st").get_attribute("data-stage")
            seen.append(stage)
            if stage == "report_ready":
                self.assertGreaterEqual(page.locator("a:text('Скачать PDF-акт SLA')").count(), 1)
            page.keyboard.press("ArrowRight")
        self.assertIn("ml_result", seen)
        self.assertIn("operator_review", seen)
        self.assertIn("report_ready", seen)
        self.assertLessEqual(overflow(page), 0)
        self.assertEqual(external, [])                                # страницы демо не ходят наружу
        ctx.close()

    def test_replay_opens_from_plain_static_server_without_our_backend(self):
        port = _free_port()
        static = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
                                   "--directory", str(DIST)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(1)
            ctx = BROWSER.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
            page = ctx.new_page()
            page.goto(f"http://127.0.0.1:{port}/#replay")
            page.wait_for_selector("text=ВОСПРОИЗВЕДЕНИЕ ЗАПИСИ", timeout=8000)
            for _ in range(8):
                page.keyboard.press("ArrowRight")
            self.assertEqual(page.locator(".st").get_attribute("data-stage"), "ml_result")
            self.assertGreaterEqual(page.locator(f"text={BANNER}").count(), 1)
            ctx.close()
        finally:
            static.terminate()

    def test_expired_link_shows_generic_message(self):
        ctx = BROWSER.new_context()
        page = ctx.new_page()
        page.goto(f"{BASE}/live/0123456789ab#t=1.invalid")
        page.wait_for_selector("text=Ссылка недействительна")
        for word in ("Traceback", "JWT", "Unauthorized"):
            self.assertNotIn(word, page.content())
        ctx.close()


if __name__ == "__main__":
    unittest.main()
