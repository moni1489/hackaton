"""Слой 3: вероятность выхода за SLA в ближайшие 6 часов.

Смысл — успеть подать заявку провайдеру ДО урока, а не писать претензию после.
Модель бинарная, обучена на собственной истории организации: метка берётся
прямо из замеров (был ли выход за SLA в следующем окне), синтетика не нужна.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ...models import Measurement, School
from . import baseline
from .features import FORECAST_FEATURES, Topology, forecast_features
from .attribution import _quoted
from .linmodel import SoftmaxRegression

HORIZON_H = 6
_model: SoftmaxRegression | None = None
_loaded = False


def model() -> SoftmaxRegression | None:
    global _model, _loaded
    if not _loaded:
        _model = SoftmaxRegression.load("forecast")
        _loaded = True
    return _model


def reload_model() -> None:
    global _loaded
    _loaded = False


def _band(probability: float) -> str:
    if probability < 0.2:
        return "низкая"
    if probability < 0.45:
        return "умеренная"
    if probability < 0.7:
        return "высокая"
    return "критическая"


def predict(db: Session, school_id: int, at: datetime | None = None,
            topo: Topology | None = None, snap: dict | None = None) -> dict:
    at = at or datetime.utcnow()
    topo = topo or Topology(db)
    school = topo.schools.get(school_id)
    if not school:
        return {"error": "школа не найдена"}
    if snap is None:
        snap = baseline.snapshot(db, at)

    rows = (db.query(Measurement)
            .filter(Measurement.school_id == school_id,
                    Measurement.timestamp >= at - timedelta(hours=26))
            .order_by(Measurement.timestamp.asc()).all())
    vector = forecast_features(school_id, at, rows, snap, topo)
    net = model()
    if vector is None or net is None:
        return {"school_id": school_id, "school_name": school.name,
                "horizon_hours": HORIZON_H, "probability": None,
                "band": "нет данных", "source": "нет модели или истории",
                "model_version": net.version if net else None}

    # Взвешивание классов при обучении смещает калибровку, поэтому крайние
    # значения подрезаются: модель не вправе заявлять полную определённость.
    probability = min(0.99, max(0.01, net.predict_proba(vector)["breach"]))
    return {
        "school_id": school_id,
        "school_name": school.name,
        "provider": school.provider,
        "district": school.region,
        "at": at.isoformat(),
        "horizon_hours": HORIZON_H,
        "probability": round(probability, 3),
        "band": _band(probability),
        "drivers": net.top_drivers(vector, "breach"),
        "features": dict(zip(FORECAST_FEATURES, [round(v, 3) for v in vector])),
        "recommendation": _advice(probability, school),
        "source": "model",
        "model_version": net.version,
        "quality": net.metrics.get("per_class", {}).get("breach", {}),
    }


def _advice(probability: float, school: School) -> str:
    if probability >= 0.7:
        return (f"Подать предупредительную заявку провайдеру {_quoted(school.provider)} сейчас: "
                f"выход за SLA "
                f"в ближайшие {HORIZON_H} ч почти неизбежен. Перевести агентов в углублённую "
                f"диагностику для фиксации доказательной базы.")
    if probability >= 0.45:
        return (f"Взять организацию на контроль: вероятность нарушения SLA в течение "
                f"{HORIZON_H} ч повышена. Уточнить у {_quoted(school.provider)} плановые работы.")
    if probability >= 0.2:
        return "Штатное наблюдение, вмешательство не требуется."
    return "Канал устойчив, риск нарушения SLA в ближайшие часы минимален."


def region_forecast(db: Session, school_ids: list[int] | None = None,
                    limit: int = 12, threshold: float = 0.25) -> list[dict]:
    """Очередь «где рванёт в ближайшие часы» — поверх карты области."""
    net = model()
    if not net:
        return []
    now = datetime.utcnow()
    topo = Topology(db)
    snap = baseline.snapshot(db, now, window_min=90, school_ids=school_ids)

    targets = school_ids or list(topo.schools)
    since = now - timedelta(hours=26)
    query = (db.query(Measurement)
             .filter(Measurement.timestamp >= since, Measurement.school_id.in_(targets)))
    per_school: dict[int, list] = {}
    for row in query.all():
        per_school.setdefault(row.school_id, []).append(row)

    out = []
    for school_id, rows in per_school.items():
        rows.sort(key=lambda r: r.timestamp)
        vector = forecast_features(school_id, now, rows, snap, topo)
        if vector is None:
            continue
        probability = min(0.99, max(0.01, net.predict_proba(vector)["breach"]))
        if probability < threshold:
            continue
        school = topo.schools[school_id]
        out.append({"school_id": school_id, "school_name": school.name,
                    "district": school.region, "provider": school.provider,
                    "lat": school.lat, "lng": school.lng, "status": school.status,
                    "probability": round(probability, 3), "band": _band(probability),
                    "horizon_hours": HORIZON_H})
    out.sort(key=lambda r: -r["probability"])
    return out[:limit]
