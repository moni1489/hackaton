"""Слой доступа к БД. PostgreSQL в проде, SQLite как offline-fallback для демо."""
import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


def _resolve_url() -> str:
    url = settings.DATABASE_URL
    # относительный sqlite-путь превращаем в абсолютный рядом с backend/
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
        name = url.replace("sqlite:///", "", 1)
        return "sqlite:///" + os.path.join(os.path.dirname(os.path.dirname(__file__)), name)
    return url


DATABASE_URL = _resolve_url()
IS_SQLITE = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if IS_SQLITE else {},
    pool_pre_ping=not IS_SQLITE,
    pool_size=10 if not IS_SQLITE else 5,
    max_overflow=20 if not IS_SQLITE else 0,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI-зависимость: сессия на запрос, гарантированное закрытие."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
