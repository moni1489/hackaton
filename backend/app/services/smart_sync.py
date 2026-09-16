"""Killer feature #1 — Smart Sync / защита от Thundering Herd.

Проблема: после восстановления связи сотни агентов одновременно выгружают
накопленные офлайн-замеры. Синхронная запись кладёт БД.

Решение: HTTP-обработчик только валидирует пакет и ставит его в очередь
(202 Accepted), а запись в БД идёт пулом фоновых воркеров с ограниченным
параллелизмом и bulk-insert. Если сконфигурирован CELERY_BROKER_URL —
пакеты уходят в RabbitMQ/Celery, иначе работает встроенная asyncio-очередь.
"""
import asyncio
import logging
from datetime import datetime

from ..cache import cache_invalidate
from ..config import settings
from ..database import SessionLocal
from ..models import Device, Incident, Measurement, School, SyncBatch
from .status import STATUS_OFFLINE, classify

log = logging.getLogger("smart_sync")

_queue: asyncio.Queue | None = None
_workers: list[asyncio.Task] = []
_stats = {"enqueued": 0, "processed": 0, "failed": 0, "rows": 0, "rejected": 0}


def stats() -> dict:
    return {**_stats,
            "queue_depth": _queue.qsize() if _queue else 0,
            "workers": len(_workers),
            "broker": "celery" if settings.CELERY_BROKER_URL else "in-process asyncio"}


async def start_workers() -> None:
    global _queue
    _queue = asyncio.Queue(maxsize=settings.SYNC_QUEUE_MAXSIZE)
    for i in range(settings.SYNC_WORKERS):
        _workers.append(asyncio.create_task(_worker(i), name=f"sync-worker-{i}"))
    log.info("Smart Sync: запущено %s воркеров", settings.SYNC_WORKERS)


async def stop_workers() -> None:
    for task in _workers:
        task.cancel()
    _workers.clear()


async def enqueue(batch_id: int, device_id: str, school_id: int, items: list[dict]) -> bool:
    """Ставит пакет в очередь. False => очередь переполнена (backpressure)."""
    if settings.CELERY_BROKER_URL:
        from .celery_app import process_batch_task  # запускается отдельным воркером
        process_batch_task.delay(batch_id, device_id, school_id, items)
        _stats["enqueued"] += 1
        return True
    if _queue is None:
        await start_workers()
    try:
        _queue.put_nowait((batch_id, device_id, school_id, items))
    except asyncio.QueueFull:
        _stats["rejected"] += 1
        return False
    _stats["enqueued"] += 1
    return True


async def _worker(index: int) -> None:
    while True:
        batch_id, device_id, school_id, items = await _queue.get()
        try:
            await asyncio.to_thread(persist_batch, batch_id, device_id, school_id, items)
            _stats["processed"] += 1
            _stats["rows"] += len(items)
        except Exception:  # noqa: BLE001
            _stats["failed"] += 1
            log.exception("Воркер %s: ошибка обработки пакета %s", index, batch_id)
        finally:
            _queue.task_done()


def persist_batch(batch_id: int, device_id: str, school_id: int, items: list[dict]) -> None:
    """Bulk-запись пакета. Вызывается и asyncio-воркером, и Celery-таском."""
    db = SessionLocal()
    try:
        batch = db.get(SyncBatch, batch_id)
        if batch:
            batch.status = "processing"
            db.commit()

        rows = []
        for item in items:
            ts = item.get("timestamp")
            try:
                ts = datetime.fromisoformat(ts) if ts else datetime.utcnow()
            except (TypeError, ValueError):
                ts = datetime.utcnow()
            rows.append({
                "device_id": device_id,
                "school_id": school_id,
                "timestamp": ts,
                "download_speed": float(item.get("download_speed") or 0),
                "upload_speed": float(item.get("upload_speed") or 0),
                "ping": float(item.get("ping") or 0),
                "jitter": float(item.get("jitter") or 0),
                "packet_loss": float(item.get("packet_loss") or 0),
                "is_offline": bool(item.get("is_offline")),
                "source": item.get("source", "backfill"),
            })
        if rows:
            db.bulk_insert_mappings(Measurement, rows)
            apply_latest(db, device_id, school_id, max(rows, key=lambda r: r["timestamp"]))

        if batch:
            batch.status = "done"
            batch.processed_at = datetime.utcnow()
            batch.items = len(rows)
        db.commit()
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


def apply_latest(db, device_id: str, school_id: int, row: dict) -> None:
    """Обновляет текущие метрики ПК и школы + автосоздание инцидента."""
    school = db.get(School, school_id)
    device = db.query(Device).filter(Device.device_id == device_id).first()
    status = classify(row["download_speed"], row["ping"], row["packet_loss"],
                      school.contract_speed_down if school else None, row["is_offline"])

    if device:
        device.current_download = row["download_speed"]
        device.current_upload = row["upload_speed"]
        device.current_ping = row["ping"]
        device.current_jitter = row["jitter"]
        device.current_packet_loss = row["packet_loss"]
        device.last_seen = row["timestamp"]
        device.status = "offline" if status == STATUS_OFFLINE else (
            "warning" if status != "Норма" else "online")
        # Динамическая конфигурация: сервер сам меняет частоту тестов агента
        from .status import interval_for
        interval, mode = interval_for(status)
        if device.test_interval_sec != interval:
            device.test_interval_sec = interval
            device.diagnostic_mode = mode
            device.config_version = (device.config_version or 1) + 1

    if school:
        # Статус школы = худший среди её ПК (агрегируем по устройствам)
        school.current_download = row["download_speed"]
        school.current_upload = row["upload_speed"]
        school.current_ping = row["ping"]
        school.current_jitter = row["jitter"]
        school.current_packet_loss = row["packet_loss"]
        school.last_measurement = row["timestamp"]
        school.status = status

        if status in ("Критично", STATUS_OFFLINE):
            open_inc = db.query(Incident).filter(
                Incident.school_id == school_id,
                Incident.status.in_(["Новый", "В работе", "Передан поставщику"]),
            ).first()
            if not open_inc:
                number = f"INC-{datetime.utcnow():%Y}-{db.query(Incident).count() + 1:04d}"
                db.add(Incident(
                    incident_number=number, school_id=school_id, device_id=device_id,
                    provider=school.provider, status="Новый", start_time=row["timestamp"],
                    severity="critical" if status == STATUS_OFFLINE else "major",
                    description=(f"Скорость {row['download_speed']} Мбит/с при договорных "
                                 f"{school.contract_speed_down} Мбит/с · Ping {row['ping']} мс · "
                                 f"Потери {row['packet_loss']}%"),
                ))
