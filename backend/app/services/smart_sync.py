"""Killer feature #1 — Smart Sync / защита от Thundering Herd.

Проблема: после восстановления связи сотни агентов одновременно выгружают
накопленные офлайн-замеры. Синхронная запись кладёт БД.

Решение: HTTP-обработчик валидирует пакет, СОХРАНЯЕТ его в БД (SyncBatch.payload)
и отвечает 202; запись замеров идёт пулом фоновых воркеров. Очередь в памяти — лишь
способ разбудить воркер: пакет не теряется при перезапуске, потому что
recover_pending() подбирает всё, что не дошло до done. Запись идемпотентна
(замер с тем же device_id+timestamp пропускается), поэтому повтор безопасен.
Если сконфигурирован CELERY_BROKER_URL — пакеты уходят в RabbitMQ/Celery.
"""
import asyncio
import json
import logging
from datetime import datetime

from ..cache import cache_invalidate
from ..config import settings
from ..database import SessionLocal
from ..models import Device, Incident, Line, Measurement, School, SyncBatch
from .lines import contracts, set_state
from .status import (STATUS_CRITICAL, STATUS_OFFLINE, STATUS_OK, classify, interval_for,
                     naive_utc, thresholds)

log = logging.getLogger("smart_sync")

MAX_ATTEMPTS = 5      # после стольких неудач пакет остаётся в БД со статусом failed для разбора
SWEEP_SEC = 300       # как часто подбирать неудавшиеся и потерянные пакеты

_queue: asyncio.Queue | None = None
_workers: list[asyncio.Task] = []
_inflight: set[int] = set()
_stats = {"enqueued": 0, "processed": 0, "failed": 0, "rows": 0, "rejected": 0}


def stats(db=None) -> dict:
    """Метрики очереди. Счётчики процесса дополняются историей из БД,
    чтобы после перезапуска сервиса панель не показывала нули."""
    data = {**_stats,
            "queue_depth": _queue.qsize() if _queue else 0,
            "workers": len(_workers),
            "broker": "celery" if settings.CELERY_BROKER_URL else "in-process asyncio"}
    if db is not None:
        from sqlalchemy import func
        done = db.query(func.count(SyncBatch.id), func.coalesce(func.sum(SyncBatch.items), 0)) \
                 .filter(SyncBatch.status == "done").one()
        data["processed"] = max(data["processed"], done[0] or 0)
        data["rows"] = max(data["rows"], int(done[1] or 0))
        data["failed"] = max(data["failed"],
                             db.query(func.count(SyncBatch.id))
                               .filter(SyncBatch.status == "failed").scalar() or 0)
        data["pending"] = (db.query(func.count(SyncBatch.id))
                           .filter(SyncBatch.status.in_(("queued", "processing", "failed")),
                                   SyncBatch.payload.isnot(None)).scalar() or 0)
    return data


async def start_workers() -> None:
    global _queue
    _queue = asyncio.Queue(maxsize=settings.SYNC_QUEUE_MAXSIZE)
    for i in range(settings.SYNC_WORKERS):
        _workers.append(asyncio.create_task(_worker(i), name=f"sync-worker-{i}"))
    _workers.append(asyncio.create_task(_sweeper(), name="sync-sweeper"))
    log.info("Smart Sync: запущено %s воркеров", settings.SYNC_WORKERS)


async def stop_workers() -> None:
    for task in _workers:
        task.cancel()
    _workers.clear()


async def enqueue(batch_id: int) -> bool:
    """Будит воркера для сохранённого пакета. False => очередь переполнена (backpressure).

    Пакет уже лежит в БД: даже если очередь потеряется, recover_pending() его подберёт.
    """
    if batch_id in _inflight:
        return True
    if settings.CELERY_BROKER_URL:
        from .celery_app import process_batch_task  # запускается отдельным воркером
        if process_batch_task is not None:
            process_batch_task.delay(batch_id)
            _stats["enqueued"] += 1
            return True
    if _queue is None:
        await start_workers()
    try:
        _queue.put_nowait(batch_id)
    except asyncio.QueueFull:
        _stats["rejected"] += 1
        return False
    _inflight.add(batch_id)
    _stats["enqueued"] += 1
    return True


async def recover_pending() -> int:
    """Возвращает в работу пакеты, принятые, но не записанные: после перезапуска
    сервиса, после сбоя записи, при переполнении очереди."""
    db = SessionLocal()
    try:
        ids = [i for (i,) in db.query(SyncBatch.id)
               .filter(SyncBatch.status.in_(("queued", "processing", "failed")),
                       SyncBatch.payload.isnot(None), SyncBatch.attempts < MAX_ATTEMPTS)
               .order_by(SyncBatch.id)]
    finally:
        db.close()
    queued = 0
    for batch_id in ids:
        if not await enqueue(batch_id):
            break          # очередь полна — остальное подберёт следующий обход
        queued += 1
    if queued:
        log.info("Smart Sync: возвращено в работу пакетов — %s", queued)
    return queued


async def _sweeper() -> None:
    await recover_pending()
    while True:
        await asyncio.sleep(SWEEP_SEC)
        try:
            await recover_pending()
        except Exception:  # noqa: BLE001
            log.exception("Smart Sync: обход неудавшихся пакетов завершился ошибкой")


async def _worker(index: int) -> None:
    while True:
        batch_id = await _queue.get()
        try:
            await asyncio.to_thread(persist_batch, batch_id)
            _stats["processed"] += 1
        except Exception:  # noqa: BLE001
            _stats["failed"] += 1
            log.exception("Воркер %s: ошибка обработки пакета %s", index, batch_id)
        finally:
            _inflight.discard(batch_id)
            _queue.task_done()


def persist_batch(batch_id: int) -> None:
    """Запись сохранённого пакета. Вызывается и asyncio-воркером, и Celery-таском.

    Payload очищается только после успешной записи; при ошибке он остаётся в БД.
    """
    db = SessionLocal()
    try:
        batch = db.get(SyncBatch, batch_id)
        if batch is None or batch.status == "done" or not batch.payload:
            return
        batch.status = "processing"
        batch.attempts = (batch.attempts or 0) + 1
        db.commit()

        device = db.query(Device).filter(Device.device_id == batch.device_id).first()
        if device is None:
            raise LookupError(f"устройство {batch.device_id} не найдено")
        items = json.loads(batch.payload)
        stored = ingest(db, device, items)

        batch.status = "done"
        batch.processed_at = datetime.utcnow()
        batch.items = len(items)
        batch.payload = None
        batch.error = None
        db.commit()
        _stats["rows"] += stored
        cache_invalidate("dash:")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        batch = db.get(SyncBatch, batch_id)
        if batch:
            batch.status = "failed"
            batch.error = str(exc)[:500]
            db.commit()
        raise
    finally:
        db.close()


def build_rows(db, device: Device, items: list[dict], th) -> list[dict]:
    """Нормализованные замеры устройства: наивный UTC, не из будущего, статус и версия порогов."""
    contract_down, contract_up = contracts(db, device)
    now = datetime.utcnow()
    rows = []
    for item in items:
        ts = item.get("timestamp")
        try:
            ts = naive_utc(datetime.fromisoformat(ts)) if ts else now
        except (TypeError, ValueError):
            ts = now
        row = {
            "device_id": device.device_id,
            "school_id": device.school_id,
            # Часы агента не должны «заморозить» школу в будущем.
            "timestamp": min(ts, now),
            "download_speed": float(item.get("download_speed") or 0),
            "upload_speed": float(item.get("upload_speed") or 0),
            "ping": float(item.get("ping") or 0),
            "jitter": float(item.get("jitter") or 0),
            "packet_loss": float(item.get("packet_loss") or 0),
            "is_offline": bool(item.get("is_offline")),
            "source": item.get("source", "backfill"),
            "threshold_id": th.id,
        }
        row["status"] = classify(row["download_speed"], row["ping"], row["packet_loss"],
                                 contract_down, row["is_offline"], upload=row["upload_speed"],
                                 jitter=row["jitter"], contract_up=contract_up, th=th)
        rows.append(row)
    return rows


def ingest(db, device: Device, items: list[dict]) -> int:
    """Записывает замеры устройства без дублей и обновляет текущее состояние.

    Общий путь для онлайн-замера и офлайн-пакета. Возвращает число новых замеров.
    Коммит — на вызывающем.
    """
    rows = build_rows(db, device, items, thresholds())
    if not rows:
        return 0
    lo, hi = min(r["timestamp"] for r in rows), max(r["timestamp"] for r in rows)
    seen = {t for (t,) in db.query(Measurement.timestamp).filter(
        Measurement.device_id == device.device_id, Measurement.timestamp.between(lo, hi))}
    fresh = []
    for row in rows:
        if row["timestamp"] not in seen:
            seen.add(row["timestamp"])
            fresh.append(row)
    if fresh:
        db.bulk_insert_mappings(Measurement, fresh)
        apply_latest(db, device.device_id, device.school_id, max(fresh, key=lambda r: r["timestamp"]))
    return len(fresh)


def apply_latest(db, device_id: str, school_id: int, row: dict) -> None:
    """Обновляет текущее состояние по самому новому замеру пакета.

    • ПК — только если замер новее уже учтённого: старая офлайн-догрузка не затирает свежее.
    • Линия — только замерами её точки мониторинга. Статус школы = статус ОСНОВНОЙ линии.
    • Рабочие места (ПК без линии) состояние школы не меняют: плохой Wi-Fi одного ноутбука
      не характеризует канал школы. Их оценка — отдельно (web: workstations).
    """
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if device is None:
        return
    ts = row["timestamp"]
    if device.last_measured is not None and ts < device.last_measured:
        return
    status = row.get("status") or classify(row["download_speed"], row["ping"], row["packet_loss"],
                                           contracts(db, device)[0], row["is_offline"],
                                           upload=row["upload_speed"], jitter=row["jitter"])

    device.current_download = row["download_speed"]
    device.current_upload = row["upload_speed"]
    device.current_ping = row["ping"]
    device.current_jitter = row["jitter"]
    device.current_packet_loss = row["packet_loss"]
    device.last_measured = ts
    device.last_seen = max(ts, device.last_seen or ts)   # последний контакт назад не откатываем
    device.status = ("offline" if status == STATUS_OFFLINE
                     else "online" if status == STATUS_OK else "warning")
    # Динамическая конфигурация: сервер сам меняет частоту тестов агента
    interval, mode = interval_for(status)
    if device.test_interval_sec != interval:
        device.test_interval_sec = interval
        device.diagnostic_mode = mode
        device.config_version = (device.config_version or 1) + 1

    line = db.get(Line, device.line_id) if device.line_id else None
    if line is None or line.role == "disabled":
        return
    if line.last_measurement is not None and ts < line.last_measurement:
        return
    set_state(line, row, status)
    school = db.get(School, school_id)
    if line.role == "main" and school:
        set_state(school, row, status)
        _open_incident(db, school, line, device_id, row, status)


def _open_incident(db, school: School, line: Line, device_id: str, row: dict, status: str) -> None:
    """Инцидент — только при устойчивом нарушении: N последовательных замеров подряд
    «Критично»/«Нет соединения» (N настраивается, ТЗ п.18). Единичный сбой не в счёт."""
    if status not in (STATUS_CRITICAL, STATUS_OFFLINE):
        return
    need = thresholds().incident_after
    recent = (db.query(Measurement.timestamp, Measurement.status)
              .filter(Measurement.device_id == device_id)
              .order_by(Measurement.timestamp.desc()).limit(need).all())
    if len(recent) < need or any(s not in (STATUS_CRITICAL, STATUS_OFFLINE) for _, s in recent):
        return
    if db.query(Incident).filter(
            Incident.school_id == school.id,
            Incident.status.in_(["Новый", "В работе", "Передан поставщику"])).first():
        return
    number = f"INC-{datetime.utcnow():%Y}-{db.query(Incident).count() + 1:04d}"
    db.add(Incident(
        incident_number=number, school_id=school.id, device_id=device_id, line_id=line.id,
        provider=line.provider or school.provider, status="Новый",
        start_time=min(t for t, _ in recent),
        severity="critical" if status == STATUS_OFFLINE else "major",
        description=(f"{need} замера подряд без нормы. Скорость {row['download_speed']} Мбит/с при "
                     f"договорных {line.contract_speed_down} Мбит/с · Ping {row['ping']} мс · "
                     f"Потери {row['packet_loss']}%"),
    ))
