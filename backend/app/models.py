"""ORM-модели. Реляционные сущности (School/Device/User) + time-series (Measurement)."""
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)

from .database import Base


class School(Base):
    """Организация образования — корневая сущность RBAC-изоляции."""
    __tablename__ = "schools"

    id = Column(Integer, primary_key=True, index=True)
    school_id_code = Column(String, index=True)
    name = Column(String)
    region = Column(String, index=True)
    address = Column(String)
    lat = Column(Float)
    lng = Column(Float)
    provider = Column(String, index=True)
    connection_type = Column(String)
    contract_speed_down = Column(Float)
    contract_speed_up = Column(Float)
    contact_name = Column(String)
    contact_phone = Column(String)
    contact_email = Column(String)
    provider_phone = Column(String)
    status = Column(String, index=True)
    current_download = Column(Float)
    current_upload = Column(Float)
    current_ping = Column(Float)
    current_jitter = Column(Float)
    current_packet_loss = Column(Float)
    last_measurement = Column(DateTime)


class Device(Base):
    """Отдельный ПК-агент внутри школы — единица ПК-уровня прослеживания."""
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, unique=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.id"), index=True)
    name = Column(String)
    room = Column(String)
    ip_address = Column(String)
    status = Column(String)
    last_seen = Column(DateTime)

    # --- ПК-уровень: инвентарь и идентичность ----------------------------
    device_type = Column(String, default="Рабочая станция")   # Шлюз / Рабочая станция / Ноутбук
    mac_address = Column(String)
    os_name = Column(String)
    cpu_model = Column(String)
    ram_gb = Column(Float)
    agent_version = Column(String)
    uptime_hours = Column(Float, default=0.0)
    link_mode = Column(String, default="Ethernet 1 Гбит/с")    # Ethernet / Wi-Fi 5 / Wi-Fi 6
    wifi_signal_dbm = Column(Float)

    # --- ПК-уровень: текущие метрики -------------------------------------
    current_download = Column(Float, default=0.0)
    current_upload = Column(Float, default=0.0)
    current_ping = Column(Float, default=0.0)
    current_jitter = Column(Float, default=0.0)
    current_packet_loss = Column(Float, default=0.0)
    availability_pct = Column(Float, default=100.0)   # аптайм агента за 24 ч
    sla_compliance_pct = Column(Float, default=100.0) # % замеров в рамках SLA

    # --- Безопасность агента ---------------------------------------------
    hardware_fingerprint = Column(String, index=True)  # SHA-256 железа: анти-спуфинг
    cert_fingerprint = Column(String)                  # отпечаток клиентского сертификата (mTLS)
    enrolled_at = Column(DateTime, default=datetime.utcnow)
    revoked = Column(Boolean, default=False)

    # --- Динамическая конфигурация агента ---------------------------------
    test_interval_sec = Column(Integer, default=900)
    diagnostic_mode = Column(String, default="standard")  # standard | deep
    config_version = Column(Integer, default=1)


class Measurement(Base):
    """Time-series замеров. Партиционируется по времени в PostgreSQL."""
    __tablename__ = "measurements"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, index=True)
    school_id = Column(Integer, index=True)
    timestamp = Column(DateTime, index=True)
    download_speed = Column(Float)
    upload_speed = Column(Float)
    ping = Column(Float)
    jitter = Column(Float)
    packet_loss = Column(Float)
    is_offline = Column(Boolean)
    source = Column(String, default="live")   # live | backfill (офлайн-догрузка)

    __table_args__ = (
        Index("ix_measure_school_ts", "school_id", "timestamp"),
        Index("ix_measure_device_ts", "device_id", "timestamp"),
    )


class Incident(Base):
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True, index=True)
    incident_number = Column(String, index=True)
    school_id = Column(Integer, index=True)
    device_id = Column(String)
    provider = Column(String)
    status = Column(String, index=True)
    start_time = Column(DateTime)
    resolved_time = Column(DateTime, nullable=True)
    description = Column(String)
    ai_claim_text = Column(Text, nullable=True)
    severity = Column(String, default="major")  # minor | major | critical

    # --- ML-атрибуция причины (killer feature «Виновник») -----------------
    root_cause = Column(String, index=True)        # device | school_lan | provider_node | regional
    root_cause_confidence = Column(Float)          # уверенность модели 0..1
    root_cause_evidence = Column(Text)             # JSON: признаки и доказательная база
    root_cause_model = Column(String)              # версия модели, вынесшей вердикт
    operator_verdict = Column(String)              # подтверждение оператора — метка для дообучения


class User(Base):
    """Учётная запись веб-панели. Роль определяет область видимости (RBAC)."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    full_name = Column(String)
    password_hash = Column(String)
    role = Column(String, default="school")     # admin | operator | school | provider
    school_id = Column(Integer, nullable=True)  # для роли school — её школа
    provider_name = Column(String, nullable=True)  # для роли provider — его зона
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    """Неизменяемый журнал. Хэш-цепочка: подмена записи ломает цепь."""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    actor = Column(String, index=True)
    actor_role = Column(String)
    action = Column(String, index=True)
    target = Column(String)
    ip_address = Column(String)
    details = Column(Text)
    prev_hash = Column(String)
    entry_hash = Column(String)


class SyncBatch(Base):
    """Учёт офлайн-догрузок: наблюдаемость Smart Sync очереди."""
    __tablename__ = "sync_batches"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, index=True)
    school_id = Column(Integer)
    received_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)
    items = Column(Integer, default=0)
    status = Column(String, default="queued")   # queued | processing | done | failed
    error = Column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("device_id", "id", name="uq_sync_device_batch"),)


class FaultEvent(Base):
    """Размеченная авария: истинная причина, район поражения и интервал.

    В демо наполняется симулятором отказов, в проде — подтверждёнными
    вердиктами операторов. Это обучающая выборка для модели атрибуции.
    """
    __tablename__ = "fault_events"

    id = Column(Integer, primary_key=True, index=True)
    cause = Column(String, index=True)   # device | school_lan | provider_node | regional
    district = Column(String, index=True)
    provider = Column(String, index=True)
    school_id = Column(Integer, index=True, nullable=True)
    device_id = Column(String, index=True, nullable=True)
    start_time = Column(DateTime, index=True)
    end_time = Column(DateTime, index=True)
    severity = Column(Float)             # множитель скорости: 0.0 — полный обрыв
    origin = Column(String, default="simulator")   # simulator | operator

    __table_args__ = (Index("ix_fault_window", "start_time", "end_time"),)


class OfficialSchool(Base):
    """Справочник eGov. Не подменяет действующие школы с агентами без сверки ID."""
    __tablename__ = "official_schools"
    external_id = Column(String, primary_key=True)
    district = Column(String, index=True)
    settlement = Column(String)
    address = Column(String)
    lat = Column(Float)
    lng = Column(Float)
    students = Column(Integer)
    source_url = Column(String)
    fetched_at = Column(DateTime)
    raw_json = Column(Text)


class PublishedConnection(Base):
    """Опубликованная характеристика, не договор и не измерение агента."""
    __tablename__ = "published_connections"
    key = Column(String, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=True, index=True)
    school_name = Column(String)
    district = Column(String)
    technology = Column(String)
    speed_down_mbps = Column(Float, nullable=True)
    source_url = Column(String)
    source_date = Column(String, nullable=True)
    retrieved_at = Column(DateTime)
    note = Column(Text)


class ExternalSourceState(Base):
    """Последний успешный снимок и состояние последней попытки обновления."""
    __tablename__ = "external_source_states"
    source = Column(String, primary_key=True)
    status = Column(String)
    attempted_at = Column(DateTime)
    fetched_at = Column(DateTime, nullable=True)
    window_start = Column(DateTime, nullable=True)
    window_end = Column(DateTime, nullable=True)
    source_url = Column(String)
    error = Column(Text, nullable=True)
    payload_json = Column(Text, default="{}")
