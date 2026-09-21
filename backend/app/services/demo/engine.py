"""Движок демонстрации: сессии, этапы, таймеры, токены зрителей и рассылка изменений.

Схема синхронизации (подробно — docs/demo/ARCHITECTURE.md):
  ведущий → POST /api/demo/... → update() под блокировкой → Store (Redis | память) → версия +1
  фоновый цикл каждого процесса раз в TICK_SEC читает ВЕРСИЮ сессии (один дешёвый вызов на
  процесс, а не на зрителя); версия изменилась → представление строится один раз и
  рассылается всем локальным SSE-подписчикам. Действие в том же процессе рассылается сразу.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta

import jwt

from ...config import settings
from ..ml.features import CAUSES
from . import scenario
from .scenario import HOLD_STAGES, IDX, STAGE_KEYS, build_view, claim_text, report_pdf
from .store import Store

log = logging.getLogger("demo.engine")

TICK_SEC = 0.5
HEARTBEAT_SEC = 5.0
PRESENCE_SEC = 15.0
STEPS = 4                                   # шагов диагностики
DEFAULT_INTERVALS = {"normal": 10, "incident_started": 8, "diagnostics": 20, "ml_result": 12,
                     "operator_confirmed": 6, "report_ready": 0}
MAX_INTERVAL = 600

store = Store()


class DemoError(Exception):
    status = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(DemoError):
    status = 404


class Conflict(DemoError):
    status = 409


# --- Токен зрителя --------------------------------------------------------------------------

def _viewer_key() -> bytes:
    """Отдельный ключ подписи: токен зрителя не проходит как токен пользователя или устройства,
    даже если знать формат остальных токенов."""
    return hashlib.sha256(("demo-viewer|" + settings.JWT_SECRET).encode()).digest()


def issue_viewer_token(sid: str, now: float | None = None) -> tuple[str, datetime]:
    issued = datetime.fromtimestamp(now or time.time(), UTC)
    expires = issued + timedelta(minutes=settings.DEMO_VIEWER_TTL_MIN)
    token = jwt.encode({"typ": "demo_viewer", "sid": sid, "iat": issued, "exp": expires,
                        "jti": secrets.token_hex(4)}, _viewer_key(), algorithm="HS256")
    return token, expires


def verify_viewer_token(token: str, sid: str) -> bool:
    """Токен годен ровно для одной сессии и пока не истёк."""
    try:
        payload = jwt.decode(token, _viewer_key(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return False
    return payload.get("typ") == "demo_viewer" and payload.get("sid") == sid


# --- Сессии ----------------------------------------------------------------------------------

def _iso(now: float) -> str:
    return datetime.fromtimestamp(now, UTC).isoformat(timespec="seconds")


def _clean_settings(raw: dict | None, current: dict | None = None) -> dict:
    cur = current or {"mode": "manual", "intervals": dict(DEFAULT_INTERVALS), "diag_step_sec": 4}
    raw = raw or {}
    mode = raw.get("mode", cur["mode"])
    if mode not in ("manual", "auto"):
        raise DemoError("Режим: manual или auto")
    intervals = dict(cur["intervals"])
    for key, seconds in (raw.get("intervals") or {}).items():
        if key not in STAGE_KEYS or key in HOLD_STAGES:
            raise DemoError(f"Для этапа «{key}» интервал не задаётся")
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) \
                or not 0 <= seconds <= MAX_INTERVAL:
            raise DemoError(f"Интервал этапа «{key}»: число секунд от 0 до {MAX_INTERVAL} (0 — ждать ведущего)")
        intervals[key] = seconds
    step = raw.get("diag_step_sec", cur["diag_step_sec"])
    if not isinstance(step, (int, float)) or isinstance(step, bool) or not 1 <= step <= 30:
        raise DemoError("Шаг диагностики: от 1 до 30 секунд")
    return {"mode": mode, "intervals": intervals, "diag_step_sec": step}


def create_session(title: str, seed: int, settings_in: dict | None, created_by: str,
                   now: float | None = None) -> dict:
    """Создаёт сессию. Модели считаются здесь — один раз; дальше только показ готового результата."""
    now = now or time.time()
    if len(store.ids()) >= settings.DEMO_MAX_SESSIONS:
        raise Conflict("Достигнут лимит демо-сессий — закройте ненужные")
    doc = {
        "id": secrets.token_urlsafe(9), "title": (title or "Демонстрация САМ ВКО").strip()[:80],
        "seed": seed, "created_at": _iso(now), "created_by": created_by, "run": 1,
        "stage": "waiting", "stage_started": now, "armed_at": now, "substep": 0, "paused_at": None,
        "settings": _clean_settings(settings_in), "decision": None, "report": None,
        "scn": scenario.compute(seed), "version": 0,
    }
    with store.lock(doc["id"]):
        return store.save(doc)


def get_doc(sid: str) -> dict:
    doc = store.load(sid)
    if doc is None:
        raise NotFound("Сессия не найдена или завершена")
    return doc


def close(sid: str) -> None:
    get_doc(sid)
    store.delete(sid)
    _view_cache.pop(sid, None)


def _mutate(sid: str, fn) -> dict:
    try:
        doc = store.update(sid, fn)
    except TimeoutError as exc:
        raise Conflict(str(exc)) from exc
    if doc is None:
        raise NotFound("Сессия не найдена или завершена")
    return doc


# --- Переходы --------------------------------------------------------------------------------

def _enter(doc: dict, stage: str, now: float) -> None:
    """Вход в этап. Уход назад сбрасывает то, что относится к более поздним этапам."""
    i = IDX[stage]
    if stage == "reset":
        doc["run"] += 1
    if i < IDX["operator_confirmed"] or stage == "reset":
        doc["decision"] = None
    if i < IDX["report_ready"] or stage == "reset":
        doc["report"] = None
        store.del_blob(doc["id"])
    doc.update(stage=stage, stage_started=now, armed_at=now, substep=0)
    if doc["paused_at"] is not None:            # пауза действует и на следующий этап
        doc["paused_at"] = now
    if stage == "report_ready":
        decision = doc["decision"]
        doc["report"] = {"at": _iso(now), "claim_text": claim_text(doc["scn"], decision)}
        store.put_blob(doc["id"], report_pdf(doc["scn"], decision))


def _gate(doc: dict, target: str) -> None:
    """Итоговое решение принимает только человек: этап решения не пропускается ни вручную, ни таймером."""
    if IDX[target] > IDX["operator_review"] and target != "reset" and doc["decision"] is None:
        raise Conflict("Нужно решение оператора: подтвердите или измените вердикт модели")


def _next_stage(doc: dict) -> str:
    stage = doc["stage"]
    if stage == "report_ready":
        return "reset"
    if stage == "reset":
        return "normal"
    return STAGE_KEYS[IDX[stage] + 1]


def control(sid: str, action: str, stage: str | None = None, now: float | None = None) -> dict:
    now = now or time.time()

    def apply(doc: dict):
        current = doc["stage"]
        if action == "start":
            if current not in ("waiting", "reset"):
                return False                         # повторный запуск безопасен: ничего не меняет
            _enter(doc, "normal", now)
        elif action == "next":
            target = _next_stage(doc)
            _gate(doc, target)
            _enter(doc, target, now)
        elif action == "goto":
            if stage not in STAGE_KEYS:
                raise DemoError("Неизвестный этап")
            if stage == current:
                return False
            _gate(doc, stage)
            _enter(doc, stage, now)
        elif action == "reset":
            _enter(doc, "reset", now)
        elif action == "pause":
            if doc["paused_at"] is not None:
                return False
            doc["paused_at"] = now
        elif action == "resume":
            if doc["paused_at"] is None:
                return False
            shift = now - doc["paused_at"]
            doc["stage_started"] += shift
            doc["armed_at"] += shift
            doc["paused_at"] = None
        else:
            raise DemoError("Неизвестное действие")

    return _mutate(sid, apply)


def set_settings(sid: str, raw: dict, now: float | None = None) -> dict:
    now = now or time.time()

    def apply(doc: dict):
        was_auto = doc["settings"]["mode"] == "auto"
        doc["settings"] = _clean_settings(raw, doc["settings"])
        if doc["settings"]["mode"] == "auto" and not was_auto:
            doc["armed_at"] = now                    # отсчёт автоперехода — с момента включения

    return _mutate(sid, apply)


def decide(sid: str, kind: str, cause: str | None, note: str, by: str,
           now: float | None = None) -> dict:
    """Решение оператора по вердикту модели: подтвердить или изменить."""
    now = now or time.time()
    if kind not in ("confirm", "change"):
        raise DemoError("Решение: confirm или change")

    def apply(doc: dict):
        if doc["stage"] != "operator_review":
            raise Conflict("Решение принимается на этапе «Решение оператора»")
        model_cause = doc["scn"]["ml"]["cause"]
        final = model_cause if kind == "confirm" else cause
        if final not in CAUSES:
            raise DemoError(f"Недопустимая причина. Ожидается одно из: {', '.join(CAUSES)}")
        doc["decision"] = {"kind": "confirm" if final == model_cause else "change", "cause": final,
                           "at": _iso(now), "by": by, "note": (note or "").strip()[:200]}
        _enter(doc, "operator_confirmed", now)

    return _mutate(sid, apply)


def make_report(sid: str, now: float | None = None) -> dict:
    """Запуск формирования обращения и PDF-акта (после решения оператора)."""
    now = now or time.time()

    def apply(doc: dict):
        if doc["stage"] == "report_ready":
            return False
        if doc["stage"] != "operator_confirmed":
            raise Conflict("Акт формируется после решения оператора")
        _enter(doc, "report_ready", now)

    return _mutate(sid, apply)


def pdf_bytes(sid: str) -> bytes:
    """PDF-акт: формируется при входе в report_ready один раз; здесь — только выдача."""
    doc = get_doc(sid)
    if not doc["report"]:
        raise NotFound("Акт ещё не сформирован")
    data = store.get_blob(sid)
    if data is None:                                 # хранилище потеряло файл — расчёт детерминирован
        data = report_pdf(doc["scn"], doc["decision"])
        store.put_blob(sid, data)
    return data


def _tick(doc: dict, now: float) -> bool:
    """Изменения, вызванные временем: шаги диагностики и автопереход. True — документ изменён."""
    if doc["paused_at"] is not None:
        return False
    cfg, stage, changed = doc["settings"], doc["stage"], False
    if stage == "diagnostics":
        done = min(STEPS, int((now - doc["stage_started"]) // cfg["diag_step_sec"]))
        if done > doc["substep"]:
            doc["substep"], changed = done, True
    seconds = cfg["intervals"].get(stage, 0)
    if cfg["mode"] == "auto" and stage not in HOLD_STAGES and seconds \
            and now >= doc["armed_at"] + seconds:
        target = _next_stage(doc)
        try:
            _gate(doc, target)
        except Conflict:
            return changed                           # без решения оператора автопереход не проходит
        _enter(doc, target, now)
        changed = True
    return changed


def tick(sid: str, now: float | None = None) -> bool:
    now = now or time.time()
    doc = store.load(sid)
    if doc is None or not _tick(copy.deepcopy(doc), now):     # проверка без блокировки
        return False
    try:
        store.update(sid, lambda d: _tick(d, now))
    except TimeoutError:
        return False
    return True


def tick_all(now: float | None = None) -> list[str]:
    ids = store.ids()
    for sid in ids:
        try:
            tick(sid, now)
        except Exception:  # noqa: BLE001 — одна сессия не должна останавливать остальные
            log.exception("Ошибка таймера сессии")
    return ids


# --- Представления ---------------------------------------------------------------------------

_view_cache: dict[str, tuple[int, str]] = {}
_view_lock = threading.Lock()
VIEW_BUILDS = 0        # сколько раз строилось представление (тесты: один раз на версию)


def view_json(sid: str) -> tuple[int, str] | None:
    """(версия, JSON представления). Строится один раз на версию, а не на зрителя."""
    global VIEW_BUILDS
    ver = store.version(sid)
    if ver is None:
        _view_cache.pop(sid, None)
        return None
    hit = _view_cache.get(sid)
    if hit and hit[0] >= ver:
        return hit
    with _view_lock:
        hit = _view_cache.get(sid)
        if hit and hit[0] >= ver:
            return hit
        doc = store.load(sid)
        if doc is None:
            _view_cache.pop(sid, None)
            return None
        VIEW_BUILDS += 1
        _view_cache[sid] = (doc["version"], json.dumps(build_view(doc), ensure_ascii=False,
                                                       separators=(",", ":")))
        return _view_cache[sid]


def stamp(payload: str) -> str:
    """Добавляет серверное время (для обратного отсчёта на клиенте) без повторной сериализации."""
    return f'{{"server_time":{time.time():.3f},{payload[1:]}'


def control_info(doc: dict) -> dict:
    stage, cfg = doc["stage"], doc["settings"]
    blocked = stage == "operator_review" and doc["decision"] is None
    return {
        "settings": cfg, "paused": doc["paused_at"] is not None, "run": doc["run"],
        "viewers": store.viewers(doc["id"], own=hub.local_viewers(doc["id"])),
        "backend": store.backend(), "created_at": doc["created_at"],
        "can": {"start": stage in ("waiting", "reset"),
                "next": not blocked and stage not in ("waiting", "reset"),
                "decide": stage == "operator_review", "report": stage == "operator_confirmed",
                "pause": doc["paused_at"] is None, "resume": doc["paused_at"] is not None},
        "awaiting_operator": blocked,
        "decision_note": (doc["decision"] or {}).get("note", ""),
        "causes": [{"key": c, "label": scenario.CAUSE_LABELS[c]} for c in CAUSES],
        "model_cause": doc["scn"]["ml"]["cause"], "ml_runs": scenario.ML_RUNS,
    }


def operator_json(sid: str) -> str:
    doc = get_doc(sid)
    got = view_json(sid)
    if got is None:
        raise NotFound("Сессия не найдена или завершена")
    return stamp('{"control":' + json.dumps(control_info(doc), ensure_ascii=False) +
                 ',"view":' + got[1] + "}")


# --- Рассылка зрителям (asyncio) ---------------------------------------------------------------

class Hub:
    """Локальные SSE-подписчики процесса. Очередь на один элемент: медленный клиент получает
    только последнее состояние и не копит память."""

    def __init__(self):
        self.subs: dict[str, set[asyncio.Queue]] = {}
        self.seen: dict[str, dict[str, float]] = {}
        self.last: dict[str, int] = {}

    def count(self) -> int:
        return sum(len(q) for q in self.subs.values())

    def subscribe(self, sid: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self.subs.setdefault(sid, set()).add(queue)
        return queue

    def unsubscribe(self, sid: str, queue: asyncio.Queue) -> None:
        self.subs.get(sid, set()).discard(queue)
        if not self.subs.get(sid):
            self.subs.pop(sid, None)

    def touch(self, sid: str, cid: str) -> None:
        self.seen.setdefault(sid, {})[cid] = time.monotonic()

    def local_viewers(self, sid: str) -> int:
        table = self.seen.get(sid, {})
        edge = time.monotonic() - PRESENCE_SEC
        for cid, seen in list(table.items()):
            if seen < edge:
                table.pop(cid, None)
        return len(table)

    def publish(self, sid: str, payload: str | None) -> None:
        for queue in self.subs.get(sid, ()):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(payload)

    async def refresh(self, sid: str) -> None:
        """Если версия сессии изменилась — построить представление и разослать локальным зрителям."""
        got = await asyncio.to_thread(view_json, sid)
        if got is None:
            if sid in self.last:
                self.last.pop(sid, None)
                self.publish(sid, None)              # None — сессия закрыта
            return
        if self.last.get(sid) != got[0]:
            self.last[sid] = got[0]
            self.publish(sid, stamp(got[1]))


hub = Hub()


async def run_loop() -> None:
    """Фоновый цикл процесса: таймеры сценария, рассылка изменений, счётчик зрителей."""
    beat = 0.0
    while True:
        try:
            await asyncio.to_thread(tick_all)
            for sid in list(hub.subs):
                await hub.refresh(sid)
            if time.monotonic() - beat >= 2.0:
                beat = time.monotonic()
                for sid in list(hub.seen):
                    await asyncio.to_thread(store.set_viewers, sid, hub.local_viewers(sid))
                    if not hub.seen[sid]:
                        hub.seen.pop(sid, None)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Ошибка фонового цикла демо")
        await asyncio.sleep(TICK_SEC)


def check_public_mode(db) -> None:
    """Публичный показ: отказ запуска, если наружу смотрит сервер с известными паролями по умолчанию."""
    from ...models import User
    from ...security import verify_password
    problems = []
    if settings.JWT_SECRET == "change-me-in-production-vko-2026":
        problems.append("JWT_SECRET равен значению по умолчанию из репозитория")
    known = {"admin123", "operator123", "school123", "provider123"}
    for user in db.query(User).filter(User.is_active.is_(True)):
        if any(verify_password(pw, user.password_hash) for pw in known):
            problems.append(f"у активной учётной записи {user.email} пароль по умолчанию")
    if problems:
        raise RuntimeError("DEMO_PUBLIC=true, но сервер небезопасен для публичного показа: "
                           + "; ".join(problems) + ". См. docs/demo/SECURITY.md "
                           "(python demo.py prepare --public).")
