"""Хранение замеров: не меньше квартала, чтобы считать рейтинг и динамику за период."""
import asyncio
import logging
from datetime import timedelta

from sqlalchemy import func

from ..config import settings
from ..database import SessionLocal
from ..models import Measurement

log = logging.getLogger("retention")
MIN_RETENTION_DAYS = 92   # квартал = 3 месяца, с запасом на длинные


def purge_old_measurements(db) -> int:
    """Удаляет замеры старше срока хранения. Срок считается от самого свежего замера,
    а не от «сейчас»: остановившийся сбор (или демо-база) не стирает историю сам."""
    newest = db.query(func.max(Measurement.timestamp)).scalar()
    if not newest:
        return 0
    cutoff = newest - timedelta(days=max(settings.RETENTION_DAYS, MIN_RETENTION_DAYS))
    removed = db.query(Measurement).filter(Measurement.timestamp < cutoff) \
        .delete(synchronize_session=False)
    db.commit()
    return removed


def _purge() -> int:
    with SessionLocal() as db:
        return purge_old_measurements(db)


async def retention_loop():
    while True:
        try:
            removed = await asyncio.to_thread(_purge)
            if removed:
                log.info("Удалено замеров старше срока хранения: %d", removed)
        except Exception:
            log.exception("Очистка старых замеров не удалась")
        await asyncio.sleep(24 * 3600)
