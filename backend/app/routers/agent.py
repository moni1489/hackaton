"""Agent API — приём данных от ПК-агентов.

Все эндпоинты требуют device-токен, привязанный к отпечатку оборудования.
School ID никогда не берётся из тела запроса — только из записи в БД.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from ..cache import cache_invalidate, rate_limit_ok
from ..config import settings
from ..database import get_db
from ..models import Device, Measurement, School, SyncBatch
from ..schemas import AgentConfig, BatchIn, EnrollRequest, EnrollResponse, MeasurementIn
from ..security import create_device_token, current_device, fingerprint, write_audit
from ..services.smart_sync import apply_latest, enqueue, stats

log = logging.getLogger("agent_api")
router = APIRouter(prefix="/api/agent", tags=["Agent API"])


def _guard(device_id: str) -> None:
    if not rate_limit_ok(f"agent:{device_id}", settings.RATE_LIMIT_AGENT):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Превышен лимит запросов агента")


def _config(device: Device) -> AgentConfig:
    return AgentConfig(
        test_interval_sec=device.test_interval_sec or settings.INTERVAL_NORMAL_SEC,
        diagnostic_mode=device.diagnostic_mode or "standard",
        config_version=device.config_version or 1,
        batch_max_items=settings.SYNC_BATCH_LIMIT,
        server_time=datetime.utcnow(),
    )


@router.post("/enroll", response_model=EnrollResponse, summary="Регистрация ПК-агента")
def enroll(payload: EnrollRequest, request: Request, db: Session = Depends(get_db)):
    """Выдаёт device-токен в обмен на код школы + одноразовый секрет развёртывания.

    Секрет проверяется до любых действий — это защищает от регистрации
    поддельных устройств под чужим School ID.
    """
    school = db.query(School).filter(School.school_id_code == payload.school_id_code).first()
    if not school:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Организация не найдена")

    expected = fingerprint("enroll", school.school_id_code, settings.JWT_SECRET)[:12]
    if payload.enrollment_secret != expected:
        write_audit(db, payload.device_id, "device", "enroll.rejected", payload.school_id_code,
                    request.client.host if request.client else "")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Неверный код развёртывания")

    hwfp = fingerprint(payload.mac_address or "", payload.device_id,
                       payload.cpu_model or "", payload.os_name or "")

    device = db.query(Device).filter(Device.device_id == payload.device_id).first()
    if device and device.school_id != school.id:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Device ID уже зарегистрирован в другой организации")
    if not device:
        device = Device(device_id=payload.device_id, school_id=school.id,
                        enrolled_at=datetime.utcnow(), status="online")
        db.add(device)

    device.name = payload.name
    device.room = payload.room
    device.ip_address = payload.ip_address
    device.mac_address = payload.mac_address
    device.os_name = payload.os_name
    device.cpu_model = payload.cpu_model
    device.ram_gb = payload.ram_gb
    device.agent_version = payload.agent_version
    device.device_type = payload.device_type
    device.link_mode = payload.link_mode
    device.hardware_fingerprint = hwfp
    device.cert_fingerprint = payload.cert_fingerprint
    device.revoked = False
    device.test_interval_sec = device.test_interval_sec or settings.INTERVAL_NORMAL_SEC
    db.commit()
    db.refresh(device)

    write_audit(db, payload.device_id, "device", "enroll.success", school.school_id_code,
                request.client.host if request.client else "",
                {"hwfp": hwfp[:16], "room": payload.room})
    cache_invalidate("dash:")

    return EnrollResponse(device_token=create_device_token(device.device_id, school.id, hwfp),
                          device_id=device.device_id, school_id=school.id,
                          hardware_fingerprint=hwfp, config=_config(device))


@router.get("/config", response_model=AgentConfig, summary="Динамическая конфигурация агента")
def get_config(device: Device = Depends(current_device)):
    """Killer feature #3: сервер сам повышает частоту тестов при деградации канала."""
    _guard(device.device_id)
    return _config(device)


@router.post("/measurements", status_code=status.HTTP_201_CREATED,
             summary="Одиночный замер (онлайн-режим)")
def push_measurement(payload: MeasurementIn, device: Device = Depends(current_device),
                     db: Session = Depends(get_db)):
    _guard(device.device_id)
    row = {
        "device_id": device.device_id, "school_id": device.school_id,
        "timestamp": payload.timestamp or datetime.utcnow(),
        "download_speed": payload.download_speed, "upload_speed": payload.upload_speed,
        "ping": payload.ping, "jitter": payload.jitter,
        "packet_loss": payload.packet_loss, "is_offline": payload.is_offline,
        "source": "live",
    }
    db.add(Measurement(**row))
    apply_latest(db, device.device_id, device.school_id, row)
    db.commit()
    cache_invalidate("dash:")
    return {"status": "accepted", "config": _config(device)}


@router.post("/measurements/batch", status_code=status.HTTP_202_ACCEPTED,
             summary="Офлайн-догрузка пакетом (Smart Sync)")
async def push_batch(payload: BatchIn, response: Response,
                     device: Device = Depends(current_device), db: Session = Depends(get_db)):
    """Killer feature #1: пакет только регистрируется и ставится в очередь.

    HTTP-обработчик не пишет в БД — поэтому одновременное возвращение сотен
    офлайн-агентов не вызывает лавины записей (Thundering Herd).
    """
    _guard(device.device_id)
    if not payload.items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пустой пакет")
    if len(payload.items) > settings.SYNC_BATCH_LIMIT:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"Не более {settings.SYNC_BATCH_LIMIT} замеров в пакете")

    batch = SyncBatch(device_id=device.device_id, school_id=device.school_id,
                      items=len(payload.items), status="queued")
    db.add(batch)
    db.commit()
    db.refresh(batch)

    ok = await enqueue(batch.id, device.device_id, device.school_id,
                       [i.model_dump(mode="json") for i in payload.items])
    if not ok:
        batch.status = "failed"
        batch.error = "очередь переполнена"
        db.commit()
        # Backpressure: агент повторит выгрузку позже — данные не теряются.
        response.headers["Retry-After"] = "120"
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Очередь синхронизации перегружена, повторите позже")

    return {"status": "queued", "batch_id": batch.id, "items": len(payload.items),
            "queue": stats(), "config": _config(device)}


@router.get("/sync/status", summary="Состояние очереди Smart Sync")
def sync_status(device: Device = Depends(current_device), db: Session = Depends(get_db)):
    rows = (db.query(SyncBatch).filter(SyncBatch.device_id == device.device_id)
            .order_by(SyncBatch.id.desc()).limit(10).all())
    return {"queue": stats(),
            "batches": [{"id": b.id, "status": b.status, "items": b.items,
                         "received_at": b.received_at, "processed_at": b.processed_at}
                        for b in rows]}


@router.post("/heartbeat", summary="Пульс агента")
def heartbeat(device: Device = Depends(current_device), db: Session = Depends(get_db)):
    _guard(device.device_id)
    device.last_seen = datetime.utcnow()
    if device.status == "offline":
        device.status = "online"
    db.commit()
    return {"status": "ok", "config": _config(device)}
