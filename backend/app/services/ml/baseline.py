"""Слой 1: сезонная базовая линия канала и детектор аномалий.

Порог SLA (60% договора) не видит деградацию внутри нормы: канал 100 Мбит/с,
просевший с 95 до 62, формально «Норма», хотя объективно сломан. Поэтому
качество сравнивается не с договором, а с собственной нормой ПК в этот час
и в этот тип дня.

Базис робастный — медиана и MAD: авария в истории не сдвигает норму, пока
занимает меньше половины корзины. Корзин 48 (24 часа × будни/выходные):
на окне в две недели это 8–20 замеров на корзину, статистически устойчиво.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from ...models import Device, Measurement, School
from .linmodel import MODELS_DIR

PROFILE_PATH: Path = MODELS_DIR / "baseline.json"
MIN_BUCKET = 4          # меньше — корзина ненадёжна, берём общую медиану ПК
MAD_TO_SIGMA = 1.4826   # нормировка MAD к стандартному отклонению
Z_ANOMALY = -3.0        # деградацией считаем только отклонение вниз

_cache: dict | None = None


def bucket_of(ts: datetime) -> int:
    return ts.hour * 2 + (1 if ts.weekday() >= 5 else 0)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _robust(values: list[float]) -> tuple[float, float]:
    """Медиана и разброс через MAD. Пол разброса — 3% от медианы."""
    med = _median(values)
    mad = _median([abs(v - med) for v in values]) * MAD_TO_SIGMA
    return med, max(mad, 0.03 * med, 0.01)


def build(db: Session) -> dict:
    """Строит профили по всей истории. Тяжёлая операция — выполняется офлайн."""
    contracts = {s.id: (s.contract_speed_down or 100.0) for s in db.query(School).all()}
    per_device: dict[str, dict[int, list[float]]] = {}

    query = db.query(Measurement.device_id, Measurement.school_id, Measurement.timestamp,
                     Measurement.download_speed, Measurement.is_offline)
    for device_id, school_id, ts, download, offline in query.yield_per(5000):
        if not ts or offline or device_id is None:
            continue   # обрывы в норму не входят: базис — это «как работает исправный канал»
        contract = contracts.get(school_id) or 100.0
        per_device.setdefault(device_id, {}).setdefault(bucket_of(ts), []).append(
            (download or 0.0) / contract)

    profiles = {}
    for device_id, buckets in per_device.items():
        everything = [v for values in buckets.values() for v in values]
        if len(everything) < 12:
            continue
        overall_med, overall_mad = _robust(everything)
        entry = {"overall": [round(overall_med, 5), round(overall_mad, 5)],
                 "samples": len(everything), "buckets": {}}
        for key, values in buckets.items():
            if len(values) < MIN_BUCKET:
                continue
            med, mad = _robust(values)
            entry["buckets"][str(key)] = [round(med, 5), round(mad, 5), len(values)]
        profiles[device_id] = entry

    payload = {"built_at": datetime.utcnow().isoformat(), "devices": len(profiles),
               "profiles": profiles}
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(payload, ensure_ascii=False))
    global _cache
    _cache = payload
    return payload


def profiles(db: Session | None = None) -> dict:
    """Ленивая загрузка профилей; при отсутствии файла строит их на месте."""
    global _cache
    if _cache is None:
        if PROFILE_PATH.exists():
            try:
                _cache = json.loads(PROFILE_PATH.read_text())
            except json.JSONDecodeError:
                _cache = None
        if _cache is None:
            if db is None:
                return {"profiles": {}, "devices": 0, "built_at": None}
            _cache = build(db)
    return _cache


def invalidate() -> None:
    global _cache
    _cache = None


def score(device_id: str, ts: datetime, ratio: float, offline: bool,
          store: dict | None = None) -> dict | None:
    """Остаток замера относительно собственной нормы ПК.

    depth — просадка в долях нормы (0.4 = «на 40% ниже обычного для этого часа»),
    z — на сколько робастных сигм ниже нормы.
    """
    entry = (store or profiles())["profiles"].get(device_id)
    if not entry:
        return None
    med, mad = entry["buckets"].get(str(bucket_of(ts)), [*entry["overall"], 0])[:2]
    if med <= 0:
        return None
    if offline:
        return {"z": -99.0, "depth": 1.0, "anomaly": True, "expected": med, "ratio": 0.0}
    z = (ratio - med) / mad
    return {"z": round(z, 2), "depth": round(max(0.0, 1.0 - ratio / med), 3),
            "anomaly": z <= Z_ANOMALY, "expected": round(med, 4), "ratio": round(ratio, 4)}


def snapshot(db: Session, at: datetime, window_min: int = 60,
             school_ids: list[int] | None = None) -> dict[str, dict]:
    """Состояние всех ПК в окне вокруг момента `at` — вход для атрибуции."""
    store = profiles(db)
    contracts = {s.id: (s.contract_speed_down or 100.0) for s in db.query(School).all()}
    half = timedelta(minutes=window_min)
    query = db.query(Measurement).filter(Measurement.timestamp >= at - half,
                                         Measurement.timestamp <= at + half)
    if school_ids:
        query = query.filter(Measurement.school_id.in_(school_ids))

    grouped: dict[str, list[Measurement]] = {}
    for row in query.all():
        grouped.setdefault(row.device_id, []).append(row)

    result = {}
    for device_id, rows in grouped.items():
        scores = []
        for row in rows:
            contract = contracts.get(row.school_id) or 100.0
            got = score(device_id, row.timestamp, (row.download_speed or 0.0) / contract,
                        bool(row.is_offline), store)
            if got:
                scores.append(got)
        if not scores:
            continue
        worst = min(scores, key=lambda s: s["z"])
        result[device_id] = {
            "school_id": rows[0].school_id,
            "z": worst["z"], "depth": worst["depth"],
            "anomaly": any(s["anomaly"] for s in scores),
            "offline": any(s["ratio"] == 0.0 for s in scores),
            "expected": worst["expected"], "ratio": worst["ratio"],
            "samples": len(scores),
        }
    return result


def device_timeline(db: Session, device_id: str, hours: int = 24,
                    now: datetime | None = None) -> list[dict]:
    """Ряд «факт против нормы» для графика в карточке ПК."""
    store = profiles(db)
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        return []
    school = db.get(School, device.school_id)
    contract = (school.contract_speed_down if school else 100.0) or 100.0
    now = now or datetime.utcnow()
    rows = (db.query(Measurement).filter(Measurement.device_id == device_id,
                                         Measurement.timestamp >= now - timedelta(hours=hours),
                                         Measurement.timestamp <= now)
            .order_by(Measurement.timestamp.asc()).all())
    out = []
    for row in rows:
        got = score(device_id, row.timestamp, (row.download_speed or 0.0) / contract,
                    bool(row.is_offline), store)
        if not got:
            continue
        out.append({"timestamp": row.timestamp.isoformat(),
                    "actual": round(got["ratio"] * contract, 1),
                    "expected": round(got["expected"] * contract, 1),
                    "z": got["z"], "anomaly": got["anomaly"]})
    return out


def coverage() -> dict:
    store = profiles()
    return {"devices": store.get("devices", 0), "built_at": store.get("built_at"),
            "buckets_per_device": 48, "z_threshold": Z_ANOMALY,
            "ready": bool(store.get("profiles"))}
