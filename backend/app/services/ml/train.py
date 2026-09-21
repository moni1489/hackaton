"""Обучение ML-моделей на истории замеров и журнале аварий.

Разметка берётся из таблицы fault_events: в демо её наполняет симулятор
отказов, в эксплуатации — подтверждённые вердикты операторов
(POST /api/ml/verdict). Так модель переучивается на реальных инцидентах,
а симулятор нужен только для холодного старта.

Запуск отдельно:  python -m app.services.ml.train
"""
from __future__ import annotations

import json
import random
from collections import namedtuple
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ...models import FaultEvent, Measurement, School
from ..lines import main_monitors
from . import baseline
from .features import (ATTRIBUTION_FEATURES, CAUSES, FORECAST_FEATURES, Topology,
                       attribution_features, breach_in_window, forecast_features)
from .linmodel import MODELS_DIR, SoftmaxRegression, evaluate

Row = namedtuple("Row", "device_id school_id timestamp download_speed upload_speed ping jitter "
                        "packet_loss is_offline")

SLOT_MINUTES = 30
ATTR_EVERY_SLOTS = 3     # снимок для атрибуции раз в 1.5 часа
FCST_EVERY_SLOTS = 8     # снимок для прогноза раз в 4 часа
FCST_SCHOOLS_PER_SLOT = 40
HOLDOUT = 0.25
HORIZON_H = 6


def _load_rows(db: Session) -> list[Row]:
    query = db.query(Measurement.device_id, Measurement.school_id, Measurement.timestamp,
                     Measurement.download_speed, Measurement.upload_speed, Measurement.ping,
                     Measurement.jitter, Measurement.packet_loss, Measurement.is_offline)
    return [Row(*r) for r in query.yield_per(10000) if r[2] is not None]


def _slot(ts: datetime) -> datetime:
    return ts.replace(minute=(ts.minute // SLOT_MINUTES) * SLOT_MINUTES,
                      second=0, microsecond=0)


def _score_all(rows: list[Row], contracts: dict[int, float]) -> dict[datetime, dict[str, dict]]:
    """Остатки всех замеров относительно сезонной нормы, разложенные по слотам."""
    store = baseline.profiles()
    by_slot: dict[datetime, dict[str, dict]] = {}
    for row in rows:
        contract = contracts.get(row.school_id) or 100.0
        got = baseline.score(row.device_id, row.timestamp,
                             (row.download_speed or 0.0) / contract,
                             bool(row.is_offline), store)
        if not got:
            continue
        by_slot.setdefault(_slot(row.timestamp), {})[row.device_id] = {
            "school_id": row.school_id, "z": got["z"], "depth": got["depth"],
            "anomaly": got["anomaly"], "offline": got["ratio"] == 0.0,
            "expected": got["expected"], "ratio": got["ratio"], "samples": 1}
    return by_slot


def _label_index(db: Session) -> list[tuple]:
    return [(f.cause, f.district, f.provider, f.school_id, f.device_id,
             f.start_time, f.end_time, f.severity) for f in db.query(FaultEvent).all()]


def _dominant_cause(events: list[tuple], school, device_ids: set[str], ts: datetime) -> str | None:
    """Истинная причина = самая тяжёлая из аварий, накрывающих школу в момент ts."""
    hits = []
    for cause, district, provider, school_id, device_id, start, end, severity in events:
        if not (start <= ts <= end):
            continue
        if cause == "regional" and district == school.region:
            hits.append((severity, cause))
        elif cause == "provider_node" and provider == school.provider and district == school.region:
            hits.append((severity, cause))
        elif cause == "school_lan" and school_id == school.id:
            hits.append((severity, cause))
        elif cause == "device" and device_id in device_ids:
            hits.append((severity, cause))
    return min(hits)[1] if hits else None


def build_attribution_set(db: Session, by_slot, topo: Topology, events) -> tuple[list, list]:
    """Выборка атрибуции: по снимку на каждые 1.5 часа истории."""
    X, y = [], []
    device_ids = {sid: {d.device_id for d in devs} for sid, devs in topo.devices.items()}
    slots = sorted(by_slot)
    rng = random.Random(11)

    for index in range(0, len(slots), ATTR_EVERY_SLOTS):
        ts = slots[index]
        snap = by_slot[ts]
        hot_schools = {state["school_id"] for state in snap.values() if state["anomaly"]}

        # Здоровые школы — контрольная группа класса «аномалия не подтверждена».
        calm = [sid for sid in topo.schools if sid not in hot_schools]
        sample = list(hot_schools) + rng.sample(calm, min(4, len(calm)))

        for school_id in sample:
            school = topo.schools.get(school_id)
            if not school:
                continue
            cause = _dominant_cause(events, school, device_ids.get(school_id, set()), ts)
            anomalous = school_id in hot_schools
            if cause and not anomalous:
                continue          # авария слабее порога детектора — метка неинформативна
            label = cause if (cause and anomalous) else "none"
            vector, _ = attribution_features(school_id, snap, topo)
            X.append(vector)
            y.append(label)
    return X, y


def build_forecast_set(db: Session, rows: list[Row], by_slot, topo: Topology) -> tuple[list, list]:
    """Выборка прогноза: выход за SLA в ближайшие 6 часов."""
    per_school: dict[int, list[Row]] = {}
    for row in rows:
        per_school.setdefault(row.school_id, []).append(row)
    for items in per_school.values():
        items.sort(key=lambda r: r.timestamp)

    slots = sorted(by_slot)
    if len(slots) < 60:
        return [], []
    rng = random.Random(23)
    X, y = [], []
    # Отступы: слева нужны сутки истории, справа — горизонт прогноза.
    left = int(24 * 60 / SLOT_MINUTES)
    right = int(HORIZON_H * 60 / SLOT_MINUTES)

    for index in range(left, len(slots) - right, FCST_EVERY_SLOTS):
        ts = slots[index]
        snap = by_slot[ts]
        candidates = rng.sample(list(topo.schools), min(FCST_SCHOOLS_PER_SLOT, len(topo.schools)))
        for school_id in candidates:
            school = topo.schools[school_id]
            history = per_school.get(school_id)
            if not history:
                continue
            vector = forecast_features(school_id, ts, history, snap, topo)
            if vector is None:
                continue
            breach = breach_in_window(history, ts, ts + timedelta(hours=HORIZON_H),
                                      school.contract_speed_down or 100.0,
                                      school.contract_speed_up)
            X.append(vector)
            y.append("breach" if breach else "stable")
    return X, y


def _split(X, y, seed=5):
    order = list(range(len(X)))
    random.Random(seed).shuffle(order)
    cut = int(len(order) * (1 - HOLDOUT))
    train, test = order[:cut], order[cut:]
    return ([X[i] for i in train], [y[i] for i in train],
            [X[i] for i in test], [y[i] for i in test])


def train_models(db: Session, *, rebuild_baseline: bool = True) -> dict:
    version = datetime.utcnow().strftime("%Y%m%d-%H%M")
    report: dict = {"version": version}

    if rebuild_baseline or not baseline.PROFILE_PATH.exists():
        profiles = baseline.build(db)
        print(f"  базис: профилей по ПК — {profiles['devices']}")
    baseline.invalidate()

    rows = _load_rows(db)
    if len(rows) < 2000:
        print("  недостаточно истории для обучения — модели не переобучались")
        return {"error": "мало данных", "samples": len(rows)}

    contracts = {s.id: (s.contract_speed_down or 100.0) for s in db.query(School).all()}
    by_slot = _score_all(rows, contracts)
    topo = Topology(db)
    events = _label_index(db)

    # --- модель атрибуции ------------------------------------------------
    X, y = build_attribution_set(db, by_slot, topo, events)
    present = [c for c in CAUSES if c in set(y)]
    if len(present) < 2:
        print("  в истории нет размеченных аварий — атрибуция не обучена")
    else:
        Xtr, ytr, Xte, yte = _split(X, y)
        model = SoftmaxRegression(present, ATTRIBUTION_FEATURES)
        model.fit(Xtr, ytr, epochs=45, lr=0.6)
        model.version = version
        model.metrics = evaluate(model, Xte, yte)
        model.metrics["train_samples"] = len(ytr)
        model.metrics["label_source"] = "симулятор отказов + вердикты операторов"
        model.save("attribution")
        report["attribution"] = {k: model.metrics[k] for k in ("samples", "accuracy", "macro_f1")}
        print(f"  атрибуция: {len(ytr)} обучающих, accuracy "
              f"{model.metrics['accuracy']}, macro-F1 {model.metrics['macro_f1']}")

    # --- модель прогноза --------------------------------------------------
    # Прогноз — о канале школы, поэтому по замерам точек мониторинга основных линий.
    monitors = {d for (d,) in db.execute(main_monitors())}
    Xf, yf = build_forecast_set(db, [r for r in rows if r.device_id in monitors], by_slot, topo)
    if len(set(yf)) < 2:
        print("  прогноз: один класс в выборке — модель не обучена")
    else:
        Xtr, ytr, Xte, yte = _split(Xf, yf, seed=9)
        forecast = SoftmaxRegression(["stable", "breach"], FORECAST_FEATURES)
        forecast.fit(Xtr, ytr, epochs=45, lr=0.6)
        forecast.version = version
        forecast.metrics = evaluate(forecast, Xte, yte)
        forecast.metrics["train_samples"] = len(ytr)
        forecast.metrics["horizon_hours"] = HORIZON_H
        forecast.save("forecast")
        report["forecast"] = {k: forecast.metrics[k] for k in ("samples", "accuracy", "macro_f1")}
        print(f"  прогноз {HORIZON_H} ч: {len(ytr)} обучающих, accuracy "
              f"{forecast.metrics['accuracy']}, macro-F1 {forecast.metrics['macro_f1']}")

    (MODELS_DIR / "training_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1))
    return report


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(MODELS_DIR.parent))
    from app.database import SessionLocal
    session = SessionLocal()
    try:
        train_models(session)
    finally:
        session.close()
