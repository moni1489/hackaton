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


def migrate_schema() -> list[str]:
    """Добавляет колонки, появившиеся в моделях, в уже существующие таблицы.

    Вызывается при старте приложения и из bootstrap.py: новая версия кода не должна
    падать на базе, созданной старой. Индексы и ограничения на существующих
    таблицах не меняются (ponytail: для этого нужен Alembic).
    """
    from sqlalchemy import inspect, text

    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    type_map = {"INTEGER": "INTEGER", "VARCHAR": "VARCHAR", "FLOAT": "FLOAT",
                "BOOLEAN": "BOOLEAN", "DATETIME": "DATETIME", "TEXT": "TEXT"}
    added = []
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in inspector.get_table_names():
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                sql_type = type_map.get(str(column.type).split("(")[0].upper(), "VARCHAR")
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {sql_type}'))
                added.append(f"{table.name}.{column.name}")
    return added
