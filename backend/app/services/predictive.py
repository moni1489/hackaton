"""Killer feature #2 — аналитика SLA по истории замеров.

Это статистический анализ накопленных измерений (без обучаемых моделей):
агрегация по срезам времени, сравнение с базовой линией и пороги SLA.
Находит воспроизводимые паттерны деградации:
  • по дням недели   (напр. «каждый понедельник — просадка»);
  • по часам суток   (напр. «пик нагрузки 10:00–12:00»);
  • тренд последних суток относительно базовой линии.
На основе этого считается SLA-соответствие и прогноз риска — доказательная
база для досудебной претензии провайдеру.
"""
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean, pstdev

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Measurement, School
from .status import is_sla_violation

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


def _rows(db: Session, school_id: int, device_id: str | None, days: int) -> list[Measurement]:
    since = datetime.utcnow() - timedelta(days=days)
    query = db.query(Measurement).filter(Measurement.school_id == school_id)
    if device_id:
        query = query.filter(Measurement.device_id == device_id)
    rows = query.filter(Measurement.timestamp >= since).order_by(Measurement.timestamp.asc()).all()
    if not rows:  # демо-БД может быть короче окна — берём что есть
        query = db.query(Measurement).filter(Measurement.school_id == school_id)
        if device_id:
            query = query.filter(Measurement.device_id == device_id)
        rows = query.order_by(Measurement.timestamp.asc()).limit(2000).all()
    return rows


def analyze(db: Session, school_id: int, device_id: str | None = None, days: int = 30) -> dict:
    school = db.get(School, school_id)
    rows = _rows(db, school_id, device_id, days)
    contract = (school.contract_speed_down if school else 100.0) or 100.0

    if not rows:
        return {"school_id": school_id, "device_id": device_id, "samples": 0,
                "sla_compliance_pct": 100.0, "violations": 0, "patterns": [],
                "risk_score": 0, "risk_level": "нет данных", "forecast": None,
                "worst_weekday": None, "worst_hour": None, "contract_speed": contract}

    violations = [r for r in rows
                  if is_sla_violation(r.download_speed or 0, r.ping or 0,
                                      r.packet_loss or 0, contract, bool(r.is_offline))]
    compliance = round(100.0 * (1 - len(violations) / len(rows)), 1)

    by_weekday: dict[int, list[float]] = defaultdict(list)
    by_hour: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        if not r.timestamp:
            continue
        by_weekday[r.timestamp.weekday()].append(r.download_speed or 0)
        by_hour[r.timestamp.hour].append(r.download_speed or 0)

    overall = mean([r.download_speed or 0 for r in rows])
    spread = pstdev([r.download_speed or 0 for r in rows]) if len(rows) > 1 else 0.0

    patterns = []
    for day, values in sorted(by_weekday.items()):
        if len(values) < 3:
            continue
        avg = mean(values)
        drop = round(100.0 * (overall - avg) / overall, 1) if overall else 0.0
        if drop >= 15:
            patterns.append({
                "type": "weekday", "key": WEEKDAYS[day], "samples": len(values),
                "avg_speed": round(avg, 1), "drop_pct": drop,
                "text": f"{WEEKDAYS[day]}: средняя скорость ниже базовой на {drop}% "
                        f"({round(avg, 1)} против {round(overall, 1)} Мбит/с)",
            })
    for hour, values in sorted(by_hour.items()):
        if len(values) < 3:
            continue
        avg = mean(values)
        drop = round(100.0 * (overall - avg) / overall, 1) if overall else 0.0
        if drop >= 20:
            patterns.append({
                "type": "hour", "key": f"{hour:02d}:00", "samples": len(values),
                "avg_speed": round(avg, 1), "drop_pct": drop,
                "text": f"Ежедневная просадка в {hour:02d}:00 — на {drop}% ниже базовой линии",
            })
    patterns.sort(key=lambda p: p["drop_pct"], reverse=True)

    recent = rows[-max(5, len(rows) // 10):]
    recent_avg = mean([r.download_speed or 0 for r in recent])
    trend = round(100.0 * (recent_avg - overall) / overall, 1) if overall else 0.0

    risk = min(100, int((100 - compliance) * 0.7
                        + len(patterns) * 6
                        + max(0, -trend) * 0.8
                        + (spread / contract * 100 * 0.2)))
    level = "низкий" if risk < 25 else "средний" if risk < 55 else "высокий" if risk < 80 else "критический"

    worst_wd = min(by_weekday.items(), key=lambda kv: mean(kv[1]), default=(None, []))
    worst_hr = min(by_hour.items(), key=lambda kv: mean(kv[1]), default=(None, []))

    return {
        "school_id": school_id,
        "school_name": school.name if school else None,
        "provider": school.provider if school else None,
        "device_id": device_id,
        "window_days": days,
        "samples": len(rows),
        "contract_speed": contract,
        "avg_speed": round(overall, 1),
        "recent_avg_speed": round(recent_avg, 1),
        "stability": round(max(0.0, 100 - spread / contract * 100), 1),
        "sla_compliance_pct": compliance,
        "sla_threshold_pct": int(settings.SLA_SPEED_RATIO * 100),
        "violations": len(violations),
        "trend_pct": trend,
        "patterns": patterns[:5],
        "risk_score": risk,
        "risk_level": level,
        "worst_weekday": WEEKDAYS[worst_wd[0]] if worst_wd[0] is not None else None,
        "worst_hour": f"{worst_hr[0]:02d}:00" if worst_hr[0] is not None else None,
        "forecast": _forecast(compliance, trend, patterns),
    }


def _forecast(compliance: float, trend: float, patterns: list) -> str:
    if compliance >= 95 and trend >= -5:
        return "Прогноз стабильный: деградации в ближайшие 7 дней не ожидается."
    if patterns and patterns[0]["type"] == "weekday":
        return (f"Прогноз: повторение просадки в {patterns[0]['key'].lower()} "
                f"с вероятностью {min(95, 55 + int(patterns[0]['drop_pct']))}%. "
                "Рекомендуется предупредительная заявка провайдеру.")
    if trend < -10:
        return (f"Прогноз негативный: скорость снизилась на {abs(trend)}% относительно базовой "
                "линии. Риск выхода за SLA в течение 48 часов.")
    return "Прогноз: возможны кратковременные отклонения, требуется наблюдение."


def region_overview(db: Session, limit: int = 8) -> list[dict]:
    """Топ школ с наибольшим риском нарушения SLA — очередь работы оператора."""
    result = []
    for school in db.query(School).all():
        if school.status in ("Критично", "Нет соединения", "Нестабильно"):
            result.append(analyze(db, school.id, days=30))
    result.sort(key=lambda r: r["risk_score"], reverse=True)
    return result[:limit]
