"""Рейтинг организаций и динамика показателей за период (до квартала).

Считается по замерам точек мониторинга основных линий (как и аналитика школы).
Оценка 0–100 = 50% соответствие SLA + 25% доступность + 25% скорость к договору.
Доля нарушений считается в SQL по действующим порогам, а не по сохранённому Measurement.status:
у замеров, принятых до появления статуса, он пуст. Логика зеркалит status.classify —
согласованность держит тест test_rating.
"""
from datetime import datetime, timedelta

from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from ..models import Measurement, School
from .lines import main_monitors
from .retention import MIN_RETENTION_DAYS
from .status import thresholds

MIN_SAMPLES = 10   # меньше замеров за период — школа не ранжируется, а не «худшая»
W_SLA, W_AVAIL, W_SPEED = 0.5, 0.25, 0.25
GRADES = [(90, "Отлично"), (75, "Хорошо"), (60, "Удовлетворительно"), (0, "Проблемная")]


def _bad(th):
    """SQL-условие «замер вне нормы» — то же, что classify() != «Норма»."""
    c, cu = School.contract_speed_down, School.contract_speed_up
    down = func.coalesce(Measurement.download_speed, 0)
    down_floor = case((c.is_(None) | (c <= 0), th.down_min),
                      (c < th.down_min, th.contract_ratio * c),
                      (th.contract_ratio * c > th.down_min, th.contract_ratio * c),
                      else_=th.down_min)
    up_floor = case((cu.is_(None) | (cu <= 0) | (cu >= th.up_min), th.up_min),
                    else_=th.contract_ratio * cu)
    return or_(Measurement.is_offline.is_(True), down <= 0, down < down_floor,
               func.coalesce(Measurement.upload_speed, 0) < up_floor,
               func.coalesce(Measurement.ping, 0) > th.ping_max,
               func.coalesce(Measurement.jitter, 0) > th.jitter_max,
               func.coalesce(Measurement.packet_loss, 0) > th.loss_max)


def aggregate(db: Session, start: datetime, end: datetime, school_ids: list[int] | None,
              daily: bool = False) -> dict:
    """{school_id: показатели} или, при daily, {(school_id, 'YYYY-MM-DD'): показатели}."""
    th = thresholds()
    up = lambda cond, col: func.avg(case((cond, col)))   # среднее только по рабочим замерам
    working = Measurement.is_offline.isnot(True)
    day = func.date(Measurement.timestamp)
    keys = [Measurement.school_id] + ([day] if daily else [])
    query = (db.query(*keys, func.count(), func.sum(case((Measurement.is_offline.is_(True), 1), else_=0)),
                      func.sum(case((_bad(th), 1), else_=0)),
                      up(working, Measurement.download_speed), up(working, Measurement.upload_speed),
                      up(working, Measurement.ping), School.contract_speed_down)
             .join(School, School.id == Measurement.school_id)
             .filter(Measurement.timestamp >= start, Measurement.timestamp < end,
                     Measurement.device_id.in_(main_monitors()))
             .group_by(*keys, School.contract_speed_down))
    if school_ids is not None:
        query = query.filter(Measurement.school_id.in_(school_ids or [-1]))

    out = {}
    for *key, n, offline, bad, down, upl, ping, contract in query.all():
        sla = 100 * (1 - bad / n)
        avail = 100 * (1 - offline / n)
        speed = min(100, 100 * (down or 0) / (contract or th.down_min))
        out[tuple(key) if daily else key[0]] = {
            "samples": n, "score": round(W_SLA * sla + W_AVAIL * avail + W_SPEED * speed, 1),
            "sla_pct": round(sla, 1), "availability_pct": round(avail, 1),
            "speed_pct": round(speed, 1),
            "avg_download": round(down, 1) if down is not None else None,
            "avg_upload": round(upl, 1) if upl is not None else None,
            "avg_ping": round(ping, 1) if ping is not None else None}
    return out


def _rank(stats: dict) -> dict:
    """{school_id: место} среди школ с достаточным числом замеров."""
    rated = sorted((i for i, s in stats.items() if s["samples"] >= MIN_SAMPLES),
                   key=lambda i: -stats[i]["score"])
    return {i: n for n, i in enumerate(rated, 1)}


def grade(score: float) -> str:
    return next(name for floor, name in GRADES if score >= floor)


def rating(db: Session, now: datetime, days: int, school_ids: list[int] | None,
           schools: list[School]) -> dict:
    """Рейтинг за [now−days, now] и сравнение с предыдущим периодом такой же длины."""
    days = max(1, min(days, MIN_RETENTION_DAYS))
    start = now - timedelta(days=days)
    cur = aggregate(db, start, now, school_ids)
    prev = aggregate(db, start - timedelta(days=days), start, school_ids)
    places, prev_places = _rank(cur), _rank(prev)

    items = []
    for s in schools:
        stat = cur.get(s.id)
        item = {"school_id": s.id, "name": s.name, "school_id_code": s.school_id_code,
                "region": s.region, "provider": s.provider, "contract_speed_down": s.contract_speed_down,
                "rank": places.get(s.id), "samples": stat["samples"] if stat else 0,
                "score": None, "grade": None, "delta": None, "rank_change": None}
        if s.id in places:
            p = prev.get(s.id)
            item.update(stat, grade=grade(stat["score"]))
            if s.id in prev_places:   # рост места — положительное число
                item["delta"] = round(stat["score"] - p["score"], 1)
                item["rank_change"] = prev_places[s.id] - places[s.id]
        items.append(item)
    items.sort(key=lambda i: (i["rank"] is None, i["rank"] or 0, i["name"] or ""))
    rated = [i for i in items if i["rank"]]
    return {"period": {"days": days, "start": start.isoformat(), "end": now.isoformat()},
            "rated": len(rated), "unrated": len(items) - len(rated),
            "avg_score": round(sum(i["score"] for i in rated) / len(rated), 1) if rated else None,
            "items": items}


def history(db: Session, now: datetime, days: int, school_ids: list[int] | None) -> list[dict]:
    """Дневная динамика: по одной школе (ids=[id]) или среднее по области видимости."""
    days = max(1, min(days, MIN_RETENTION_DAYS))
    per_day: dict[str, list[dict]] = {}
    for (_, d), stat in aggregate(db, now - timedelta(days=days), now, school_ids, daily=True).items():
        per_day.setdefault(str(d), []).append(stat)
    return [{"day": d, "schools": len(rows), "samples": sum(r["samples"] for r in rows),
             **{k: round(sum(r[k] for r in rows if r[k] is not None) / max(1, sum(r[k] is not None for r in rows)), 1)
                for k in ("score", "sla_pct", "availability_pct", "avg_download", "avg_ping")}}
            for d, rows in sorted(per_day.items())]
