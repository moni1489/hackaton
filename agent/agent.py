"""ПК-агент САМ ВКО v2.

Устанавливается на отдельный компьютер школы и обеспечивает ПК-уровень
прослеживания качества связи.

Особенности:
  • регистрация по коду развёртывания школы (постоянному, не одноразовому); токен
    выдаётся под отпечаток оборудования. Привязку к железу гарантирует только mTLS;
  • локальный спул (SQLite): при обрыве связи замеры копятся и уходят пакетами (Smart
    Sync). Копия удаляется ТОЛЬКО после того, как сервер подтвердил запись пакета
    (status=done), а не на ответе 202 — принятый пакет ещё может быть не записан;
  • выгрузка со случайным джиттером старта, чтобы сотни агентов не ударили в сервер вместе;
  • интервал тестирования задаёт сервер (динамическая конфигурация);
  • точка мониторинга линии — VKO_DEVICE_TYPE=Шлюз (+ VKO_LINE_CODE / VKO_LINE_ROLE):
    только она определяет статус школы; обычное рабочее место оценивается отдельно.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import random
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import requests

BACKEND_URL = os.environ.get("VKO_BACKEND", "https://codemasters1.onrender.com")
SCHOOL_CODE = os.environ.get("VKO_SCHOOL_CODE", "VKO-RID-001")
ENROLL_SECRET = os.environ.get("VKO_ENROLL_SECRET", "")
ROOM = os.environ.get("VKO_ROOM", "Кабинет информатики №1")
DEVICE_TYPE = os.environ.get("VKO_DEVICE_TYPE", "Рабочая станция")   # «Шлюз» — точка мониторинга линии
LINE_CODE = os.environ.get("VKO_LINE_CODE")   # какую линию измеряет точка мониторинга
LINE_ROLE = os.environ.get("VKO_LINE_ROLE")   # main | backup — если линию создаёт эта регистрация
VERIFY_TLS = os.environ.get("VKO_VERIFY_TLS", "1") != "0"
CLIENT_CERT = os.environ.get("VKO_CLIENT_CERT")   # путь к mTLS-сертификату (cert,key)
STATE_DIR = Path(os.environ.get("VKO_STATE_DIR", Path.home() / ".vko-agent"))
AGENT_VERSION = "2.0.0"

STATE_DIR.mkdir(parents=True, exist_ok=True)
SPOOL_DB = STATE_DIR / "spool.sqlite"
STATE_FILE = STATE_DIR / "state.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vko-agent")


# --- Идентичность ПК -------------------------------------------------------

def machine_id() -> str:
    """Стабильный идентификатор конкретного компьютера."""
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            return Path(path).read_text().strip()
        except OSError:
            continue
    return f"{uuid.getnode():012x}"


def mac_address() -> str:
    node = uuid.getnode()
    return ":".join(f"{(node >> shift) & 0xFF:02X}" for shift in range(40, -8, -8))


def inventory() -> dict:
    """Инвентарные данные ПК. Персональные данные не собираются."""
    host = socket.gethostname()
    try:
        ip = socket.gethostbyname(host)
    except OSError:
        ip = "0.0.0.0"
    try:
        ram_gb = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3, 1)
    except (ValueError, OSError, AttributeError):
        ram_gb = None
    return {
        "device_id": f"PC-{SCHOOL_CODE}-{machine_id()[:6].upper()}",
        "name": f"ПК-агент · {host}",
        "room": ROOM,
        "ip_address": ip,
        "mac_address": mac_address(),
        "os_name": f"{platform.system()} {platform.release()}",
        "cpu_model": platform.processor() or platform.machine(),
        "ram_gb": ram_gb,
        "agent_version": AGENT_VERSION,
        "device_type": DEVICE_TYPE,
        "link_mode": "Ethernet 1 Гбит/с",
        "line_code": LINE_CODE,
        "line_role": LINE_ROLE,
    }


# --- Локальный спул --------------------------------------------------------

def spool_init() -> sqlite3.Connection:
    conn = sqlite3.connect(SPOOL_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS pending (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        batch_id INTEGER)""")          # batch_id — пакет сервера, принявший строку (ждёт подтверждения)
    if "batch_id" not in {row[1] for row in conn.execute("PRAGMA table_info(pending)")}:
        conn.execute("ALTER TABLE pending ADD COLUMN batch_id INTEGER")   # спул старой версии
    conn.commit()
    return conn


def spool_add(conn: sqlite3.Connection, payload: dict) -> None:
    conn.execute("INSERT INTO pending (payload, created_at) VALUES (?, ?)",
                 (json.dumps(payload), datetime.now(UTC).isoformat()))
    conn.commit()


def spool_take(conn: sqlite3.Connection, limit: int) -> tuple[list[int], list[dict]]:
    """Строки, ещё не принятые сервером."""
    rows = conn.execute("SELECT id, payload FROM pending WHERE batch_id IS NULL ORDER BY id LIMIT ?",
                        (limit,)).fetchall()
    return [r[0] for r in rows], [json.loads(r[1]) for r in rows]


def spool_mark_sent(conn: sqlite3.Connection, ids: list[int], batch_id: int) -> None:
    conn.executemany("UPDATE pending SET batch_id = ? WHERE id = ?", [(batch_id, i) for i in ids])
    conn.commit()


def spool_release(conn: sqlite3.Connection, batch_id: int) -> None:
    conn.execute("UPDATE pending SET batch_id = NULL WHERE batch_id = ?", (batch_id,))
    conn.commit()


def spool_drop_batch(conn: sqlite3.Connection, batch_id: int) -> None:
    conn.execute("DELETE FROM pending WHERE batch_id = ?", (batch_id,))
    conn.commit()


def spool_size(conn: sqlite3.Connection, unsent_only: bool = False) -> int:
    where = " WHERE batch_id IS NULL" if unsent_only else ""
    return conn.execute(f"SELECT COUNT(*) FROM pending{where}").fetchone()[0]


# --- HTTP ------------------------------------------------------------------

def session_for(token: str | None) -> requests.Session:
    session = requests.Session()
    session.verify = VERIFY_TLS            # HTTPS/TLS обязателен в проде
    if CLIENT_CERT and "," in CLIENT_CERT:  # mTLS: клиентский сертификат устройства
        session.cert = tuple(CLIENT_CERT.split(",", 1))
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    session.headers["User-Agent"] = f"VKO-Agent/{AGENT_VERSION}"
    return session


def enroll() -> dict:
    """Регистрация ПК и получение device-токена."""
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
        if state.get("device_token"):
            log.info("Использую сохранённый токен устройства %s", state["device_id"])
            return state

    if not ENROLL_SECRET:
        log.error("Не задан VKO_ENROLL_SECRET — получите код в панели администратора "
                  "(Управление → код развёртывания).")
        sys.exit(1)

    payload = {**inventory(), "school_id_code": SCHOOL_CODE,
               "enrollment_secret": ENROLL_SECRET,
               "cert_fingerprint": hashlib.sha256(machine_id().encode()).hexdigest()[:40]}
    response = session_for(None).post(f"{BACKEND_URL}/api/agent/enroll", json=payload, timeout=20)
    if response.status_code != 200:
        log.error("Регистрация отклонена (%s): %s", response.status_code, response.text[:200])
        sys.exit(1)

    state = response.json()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    try:
        STATE_FILE.chmod(0o600)   # токен доступен только владельцу
    except OSError:
        pass
    log.info("ПК зарегистрирован: %s (школа %s)", state["device_id"], state["school_id"])
    return state


# --- Замер -----------------------------------------------------------------

def measure(deep: bool) -> dict:
    """Инструментальный замер. В режиме deep добавляется контроль потерь пакетов."""
    result = {"timestamp": datetime.now(UTC).isoformat(), "download_speed": 0.0,
              "upload_speed": 0.0, "ping": 0.0, "jitter": 0.0, "packet_loss": 0.0,
              "is_offline": True, "source": "live"}
    try:
        proc = subprocess.run(["speedtest-cli", "--json"], capture_output=True,
                              text=True, timeout=180)
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            result.update(download_speed=round(data["download"] / 1e6, 2),
                          upload_speed=round(data["upload"] / 1e6, 2),
                          ping=round(data["ping"], 2), is_offline=False)
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError, KeyError) as exc:
        log.warning("Замер скорости не выполнен: %s", exc)

    loss, jitter = ping_probe(count=20 if deep else 5)
    if loss is not None:
        result["packet_loss"] = loss
        result["jitter"] = jitter
        if loss >= 100:
            result["is_offline"] = True
        elif result["is_offline"] and loss < 100:
            result["is_offline"] = False      # канал жив, не отработал только speedtest
    return result


def ping_probe(count: int, host: str = "8.8.8.8") -> tuple[float | None, float]:
    """Потери и джиттер по ICMP."""
    try:
        proc = subprocess.run(["ping", "-c", str(count), "-w", str(count * 2), host],
                              capture_output=True, text=True, timeout=count * 3)
        loss = jitter = None
        for line in proc.stdout.splitlines():
            if "packet loss" in line:
                loss = float(line.split("%")[0].split()[-1])
            if "rtt" in line or "round-trip" in line:
                jitter = float(line.split("=")[1].split("/")[3].split()[0])
        return (loss if loss is not None else 100.0), (jitter or 0.0)
    except (subprocess.SubprocessError, OSError, ValueError, IndexError):
        return None, 0.0


# --- Основной цикл ---------------------------------------------------------

def run() -> None:
    state = enroll()
    token, device_id = state["device_token"], state["device_id"]
    config = state.get("config") or {"test_interval_sec": 900, "diagnostic_mode": "standard",
                                     "batch_max_items": 2000}
    conn = spool_init()
    log.info("Агент запущен. Сервер: %s, интервал: %s с", BACKEND_URL,
             config["test_interval_sec"])

    while True:
        deep = config.get("diagnostic_mode") == "deep"
        sample = measure(deep)
        log.info("Замер: ↓%s Мбит/с ↑%s ping %s мс loss %s%%", sample["download_speed"],
                 sample["upload_speed"], sample["ping"], sample["packet_loss"])

        session = session_for(token)
        try:
            response = session.post(f"{BACKEND_URL}/api/agent/measurements",
                                    json=sample, timeout=15)
            if response.status_code in (200, 201):
                config = response.json().get("config", config)
                flush_spool(session, conn, config)
            elif response.status_code in (401, 403):
                log.error("Токен отклонён сервером — требуется повторная регистрация")
                STATE_FILE.unlink(missing_ok=True)
                sys.exit(2)
            else:
                raise requests.RequestException(f"HTTP {response.status_code}")
        except requests.RequestException as exc:
            sample["source"] = "backfill"
            spool_add(conn, sample)
            log.warning("Сервер недоступен (%s). В спуле накоплено: %s замеров",
                        exc, spool_size(conn))

        # Джиттер интервала ±20%: агенты не синхронизируются между собой
        interval = config.get("test_interval_sec", 900)
        time.sleep(max(30, int(interval * random.uniform(0.8, 1.2))))


def confirm_sent(session: requests.Session, conn: sqlite3.Connection) -> None:
    """Удаляет локальные копии ТОЛЬКО после подтверждения записи сервером (status=done).

    202 значит «пакет принят», не «замеры записаны». Пока сервер не подтвердил — копия
    остаётся; если он пакета не знает (404) — строки возвращаются в очередь на отправку.
    """
    batches = [row[0] for row in conn.execute(
        "SELECT DISTINCT batch_id FROM pending WHERE batch_id IS NOT NULL")]
    for batch_id in batches:
        try:
            response = session.get(f"{BACKEND_URL}/api/agent/measurements/batch/{batch_id}",
                                   timeout=15)
        except requests.RequestException:
            return                       # связи нет — спросим в следующем цикле
        if response.status_code == 404:
            spool_release(conn, batch_id)
        elif response.status_code == 200:
            state = response.json()
            if state.get("status") == "done":
                spool_drop_batch(conn, batch_id)
                log.info("Сервер подтвердил запись пакета %s", batch_id)
            elif state.get("status") == "failed":
                log.warning("Пакет %s пока не записан (%s) — копия сохранена, сервер повторит",
                            batch_id, state.get("error"))


def flush_spool(session: requests.Session, conn: sqlite3.Connection, config: dict) -> None:
    """Smart Sync: выгружает накопленное пакетами, уважая backpressure сервера."""
    if not spool_size(conn):
        return
    confirm_sent(session, conn)
    pending = spool_size(conn, unsent_only=True)
    if not pending:
        return
    log.info("Восстановлена связь. Выгружаю накопленные замеры: %s", pending)
    # Случайная задержка старта выгрузки: размазывает лавину одновременных агентов
    time.sleep(random.uniform(0, 30))

    limit = min(config.get("batch_max_items", 2000), 500)
    while spool_size(conn, unsent_only=True):
        ids, items = spool_take(conn, limit)
        try:
            response = session.post(f"{BACKEND_URL}/api/agent/measurements/batch",
                                    json={"items": items}, timeout=60)
        except requests.RequestException as exc:
            log.warning("Выгрузка прервана: %s — повторю в следующем цикле", exc)
            return
        if response.status_code == 202:
            spool_mark_sent(conn, ids, response.json()["batch_id"])
            log.info("Пакет принят сервером: %s замеров; копии хранятся до подтверждения записи",
                     len(ids))
        elif response.status_code == 503:
            wait = int(response.headers.get("Retry-After", 120))
            log.warning("Сервер перегружен, повтор через %s с", wait)
            time.sleep(wait)
            return
        else:
            log.error("Пакет отклонён (%s): %s", response.status_code, response.text[:200])
            return


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        log.info("Агент остановлен")
