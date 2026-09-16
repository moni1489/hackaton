"""Запуск: python main.py  (или: uvicorn app.main:app --reload)"""
import uvicorn

from app.config import settings
from app.main import app  # noqa: F401 — реэкспорт для uvicorn app.main:app

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=settings.DEBUG)
