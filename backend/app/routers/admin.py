"""Admin API — управление ПК-агентами, пользователями и кодами развёртывания."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Device, School, SyncBatch, User
from ..security import (
    current_user, fingerprint, hash_password, require_roles, write_audit,
)
from ..services.smart_sync import stats as sync_stats

router = APIRouter(prefix="/api/admin", tags=["Admin API"])


@router.get("/enrollment-code/{school_id_code}", summary="Код развёртывания агента для школы")
def enrollment_code(school_id_code: str, db: Session = Depends(get_db),
                    user: User = Depends(require_roles("admin", "operator"))):
    school = db.query(School).filter(School.school_id_code == school_id_code).first()
    if not school:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Организация не найдена")
    return {"school_id_code": school_id_code,
            "enrollment_secret": fingerprint("enroll", school_id_code, settings.JWT_SECRET)[:12],
            "hint": "Одноразово вводится при установке агента на ПК школы"}


@router.post("/devices/{device_id}/revoke", summary="Отозвать доступ ПК-агента")
def revoke_device(device_id: str, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_roles("admin"))):
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Устройство не найдено")
    device.revoked = True
    device.status = "offline"
    db.commit()
    write_audit(db, user.email, user.role, "device.revoked", device_id,
                request.client.host if request.client else "")
    return {"status": "revoked", "device_id": device_id}


@router.post("/devices/{device_id}/diagnostic-mode", summary="Ручное переключение режима диагностики")
def set_mode(device_id: str, mode: str, request: Request, db: Session = Depends(get_db),
             user: User = Depends(require_roles("admin", "operator"))):
    """Ручное управление поверх автоматической динамической конфигурации."""
    if mode not in ("standard", "deep"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Режим: standard | deep")
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Устройство не найдено")
    device.diagnostic_mode = mode
    device.test_interval_sec = (settings.INTERVAL_UNSTABLE_SEC if mode == "deep"
                                else settings.INTERVAL_NORMAL_SEC)
    device.config_version = (device.config_version or 1) + 1
    db.commit()
    write_audit(db, user.email, user.role, "device.mode_changed", device_id,
                request.client.host if request.client else "",
                {"mode": mode, "interval": device.test_interval_sec})
    return {"status": "ok", "device_id": device_id, "mode": mode,
            "test_interval_sec": device.test_interval_sec,
            "config_version": device.config_version}


@router.get("/sync", summary="Мониторинг очереди Smart Sync")
def sync_monitor(limit: int = 20, db: Session = Depends(get_db),
                 user: User = Depends(require_roles("admin", "operator"))):
    rows = db.query(SyncBatch).order_by(SyncBatch.id.desc()).limit(limit).all()
    return {"queue": sync_stats(db),
            "recent": [{"id": b.id, "device_id": b.device_id, "items": b.items,
                        "status": b.status, "received_at": b.received_at,
                        "processed_at": b.processed_at, "error": b.error} for b in rows]}


@router.get("/users", summary="Пользователи системы")
def users(db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    return [{"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role,
             "school_id": u.school_id, "provider_name": u.provider_name,
             "is_active": u.is_active} for u in db.query(User).order_by(User.id).all()]


@router.post("/users", status_code=status.HTTP_201_CREATED, summary="Создать пользователя")
def create_user(email: str, password: str, full_name: str, role: str,
                request: Request, school_id: int | None = None,
                provider_name: str | None = None, db: Session = Depends(get_db),
                user: User = Depends(require_roles("admin"))):
    if role not in ("admin", "operator", "school", "provider"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Недопустимая роль")
    if db.query(User).filter(User.email == email.lower()).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Пользователь уже существует")
    new = User(email=email.lower(), full_name=full_name, role=role, school_id=school_id,
               provider_name=provider_name, password_hash=hash_password(password))
    db.add(new)
    db.commit()
    write_audit(db, user.email, user.role, "user.created", email,
                request.client.host if request.client else "", {"role": role})
    return {"id": new.id, "email": new.email, "role": new.role}
