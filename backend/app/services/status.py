"""Единые правила: пороги качества (ТЗ п.11), классификация замеров, свежесть данных."""
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import SQLAlchemyError

from ..config import settings

STATUS_OK = "Норма"
STATUS_UNSTABLE = "Нестабильно"
STATUS_CRITICAL = "Критично"
STATUS_OFFLINE = "Нет соединения"     # замер выполнен, и канал не работает
STATUS_NO_DATA = "Нет свежих данных"  # замеров нет: о канале ничего не известно

ALL_STATUSES = [STATUS_OK, STATUS_UNSTABLE, STATUS_CRITICAL, STATUS_OFFLINE, STATUS_NO_DATA]
BAD_STATUSES = (STATUS_UNSTABLE, STATUS_CRITICAL, STATUS_OFFLINE)


def naive_utc(dt: datetime) -> datetime:
    """Наивный UTC — так время хранится в БД; aware и naive сравнивать нельзя."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


# --- Пороги ----------------------------------------------------------------

@dataclass(frozen=True)
class Thresholds:
    id: int | None
    down_min: float
    up_min: float
    ping_max: float
    jitter_max: float
    loss_max: float
    availability_min: float
    contract_ratio: float
    incident_after: int
    stale_after_min: int

    @classmethod
    def from_settings(cls) -> "Thresholds":
        return cls(None, settings.SLA_DOWN_MBPS, settings.SLA_UP_MBPS, settings.SLA_PING_MS,
                   settings.SLA_JITTER_MS, settings.SLA_LOSS_PCT, settings.SLA_AVAILABILITY_PCT,
                   settings.SLA_SPEED_RATIO, settings.INCIDENT_AFTER_BAD, settings.STALE_AFTER_MIN)

    @classmethod
    def from_row(cls, row) -> "Thresholds":
        return cls(row.id, row.down_min, row.up_min, row.ping_max, row.jitter_max, row.loss_max,
                   row.availability_min, row.contract_ratio, row.incident_after,
                   row.stale_after_min)


_cache: list = [0.0, None]   # [время загрузки, Thresholds]
_TTL_SEC = 60


def thresholds() -> Thresholds:
    """Действующие пороги: последняя версия из БД, иначе значения из окружения.

    Кэш на минуту: classify() вызывается сотни тысяч раз при обучении моделей.
    Смена порогов администратором сбрасывает кэш этого процесса сразу, остальные
    процессы подхватят её в течение минуты.
    """
    if _cache[1] is None or time.monotonic() - _cache[0] > _TTL_SEC:
        _cache[:] = [time.monotonic(), _load()]
    return _cache[1]


def _load() -> Thresholds:
    from ..database import SessionLocal
    from ..models import Threshold
    db = SessionLocal()
    try:
        row = db.query(Threshold).order_by(Threshold.id.desc()).first()
        return Thresholds.from_row(row) if row else Thresholds.from_settings()
    except SQLAlchemyError:   # таблицы ещё нет (до миграции) — стартовые значения
        return Thresholds.from_settings()
    finally:
        db.close()


def reset_thresholds() -> None:
    _cache[1] = None


# --- Классификация ----------------------------------------------------------

def speed_floor(base: float, contract: float | None, ratio: float) -> float:
    """Минимальная приемлемая скорость: базовый порог ТЗ и доля договорной — что строже.

    Если договор сам ниже базового порога (канал 10 Мбит/с при пороге 20), судим
    только по договору, иначе такая школа была бы «нарушителем» всегда.
    """
    if not contract:
        return base
    if contract < base:
        return ratio * contract
    return max(base, ratio * contract)


def classify(download: float, ping: float, loss: float, contract_down: float | None,
             is_offline: bool = False, *, upload: float | None = None,
             jitter: float | None = None, contract_up: float | None = None,
             th: Thresholds | None = None) -> str:
    """Статус замера по Download, Upload, Ping, Jitter и Packet Loss.

    upload/jitter необязательны: старые вызовы без них оценивают только то, что передали.
    «Критично» — выход за порог в 2–2.5 раза (для скоростей — вдвое ниже порога).
    """
    if is_offline or download <= 0:
        return STATUS_OFFLINE
    th = th or thresholds()
    checks = [(download, speed_floor(th.down_min, contract_down, th.contract_ratio), -1, 0.5),
              (ping, th.ping_max, 1, 1.5),
              (loss, th.loss_max, 1, 2.5)]
    if upload is not None:
        checks.append((upload, speed_floor(th.up_min, contract_up, th.contract_ratio), -1, 0.5))
    if jitter is not None:
        checks.append((jitter, th.jitter_max, 1, 1.5))

    status = STATUS_OK
    for value, limit, direction, severe in checks:
        if direction < 0 and value < limit or direction > 0 and value > limit:
            status = STATUS_UNSTABLE
            if direction < 0 and value < limit * severe or direction > 0 and value > limit * severe:
                return STATUS_CRITICAL
    return status


def is_sla_violation(download: float, ping: float, loss: float, contract_down: float | None,
                     is_offline: bool = False, **extra) -> bool:
    return classify(download, ping, loss, contract_down, is_offline, **extra) in BAD_STATUSES


def interval_for(status: str) -> tuple[int, str]:
    """Динамическая конфигурация агента: чем хуже связь — тем чаще диагностика."""
    if status == STATUS_OK:
        return settings.INTERVAL_NORMAL_SEC, "standard"
    if status == STATUS_UNSTABLE:
        return settings.INTERVAL_UNSTABLE_SEC, "deep"
    return settings.INTERVAL_CRITICAL_SEC, "deep"


# --- Свежесть данных --------------------------------------------------------

def is_fresh(last: datetime | None, now: datetime, th: Thresholds | None = None) -> bool:
    return last is not None and now - last <= timedelta(
        minutes=(th or thresholds()).stale_after_min)


def effective_status(stored: str | None, last: datetime | None, now: datetime,
                     th: Thresholds | None = None) -> str:
    """Сохранённый статус верен только пока замер свежий. Иначе — «нет свежих данных»,
    а не последнее известное значение, выдаваемое за текущее."""
    return stored if stored and is_fresh(last, now, th) else STATUS_NO_DATA


def freshness(last: datetime | None, now: datetime, th: Thresholds | None = None) -> dict:
    th = th or thresholds()
    return {"last_measurement": last.isoformat() if last else None,
            "age_min": None if last is None else max(0, int((now - last).total_seconds() // 60)),
            "stale_after_min": th.stale_after_min,
            "is_stale": not is_fresh(last, now, th)}
