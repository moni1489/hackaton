"""Единые правила классификации качества связи и SLA."""
from ..config import settings

STATUS_OK = "Норма"
STATUS_UNSTABLE = "Нестабильно"
STATUS_CRITICAL = "Критично"
STATUS_OFFLINE = "Нет соединения"

ALL_STATUSES = [STATUS_OK, STATUS_UNSTABLE, STATUS_CRITICAL, STATUS_OFFLINE]


def classify(download: float, ping: float, loss: float,
             contract_down: float | None, is_offline: bool = False) -> str:
    if is_offline or download <= 0:
        return STATUS_OFFLINE
    contract = contract_down or 100.0
    ratio = download / contract if contract else 1.0
    if ratio < settings.SLA_SPEED_RATIO * 0.5 or ping > settings.SLA_PING_MS * 1.5 \
            or loss > settings.SLA_LOSS_PCT * 2.5:
        return STATUS_CRITICAL
    if ratio < settings.SLA_SPEED_RATIO or ping > settings.SLA_PING_MS \
            or loss > settings.SLA_LOSS_PCT:
        return STATUS_UNSTABLE
    return STATUS_OK


def is_sla_violation(download: float, ping: float, loss: float,
                     contract_down: float | None, is_offline: bool = False) -> bool:
    return classify(download, ping, loss, contract_down, is_offline) in (
        STATUS_UNSTABLE, STATUS_CRITICAL, STATUS_OFFLINE)


def interval_for(status: str) -> tuple[int, str]:
    """Динамическая конфигурация агента: чем хуже связь — тем чаще диагностика."""
    if status == STATUS_OK:
        return settings.INTERVAL_NORMAL_SEC, "standard"
    if status == STATUS_UNSTABLE:
        return settings.INTERVAL_UNSTABLE_SEC, "deep"
    return settings.INTERVAL_CRITICAL_SEC, "deep"
