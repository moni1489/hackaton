"""Централизованная конфигурация (12-factor: всё через переменные окружения)."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "САМ ВКО · Internet Quality Monitoring"
    VERSION: str = "2.0.0"
    DEBUG: bool = True

    # --- База данных -------------------------------------------------------
    # PROD: postgresql+psycopg://user:pass@host:5432/vko
    # DEV : sqlite:///hackathon.db  (fallback, чтобы демо поднималось без инфры)
    DATABASE_URL: str = "sqlite:///hackathon.db"

    # --- Кэш / брокер ------------------------------------------------------
    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_TTL: int = 10                 # сек, TTL дашбордных агрегатов
    RATE_LIMIT_AGENT: int = 120         # запросов/мин с одного устройства
    RATE_LIMIT_WEB: int = 600           # запросов/мин с одного пользователя
    CELERY_BROKER_URL: str = ""         # amqp://... — если пусто, работает in-process очередь

    # --- Безопасность ------------------------------------------------------
    JWT_SECRET: str = "change-me-in-production-vko-2026"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_MIN: int = 60 * 8
    DEVICE_TOKEN_TTL_DAYS: int = 365
    REQUIRE_MTLS: bool = False          # True => обязателен заголовок от TLS-терминатора
    MTLS_HEADER: str = "X-Client-Cert-Fingerprint"
    CORS_ORIGINS: str = "*"

    # --- Smart Sync (защита от Thundering Herd) ---------------------------
    SYNC_QUEUE_MAXSIZE: int = 50_000
    SYNC_WORKERS: int = 4
    SYNC_BATCH_LIMIT: int = 2_000       # макс. измерений в одном batch-пакете

    # --- Динамическая конфигурация агента ---------------------------------
    INTERVAL_NORMAL_SEC: int = 900      # штатный режим — раз в 15 мин
    INTERVAL_UNSTABLE_SEC: int = 180    # «Нестабильно» — углублённая диагностика
    INTERVAL_CRITICAL_SEC: int = 60     # «Критично» — максимальная частота

    # --- Пороги SLA --------------------------------------------------------
    SLA_SPEED_RATIO: float = 0.6        # факт < 60% договора => нарушение
    SLA_PING_MS: float = 80.0
    SLA_LOSS_PCT: float = 2.0

    # --- AI ----------------------------------------------------------------
    GENAI_TOKEN: str = ""
    GENAI_MODEL: str = "gemini-1.5-pro"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
