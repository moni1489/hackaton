"""Demo API — управляемая демонстрация: панель ведущего и дашборд зрителя.

Подключается только при DEMO_MODE=true (см. main.py). Два независимых контура доступа:
  • ведущий  — обычный вход (JWT) и роль admin/operator; действия пишутся в журнал аудита;
  • зритель  — временный токен, привязанный к ОДНОЙ сессии; только чтение состояния и PDF.
Токен зрителя подписан отдельным ключом и не подходит ни к одному другому API.
"""
import asyncio
import json
import logging
from typing import Literal
from urllib.parse import urlsplit

from anyio import from_thread
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..cache import rate_limit_ok
from ..config import settings
from ..database import get_db
from ..models import User
from ..security import bearer, require_roles, write_audit
from ..services.demo import engine, scenario
from ..services.demo.engine import DemoError
from ..services.ml import attribution, forecast

log = logging.getLogger("demo.api")
router = APIRouter(prefix="/api/demo", tags=["Demo API"])

SID = Path(pattern=r"^[A-Za-z0-9_-]{6,32}$")
JSON = "application/json"
operator = require_roles("admin", "operator")


class Settings(BaseModel):
    mode: Literal["manual", "auto"] | None = None
    intervals: dict[str, float] | None = None
    diag_step_sec: float | None = None

    def clean(self) -> dict:
        return self.model_dump(exclude_none=True)


class CreateBody(Settings):
    title: str = Field("", max_length=80)
    public_url: str | None = Field(None, max_length=200)


class LinkBody(BaseModel):
    public_url: str | None = Field(None, max_length=200)


class ControlBody(BaseModel):
    action: Literal["start", "pause", "resume", "next", "goto", "reset"]
    stage: str | None = Field(None, max_length=32)


class VerdictBody(BaseModel):
    decision: Literal["confirm", "change"]
    cause: str | None = Field(None, max_length=32)
    note: str = Field("", max_length=200)


def _fail(exc: DemoError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


def _throttle(key: str, limit: int, message: str = "Слишком много запросов — подождите минуту") -> None:
    if not rate_limit_ok(key, limit):
        raise HTTPException(429, message, headers={"Retry-After": "60"})


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _operator_action(request: Request, db: Session, user: User, action: str, sid: str | None,
                     fn, details: dict | None = None):
    """Общая обвязка действий ведущего: лимит, выполнение, журнал, мгновенная рассылка зрителям.
    sid=None — идентификатор сессии возвращает сама fn (создание)."""
    _throttle(f"demo:op:{user.id}", settings.DEMO_RATE_LIMIT_OPERATOR)
    try:
        result = fn()
    except DemoError as exc:
        raise _fail(exc) from exc
    target = sid or result
    write_audit(db, user.email, user.role, f"demo.{action}", target, _ip(request), details or {})
    from_thread.run(engine.hub.refresh, target)   # зрители этого процесса узнают сразу, не ждут цикла
    return result


def _base_url(request: Request, override: str | None) -> str:
    base = (override or settings.DEMO_PUBLIC_URL or f"{request.url.scheme}://{request.headers.get('host', '')}")
    parts = urlsplit(base.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc or any(c.isspace() for c in base):
        raise HTTPException(400, "Публичный адрес: http(s)://хост[:порт]")
    return f"{parts.scheme}://{parts.netloc}"


def _qr_svg(url: str) -> str | None:
    try:
        import segno
    except ImportError:            # без библиотеки панель покажет ссылку без QR
        return None
    return segno.make(url, error="m").svg_inline(scale=6, border=2, dark="#0E1420", light="#FFFFFF")


def _link(request: Request, sid: str, override: str | None) -> dict:
    token, expires = engine.issue_viewer_token(sid)
    url = f"{_base_url(request, override)}/live/{sid}#t={token}"
    return {"viewer_url": url, "qr_svg": _qr_svg(url), "expires_at": expires.isoformat(timespec="seconds"),
            "ttl_min": settings.DEMO_VIEWER_TTL_MIN}


def _json(text: str) -> Response:
    return Response(content=text, media_type=JSON, headers={"Cache-Control": "no-store"})


# --- Публичное: включён ли режим, готовность -------------------------------------------------

@router.get("/config", summary="Включён ли демонстрационный режим")
def config():
    return {"enabled": True, "public": settings.DEMO_PUBLIC}


@router.get("/health", summary="Готовность демонстрации")
def health():
    attr, fcst = attribution.model(), forecast.model()
    return {"ok": True, "store": engine.store.backend(), "sessions": len(engine.store.ids()),
            "models": {"attribution": bool(attr), "forecast": bool(fcst)},
            "scenario_ready": scenario.ML_RUNS > 0}


# --- Панель ведущего (admin / operator) ------------------------------------------------------

@router.post("/sessions", summary="Создать демонстрационную сессию")
def create_session(body: CreateBody, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(operator)):
    sid = _operator_action(
        request, db, user, "create", None,
        lambda: engine.create_session(body.title, scenario.SEED, body.clean(), user.email)["id"])
    link = json.dumps(_link(request, sid, body.public_url), ensure_ascii=False)
    return _json(f'{{"session_id":"{sid}","link":{link},"state":{engine.operator_json(sid)}}}')


@router.get("/sessions", summary="Активные сессии")
def list_sessions(user: User = Depends(operator)):
    _throttle(f"demo:opr:{user.id}", settings.DEMO_RATE_LIMIT_OPERATOR * 4)
    out = []
    for sid in engine.store.ids():
        doc = engine.store.load(sid)
        if doc:
            out.append({"id": sid, "title": doc["title"], "stage": doc["stage"], "run": doc["run"],
                        "created_at": doc["created_at"]})
    return out


@router.get("/sessions/{sid}", summary="Состояние сессии для панели ведущего")
def get_session(sid: str = SID, user: User = Depends(operator)):
    _throttle(f"demo:opr:{user.id}", settings.DEMO_RATE_LIMIT_OPERATOR * 4)   # панель опрашивает часто
    try:
        return _json(engine.operator_json(sid))
    except DemoError as exc:
        raise _fail(exc) from exc


@router.post("/sessions/{sid}/link", summary="Новая ссылка и QR-код для зрителей")
def new_link(body: LinkBody, request: Request, sid: str = SID, db: Session = Depends(get_db),
             user: User = Depends(operator)):
    _operator_action(request, db, user, "link", sid, lambda: engine.get_doc(sid))
    return _link(request, sid, body.public_url)


@router.post("/sessions/{sid}/control", summary="Запуск, пауза, следующий этап, сброс")
def control(body: ControlBody, request: Request, sid: str = SID, db: Session = Depends(get_db),
            user: User = Depends(operator)):
    _operator_action(request, db, user, "control", sid,
                     lambda: engine.control(sid, body.action, body.stage),
                     {"action": body.action, "stage": body.stage})
    return _json(engine.operator_json(sid))


@router.put("/sessions/{sid}/settings", summary="Ручной/автоматический режим и интервалы")
def settings_(body: Settings, request: Request, sid: str = SID, db: Session = Depends(get_db),
              user: User = Depends(operator)):
    _operator_action(request, db, user, "settings", sid, lambda: engine.set_settings(sid, body.clean()),
                     body.clean())
    return _json(engine.operator_json(sid))


@router.post("/sessions/{sid}/verdict", summary="Решение оператора: подтвердить или изменить вердикт")
def verdict(body: VerdictBody, request: Request, sid: str = SID, db: Session = Depends(get_db),
            user: User = Depends(operator)):
    _operator_action(request, db, user, "verdict", sid,
                     lambda: engine.decide(sid, body.decision, body.cause, body.note, user.email),
                     {"decision": body.decision, "cause": body.cause, "note": body.note})
    return _json(engine.operator_json(sid))


@router.post("/sessions/{sid}/report", summary="Сформировать обращение и PDF-акт SLA")
def report(request: Request, sid: str = SID, db: Session = Depends(get_db),
           user: User = Depends(operator)):
    _operator_action(request, db, user, "report", sid, lambda: engine.make_report(sid))
    return _json(engine.operator_json(sid))


@router.delete("/sessions/{sid}", summary="Закрыть сессию (ссылки зрителей перестают работать)")
def close_session(request: Request, sid: str = SID, db: Session = Depends(get_db),
                  user: User = Depends(operator)):
    _operator_action(request, db, user, "close", sid, lambda: engine.close(sid))
    return {"closed": True}


@router.get("/sessions/{sid}/report.pdf", summary="PDF-акт (панель ведущего)", response_class=Response)
def operator_pdf(sid: str = SID, user: User = Depends(operator)):
    return _pdf(sid)


def _pdf(sid: str) -> Response:
    try:
        data = engine.pdf_bytes(sid)
    except DemoError as exc:
        raise _fail(exc) from exc
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="SLA_DEMO_act.pdf"',
                             "Cache-Control": "no-store"})


# --- Зритель: токен на одну сессию, только чтение ------------------------------------------------

GENERIC_401 = "Ссылка недействительна или срок её действия истёк"


def viewer(request: Request, sid: str = SID, cid: str | None = Query(None, pattern=r"^[A-Za-z0-9_-]{4,40}$"),
           creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
    """Проверка токена зрителя и лимитов. Подробности причин наружу не выдаются."""
    if creds is None or not engine.verify_viewer_token(creds.credentials, sid):
        raise HTTPException(401, GENERIC_401)
    who = cid or _ip(request) or "anon"
    _throttle(f"demo:v:{sid}:{who}", settings.DEMO_RATE_LIMIT_VIEWER, "Слишком много запросов")
    _throttle(f"demo:s:{sid}", settings.DEMO_RATE_LIMIT_SESSION, "Слишком много запросов")
    return who


@router.get("/live/{sid}/state", summary="Полное текущее состояние (загрузка, reload, polling)")
async def live_state(sid: str = SID, who: str = Depends(viewer)):
    got = await asyncio.to_thread(engine.view_json, sid)
    if got is None:
        raise HTTPException(404, "Демонстрация завершена")
    engine.hub.touch(sid, who)
    return _json(engine.stamp(got[1]))


def _frame(payload: str) -> str:
    return f"event: state\ndata: {payload}\n\n"


@router.get("/live/{sid}/stream", summary="Поток изменений (Server-Sent Events)")
async def live_stream(sid: str = SID, who: str = Depends(viewer)):
    if engine.hub.count() >= settings.DEMO_MAX_VIEWERS:
        raise HTTPException(503, "Слишком много подключений, попробуйте позже")
    queue = engine.hub.subscribe(sid)               # сначала подписка, потом чтение: изменение не потеряется
    try:
        got = await asyncio.to_thread(engine.view_json, sid)
    except BaseException:
        engine.hub.unsubscribe(sid, queue)
        raise
    if got is None:
        engine.hub.unsubscribe(sid, queue)
        raise HTTPException(404, "Демонстрация завершена")
    engine.hub.touch(sid, who)

    async def events():
        try:
            yield "retry: 2000\n\n"
            yield _frame(engine.stamp(got[1]))
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), engine.HEARTBEAT_SEC)
                except asyncio.TimeoutError:
                    engine.hub.touch(sid, who)
                    yield ": ping\n\n"      # держит соединение и подтверждает присутствие зрителя
                    continue
                if item is None:
                    yield "event: closed\ndata: {}\n\n"
                    return
                engine.hub.touch(sid, who)
                yield _frame(item)
        finally:
            engine.hub.unsubscribe(sid, queue)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform",
                                      "X-Accel-Buffering": "no"})


@router.get("/live/{sid}/report.pdf", summary="PDF-акт для зрителя (после формирования)",
            response_class=Response)
def live_pdf(sid: str = SID, who: str = Depends(viewer)):
    return _pdf(sid)
