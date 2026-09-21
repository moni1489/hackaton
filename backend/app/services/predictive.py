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

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Measurement, School
from .lines import main_monitors
from .status import (BAD_STATUSES, effective_status, is_sla_violation, speed_floor,
                     thresholds)

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


def _rows(db: Session, school_id: int, device_id: str | None, days: int,
          now: datetime) -> list[Measurement]:
    """Замеры окна [now − days, now]. Без отката на «что есть»: устаревшая история
    не выдаётся за текущую. Линию школы характеризуют только точки мониторинга
    основной линии — рабочие места и Wi-Fi к качеству канала провайдера не относятся."""
    query = db.query(Measurement).filter(
        Measurement.school_id == school_id,
        Measurement.timestamp >= now - timedelta(days=days), Measurement.timestamp <= now)
    if device_id:
        query = query.filter(Measurement.device_id == device_id)
    else:
        query = query.filter(Measurement.device_id.in_(main_monitors()))
    return query.order_by(Measurement.timestamp.asc()).all()


def _no_data(db: Session, school: School | None, school_id: int, device_id: str | None,
             days: int, contract: float) -> dict:
    """Пусто: замеров в окне нет. Явное состояние, а не «100% соответствия»."""
    query = db.query(func.max(Measurement.timestamp)).filter(Measurement.school_id == school_id)
    if device_id:
        query = query.filter(Measurement.device_id == device_id)
    last = query.scalar()
    return {"school_id": school_id, "school_name": school.name if school else None,
            "provider": school.provider if school else None, "device_id": device_id,
            "window_days": days, "samples": 0, "data_state": "no_data",
            "last_measurement": last.isoformat() if last else None,
            "contract_speed": contract, "avg_speed": None, "recent_avg_speed": None,
            "stability": None, "sla_compliance_pct": None, "availability_pct": None,
            "violations": 0, "trend_pct": None, "patterns": [], "risk_score": 0,
            "risk_level": "нет данных", "forecast": None,
            "worst_weekday": None, "worst_hour": None}


def analyze(db: Session, school_id: int, device_id: str | None = None, days: int = 30,
            now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    th = thresholds()
    school = db.get(School, school_id)
    rows = _rows(db, school_id, device_id, days, now)
    contract = (school.contract_speed_down if school else 100.0) or 100.0
    contract_up = school.contract_speed_up if school else None

    if not rows:
        return _no_data(db, school, school_id, device_id, days, contract)

    violations = [r for r in rows
                  if is_sla_violation(r.download_speed or 0, r.ping or 0, r.packet_loss or 0,
                                      contract, bool(r.is_offline), upload=r.upload_speed or 0,
                                      jitter=r.jitter or 0, contract_up=contract_up)]
    compliance = round(100.0 * (1 - len(violations) / len(rows)), 1)
    # ТЗ п.11, п.14: доступность и устойчивое несоответствие договору — отдельно от общего SLA.
    offline = sum(1 for r in rows if r.is_offline)
    availability = round(100.0 * (1 - offline / len(rows)), 1)
    below_contract = sum(1 for r in rows if not r.is_offline
                         and (r.download_speed or 0) < th.contract_ratio * contract)

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
        "data_state": "ok",
        "last_measurement": rows[-1].timestamp.isoformat(),
        "sla_compliance_pct": compliance,
        "sla_threshold_pct": int(th.contract_ratio * 100),
        "speed_floor": round(speed_floor(th.down_min, contract, th.contract_ratio), 1),
        "availability_pct": availability,
        "availability_ok": availability >= th.availability_min,
        "availability_norm_pct": th.availability_min,
        "below_contract": below_contract,
        "below_contract_pct": round(100.0 * below_contract / len(rows), 1),
        "threshold_id": th.id,
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


def region_overview(db: Session, limit: int = 8, school_ids: list[int] | None = None,
                    now: datetime | None = None) -> list[dict]:
    """Топ школ с наибольшим риском нарушения SLA — очередь работы оператора.

    Берутся школы, у которых СВЕЖИЙ статус плохой: школа без свежих данных в очередь
    риска не попадает (о ней нечего сказать), она видна на карте как «нет свежих данных».
    """
    now = now or datetime.utcnow()
    query = db.query(School)
    if school_ids is not None:
        query = query.filter(School.id.in_(school_ids or [-1]))
    result = [analyze(db, school.id, days=30, now=now) for school in query.all()
              if effective_status(school.status, school.last_measurement, now) in BAD_STATUSES]
    result = [r for r in result if r["samples"]]
    result.sort(key=lambda r: r["risk_score"], reverse=True)
    return result[:limit]
