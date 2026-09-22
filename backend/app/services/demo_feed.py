"""Демо-поток: держит демо-базу живой, когда настоящих ПК-агентов нет.

В демо никто не шлёт замеры, поэтому через STALE_AFTER_MIN вся область уходит
в «нет свежих данных» и карточки школ пустеют. Здесь два шага:

1. Разовый перенос истории на целое число суток вперёд, если база «отлежалась»
   (час суток сохраняется — сезонные профили ML остаются верны).
2. Цикл, который дописывает замеры от последнего до текущего момента тем же
   путём, что и агент (smart_sync.ingest): профиль каждого ПК за последние сутки
   плюс случайный разброс. Ничего не выдумывает заново — продолжает то, что есть.

Включается DEMO_FEED=true. В бою должен быть выключен: перенос истории меняет
время реальных замеров, а дописанные точки — не измерения, а имитация.
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta

from sqlalchemy import func, or_, text

from ..config import settings
from ..database import SessionLocal
from ..models import Device, Incident, Measurement, School
from .ml import attribution, baseline
from .smart_sync import OPEN_INCIDENT, ingest

log = logging.getLogger("demo_feed")

SLOT_MINUTES = 30          # шаг демо-истории (как в bootstrap.py)
PROFILE_HOURS = 24         # окно, по которому берётся профиль ПК
MAX_BACKFILL_SLOTS = 96    # не дописывать больше двух суток за один заход

# Время, которое сдвигается вместе с замерами: иначе «последний замер» школы
# останется в прошлом и карточка снова покажет «нет данных».
SHIFTED = {
    "measurements": ("timestamp",),
    "schools": ("last_measurement",),
    "lines": ("last_measurement",),
    "devices": ("last_seen", "last_measured"),
    "incidents": ("start_time", "resolved_time"),
    "fault_events": ("start_time", "end_time"),
}


def shift_history(db, now: datetime | None = None) -> int:
    """Переносит всю историю на N целых суток вперёд. Возвращает N (0 — не потребовалось).

    Целые сутки, а не точная разница: час суток и день недели — признаки моделей
    (сезонный базис «час × тип дня»), их сдвигать нельзя.
    """
    if db.bind.dialect.name != "sqlite":
        log.warning("Перенос демо-истории реализован только для sqlite-демо — пропущен")
        return 0
    now = now or datetime.utcnow()
    newest = db.query(func.max(Measurement.timestamp)).scalar()
    if not newest:
        return 0
    days = (now - newest).days                      # целых суток «отлежки»
    if days < 1:
        return 0
    for table, columns in SHIFTED.items():
        sets = ", ".join(f"{c} = datetime({c}, '+{days} days')" for c in columns)
        db.execute(text(f"UPDATE {table} SET {sets}"))
    db.commit()
    log.info("Демо-история перенесена на %d сут вперёд (было %s)", days, newest)
    return days


def _profiles(db, since: datetime) -> dict[str, dict]:
    """Средние метрики каждого ПК за последние сутки — основа для следующих точек."""
    rows = db.query(
        Measurement.device_id,
        func.avg(Measurement.download_speed), func.avg(Measurement.upload_speed),
        func.avg(Measurement.ping), func.avg(Measurement.jitter),
        func.avg(Measurement.packet_loss), func.max(Measurement.timestamp),
    ).filter(Measurement.timestamp >= since).group_by(Measurement.device_id).all()
    # ПК, молчавший в конце истории, молчит и дальше: авария не должна «рассасываться»
    # сама собой — иначе из демо через сутки пропадёт состояние «Нет соединения».
    down_now = {d for (d,) in db.query(Measurement.device_id).filter(
        Measurement.timestamp >= since + timedelta(hours=PROFILE_HOURS - 1),
        Measurement.is_offline.is_(True)).distinct()}
    return {r[0]: {"download": r[1] or 0.0, "upload": r[2] or 0.0, "ping": r[3] or 0.0,
                   "jitter": r[4] or 0.0, "loss": r[5] or 0.0, "last": r[6],
                   "offline": r[0] in down_now} for r in rows}


def _fresh_profile(school: School | None, start: datetime) -> dict:
    """Профиль ПК, у которого истории нет вовсе: добротный канал по договору.

    Такие школы есть в демо-базе (заведены позже наполнения истории) — без этого
    их карточка остаётся пустой навсегда.
    """
    down = (school.contract_speed_down if school and school.contract_speed_down else 50.0) * 0.8
    up = (school.contract_speed_up if school and school.contract_speed_up else down / 2) * 0.8
    return {"download": down, "upload": up, "ping": 24.0, "jitter": 4.0, "loss": 0.4,
            "last": start}


def _point(p: dict, ts: datetime) -> dict:
    """Одна точка: профиль ПК × случайный разброс. Молчавший ПК молчит и дальше."""
    if p.get("offline") or p["download"] < 0.5:     # «Нет соединения» на конец истории
        return {"timestamp": ts.isoformat(), "download_speed": 0.0, "upload_speed": 0.0,
                "ping": 0.0, "jitter": 0.0, "packet_loss": 100.0, "is_offline": True,
                "source": "live"}
    k = random.gauss(1.0, 0.07)
    return {
        "timestamp": ts.isoformat(),
        "download_speed": round(max(0.3, p["download"] * k), 1),
        "upload_speed": round(max(0.2, p["upload"] * k), 1),
        "ping": round(max(1.0, p["ping"] / max(k, 0.3)), 1),
        "jitter": round(max(0.3, p["jitter"] * random.gauss(1.0, 0.15)), 1),
        "packet_loss": round(max(0.0, p["loss"] * random.gauss(1.0, 0.3)), 2),
        "is_offline": False, "source": "live",
    }


def tick(now: datetime | None = None) -> int:
    """Дописывает замеры всех ПК до текущего момента. Возвращает число новых точек."""
    now = now or datetime.utcnow()
    written = 0
    with SessionLocal() as db:
        newest = db.query(func.max(Measurement.timestamp)).scalar()
        if not newest:
            return 0
        profiles = _profiles(db, newest - timedelta(hours=PROFILE_HOURS))
        step = timedelta(minutes=SLOT_MINUTES)
        schools = {s.id: s for s in db.query(School).all()}
        cold_start = now - step * MAX_BACKFILL_SLOTS
        for device in db.query(Device).all():
            p = profiles.get(device.device_id)
            if not p or not p["last"]:
                p = _fresh_profile(schools.get(device.school_id), cold_start)
            slots = []
            ts = p["last"] + step
            while ts <= now and len(slots) < MAX_BACKFILL_SLOTS:
                slots.append(_point(p, ts))
                ts += step
            if slots:
                written += ingest(db, device, slots)
                db.commit()   # короткие транзакции: на sqlite длинная запись блокирует остальных
    return written


def _startup() -> int:
    with SessionLocal() as db:
        shift_history(db)
    written = tick()
    # Профили нормы лежат в артефакте обучения: после переноса истории и появления
    # новых ПК их надо пересобрать, иначе атрибуция скажет «нет данных» (2 с).
    with SessionLocal() as db:
        log.info("Демо-поток: профили нормы пересобраны для %d ПК", baseline.build(db)["devices"])
        log.info("Демо-поток: переразмечено инцидентов: %d", _reattribute(db))
    return written


def _reattribute(db) -> int:
    """Открытые инциденты демо-базы без внятной причины — переразметить на «сейчас».

    Они родились в истории, когда профилей нормы ещё не было, и висят в ленте
    с вердиктом «аномалия не подтверждена». Новые инциденты размечаются сразу
    при открытии (smart_sync._attribute).
    """
    done = 0
    for incident in db.query(Incident).filter(
            Incident.status.in_(OPEN_INCIDENT),
            or_(Incident.root_cause.is_(None), Incident.root_cause == "none")).all():
        verdict = attribution.diagnose(db, incident.school_id)
        if verdict.get("cause") in (None, "none", "no_data"):
            continue
        incident.root_cause = verdict["cause"]
        incident.root_cause_confidence = verdict["confidence"]
        incident.root_cause_model = verdict["model_version"]
        done += 1
    db.commit()
    return done


async def demo_loop():
    """Первый заход подтягивает базу к «сейчас», дальше — по одной точке за слот."""
    if not settings.DEMO_FEED:
        return
    try:
        log.info("Демо-поток: дописано замеров при старте: %d", await asyncio.to_thread(_startup))
    except Exception:
        log.exception("Демо-поток: стартовая догрузка не удалась")
    while True:
        await asyncio.sleep(SLOT_MINUTES * 60)
        try:
            written = await asyncio.to_thread(tick)
            log.info("Демо-поток: дописано замеров: %d", written)
        except Exception:
            log.exception("Демо-поток: очередной заход не удался")


def demo() -> None:
    """Самопроверка: сдвиг истории и продолжение профиля."""
    now = datetime(2026, 9, 22, 8, 0)
    p = {"download": 50.0, "upload": 25.0, "ping": 20.0, "jitter": 3.0, "loss": 0.5,
         "last": now}
    point = _point(p, now)
    assert not point["is_offline"] and 20 < point["download_speed"] < 90, point
    assert point["ping"] > 0 and point["packet_loss"] >= 0, point
    assert _point({**p, "download": 0.0}, now)["is_offline"], "молчащий ПК должен молчать"
    assert _point({**p, "offline": True}, now)["is_offline"], "авария не рассасывается сама"

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from ..models import Base
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        old = now - timedelta(days=5, hours=3)
        db.add(Measurement(device_id="PC-1", school_id=1, timestamp=old, download_speed=50.0,
                           upload_speed=25.0, ping=20.0, jitter=3.0, packet_loss=0.5,
                           is_offline=False))
        db.commit()
        assert shift_history(db, now=now) == 5
        moved = db.query(func.max(Measurement.timestamp)).scalar()
        assert moved == old + timedelta(days=5), moved
        assert moved.hour == old.hour, "час суток обязан сохраниться"
        assert shift_history(db, now=now) == 0, "свежую базу двигать не надо"
    print("demo_feed: ok")


if __name__ == "__main__":
    demo()
