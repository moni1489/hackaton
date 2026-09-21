"""Web API — данные для дашборда, карты, карточки школы и ПК-уровня.

Время «сейчас» у всех эндпоинтов задаётся параметром as_of: без него это текущий момент
(боевой режим), с ним — момент истории (режим демонстрации). Данные старше порога
свежести не выдаются за текущие: школа получает статус «Нет свежих данных».
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..cache import backend_name, cache_get, cache_set, rate_limit_ok
from ..config import settings
from ..database import get_db
from ..models import Device, Incident, Line, Measurement, School, SyncBatch, User
from ..security import (
    FULL_SCOPE_ROLES, can_access_school, current_user, require_roles, scope_key, scope_schools,
    verify_audit_chain, write_audit,
)
from ..services.ml.features import CAUSE_LABELS, CAUSE_OWNER
from ..services.predictive import analyze, region_overview
from ..services.smart_sync import stats as sync_stats
from ..services.status import (
    ALL_STATUSES, STATUS_NO_DATA, STATUS_OFFLINE, STATUS_OK, effective_status, freshness,
    is_fresh, naive_utc, thresholds,
)

router = APIRouter(prefix="/api/web", tags=["Web API"])

_scope = scope_schools   # RBAC-изоляция выборки школ (запрет по умолчанию)
OPEN_INCIDENTS = ["Новый", "В работе", "Передан поставщику", "Ожидает информации"]


def _guard(user: User) -> None:
    if not rate_limit_ok(f"web:{user.id}", settings.RATE_LIMIT_WEB):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов")


def _now(as_of: datetime | None) -> datetime:
    return naive_utc(as_of) if as_of else datetime.utcnow()


def _school_ids(db: Session, user: User) -> list[int] | None:
    """None — вся область (фильтр не нужен), иначе идентификаторы школ в области видимости."""
    if user.role in FULL_SCOPE_ROLES:
        return None
    return [i for (i,) in scope_schools(db.query(School.id), user)]


def _limit(query, column, ids: list[int] | None):
    return query if ids is None else query.filter(column.in_(ids or [-1]))


def school_brief(s: School, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    fresh = freshness(s.last_measurement, now)
    return {
        "id": s.id, "school_id_code": s.school_id_code, "name": s.name, "region": s.region,
        "address": s.address, "lat": s.lat, "lng": s.lng, "provider": s.provider,
        "connection_type": s.connection_type, "contract_speed_down": s.contract_speed_down,
        "contract_speed_up": s.contract_speed_up,
        # Статус — только по свежему замеру основной линии; иначе «Нет свежих данных».
        "status": effective_status(s.status, s.last_measurement, now),
        "last_known_status": s.status,
        "current_download": s.current_download, "current_upload": s.current_upload,
        "current_ping": s.current_ping, "current_jitter": s.current_jitter,
        "current_packet_loss": s.current_packet_loss,
        "last_measurement": fresh["last_measurement"],
        "age_min": fresh["age_min"], "is_stale": fresh["is_stale"],
    }


def device_dict(d: Device, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    return {
        "id": d.id, "device_id": d.device_id, "school_id": d.school_id, "name": d.name,
        "room": d.room, "ip_address": d.ip_address, "mac_address": d.mac_address,
        "status": d.status if is_fresh(d.last_measured, now) else "stale",
        "device_type": d.device_type, "os_name": d.os_name,
        "role": "monitor" if d.line_id else "workstation", "line_id": d.line_id,
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
        "last_measured": d.last_measured.isoformat() if d.last_measured else None,
        "enrolled_at": d.enrolled_at.isoformat() if d.enrolled_at else None,
    }


def line_dict(line: Line, now: datetime) -> dict:
    return {
        "id": line.id, "code": line.code, "role": line.role, "provider": line.provider,
        "connection_type": line.connection_type,
        "contract_speed_down": line.contract_speed_down,
        "contract_speed_up": line.contract_speed_up,
        "status": effective_status(line.status, line.last_measurement, now),
        "current_download": line.current_download, "current_upload": line.current_upload,
        "current_ping": line.current_ping, "current_jitter": line.current_jitter,
        "current_packet_loss": line.current_packet_loss,
        "last_measurement": line.last_measurement.isoformat() if line.last_measurement else None,
    }


@router.get("/overview", summary="KPI, распределение статусов и справочники фильтров")
def overview(as_of: datetime | None = None, db: Session = Depends(get_db),
             user: User = Depends(current_user)):
    _guard(user)
    now = _now(as_of)
    key = f"dash:overview:{scope_key(user)}:{as_of.isoformat() if as_of else 'live'}"
    cached = cache_get(key)
    if cached:
        return {**cached, "cached": True}

    th = thresholds()
    schools = _scope(db.query(School), user).all()
    ids = [s.id for s in schools]
    eff = {s.id: effective_status(s.status, s.last_measurement, now, th) for s in schools}
    measured = [s for s in schools if eff[s.id] not in (STATUS_OFFLINE, STATUS_NO_DATA)]

    inc_query = db.query(Incident).filter(Incident.status.in_(OPEN_INCIDENTS),
                                          Incident.school_id.in_(ids or [-1]))
    device_rows = db.query(Device).filter(Device.school_id.in_(ids or [-1])).all()

    def avg(attr):
        vals = [getattr(s, attr) or 0 for s in measured]
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    counts = {"normal": 0, "unstable": 0, "critical": 0, "offline": 0, "no_data": 0}
    key_map = {"Норма": "normal", "Нестабильно": "unstable", "Критично": "critical",
               "Нет соединения": "offline", STATUS_NO_DATA: "no_data"}
    for s in schools:
        counts[key_map[eff[s.id]]] += 1

    with_data = len(schools) - counts["no_data"]
    last = max((s.last_measurement for s in schools if s.last_measurement), default=None)
    payload = {
        "total_schools": len(schools),
        "total_devices": len(device_rows),
        "devices_online": sum(1 for d in device_rows
                              if d.status == "online" and is_fresh(d.last_measured, now, th)),
        "active_incidents": inc_query.count(),
        "avg_download": avg("current_download"),
        "avg_upload": avg("current_upload"),
        "avg_ping": avg("current_ping"),
        "avg_loss": avg("current_packet_loss"),
        # Доля школ в норме среди тех, о ком есть свежие данные; нет данных — нет процента.
        "sla_compliance": round(100 * counts["normal"] / with_data, 1) if with_data else None,
        "status_counts": counts,
        "freshness": {**freshness(last, now, th),
                      "mode": "history" if as_of else "live",
                      "as_of": now.isoformat(),
                      "schools_with_data": with_data, "schools_without_data": counts["no_data"]},
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
                 as_of: datetime | None = None,
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    _guard(user)
    now = _now(as_of)
    cutoff = now - timedelta(minutes=thresholds().stale_after_min)
    query = _scope(db.query(School), user)
    if region and not region.startswith("Все"):
        query = query.filter(School.region == region)
    if provider and not provider.startswith("Все"):
        query = query.filter(School.provider == provider)
    if status_filter and not status_filter.startswith("Все"):
        if status_filter == STATUS_NO_DATA:
            query = query.filter(School.last_measurement.is_(None)
                                 | (School.last_measurement < cutoff) | School.status.is_(None))
        else:
            query = query.filter(School.status == status_filter, School.last_measurement >= cutoff)
    if connection_type and not connection_type.startswith("Все"):
        query = query.filter(School.connection_type == connection_type)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(School.name.ilike(pattern) | School.school_id_code.ilike(pattern))
    return [school_brief(s, now) for s in query.order_by(School.name).all()]


@router.get("/schools/{school_id}", summary="Карточка школы: линии, рабочие места, контакты, инциденты")
def school_detail(school_id: int, as_of: datetime | None = None, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    _guard(user)
    now = _now(as_of)
    school = db.get(School, school_id)
    if not school:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Школа не найдена")
    if not can_access_school(user, school):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к данным этой организации")

    devices = db.query(Device).filter(Device.school_id == school_id).order_by(Device.id).all()
    lines = db.query(Line).filter(Line.school_id == school_id).order_by(Line.id).all()
    incidents = (db.query(Incident).filter(Incident.school_id == school_id)
                 .order_by(Incident.id.desc()).limit(50).all())

    # Рабочие места оцениваются отдельно от основной линии и на статус школы не влияют.
    stations = [d for d in devices if not d.line_id]
    fresh_stations = [d for d in stations if is_fresh(d.last_measured, now)]
    from ..services.public_data import school_connections
    return {
        **school_brief(school, now),
        "published_connections": school_connections(db, school_id),
        "contact_name": school.contact_name, "contact_phone": school.contact_phone,
        "contact_email": school.contact_email, "provider_phone": school.provider_phone,
        "lines": [line_dict(line, now) for line in lines],
        "workstations": {
            "total": len(stations), "with_fresh_data": len(fresh_stations),
            "degraded": sum(1 for d in fresh_stations if d.status == "warning"),
            "offline": sum(1 for d in fresh_stations if d.status == "offline"),
            "note": "Оцениваются отдельно; статус школы определяет основная линия",
        },
        "devices": [device_dict(d, now) for d in devices],
        "incidents": [{"id": i.id, "incident_number": i.incident_number, "status": i.status,
                       "severity": i.severity, "device_id": i.device_id,
                       "start_time": i.start_time.isoformat() if i.start_time else None,
                       "description": i.description} for i in incidents],
    }


@router.get("/schools/{school_id}/measurements", summary="История замеров школы")
def school_measurements(school_id: int, hours: int = 720, limit: int = 500,
                        as_of: datetime | None = None,
                        db: Session = Depends(get_db), user: User = Depends(current_user)):
    _guard(user)
    school = db.get(School, school_id)
    if not school or not can_access_school(user, school):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа")
    now = _now(as_of)
    rows = (db.query(Measurement)
            .filter(Measurement.school_id == school_id,
                    Measurement.timestamp >= now - timedelta(hours=hours),
                    Measurement.timestamp <= now)
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
def device_detail(device_id: str, as_of: datetime | None = None, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    _guard(user)
    now = _now(as_of)
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Устройство не найдено")
    school = db.get(School, device.school_id)
    if not school or not can_access_school(user, school):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к этому устройству")

    rows = (db.query(Measurement).filter(Measurement.device_id == device_id,
                                         Measurement.timestamp <= now)
            .order_by(Measurement.timestamp.desc()).limit(200).all())[::-1]
    batches = (db.query(SyncBatch).filter(SyncBatch.device_id == device_id)
               .order_by(SyncBatch.id.desc()).limit(5).all())

    offline = sum(1 for r in rows if r.is_offline)
    return {
        "device": device_dict(device, now),
        "school": {"id": school.id, "name": school.name, "school_id_code": school.school_id_code,
                   "region": school.region, "provider": school.provider,
                   "contract_speed_down": school.contract_speed_down},
        "measurements": [{"timestamp": r.timestamp.isoformat() if r.timestamp else None,
                          "download_speed": r.download_speed, "upload_speed": r.upload_speed,
                          "ping": r.ping, "jitter": r.jitter, "packet_loss": r.packet_loss,
                          "is_offline": r.is_offline, "source": r.source,
                          "status": r.status} for r in rows],
        "summary": {
            "samples": len(rows),
            "offline_samples": offline,
            "avg_download": round(sum(r.download_speed or 0 for r in rows) / len(rows), 1) if rows else 0,
            "avg_ping": round(sum(r.ping or 0 for r in rows) / len(rows), 1) if rows else 0,
            "max_download": round(max((r.download_speed or 0 for r in rows), default=0), 1),
            "min_download": round(min((r.download_speed or 0 for r in rows), default=0), 1),
            "backfilled": sum(1 for r in rows if r.source == "backfill"),
        },
        "analytics": analyze(db, device.school_id, device_id=device_id, days=30, now=now),
        "sync_batches": [{"id": b.id, "status": b.status, "items": b.items,
                          "received_at": b.received_at.isoformat() if b.received_at else None,
                          "processed_at": b.processed_at.isoformat() if b.processed_at else None}
                         for b in batches],
    }


@router.get("/devices", summary="Все ПК-агенты в области видимости")
def list_devices(school_id: int | None = None, status_filter: str | None = Query(None, alias="status"),
                 as_of: datetime | None = None,
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    _guard(user)
    now = _now(as_of)
    allowed = {s.id: s.name for s in _scope(db.query(School), user).all()}
    query = db.query(Device).filter(Device.school_id.in_(list(allowed) or [-1]))
    if school_id:
        query = query.filter(Device.school_id == school_id)
    rows = [{**device_dict(d, now), "school_name": allowed.get(d.school_id)}
            for d in query.order_by(Device.school_id, Device.id).all()]
    return [r for r in rows if r["status"] == status_filter] if status_filter else rows


# --- Инциденты -------------------------------------------------------------

@router.get("/incidents", summary="Лента инцидентов")
def incidents(limit: int = 100, db: Session = Depends(get_db),
              user: User = Depends(current_user)):
    _guard(user)
    query = _limit(db.query(Incident), Incident.school_id, _school_ids(db, user))
    names = {s.id: s for s in db.query(School).all()}
    rows = query.order_by(Incident.id.desc()).limit(min(limit, 300)).all()
    return [{"id": i.id, "incident_number": i.incident_number, "school_id": i.school_id,
             "school_name": names[i.school_id].name if i.school_id in names else "—",
             "region": names[i.school_id].region if i.school_id in names else "—",
             "device_id": i.device_id, "line_id": i.line_id, "provider": i.provider,
             "status": i.status, "severity": i.severity,
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
def school_analytics(school_id: int, days: int = 30, as_of: datetime | None = None,
                     db: Session = Depends(get_db), user: User = Depends(current_user)):
    _guard(user)
    school = db.get(School, school_id)
    if not school or not can_access_school(user, school):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа")
    return analyze(db, school_id, days=days, now=_now(as_of))


@router.get("/risk-queue", summary="Школы с наибольшим риском нарушения SLA")
def risk_queue(limit: int = 8, as_of: datetime | None = None, db: Session = Depends(get_db),
               user: User = Depends(current_user)):
    _guard(user)
    now = _now(as_of)
    # Ключ содержит область видимости: у двух провайдеров кэш не общий.
    key = f"dash:risk:{scope_key(user)}:{limit}:{as_of.isoformat() if as_of else 'live'}"
    cached = cache_get(key)
    if cached:
        return cached
    rows = region_overview(db, limit=limit, school_ids=_school_ids(db, user), now=now)
    cache_set(key, rows, ttl=30)
    return rows


@router.get("/trend", summary="Почасовой тренд по основным линиям (для графика)")
def trend(hours: int = 24, as_of: datetime | None = None, db: Session = Depends(get_db),
          user: User = Depends(current_user)):
    """Только замеры точек мониторинга основных линий и только школ из области видимости.

    Окно считается от «сейчас»: если свежих замеров нет — пустой ряд, а не старая история.
    """
    _guard(user)
    now = _now(as_of)
    key = f"dash:trend:{scope_key(user)}:{hours}:{as_of.isoformat() if as_of else 'live'}"
    cached = cache_get(key)
    if cached is not None:
        return cached
    monitors = (select(Device.device_id).join(Line, Line.id == Device.line_id)
                .where(Line.role == "main"))
    query = db.query(Measurement).filter(
        Measurement.timestamp >= now - timedelta(hours=hours), Measurement.timestamp <= now,
        Measurement.device_id.in_(monitors))
    rows = _limit(query, Measurement.school_id, _school_ids(db, user)).all()
    buckets: dict[str, list] = {}
    for r in rows:
        if r.timestamp:
            buckets.setdefault(r.timestamp.strftime("%H:00"), []).append(r)
    data = [{"label": label,
             "download": round(sum(x.download_speed or 0 for x in items) / len(items), 1),
             "upload": round(sum(x.upload_speed or 0 for x in items) / len(items), 1),
             "ping": round(sum(x.ping or 0 for x in items) / len(items), 1),
             "samples": len(items)}
            for label, items in sorted(buckets.items())]
    cache_set(key, data, ttl=60)
    return data


@router.get("/system", summary="Состояние подсистем (для панели администратора)")
def system_state(db: Session = Depends(get_db),
                 user: User = Depends(require_roles("admin", "operator"))):
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
                     "device_auth": "JWT, привязан к записи устройства (+ mTLS при REQUIRE_MTLS)",
                     "rbac": True},
    }
