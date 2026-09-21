"""Web API — данные для дашборда, карты, карточки школы и ПК-уровня."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..cache import backend_name, cache_get, cache_set, rate_limit_ok
from ..config import settings
from ..database import get_db
from ..models import Device, Incident, Measurement, School, SyncBatch, User
from ..security import can_access_school, current_user, require_roles, verify_audit_chain, write_audit
from ..services.ml.features import CAUSE_LABELS, CAUSE_OWNER
from ..services.predictive import analyze, region_overview
from ..services.smart_sync import stats as sync_stats
from ..services.status import ALL_STATUSES

router = APIRouter(prefix="/api/web", tags=["Web API"])


def _guard(user: User) -> None:
    if not rate_limit_ok(f"web:{user.id}", settings.RATE_LIMIT_WEB):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов")


def _scope(query, user: User):
    """RBAC-изоляция выборки школ."""
    if user.role == "school" and user.school_id:
        return query.filter(School.id == user.school_id)
    if user.role == "provider" and user.provider_name:
        return query.filter(School.provider == user.provider_name)
    return query


def school_brief(s: School) -> dict:
    return {
        "id": s.id, "school_id_code": s.school_id_code, "name": s.name, "region": s.region,
        "address": s.address, "lat": s.lat, "lng": s.lng, "provider": s.provider,
        "connection_type": s.connection_type, "contract_speed_down": s.contract_speed_down,
        "contract_speed_up": s.contract_speed_up, "status": s.status,
        "current_download": s.current_download, "current_upload": s.current_upload,
        "current_ping": s.current_ping, "current_jitter": s.current_jitter,
        "current_packet_loss": s.current_packet_loss,
        "last_measurement": s.last_measurement.isoformat() if s.last_measurement else None,
    }


def device_dict(d: Device) -> dict:
    return {
        "id": d.id, "device_id": d.device_id, "school_id": d.school_id, "name": d.name,
        "room": d.room, "ip_address": d.ip_address, "mac_address": d.mac_address,
        "status": d.status, "device_type": d.device_type, "os_name": d.os_name,
        "cpu_model": d.cpu_model, "ram_gb": d.ram_gb, "agent_version": d.agent_version,
        "uptime_hours": d.uptime_hours, "link_mode": d.link_mode,
        "wifi_signal_dbm": d.wifi_signal_dbm,
        "current_download": d.current_download, "current_upload": d.current_upload,
        "current_ping": d.current_ping, "current_jitter": d.current_jitter,
        "current_packet_loss": d.current_packet_loss,
        "availability_pct": d.availability_pct, "sla_compliance_pct": d.sla_compliance_pct,
        "test_interval_sec": d.test_interval_sec, "diagnostic_mode": d.diagnostic_mode,
        "config_version": d.config_version, "revoked": d.revoked,
        "hardware_fingerprint": (d.hardware_fingerprint or "")[:16],
        "last_seen": d.last_seen.isoformat() if d.last_seen else None,
        "enrolled_at": d.enrolled_at.isoformat() if d.enrolled_at else None,
    }


@router.get("/overview", summary="KPI, распределение статусов и справочники фильтров")
def overview(db: Session = Depends(get_db), user: User = Depends(current_user)):
    _guard(user)
    key = f"dash:overview:{user.role}:{user.school_id or user.provider_name or 'all'}"
    cached = cache_get(key)
    if cached:
        return {**cached, "cached": True}

    base = _scope(db.query(School), user)
    schools = base.all()
    ids = [s.id for s in schools]
    live = [s for s in schools if s.status != "Нет соединения"]

    inc_query = db.query(Incident).filter(
        Incident.status.in_(["Новый", "В работе", "Передан поставщику", "Ожидает информации"]))
    if user.role != "admin" and user.role != "operator":
        inc_query = inc_query.filter(Incident.school_id.in_(ids or [-1]))

    devices = db.query(Device)
    if ids and user.role not in ("admin", "operator"):
        devices = devices.filter(Device.school_id.in_(ids or [-1]))
    device_rows = devices.all()

    def avg(attr):
        vals = [getattr(s, attr) or 0 for s in live]
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    counts = {"normal": 0, "unstable": 0, "critical": 0, "offline": 0}
    key_map = {"Норма": "normal", "Нестабильно": "unstable",
               "Критично": "critical", "Нет соединения": "offline"}
    for s in schools:
        counts[key_map.get(s.status, "offline")] += 1

    sla_ok = sum(1 for s in schools if s.status == "Норма")
    payload = {
        "total_schools": len(schools),
        "total_devices": len(device_rows),
        "devices_online": sum(1 for d in device_rows if d.status == "online"),
        "active_incidents": inc_query.count(),
        "avg_download": avg("current_download"),
        "avg_upload": avg("current_upload"),
        "avg_ping": avg("current_ping"),
        "avg_loss": avg("current_packet_loss"),
        "sla_compliance": round(100 * sla_ok / len(schools), 1) if schools else 100.0,
        "status_counts": counts,
        "filters": {
            "regions": ["Все районы"] + sorted({s.region for s in schools if s.region}),
            "providers": ["Все провайдеры"] + sorted({s.provider for s in schools if s.provider}),
            "connection_types": ["Все типы"] + sorted({s.connection_type for s in schools
                                                       if s.connection_type}),
            "statuses": ["Все статусы"] + ALL_STATUSES,
        },
        "generated_at": datetime.utcnow().isoformat(),
    }
    cache_set(key, payload)
    return {**payload, "cached": False}


@router.get("/schools", summary="Школы для карты и списка (с учётом роли)")
def list_schools(region: str | None = None, provider: str | None = None,
                 status_filter: str | None = Query(None, alias="status"),
                 connection_type: str | None = None, search: str | None = None,
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    _guard(user)
    query = _scope(db.query(School), user)
    if region and not region.startswith("Все"):
        query = query.filter(School.region == region)
    if provider and not provider.startswith("Все"):
        query = query.filter(School.provider == provider)
    if status_filter and not status_filter.startswith("Все"):
        query = query.filter(School.status == status_filter)
    if connection_type and not connection_type.startswith("Все"):
        query = query.filter(School.connection_type == connection_type)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(School.name.ilike(pattern) | School.school_id_code.ilike(pattern))
    return [school_brief(s) for s in query.order_by(School.name).all()]


@router.get("/schools/{school_id}", summary="Карточка школы: контакты, ПК-агенты, инциденты")
def school_detail(school_id: int, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    _guard(user)
    school = db.get(School, school_id)
    if not school:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Школа не найдена")
    if not can_access_school(user, school):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к данным этой организации")

    devices = db.query(Device).filter(Device.school_id == school_id).order_by(Device.id).all()
    incidents = (db.query(Incident).filter(Incident.school_id == school_id)
                 .order_by(Incident.id.desc()).limit(50).all())
    from ..services.public_data import school_connections
    return {
        **school_brief(school),
        "published_connections": school_connections(db, school_id),
        "contact_name": school.contact_name, "contact_phone": school.contact_phone,
        "contact_email": school.contact_email, "provider_phone": school.provider_phone,
        "devices": [device_dict(d) for d in devices],
        "incidents": [{"id": i.id, "incident_number": i.incident_number, "status": i.status,
                       "severity": i.severity, "device_id": i.device_id,
                       "start_time": i.start_time.isoformat() if i.start_time else None,
                       "description": i.description} for i in incidents],
    }


@router.get("/schools/{school_id}/measurements", summary="История замеров школы")
def school_measurements(school_id: int, hours: int = 720, limit: int = 500,
                        db: Session = Depends(get_db), user: User = Depends(current_user)):
    _guard(user)
    school = db.get(School, school_id)
    if not school or not can_access_school(user, school):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа")
    since = datetime.utcnow() - timedelta(hours=hours)
    rows = (db.query(Measurement)
            .filter(Measurement.school_id == school_id, Measurement.timestamp >= since)
            .order_by(Measurement.timestamp.asc()).all())
    if not rows:
        rows = (db.query(Measurement).filter(Measurement.school_id == school_id)
                .order_by(Measurement.timestamp.asc()).all())

    # В школе несколько ПК-агентов: для графика организации усредняем их замеры
    # по часовым интервалам, иначе линия превращается в «пилу» из разных ПК.
    buckets: dict[datetime, list[Measurement]] = {}
    for row in rows:
        if not row.timestamp:
            continue
        buckets.setdefault(row.timestamp.replace(minute=0, second=0, microsecond=0), []).append(row)

    def mean(items, attr):
        values = [getattr(i, attr) or 0 for i in items]
        return round(sum(values) / len(values), 1) if values else 0.0

    series = [{"timestamp": key.isoformat(),
               "devices": len({i.device_id for i in items}),
               "download_speed": mean(items, "download_speed"),
               "upload_speed": mean(items, "upload_speed"),
               "ping": mean(items, "ping"),
               "jitter": mean(items, "jitter"),
               "packet_loss": round(mean(items, "packet_loss"), 2),
               "is_offline": all(i.is_offline for i in items),
               "source": "backfill" if any(i.source == "backfill" for i in items) else "live"}
              for key, items in sorted(buckets.items())]
    return series[-limit:]


# --- ПК-уровень прослеживания ---------------------------------------------

@router.get("/devices/{device_id}", summary="Карточка отдельного ПК-агента")
def device_detail(device_id: str, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    _guard(user)
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Устройство не найдено")
    school = db.get(School, device.school_id)
    if not school or not can_access_school(user, school):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к этому устройству")

    rows = (db.query(Measurement).filter(Measurement.device_id == device_id)
            .order_by(Measurement.timestamp.desc()).limit(200).all())[::-1]
    batches = (db.query(SyncBatch).filter(SyncBatch.device_id == device_id)
               .order_by(SyncBatch.id.desc()).limit(5).all())

    offline = sum(1 for r in rows if r.is_offline)
    return {
        "device": device_dict(device),
        "school": {"id": school.id, "name": school.name, "school_id_code": school.school_id_code,
                   "region": school.region, "provider": school.provider,
                   "contract_speed_down": school.contract_speed_down},
        "measurements": [{"timestamp": r.timestamp.isoformat() if r.timestamp else None,
                          "download_speed": r.download_speed, "upload_speed": r.upload_speed,
                          "ping": r.ping, "jitter": r.jitter, "packet_loss": r.packet_loss,
                          "is_offline": r.is_offline, "source": r.source} for r in rows],
        "summary": {
            "samples": len(rows),
            "offline_samples": offline,
            "avg_download": round(sum(r.download_speed or 0 for r in rows) / len(rows), 1) if rows else 0,
            "avg_ping": round(sum(r.ping or 0 for r in rows) / len(rows), 1) if rows else 0,
            "max_download": round(max((r.download_speed or 0 for r in rows), default=0), 1),
            "min_download": round(min((r.download_speed or 0 for r in rows), default=0), 1),
            "backfilled": sum(1 for r in rows if r.source == "backfill"),
        },
        "analytics": analyze(db, device.school_id, device_id=device_id, days=30),
        "sync_batches": [{"id": b.id, "status": b.status, "items": b.items,
                          "received_at": b.received_at.isoformat() if b.received_at else None,
                          "processed_at": b.processed_at.isoformat() if b.processed_at else None}
                         for b in batches],
    }


@router.get("/devices", summary="Все ПК-агенты в области видимости")
def list_devices(school_id: int | None = None, status_filter: str | None = Query(None, alias="status"),
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    _guard(user)
    allowed = [s.id for s in _scope(db.query(School), user).all()]
    query = db.query(Device).filter(Device.school_id.in_(allowed or [-1]))
    if school_id:
        query = query.filter(Device.school_id == school_id)
    if status_filter:
        query = query.filter(Device.status == status_filter)
    names = {s.id: s.name for s in db.query(School).all()}
    return [{**device_dict(d), "school_name": names.get(d.school_id)}
            for d in query.order_by(Device.school_id, Device.id).all()]


# --- Инциденты -------------------------------------------------------------

@router.get("/incidents", summary="Лента инцидентов")
def incidents(limit: int = 100, db: Session = Depends(get_db),
              user: User = Depends(current_user)):
    _guard(user)
    allowed = [s.id for s in _scope(db.query(School), user).all()]
    query = db.query(Incident)
    if user.role not in ("admin", "operator"):
        query = query.filter(Incident.school_id.in_(allowed or [-1]))
    names = {s.id: s for s in db.query(School).all()}
    rows = query.order_by(Incident.id.desc()).limit(min(limit, 300)).all()
    return [{"id": i.id, "incident_number": i.incident_number, "school_id": i.school_id,
             "school_name": names[i.school_id].name if i.school_id in names else "—",
             "region": names[i.school_id].region if i.school_id in names else "—",
             "device_id": i.device_id, "provider": i.provider, "status": i.status,
             "severity": i.severity,
             "start_time": i.start_time.isoformat() if i.start_time else None,
             "description": i.description,
             "root_cause": i.root_cause,
             "root_cause_label": CAUSE_LABELS.get(i.root_cause or ""),
             "responsible": CAUSE_OWNER.get(i.root_cause or ""),
             "root_cause_confidence": i.root_cause_confidence,
             "operator_verdict": i.operator_verdict,
             "has_claim": bool(i.ai_claim_text)} for i in rows]


@router.patch("/incidents/{incident_id}", summary="Смена статуса инцидента")
def update_incident(incident_id: int, new_status: str, request: Request,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_roles("admin", "operator"))):
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Инцидент не найден")
    old = incident.status
    incident.status = new_status
    if new_status == "Устранён":
        incident.resolved_time = datetime.utcnow()
    db.commit()
    write_audit(db, user.email, user.role, "incident.status_changed", incident.incident_number,
                request.client.host if request.client else "", {"from": old, "to": new_status})
    return {"status": "ok", "incident_id": incident_id, "new_status": new_status}


# --- Предиктивная аналитика ------------------------------------------------

@router.get("/schools/{school_id}/analytics", summary="Предиктивный SLA-анализ школы")
def school_analytics(school_id: int, days: int = 30, db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    _guard(user)
    school = db.get(School, school_id)
    if not school or not can_access_school(user, school):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа")
    return analyze(db, school_id, days=days)


@router.get("/risk-queue", summary="Школы с наибольшим риском нарушения SLA")
def risk_queue(limit: int = 8, db: Session = Depends(get_db),
               user: User = Depends(current_user)):
    _guard(user)
    key = f"dash:risk:{user.role}:{user.school_id or 'all'}:{limit}"
    cached = cache_get(key)
    if cached:
        return cached
    rows = region_overview(db, limit=limit)
    if user.role not in ("admin", "operator"):
        allowed = {s.id for s in _scope(db.query(School), user).all()}
        rows = [r for r in rows if r["school_id"] in allowed]
    cache_set(key, rows, ttl=30)
    return rows


@router.get("/trend", summary="Почасовой тренд по области (для графика)")
def trend(hours: int = 24, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _guard(user)
    key = f"dash:trend:{user.role}:{hours}"
    cached = cache_get(key)
    if cached:
        return cached
    since = datetime.utcnow() - timedelta(hours=hours)
    rows = (db.query(Measurement).filter(Measurement.timestamp >= since).all()
            or db.query(Measurement).order_by(Measurement.timestamp.desc()).limit(2000).all())
    buckets: dict[str, list] = {}
    for r in rows:
        if not r.timestamp:
            continue
        label = r.timestamp.strftime("%H:00")
        buckets.setdefault(label, []).append(r)
    data = [{"label": label,
             "download": round(sum(x.download_speed or 0 for x in items) / len(items), 1),
             "upload": round(sum(x.upload_speed or 0 for x in items) / len(items), 1),
             "ping": round(sum(x.ping or 0 for x in items) / len(items), 1),
             "samples": len(items)}
            for label, items in sorted(buckets.items())]
    cache_set(key, data, ttl=60)
    return data


@router.get("/system", summary="Состояние подсистем (для панели администратора)")
def system_state(db: Session = Depends(get_db), user: User = Depends(current_user)):
    _guard(user)
    return {
        "api": {"framework": "FastAPI", "version": settings.VERSION, "async": True},
        "database": {"dialect": db.bind.dialect.name,
                     "schools": db.query(func.count(School.id)).scalar(),
                     "devices": db.query(func.count(Device.id)).scalar(),
                     "measurements": db.query(func.count(Measurement.id)).scalar()},
        "cache": {"backend": backend_name(), "ttl_sec": settings.CACHE_TTL},
        "smart_sync": sync_stats(db),
        "audit": verify_audit_chain(db) if user.role == "admin" else {"valid": None},
        "security": {"mtls_required": settings.REQUIRE_MTLS,
                     "device_auth": "JWT + hardware fingerprint",
                     "rbac": True},
    }
