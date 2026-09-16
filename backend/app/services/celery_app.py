"""Celery-воркер Smart Sync (включается, когда задан CELERY_BROKER_URL).

Запуск:  celery -A app.services.celery_app worker -Q sync -c 8
"""
from ..config import settings

try:
    from celery import Celery

    celery_app = Celery("vko", broker=settings.CELERY_BROKER_URL or "memory://",
                        backend=settings.REDIS_URL)
    celery_app.conf.update(task_acks_late=True, worker_prefetch_multiplier=1,
                           task_default_queue="sync")

    @celery_app.task(name="sync.process_batch", bind=True, max_retries=5,
                     default_retry_delay=30, rate_limit="200/m")
    def process_batch_task(self, batch_id: int, device_id: str, school_id: int, items: list):
        """Ограничение rate_limit размазывает пик офлайн-догрузок во времени."""
        from .smart_sync import persist_batch
        try:
            persist_batch(batch_id, device_id, school_id, items)
        except Exception as exc:  # noqa: BLE001
            raise self.retry(exc=exc) from exc

except ImportError:  # celery не установлен — используется asyncio-очередь
    celery_app = None
    process_batch_task = None
