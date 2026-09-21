"""Точка сборки приложения САМ ВКО.

Модульная архитектура: Auth API · Agent API · Web API · Admin API · AI API.
"""
import logging
import asyncio
from contextlib import suppress
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .cache import backend_name
from .config import settings
from .database import SessionLocal, engine, migrate_schema
from .routers import admin, agent, ai, auth, export, ml, web, public_data
from .services.external_network import poll_sources
from .services.lines import ensure_defaults
from .services.retention import retention_loop
from .services.smart_sync import start_workers, stats, stop_workers

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    for column in migrate_schema():
        log.info("Миграция схемы: + %s", column)
    with SessionLocal() as db:
        ensure_defaults(db)
        if settings.DEMO_MODE and settings.DEMO_PUBLIC:
            from .services.demo.engine import check_public_mode
            check_public_mode(db)          # публичный показ не стартует с паролями по умолчанию
    # Публичный показ: агенты, очередь синка и внешние источники не нужны (и наружу не смотрят).
    background = not (settings.DEMO_MODE and settings.DEMO_PUBLIC)
    demo_task = None
    if settings.DEMO_MODE:
        from .services.demo import engine as demo_engine, scenario as demo_scenario
        await asyncio.to_thread(demo_scenario.compute)     # прогрев: первая сессия создаётся мгновенно
        demo_task = asyncio.create_task(demo_engine.run_loop())
    if background:
        await start_workers()
    external_task = asyncio.create_task(poll_sources()) if background and settings.EXTERNAL_POLL_ENABLED else None
    retention_task = asyncio.create_task(retention_loop()) if background else None
    log.info("Запуск: БД=%s, кэш=%s%s", engine.dialect.name, backend_name(),
             ", ДЕМО-РЕЖИМ" + (" (публичный)" if settings.DEMO_PUBLIC else "") if settings.DEMO_MODE else "")
    try:
        yield
    finally:
        for task in (demo_task, retention_task, external_task):
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        if background:
            await stop_workers()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description="Мониторинг качества интернет-соединения организаций образования ВКО: "
                "карта области, карточка школы и прослеживание на уровне отдельного ПК.",
    lifespan=lifespan,
    docs_url=None if settings.DEMO_PUBLIC else "/docs",
    redoc_url=None if settings.DEMO_PUBLIC else "/redoc",
    openapi_url=None if settings.DEMO_PUBLIC else "/openapi.json",
    openapi_tags=[
        {"name": "Auth API", "description": "Вход, роли, аудит"},
        {"name": "Agent API", "description": "Приём данных от ПК-агентов, Smart Sync"},
        {"name": "Web API", "description": "Дашборд, карта, карточка школы, ПК-уровень"},
        {"name": "Admin API", "description": "Управление агентами и пользователями"},
        {"name": "Integration / AI API", "description": "Претензии и PDF-акты SLA"},
        {"name": "ML API — «Виновник»", "description": "Атрибуция причины деградации "
                                                       "и прогноз пробоя SLA"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.CORS_ORIGINS == "*" else settings.CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "Retry-After"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Базовый набор защитных заголовков + HSTS для HTTPS-развёртывания."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if not settings.DEBUG:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Необработанная ошибка на %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка сервера"})


if settings.DEMO_MODE and settings.DEMO_PUBLIC:
    # Публичный показ: наружу — только вход оператора и демо-API, без административных и рабочих API.
    app.include_router(auth.public_router)
else:
    app.include_router(auth.router)
    app.include_router(agent.router)
    app.include_router(web.router)
    app.include_router(export.router)
    app.include_router(admin.router)
    app.include_router(ai.router)
    app.include_router(ml.router)
    app.include_router(public_data.router)
if settings.DEMO_MODE:
    from .routers import demo
    app.include_router(demo.router)


@app.get("/", tags=["Web API"], summary="Health-check")
def root():
    if settings.DEMO_MODE and settings.DEMO_PUBLIC:
        return {"status": "ok"}          # публичный режим: без версий, БД и внутренних счётчиков
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.VERSION,
            "database": engine.dialect.name, "cache": backend_name(),
            "smart_sync": stats(), "docs": "/docs"}


def _serve_frontend() -> None:
    """Демо-режим: одна точка входа для зрителей и ведущего — API и собранный фронтенд с одного
    адреса (нет CORS, одна ссылка в QR). Без сборки (frontend/dist) сервер работает как API."""
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    index = dist / "index.html"
    if not index.exists():
        log.warning("Демо: frontend/dist не собран — страницы /live и /demo недоступны "
                    "(python demo.py prepare)")
        return

    @app.get("/live/{rest:path}", include_in_schema=False)
    @app.get("/demo", include_in_schema=False)
    def spa(rest: str = ""):
        return FileResponse(index, headers={"Cache-Control": "no-store"})

    app.mount("/", StaticFiles(directory=dist), name="frontend")   # последним: не перекрывает API


if settings.DEMO_MODE:
    _serve_frontend()
