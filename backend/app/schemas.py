"""Pydantic-контракты API."""
from datetime import datetime

from pydantic import BaseModel, Field


# --- Auth ------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    full_name: str
    school_id: int | None = None
    provider_name: str | None = None


# --- Agent -----------------------------------------------------------------
class EnrollRequest(BaseModel):
    """Первичная регистрация ПК-агента (выполняется по одноразовому коду школы)."""
    device_id: str
    school_id_code: str
    enrollment_secret: str
    name: str = "ПК-агент"
    room: str = "—"
    ip_address: str | None = None
    mac_address: str | None = None
    os_name: str | None = None
    cpu_model: str | None = None
    ram_gb: float | None = None
    agent_version: str = "2.0.0"
    device_type: str = "Рабочая станция"
    link_mode: str = "Ethernet 1 Гбит/с"
    cert_fingerprint: str | None = None
    line_code: str | None = None     # какую линию измеряет точка мониторинга (ТЗ п.10)
    line_role: str | None = None     # main | backup — при создании новой линии


class EnrollResponse(BaseModel):
    device_token: str
    device_id: str
    school_id: int
    hardware_fingerprint: str
    config: "AgentConfig"


class MeasurementIn(BaseModel):
    timestamp: datetime | None = None
    download_speed: float = 0
    upload_speed: float = 0
    ping: float = 0
    jitter: float = 0
    packet_loss: float = 0
    is_offline: bool = False
    source: str = "live"


class BatchIn(BaseModel):
    """Офлайн-догрузка: агент шлёт накопленные замеры одним пакетом."""
    items: list[MeasurementIn] = Field(default_factory=list)


class AgentConfig(BaseModel):
    test_interval_sec: int
    diagnostic_mode: str
    config_version: int
    batch_max_items: int
    server_time: datetime


class LegacyMeasurement(MeasurementIn):
    """Совместимость с агентом v1 (без токена, только для демо-стенда)."""
    device_id: str = "AGENT-01"
    school_id: int = 1
