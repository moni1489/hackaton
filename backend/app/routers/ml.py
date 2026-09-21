"""ML API: атрибуция причины деградации и прогноз пробоя SLA.

Отвечает на два вопроса, которых не было в системе:
  • кто виноват в текущей деградации — провайдер, район, школа или конкретный ПК;
  • какова вероятность выхода за SLA в ближайшие 6 часов.
"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Device, FaultEvent, Incident, School, User
from ..security import (
    FULL_SCOPE_ROLES, can_access_school, current_user, require_roles, scope_schools, write_audit,
)
from ..services.status import freshness, last_measured, naive_utc
from ..services.ml import attribution, baseline, forecast
from ..services.ml.features import CAUSES, CAUSE_LABELS, CAUSE_OWNER
from ..services.ml.train import train_models

router = APIRouter(prefix="/api/ml", tags=["ML API"])


def _visible(db: Session, user: User) -> list[int] | None:
    """Идентификаторы школ в зоне видимости роли; None — вся область; [] — ничего."""
    if user.role in FULL_SCOPE_ROLES:
        return None
    return [i for (i,) in scope_schools(db.query(School.id), user)]


def _now(as_of: datetime | None) -> datetime:
    return naive_utc(as_of) if as_of else datetime.utcnow()


@router.get("/board", summary="Живая доска предполагаемых источников")
def board(limit: int = 40, as_of: datetime | None = None, db: Session = Depends(get_db),
          user: User = Depends(current_user)):
    return attribution.live_board(db, school_ids=_visible(db, user), limit=min(limit, 200),
                                  now=_now(as_of))


@router.get("/summary", summary="Сводка предполагаемых источников по области")
def summary(as_of: datetime | None = None, db: Session = Depends(get_db),
            user: User = Depends(current_user)):
    """Вместе со сводкой отдаётся свежесть данных: пустая доска при устаревших данных
    означает «нет данных», а не «отклонений нет»."""
    now = _now(as_of)
    ids = _visible(db, user)
    return {**attribution.cause_summary(db, school_ids=ids, now=now),
            "freshness": {**freshness(last_measured(db, ids, upto=now), now),
                          "mode": "history" if as_of else "live"}}


@router.get("/attribution/{school_id}", summary="Гипотеза об источнике по организации")
def school_attribution(school_id: int, at: datetime | None = None, as_of: datetime | None = None,
                       db: Session = Depends(get_db), user: User = Depends(current_user)):
    school = db.get(School, school_id)
    if not school or not can_access_school(user, school):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа")
    return attribution.diagnose(db, school_id, at=naive_utc(at) if at else _now(as_of))


@router.get("/incident/{incident_id}", summary="Вердикт по инциденту (с записью в карточку)")
def incident_attribution(incident_id: int, request: Request, db: Session = Depends(get_db),
                         user: User = Depends(current_user)):
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Инцидент не найден")
    school = db.get(School, incident.school_id)
    if not school or not can_access_school(user, school):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа")
    verdict = attribution.diagnose_incident(db, incident)
    write_audit(db, user.email, user.role, "ml.attribution", incident.incident_number,
                request.client.host if request.client else "",
                {"cause": verdict.get("cause"), "confidence": verdict.get("confidence")})
    return verdict


@router.post("/verdict/{incident_id}", summary="Вердикт оператора — метка для дообучения")
def operator_verdict(incident_id: int, cause: str, request: Request,
                     db: Session = Depends(get_db),
                     user: User = Depends(require_roles("admin", "operator"))):
    """Подтверждение или исправление вердикта модели.

    Это замыкает цикл обучения: подтверждённая причина ложится в журнал аварий
    как метка с origin=operator, и следующее переобучение опирается уже на
    реальные инциденты, а не только на симулятор.
    """
    if cause not in CAUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Недопустимая причина. Ожидается одно из: {', '.join(CAUSES)}")
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Инцидент не найден")
    school = db.get(School, incident.school_id)
    if not school:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Организация не найдена")

    incident.operator_verdict = cause
    if cause != "none":
        db.add(FaultEvent(
            cause=cause, district=school.region, provider=school.provider,
            school_id=school.id,
            device_id=incident.device_id if cause == "device" else None,
            start_time=incident.start_time or datetime.utcnow(),
            end_time=incident.resolved_time or datetime.utcnow(),
            severity=0.3, origin="operator"))
    db.commit()
    write_audit(db, user.email, user.role, "ml.operator_verdict", incident.incident_number,
                request.client.host if request.client else "",
                {"cause": cause, "model_said": incident.root_cause})
    return {"status": "ok", "incident_id": incident_id, "operator_verdict": cause,
            "model_verdict": incident.root_cause,
            "agreement": incident.root_cause == cause,
            "note": "Метка учтена, будет использована при следующем переобучении"}


@router.get("/forecast/{school_id}", summary="Вероятность выхода за SLA в ближайшие 6 часов")
def school_forecast(school_id: int, as_of: datetime | None = None, db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    school = db.get(School, school_id)
    if not school or not can_access_school(user, school):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа")
    return forecast.predict(db, school_id, at=_now(as_of))


@router.get("/forecast", summary="Очередь риска по области — где рванёт первым")
def region_forecast(limit: int = 12, threshold: float = 0.25, as_of: datetime | None = None,
                    db: Session = Depends(get_db), user: User = Depends(current_user)):
    return forecast.region_forecast(db, school_ids=_visible(db, user), limit=min(limit, 100),
                                    threshold=threshold, now=_now(as_of))


@router.get("/timeline/{device_id}", summary="Факт против сезонной нормы по ПК")
def timeline(device_id: str, hours: int = 24, as_of: datetime | None = None,
             db: Session = Depends(get_db), user: User = Depends(current_user)):
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ПК-агент не найден")
    school = db.get(School, device.school_id)
    if not school or not can_access_school(user, school):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа")
    return {"device_id": device_id, "hours": hours,
            "points": baseline.device_timeline(db, device_id, hours=hours, now=_now(as_of))}


@router.get("/model-info", summary="Версии моделей, метрики и покрытие базиса")
def model_info(db: Session = Depends(get_db), user: User = Depends(current_user)):
    attr, fcst = attribution.model(), forecast.model()
    labelled = db.query(FaultEvent).count()
    operator_labels = db.query(FaultEvent).filter(FaultEvent.origin == "operator").count()
    return {
        "baseline": baseline.coverage(),
        "attribution": {
            "trained": bool(attr),
            "version": attr.version if attr else None,
            "classes": [{"key": c, "label": CAUSE_LABELS[c], "responsible": CAUSE_OWNER[c]}
                        for c in (attr.classes if attr else CAUSES)],
            "features": attr.features if attr else [],
            "metrics": attr.metrics if attr else {},
            "fallback": "детерминированные правила границ поражения",
            "output": "предполагаемый источник + достаточность данных; без сопоставимых "
                      "школ вывод «источник не определён»",
        },
        "forecast": {
            "trained": bool(fcst),
            "version": fcst.version if fcst else None,
            "horizon_hours": forecast.HORIZON_H,
            "features": fcst.features if fcst else [],
            "metrics": fcst.metrics if fcst else {},
        },
        "labels": {"total": labelled, "from_operators": operator_labels,
                   "from_simulator": labelled - operator_labels},
        "runtime": "чистый Python, без внешних ML-зависимостей",
    }


@router.post("/retrain", summary="Переобучить модели на текущих данных")
def retrain(request: Request, rebuild_baseline: bool = True, db: Session = Depends(get_db),
            user: User = Depends(require_roles("admin"))):
    report = train_models(db, rebuild_baseline=rebuild_baseline)
    attribution.reload_model()
    forecast.reload_model()
    write_audit(db, user.email, user.role, "ml.retrain", "models",
                request.client.host if request.client else "", report)
    return report
