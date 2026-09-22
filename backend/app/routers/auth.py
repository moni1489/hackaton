"""Auth API — вход, профиль, проверка целостности аудита."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import User
from ..schemas import LoginRequest, TokenResponse
from ..security import (
    create_access_token, current_user, require_roles, verify_audit_chain, verify_password,
    write_audit,
)

router = APIRouter(prefix="/api/auth", tags=["Auth API"])


@router.post("/login", response_model=TokenResponse, summary="Вход в веб-панель")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email.lower().strip()).first()
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        write_audit(db, payload.email, "anonymous", "login.failed", "",
                    request.client.host if request.client else "")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")

    token = create_access_token(
        f"user:{user.id}",
        {"role": user.role, "school_id": user.school_id, "provider": user.provider_name},
    )
    write_audit(db, user.email, user.role, "login.success", "",
                request.client.host if request.client else "")
    return TokenResponse(access_token=token, role=user.role, full_name=user.full_name,
                         school_id=user.school_id, provider_name=user.provider_name,
                         district=user.district)


@router.get("/me", summary="Текущий профиль и область видимости")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role,
            "school_id": user.school_id, "provider_name": user.provider_name,
            "district": user.district,
            "scope": {"admin": "вся область", "operator": "вся область",
                      "district": "свой район/город",
                      "school": "своя организация",
                      "provider": "линии своего провайдера"}.get(user.role, "ограничено")}


@router.get("/audit", summary="Журнал действий (только администратор)")
def audit(limit: int = 100, db: Session = Depends(get_db),
          user: User = Depends(require_roles("admin"))):
    from ..models import AuditLog
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 500)).all()
    return {"integrity": verify_audit_chain(db),
            "entries": [{"id": r.id, "timestamp": r.timestamp, "actor": r.actor,
                         "role": r.actor_role, "action": r.action, "target": r.target,
                         "ip": r.ip_address, "hash": (r.entry_hash or "")[:16]} for r in rows]}


@router.get("/policy", summary="Действующие политики безопасности")
def policy():
    return {
        "transport": "HTTPS/TLS 1.3 обязателен; допускается выделенный VPN-туннель",
        "device_auth": "JWT устройства + сверка отпечатка оборудования с записью в БД"
                       + (" + mTLS клиентский сертификат" if settings.REQUIRE_MTLS
                          else "; привязка к железу не доказывается — для неё включите mTLS "
                               "(REQUIRE_MTLS)"),
        "mtls_required": settings.REQUIRE_MTLS,
        "rbac_roles": ["admin", "operator", "district", "school", "provider"],
        "rate_limits": {"agent_per_min": settings.RATE_LIMIT_AGENT,
                        "web_per_min": settings.RATE_LIMIT_WEB},
        "audit": "append-only журнал со сцепленными SHA-256 хэшами",
        "data_minimization": "персональные данные учащихся не собираются; "
                             "хранятся только сетевые метрики и служебные контакты",
    }
