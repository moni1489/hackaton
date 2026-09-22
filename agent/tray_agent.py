"""
САМ ВКО — Агент мониторинга в системном трее
Работает на Windows и Linux (X11/Wayland через AppIndicator/pystray).

Установка:
  pip install pystray pillow requests speedtest-cli

Запуск:
  python tray_agent.py

Первый запуск:
  Откроется окно настройки (EnrollDialog) для ввода кода школы и адреса сервера.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import queue
import random
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import uuid
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from tkinter import messagebox, ttk

import requests

# Защита от 'NoneType' object has no attribute 'fileno' в PyInstaller noconsole режиме
if sys.stdout is None:
    try:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass
if sys.stderr is None:
    try:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass

# ── Попытка импорта pystray + PIL ──────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw, ImageFont
    import pystray
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False
    print("[WARN] pystray/Pillow не установлены — запуск в консольном режиме")

AGENT_VERSION = "2.1.0"
DEFAULT_DASHBOARD_URL = "https://hackatondsa.vercel.app/"
DEFAULT_BACKEND = "https://codemasters1.onrender.com"
STATE_DIR = Path(os.environ.get("VKO_STATE_DIR", Path.home() / ".vko-agent"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
SPOOL_DB   = STATE_DIR / "spool.sqlite"
STATE_FILE = STATE_DIR / "state.json"
CONFIG_FILE = STATE_DIR / "config.json"

log = logging.getLogger("vko-tray")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(STATE_DIR / "agent.log", encoding="utf-8"),
    ],
)

# Заглушаем внутренние ошибки pystray (когда в Wayland/Sway нет X11 systray manager)
for _logger_name in ("pystray", "pystray._base", "pystray._xorg"):
    logging.getLogger(_logger_name).setLevel(logging.CRITICAL)


def is_systray_supported() -> bool:
    """Проверяет наличие активного менеджера системного трея."""
    if platform.system() != "Linux":
        return True
    try:
        import Xlib.display, Xlib.X
        d = Xlib.display.Display()
        atom = d.get_atom(f"_NET_SYSTEM_TRAY_S{d.get_default_screen()}")
        owner = d.get_selection_owner(atom)
        d.close()
        return owner != Xlib.X.NONE
    except Exception:
        return False

# ── Статус агента (shared state) ───────────────────────────────────────────────
STATUS = {
    "text":     "Инициализация...",
    "download": 0.0,
    "upload":   0.0,
    "ping":     0.0,
    "loss":     0.0,
    "offline":  True,
    "spool":    0,
    "last_ok":  None,
}
_status_lock = threading.Lock()
_ui_queue: queue.Queue = queue.Queue()   # события для Tk-потока


def set_status(**kwargs):
    with _status_lock:
        STATUS.update(kwargs)
    _ui_queue.put(("status", dict(STATUS)))


# ──────────────────────────────────────────────────────────────────────────────
#  ИКОНКИ ДЛЯ ТРЕЯ (генерируются динамически через Pillow)
# ──────────────────────────────────────────────────────────────────────────────

_ICON_SIZE = (64, 64)
_COLORS = {
    "ok":      "#16A34A",
    "warn":    "#D97706",
    "error":   "#DC2626",
    "init":    "#475569",
}

def make_icon(state: str = "init") -> "Image.Image":
    """Рисует цветной круг с буквой 'В' (ВКО) для трея."""
    img = Image.new("RGBA", _ICON_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = _COLORS.get(state, _COLORS["init"])
    draw.ellipse([4, 4, 60, 60], fill=color)
    # Буква В по центру
    font = None
    for font_path in (
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ):
        if os.path.exists(font_path):
            try:
                font = ImageFont.truetype(font_path, 32)
                break
            except Exception:
                pass
    if font is None:
        try:
            font = ImageFont.load_default()
        except Exception:
            pass
    try:
        draw.text((20, 12), "В", fill="white", font=font)
    except Exception:
        pass
    return img


# ──────────────────────────────────────────────────────────────────────────────
#  ИДЕНТИЧНОСТЬ ПК
# ──────────────────────────────────────────────────────────────────────────────

def machine_id() -> str:
    if platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"SOFTWARE\Microsoft\Cryptography")
            val, _ = winreg.QueryValueEx(key, "MachineGuid")
            return val
        except Exception:
            pass
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            return Path(path).read_text().strip()
        except OSError:
            pass
    return f"{uuid.getnode():012x}"


def mac_address() -> str:
    node = uuid.getnode()
    return ":".join(f"{(node >> s) & 0xFF:02X}" for s in range(40, -8, -8))


def inventory(school_code: str, room: str) -> dict:
    host = socket.gethostname()
    try:
        ip = socket.gethostbyname(host)
    except OSError:
        ip = "0.0.0.0"
    try:
        if platform.system() == "Windows":
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            ram_gb = round(stat.ullTotalPhys / (1024 ** 3), 1)
        else:
            ram_gb = round(
                os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3, 1
            )
    except Exception:
        ram_gb = None
    return {
        "device_id":        f"PC-{school_code}-{machine_id()[:6].upper()}",
        "name":             f"ПК-агент · {host}",
        "room":             room,
        "ip_address":       ip,
        "mac_address":      mac_address(),
        "os_name":          f"{platform.system()} {platform.release()}",
        "cpu_model":        platform.processor() or platform.machine(),
        "ram_gb":           ram_gb,
        "agent_version":    AGENT_VERSION,
        "device_type":      "Рабочая станция",
        "link_mode":        "Ethernet 1 Гбит/с",
    }


# ──────────────────────────────────────────────────────────────────────────────
#  СПУЛ (локальная SQLite-очередь при обрыве)
# ──────────────────────────────────────────────────────────────────────────────

def spool_init() -> sqlite3.Connection:
    conn = sqlite3.connect(SPOOL_DB, check_same_thread=False)
    conn.execute("""CREATE TABLE IF NOT EXISTS pending (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL)""")
    conn.commit()
    return conn


def spool_add(conn, payload): conn.execute(
    "INSERT INTO pending (payload, created_at) VALUES (?, ?)",
    (json.dumps(payload), datetime.now(UTC).isoformat())); conn.commit()


def spool_take(conn, limit):
    rows = conn.execute("SELECT id, payload FROM pending ORDER BY id LIMIT ?",
                        (limit,)).fetchall()
    return [r[0] for r in rows], [json.loads(r[1]) for r in rows]


def spool_drop(conn, ids):
    conn.executemany("DELETE FROM pending WHERE id = ?", [(i,) for i in ids])
    conn.commit()


def spool_size(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM pending").fetchone()[0]


# ──────────────────────────────────────────────────────────────────────────────
#  ЗАМЕР СКОРОСТИ
# ──────────────────────────────────────────────────────────────────────────────

def _fallback_speed_test() -> tuple[float, float]:
    """Быстрый резервный замер через HTTP, если speedtest-cli недоступен."""
    try:
        t0 = time.perf_counter()
        r = requests.get("https://speed.cloudflare.com/__down?bytes=2500000", timeout=10)
        dt = time.perf_counter() - t0
        if dt > 0 and r.status_code == 200:
            dl_mbps = round((len(r.content) * 8) / (dt * 1e6), 2)
            ul_mbps = round(dl_mbps * 0.85, 2)
            return dl_mbps, ul_mbps
    except Exception as exc:
        log.warning("Резервный тест скорости не удался: %s", exc)
    return 0.0, 0.0


def measure() -> dict:
    result = {
        "timestamp": datetime.now(UTC).isoformat(),
        "download_speed": 0.0, "upload_speed": 0.0,
        "ping": 0.0, "jitter": 0.0, "packet_loss": 0.0,
        "is_offline": True, "source": "live",
    }
    # 1. Сначала быстрая ping-проба
    set_status(text="Ping-проба...")
    loss, ping_avg, jitter = _ping_probe(count=4)
    if loss is not None:
        result["packet_loss"] = loss
        result["jitter"] = jitter
        if ping_avg > 0:
            result["ping"] = ping_avg
        if loss >= 100:
            result["is_offline"] = True
        elif loss < 100:
            result["is_offline"] = False

    # 2. Если связь есть — замер скорости через speedtest или HTTP fallback
    if not result["is_offline"]:
        set_status(text="Тест скорости...", ping=result["ping"], loss=result["packet_loss"])
        speed_ok = False
        try:
            import speedtest
            st = speedtest.Speedtest(timeout=25)
            st.get_best_server()
            st.download()
            st.upload()
            data = st.results.dict()
            result.update(
                download_speed=round(data["download"] / 1e6, 2),
                upload_speed=round(data["upload"] / 1e6, 2),
                ping=round(data.get("ping", result["ping"]), 2),
                is_offline=False,
            )
            speed_ok = True
        except Exception as exc:
            log.warning("Speedtest не выполнен (%s), пробую резервный HTTP замер", exc)

        if not speed_ok or result["download_speed"] <= 0.0:
            dl, ul = _fallback_speed_test()
            if dl > 0:
                result["download_speed"] = dl
                result["upload_speed"] = ul
                result["is_offline"] = False

    set_status(
        download=result["download_speed"],
        upload=result["upload_speed"],
        ping=result["ping"],
        loss=result["packet_loss"],
        offline=result["is_offline"],
        text="В норме" if not result["is_offline"] else "Нет связи",
    )
    return result


def _ping_probe(count: int, host: str = "8.8.8.8") -> tuple[float | None, float, float]:
    """Возвращает (loss_percent, ping_avg_ms, jitter_ms)."""
    try:
        if platform.system() == "Windows":
            cmd = ["ping", "-n", str(count), host]
        else:
            cmd = ["ping", "-c", str(count), "-W", "2", host]
        proc = subprocess.run(cmd, capture_output=True, timeout=count * 3)
        encoding = "cp866" if platform.system() == "Windows" else "utf-8"
        stdout_text = proc.stdout.decode(encoding, errors="replace") if proc.stdout else ""

        loss = None
        ping_avg = 0.0
        jitter = 0.0

        # Поиск процента потерь: (0% потерь), (0% loss), etc.
        m_loss = re.search(r"\((\d+(?:[.,]\d+)?)\s*%", stdout_text)
        if m_loss:
            try:
                loss = float(m_loss.group(1).replace(",", "."))
            except ValueError:
                pass

        if loss is None:
            for line in stdout_text.splitlines():
                if "packet loss" in line.lower() or "потер" in line.lower() or "lost" in line.lower():
                    for part in line.split():
                        p = part.strip("%()")
                        try:
                            loss = float(p.replace(",", "."))
                            break
                        except ValueError:
                            pass

        # Поиск среднего пинга (Windows: "Среднее = 91 мсек" / "Average = 91ms", Linux: rtt min/avg/max/mdev)
        m_avg = re.search(r"(?:среднее|average)\s*=\s*(\d+(?:[.,]\d+)?)", stdout_text, re.IGNORECASE)
        if m_avg:
            try:
                ping_avg = float(m_avg.group(1).replace(",", "."))
            except ValueError:
                pass
        else:
            m_rtt = re.search(r"rtt\s+min/avg/max/(?:mdev|jitter)\s*=\s*[\d.]+/([\d.]+)/[\d.]+/([\d.]+)", stdout_text)
            if m_rtt:
                try:
                    ping_avg = float(m_rtt.group(1))
                    jitter = float(m_rtt.group(2))
                except ValueError:
                    pass

        # Для Windows jitter оцениваем как разницу (максимальное - минимальное)
        if jitter == 0.0:
            m_min = re.search(r"(?:минимальное|minimum)\s*=\s*(\d+(?:[.,]\d+)?)", stdout_text, re.IGNORECASE)
            m_max = re.search(r"(?:максимальное|maximum)\s*=\s*(\d+(?:[.,]\d+)?)", stdout_text, re.IGNORECASE)
            if m_min and m_max:
                try:
                    jitter = abs(float(m_max.group(1).replace(",", ".")) - float(m_min.group(1).replace(",", ".")))
                except ValueError:
                    pass

        return (loss if loss is not None else (100.0 if not stdout_text else 0.0)), ping_avg, jitter
    except Exception:
        return None, 0.0, 0.0


# ──────────────────────────────────────────────────────────────────────────────
#  РЕГИСТРАЦИЯ АГЕНТА
# ──────────────────────────────────────────────────────────────────────────────

def make_session(token: str | None, verify: bool = True) -> requests.Session:
    s = requests.Session()
    s.verify = verify
    s.headers["User-Agent"] = f"VKO-Agent/{AGENT_VERSION}"
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    return s


def enroll(backend: str, school_code: str, enroll_secret: str,
           room: str, verify_tls: bool) -> dict:
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
        if state.get("device_token"):
            log.info("Используется сохранённый токен: %s", state["device_id"])
            return state
    payload = {
        **inventory(school_code, room),
        "school_id_code": school_code,
        "enrollment_secret": enroll_secret,
        "cert_fingerprint": hashlib.sha256(machine_id().encode()).hexdigest()[:40],
    }
    resp = make_session(None, verify_tls).post(
        f"{backend}/api/agent/enroll", json=payload, timeout=20
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Регистрация отклонена ({resp.status_code}): {resp.text[:200]}")
    state = resp.json()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    try:
        STATE_FILE.chmod(0o600)
    except OSError:
        pass
    log.info("ПК зарегистрирован: %s (школа %s)", state["device_id"], state["school_id"])
    return state


# ──────────────────────────────────────────────────────────────────────────────
#  ОСНОВНОЙ ЦИКЛ АГЕНТА (отдельный поток)
# ──────────────────────────────────────────────────────────────────────────────

_stop_event = threading.Event()


def agent_loop(cfg: dict) -> None:
    backend      = cfg["backend"]
    school_code  = cfg["school_code"]
    enroll_secret= cfg["enroll_secret"]
    room         = cfg.get("room", "Кабинет информатики")
    verify_tls   = cfg.get("verify_tls", True)

    set_status(text="Регистрация на сервере...")
    try:
        state = enroll(backend, school_code, enroll_secret, room, verify_tls)
    except Exception as exc:
        set_status(text=f"Ошибка регистрации: {exc}", offline=True)
        _ui_queue.put(("error", str(exc)))
        _ui_queue.put(("reauth", None))
        return

    token     = state["device_token"]
    device_id = state["device_id"]
    srv_cfg   = state.get("config") or {"test_interval_sec": 900}

    conn = spool_init()
    log.info("Агент запущен. Интервал: %s с", srv_cfg.get("test_interval_sec", 900))
    set_status(text="Агент работает")

    while not _stop_event.is_set():
        sample = measure()
        sample["device_id"] = device_id

        log.info("↓%.1f ↑%.1f ping%.0f loss%.0f%%",
                 sample["download_speed"], sample["upload_speed"],
                 sample["ping"], sample["packet_loss"])

        session = make_session(token, verify_tls)
        try:
            resp = session.post(
                f"{backend}/api/agent/measurements",
                json=sample, timeout=15,
            )
            if resp.status_code in (200, 201):
                srv_cfg = resp.json().get("config", srv_cfg)
                set_status(last_ok=datetime.now().strftime("%H:%M:%S"), spool=spool_size(conn))
                _flush_spool(session, conn, srv_cfg, backend)
            elif resp.status_code in (401, 403):
                set_status(text="Токен отклонён — требуется повторная регистрация", offline=True)
                STATE_FILE.unlink(missing_ok=True)
                _ui_queue.put(("reauth", None))
                return
            else:
                raise requests.RequestException(f"HTTP {resp.status_code}")
        except requests.RequestException as exc:
            sample["source"] = "backfill"
            spool_add(conn, sample)
            n = spool_size(conn)
            set_status(text=f"Сервер недоступен. Спул: {n} замеров", spool=n)
            log.warning("Сервер: %s | спул=%s", exc, n)

        interval = srv_cfg.get("test_interval_sec", 900)
        sleep_sec = max(30, int(interval * random.uniform(0.8, 1.2)))
        log.info("Следующий замер через %s с", sleep_sec)
        _stop_event.wait(sleep_sec)


def _flush_spool(session, conn, srv_cfg, backend):
    pending = spool_size(conn)
    if not pending:
        return
    log.info("Восстановлена связь. Выгружаю %s замеров из спула", pending)
    time.sleep(random.uniform(0, 10))
    limit = min(srv_cfg.get("batch_max_items", 2000), 500)
    while spool_size(conn) and not _stop_event.is_set():
        ids, items = spool_take(conn, limit)
        try:
            resp = session.post(
                f"{backend}/api/agent/measurements/batch",
                json={"items": items}, timeout=60,
            )
        except requests.RequestException as exc:
            log.warning("Выгрузка прервана: %s", exc)
            return
        if resp.status_code == 202:
            spool_drop(conn, ids)
        elif resp.status_code == 503:
            wait = int(resp.headers.get("Retry-After", 120))
            log.warning("Сервер перегружен. Повтор через %s с", wait)
            _stop_event.wait(wait)
            return
        else:
            log.error("Пакет отклонён (%s): %s", resp.status_code, resp.text[:200])
            return


# ──────────────────────────────────────────────────────────────────────────────
#  GUI: ДИАЛОГ НАСТРОЙКИ (первый запуск / реавторизация)
# ──────────────────────────────────────────────────────────────────────────────

class EnrollDialog(tk.Toplevel):
    """Окно первоначальной настройки агента."""

    def __init__(self, master, on_save):
        super().__init__(master)
        self.title("САМ ВКО — Настройка агента")
        self.resizable(False, False)
        self.grab_set()
        self.on_save = on_save

        # Загрузим сохранённый конфиг если есть
        saved = {}
        if CONFIG_FILE.exists():
            try:
                saved = json.loads(CONFIG_FILE.read_text())
            except Exception:
                pass

        pad = {"padx": 12, "pady": 6}
        frame = ttk.Frame(self, padding=20)
        frame.grid(row=0, column=0, sticky="nsew")

        # Заголовок
        ttk.Label(frame, text="САМ ВКО · Агент мониторинга",
                  font=("Segoe UI", 13, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 16))

        fields = [
            ("Адрес сервера:", "backend",       saved.get("backend", DEFAULT_BACKEND)),
            ("Код школы:",     "school_code",    saved.get("school_code", "VKO-RID-001")),
            ("Код развёртывания:", "enroll_secret", saved.get("enroll_secret", "3bc0679debbf")),
            ("Кабинет/Комната:",  "room",        saved.get("room", "Кабинет информатики №1")),
        ]
        self._vars: dict[str, tk.StringVar] = {}
        for i, (label, key, default) in enumerate(fields, start=1):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="w", **pad)
            var = tk.StringVar(value=default)
            self._vars[key] = var
            show = "*" if key == "enroll_secret" else ""
            ttk.Entry(frame, textvariable=var, width=38, show=show).grid(
                row=i, column=1, sticky="ew", **pad)

        self._verify_var = tk.BooleanVar(value=saved.get("verify_tls", True))
        ttk.Checkbutton(frame, text="Проверять TLS-сертификат сервера",
                        variable=self._verify_var).grid(
            row=len(fields) + 1, column=0, columnspan=2, sticky="w", **pad)

        ttk.Label(frame,
                  text="Код развёртывания — получите в панели администратора\n"
                       "Управление → Школы → «Код агента»",
                  foreground="gray", font=("Segoe UI", 9)).grid(
            row=len(fields) + 2, column=0, columnspan=2, pady=(0, 10))

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=len(fields) + 3, column=0, columnspan=2)
        ttk.Button(btn_frame, text="Сохранить и запустить",
                   command=self._save).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="Отмена",
                   command=self.destroy).pack(side="left", padx=6)

    def _save(self):
        cfg = {k: v.get().strip() for k, v in self._vars.items()}
        cfg["verify_tls"] = self._verify_var.get()
        if not cfg["backend"] or not cfg["school_code"] or not cfg["enroll_secret"]:
            messagebox.showerror("Ошибка", "Заполните все обязательные поля.", parent=self)
            return
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
        self.destroy()
        self.on_save(cfg)


# ──────────────────────────────────────────────────────────────────────────────
#  GUI: СТАТУСНОЕ ОКНО
# ──────────────────────────────────────────────────────────────────────────────

class StatusWindow(tk.Toplevel):
    def __init__(self, master, has_tray_func=None):
        super().__init__(master)
        self._has_tray_func = has_tray_func
        self.title("САМ ВКО — Статус агента")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="САМ ВКО · Агент мониторинга",
                  font=("Segoe UI", 12, "bold")).pack(pady=(0, 12))

        self._labels: dict[str, tk.StringVar] = {}
        rows = [
            ("Состояние",     "text"),
            ("↓ Download",    "download"),
            ("↑ Upload",      "upload"),
            ("Ping",          "ping"),
            ("Packet Loss",   "loss"),
            ("Последний OK",  "last_ok"),
            ("В очереди (спул)", "spool"),
        ]
        for label, key in rows:
            row_f = ttk.Frame(frame)
            row_f.pack(fill="x", pady=2)
            ttk.Label(row_f, text=f"{label}:", width=22, anchor="w").pack(side="left")
            var = tk.StringVar(value="—")
            self._labels[key] = var
            ttk.Label(row_f, textvariable=var, anchor="w",
                      font=("Segoe UI", 10, "bold")).pack(side="left")

        panel_url = os.environ.get("VKO_FRONTEND", DEFAULT_DASHBOARD_URL)
        def _open_panel():
            if platform.system() == "Windows":
                webbrowser.open(panel_url)
            else:
                try:
                    subprocess.Popen(["xdg-open", panel_url])
                except Exception:
                    webbrowser.open(panel_url)

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=(16, 0))
        ttk.Button(btn_frame, text="Открыть панель",
                   command=_open_panel).pack(side="left", padx=4)

        has_tray = self._has_tray_func() if self._has_tray_func else False
        close_text = "Скрыть" if has_tray else "Выход"
        ttk.Button(btn_frame, text=close_text,
                   command=self._on_close).pack(side="left", padx=4)

        if has_tray:
            self.withdraw()
        else:
            self.deiconify()

    def _on_close(self):
        has_tray = self._has_tray_func() if self._has_tray_func else False
        if has_tray:
            self.withdraw()
        else:
            self.master.destroy()

    def refresh(self, st: dict):
        unit = {"download": " Мбит/с", "upload": " Мбит/с",
                "ping": " мс", "loss": "%", "spool": " замеров"}
        for key, var in self._labels.items():
            val = st.get(key, "—")
            if isinstance(val, float):
                val = f"{val:.1f}"
            if val is None:
                val = "—"
            suf = unit.get(key, "")
            var.set(f"{val}{suf}")


# ──────────────────────────────────────────────────────────────────────────────
#  ГЛАВНОЕ ПРИЛОЖЕНИЕ
# ──────────────────────────────────────────────────────────────────────────────

class TrayApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()   # главное окно скрыто, работаем через трей
        self.root.title("САМ ВКО")

        self._tray_icon: "pystray.Icon | None" = None
        self._agent_thread: threading.Thread | None = None
        self._has_active_tray = False
        self._status_win = StatusWindow(self.root, has_tray_func=lambda: self._has_active_tray)

        # Периодическая проверка очереди событий из фонового потока
        self.root.after(500, self._poll_ui_queue)

    # ── Трей ──────────────────────────────────────────────────────────────────

    def _build_tray_menu(self) -> "pystray.Menu":
        return pystray.Menu(
            pystray.MenuItem("📊 Показать статус", self._show_status, default=True),
            pystray.MenuItem("🌐 Открыть панель", lambda: webbrowser.open(DEFAULT_DASHBOARD_URL)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("⚙ Настройки", self._open_settings),
            pystray.MenuItem("🔄 Перезапустить агент", self._restart_agent),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("✖ Завершить", self._quit),
        )

    def _run_tray_safely(self):
        try:
            if self._tray_icon:
                self._tray_icon.run()
        except Exception as e:
            log.warning("Pystray loop завершился или не поддерживается WM: %s", e)

    def _start_tray(self):
        if not HAS_TRAY or not is_systray_supported():
            log.info("Системный трей не обнаружен в оконном менеджере. Агент работает через окно статуса.")
            self._has_active_tray = False
            self.root.after(100, self._status_win.deiconify)
            return

        icon_img = make_icon("init")
        try:
            # На Linux X11/Xlib заголовок окна WM_NAME кодируется в latin-1,
            # поэтому кириллица вызывает UnicodeEncodeError. Для Linux используем ASCII.
            tray_title = "SAM-VKO Agent" if platform.system() == "Linux" else "САМ ВКО"
            self._tray_icon = pystray.Icon(
                "vko-agent", icon_img, tray_title, menu=self._build_tray_menu()
            )
            self._has_active_tray = True
            t = threading.Thread(target=self._run_tray_safely, daemon=True)
            t.start()
        except Exception as exc:
            log.warning("Системный трей недоступен в этом окружении (%s). Окно статуса открыто.", exc)
            self._tray_icon = None
            self._has_active_tray = False
            self.root.after(100, self._status_win.deiconify)

    def _update_tray_icon(self, st: dict):
        if not self._tray_icon:
            return
        try:
            if st["offline"]:
                state = "error"
            elif st["loss"] > 5 or (st["download"] > 0 and st["download"] < 10):
                state = "warn"
            else:
                state = "ok"
            self._tray_icon.icon = make_icon(state)
            if platform.system() == "Linux":
                self._tray_icon.title = (
                    f"SAM VKO: D:{st['download']:.0f} U:{st['upload']:.0f} "
                    f"ping:{st['ping']:.0f}ms"
                )
            else:
                self._tray_icon.title = (
                    f"САМ ВКО  ↓{st['download']:.0f} ↑{st['upload']:.0f} "
                    f"ping {st['ping']:.0f}мс  {st['text']}"
                )
        except Exception:
            pass

    # ── Обработка событий из фонового потока ──────────────────────────────────

    def _poll_ui_queue(self):
        try:
            while True:
                event, data = _ui_queue.get_nowait()
                if event == "status":
                    self._status_win.refresh(data)
                    self._update_tray_icon(data)
                elif event == "error":
                    messagebox.showerror("Ошибка агента", data, parent=self.root)
                elif event == "reauth":
                    self._open_settings()
        except queue.Empty:
            pass
        self.root.after(500, self._poll_ui_queue)

    # ── Управление агентом ────────────────────────────────────────────────────

    def _start_agent(self, cfg: dict):
        _stop_event.clear()
        self._agent_thread = threading.Thread(
            target=agent_loop, args=(cfg,), daemon=True
        )
        self._agent_thread.start()

    def _restart_agent(self, *_):
        _stop_event.set()
        if CONFIG_FILE.exists():
            cfg = json.loads(CONFIG_FILE.read_text())
            time.sleep(0.5)
            self._start_agent(cfg)

    # ── Кнопки трей-меню ──────────────────────────────────────────────────────

    def _show_status(self, *_):
        self.root.after(0, self._status_win.deiconify)

    def _open_settings(self, *_):
        def on_save(cfg):
            _stop_event.set()
            time.sleep(0.3)
            # сбрасываем сохранённый токен если поменяли школу
            if STATE_FILE.exists():
                old = json.loads(STATE_FILE.read_text())
                if old.get("school_id_code") != cfg.get("school_code"):
                    STATE_FILE.unlink(missing_ok=True)
            self._start_agent(cfg)
        self.root.after(0, lambda: EnrollDialog(self.root, on_save))

    def _quit(self, *_):
        _stop_event.set()
        if self._tray_icon:
            self._tray_icon.stop()
        self.root.after(0, self.root.quit)

    # ── Запуск ────────────────────────────────────────────────────────────────

    def run(self):
        self._start_tray()

        if CONFIG_FILE.exists():
            try:
                cfg = json.loads(CONFIG_FILE.read_text())
                self._start_agent(cfg)
            except Exception as exc:
                log.error("Не удалось прочитать конфиг: %s", exc)
                self.root.after(200, self._open_settings)
        else:
            # Первый запуск — показываем диалог настройки
            self.root.after(200, self._open_settings)

        self.root.mainloop()


# ──────────────────────────────────────────────────────────────────────────────
#  АВТОЗАПУСК (Windows: реестр, Linux: ~/.config/autostart)
# ──────────────────────────────────────────────────────────────────────────────

def install_autostart():
    exe = sys.executable
    script = Path(__file__).resolve()

    if platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE,
            )
            winreg.SetValueEx(key, "SAM-VKO-Agent", 0, winreg.REG_SZ,
                              f'"{exe}" "{script}"')
            winreg.CloseKey(key)
            print("✅ Автозапуск добавлен в реестр Windows")
        except Exception as exc:
            print(f"❌ Ошибка добавления в реестр: {exc}")

    elif platform.system() == "Linux":
        autostart_dir = Path.home() / ".config" / "autostart"
        autostart_dir.mkdir(parents=True, exist_ok=True)
        desktop = autostart_dir / "vko-agent.desktop"
        desktop.write_text(
            f"""[Desktop Entry]
Type=Application
Name=САМ ВКО Агент
Comment=Агент мониторинга качества интернета
Exec={exe} {script}
Icon=network-transmit-receive
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
""")
        print(f"✅ Автозапуск добавлен: {desktop}")

    else:
        print(f"⚠ Автозапуск для {platform.system()} не реализован")


_single_instance_handle = None

def ensure_single_instance() -> bool:
    """Запрещает запуск нескольких копий агента одновременно."""
    global _single_instance_handle
    if platform.system() == "Windows":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # Локальный мьютекс сессии пользователя (без Global\, чтобы не требовались права админа)
            mutex = kernel32.CreateMutexW(None, False, "SAM_VKO_Tray_Agent_Mutex")
            last_err = kernel32.GetLastError()
            if not mutex or last_err == 183:  # ERROR_ALREADY_EXISTS (183)
                if mutex:
                    kernel32.CloseHandle(mutex)
                return False
            _single_instance_handle = mutex
            return True
        except Exception:
            return True
    else:
        try:
            import fcntl
            lock_path = STATE_DIR / "agent.lock"
            f = open(lock_path, "w")
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _single_instance_handle = f
            return True
        except Exception:
            return False


# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()

    # Защита от рекурсивных вызовов внешних утилит (например speedtest)
    if any(arg in sys.argv for arg in ("-m", "speedtest")):
        sys.exit(0)

    if "--install-autostart" in sys.argv:
        install_autostart()
        sys.exit(0)

    if not ensure_single_instance():
        print("Агент уже запущен в системном трее.")
        sys.exit(0)

    if "--console" in sys.argv or not HAS_TRAY:
        # Консольный режим без GUI (например на сервере или в headless)
        if not CONFIG_FILE.exists():
            print("Нет конфига. Создайте ~/.vko-agent/config.json или запустите без --console")
            sys.exit(1)
        cfg = json.loads(CONFIG_FILE.read_text())
        try:
            agent_loop(cfg)
        except KeyboardInterrupt:
            print("Агент остановлен")
    else:
        app = TrayApp()
        app.run()
