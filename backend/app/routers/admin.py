"""Admin API — управление ПК-агентами, линиями, порогами, пользователями и кодами развёртывания."""
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Device, Line, School, SyncBatch, Threshold, User
from ..schemas import LineIn, ThresholdsIn
from ..security import (
    fingerprint, hash_password, require_roles, write_audit,
)
from ..services.lines import ROLES, set_state
from ..services.smart_sync import stats as sync_stats
from ..services.status import reset_thresholds, thresholds

router = APIRouter(prefix="/api/admin", tags=["Admin API"])


@router.get("/enrollment-code/{school_id_code}", summary="Код развёртывания агента для школы")
def enrollment_code(school_id_code: str, db: Session = Depends(get_db),
                    user: User = Depends(require_roles("admin", "operator"))):
    school = db.query(School).filter(School.school_id_code == school_id_code).first()
    if not school:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Организация не найдена")
    return {"school_id_code": school_id_code,
            "enrollment_secret": fingerprint("enroll", school_id_code, settings.JWT_SECRET)[:12],
            "hint": "Постоянный код школы (не одноразовый): вводится при установке агента на ПК"}


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


@router.post("/devices/{device_id}/reset-enrollment",
             summary="Сбросить привязку к оборудованию (повторная регистрация ПК)")
def reset_enrollment(device_id: str, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_roles("admin"))):
    """Единственный путь вернуть отозванное устройство или перенести Device ID на новое
    оборудование: агент не может сделать это сам, даже зная код развёртывания школы."""
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Устройство не найдено")
    device.revoked = False
    device.hardware_fingerprint = None
    device.cert_fingerprint = None
    db.commit()
    write_audit(db, user.email, user.role, "device.enrollment_reset", device_id,
                request.client.host if request.client else "")
    return {"status": "reset", "device_id": device_id}


@router.post("/devices/{device_id}/line", summary="Привязать точку мониторинга к линии")
def bind_line(device_id: str, request: Request, line_id: int | None = None,
              db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    """line_id не задан — устройство становится обычным рабочим местом."""
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Устройство не найдено")
    if line_id is not None:
        line = db.get(Line, line_id)
        if not line or line.school_id != device.school_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Линия принадлежит другой организации")
    device.line_id = line_id
    db.commit()
    write_audit(db, user.email, user.role, "device.line_bound", device_id,
                request.client.host if request.client else "", {"line_id": line_id})
    return {"status": "ok", "device_id": device_id, "line_id": line_id}


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


# --- Линии связи (ТЗ п.10, п.14) ------------------------------------------

def _line_out(line: Line) -> dict:
    return {"id": line.id, "school_id": line.school_id, "code": line.code, "role": line.role,
            "provider": line.provider, "connection_type": line.connection_type,
            "contract_speed_down": line.contract_speed_down,
            "contract_speed_up": line.contract_speed_up}


@router.post("/schools/{school_id}/lines", status_code=status.HTTP_201_CREATED,
             summary="Добавить линию (обычно резервную)")
def create_line(school_id: int, payload: LineIn, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_roles("admin"))):
    school = db.get(School, school_id)
    if not school:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Организация не найдена")
    role = payload.role or "backup"
    if role not in ROLES or role == "main":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Новая линия — backup или disabled; основной её делает PATCH /lines/{id}")
    line = Line(school_id=school_id, code=payload.code or f"{school.school_id_code}-L{role[0].upper()}",
                role=role, provider=payload.provider or school.provider,
                connection_type=payload.connection_type or school.connection_type,
                contract_speed_down=payload.contract_speed_down,
                contract_speed_up=payload.contract_speed_up)
    db.add(line)
    db.commit()
    write_audit(db, user.email, user.role, "line.created", school.school_id_code,
                request.client.host if request.client else "", {"line": line.code, "role": role})
    return _line_out(line)


@router.patch("/lines/{line_id}", summary="Изменить линию: роль, поставщик, договорные скорости")
def update_line(line_id: int, payload: LineIn, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_roles("admin"))):
    line = db.get(Line, line_id)
    if not line:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Линия не найдена")
    changes = payload.model_dump(exclude_none=True)
    role = changes.pop("role", None)
    if role:
        if role not in ROLES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Роль линии: {' | '.join(ROLES)}")
        if role == "main":
            # «Сделать основной»: прежняя основная становится резервной.
            for other in db.query(Line).filter(Line.school_id == line.school_id, Line.role == "main",
                                               Line.id != line.id):
                other.role = "backup"
            school = db.get(School, line.school_id)
            if school and line.last_measurement:
                set_state(school, {"download_speed": line.current_download or 0,
                                   "upload_speed": line.current_upload or 0,
                                   "ping": line.current_ping or 0, "jitter": line.current_jitter or 0,
                                   "packet_loss": line.current_packet_loss or 0,
                                   "timestamp": line.last_measurement}, line.status)
        elif line.role == "main" and not db.query(Line).filter(
                Line.school_id == line.school_id, Line.role == "main", Line.id != line.id).first():
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "Нельзя снять основную линию: сначала назначьте другую основной")
        line.role = role
    for field, value in changes.items():
        setattr(line, field, value)
    db.commit()
    write_audit(db, user.email, user.role, "line.updated", line.code or str(line.id),
                request.client.host if request.client else "", {**changes, "role": role})
    return _line_out(line)


# --- Пороги (ТЗ п.11, п.20) ------------------------------------------------

@router.get("/thresholds", summary="Действующие пороги и история версий")
def get_thresholds(db: Session = Depends(get_db),
                   user: User = Depends(require_roles("admin", "operator"))):
    history = db.query(Threshold).order_by(Threshold.id.desc()).limit(10).all()
    return {"active": asdict(thresholds()),
            "history": [{"id": t.id, "created_at": t.created_at, "created_by": t.created_by}
                        for t in history]}


@router.put("/thresholds", summary="Задать новые пороги (новая версия)")
def put_thresholds(payload: ThresholdsIn, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_roles("admin"))):
    """Пороги не зашиты в агент и не меняются задним числом: каждая версия сохраняется,
    а замер хранит threshold_id той версии, по которой он оценён."""
    row = Threshold(created_by=user.email, **payload.model_dump())
    db.add(row)
    db.commit()
    reset_thresholds()
    write_audit(db, user.email, user.role, "thresholds.changed", f"v{row.id}",
                request.client.host if request.client else "", payload.model_dump())
    return {"status": "ok", "active": asdict(thresholds())}


@router.get("/users", summary="Пользователи системы")
def users(db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    return [{"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role,
             "school_id": u.school_id, "provider_name": u.provider_name, "district": u.district,
             "is_active": u.is_active} for u in db.query(User).order_by(User.id).all()]


@router.post("/users", status_code=status.HTTP_201_CREATED, summary="Создать пользователя")
def create_user(email: str, password: str, full_name: str, role: str,
                request: Request, school_id: int | None = None,
                provider_name: str | None = None, district: str | None = None,
                db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    if role not in ("admin", "operator", "district", "school", "provider"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Недопустимая роль")
    scope_field = {"school": school_id, "provider": provider_name, "district": district}
    if role in scope_field and not scope_field[role]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Для роли «{role}» нужна область видимости (school_id / provider_name / district)")
    if db.query(User).filter(User.email == email.lower()).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Пользователь уже существует")
    new = User(email=email.lower(), full_name=full_name, role=role, school_id=school_id,
               provider_name=provider_name, district=district,
               password_hash=hash_password(password))
    db.add(new)
    db.commit()
    write_audit(db, user.email, user.role, "user.created", email,
                request.client.host if request.client else "", {"role": role})
    return {"id": new.id, "email": new.email, "role": new.role}
