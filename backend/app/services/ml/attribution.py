"""Слой 2: вердикт «кто виноват» с доказательной базой.

Выход этого модуля — единственное, что провайдер не может оспорить ссылкой
на «проблему внутри школы»: граница просадки измерена по независимым агентам
в разных организациях и districts, а не по одной точке.
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from ...models import Device, Incident, School
from . import baseline
from .features import (ATTRIBUTION_FEATURES, CAUSE_LABELS, CAUSE_OWNER, Topology,
                       attribution_features)
from .linmodel import SoftmaxRegression

_model: SoftmaxRegression | None = None
_loaded = False


def model() -> SoftmaxRegression | None:
    global _model, _loaded
    if not _loaded:
        _model = SoftmaxRegression.load("attribution")
        _loaded = True
    return _model


def reload_model() -> None:
    global _loaded
    _loaded = False
    baseline.invalidate()


def _rule_based(vector: list[float]) -> tuple[str, float]:
    """Запасной путь без модели — те же границы, но заданные вручную.

    Нужен, чтобы система оставалась работоспособной до первого обучения
    и на площадке, где обученные веса не выложены.
    """
    f = dict(zip(ATTRIBUTION_FEATURES, vector))
    if f["dev_frac"] == 0:
        return "none", 0.5
    if f["peer_other_prov_dist"] >= 0.4 and f["peer_prov_dist"] >= 0.4:
        return "regional", 0.6
    if f["peer_prov_dist"] >= 0.4 and f["peer_other_prov_dist"] < 0.25:
        return "provider_node", 0.6
    if f["dev_all"] or (f["gw_anom"] and f["dev_frac"] >= 0.5):
        return "school_lan", 0.6
    if f["dev_single"] or f["dev_frac"] < 0.5:
        return "device", 0.6
    return "none", 0.4


def _quoted(name: str | None) -> str:
    """Названия операторов уже содержат кавычки — не вкладываем их повторно."""
    name = name or "—"
    return name if "«" in name else f"«{name}»"


def _narrative(cause: str, school: School, ctx: dict) -> str:
    """Формулировка для оператора и для приложения к претензии."""
    total    = ctx.get("devices_total", 1)
    hit      = ctx.get("devices_affected", 0)
    peers    = ctx.get("peers_same_provider_district", 0)
    peers_hit = ctx.get("peers_same_provider_district_affected", 0)
    depth    = ctx.get("avg_depth_pct", 0)

    if cause == "provider_node":
        return (f"Просадка на {depth}% ниже нормы зафиксирована одновременно на {hit} из {total} "
                f"ПК организации и ещё на {peers_hit} из {peers} школ провайдера "
                f"{_quoted(school.provider)} в районе {_quoted(school.region)}. Школы других "
                f"провайдеров в том же районе отклонений не показывают "
                f"({ctx['peers_other_providers_district_affected_pct']}%). "
                f"Граница отказа совпадает с зоной обслуживания провайдера — "
                f"причина вне периметра организации образования.")
    if cause == "regional":
        return (f"Отклонения охватывают школы всех провайдеров района {_quoted(school.region)} "
                f"({ctx['peers_other_providers_district_affected_pct']}% организаций других "
                f"операторов). Признак магистральной аварии или перебоя энергоснабжения узла "
                f"связи, а не отказа конкретного оператора.")
    if cause == "school_lan":
        return (f"Просадка на {depth}% охватывает {hit} из {total} ПК организации "
                f"{'включая шлюз' if ctx['gateway_affected'] else 'при исправном шлюзе'}, "
                f"тогда как {peers - peers_hit} из {peers} школ провайдера {_quoted(school.provider)} "
                f"в районе работают штатно. Граница отказа совпадает с границей школы — "
                f"проверять шлюз, коммутатор и внутреннюю кабельную сеть.")
    if cause == "device":
        worst = ctx["affected"][0] if ctx["affected"] else {}
        link = worst.get("link_mode") or "—"
        signal = (f", уровень сигнала {worst['wifi_signal_dbm']} дБм"
                  if worst.get("wifi_signal_dbm") else "")
        return (f"Отклонение локализовано на ПК «{worst.get('name', '—')}» "
                f"({worst.get('room', '—')}, {link}{signal}): просадка "
                f"{worst.get('depth_pct', depth)}% при исправных остальных "
                f"{total - hit} ПК организации. Канал провайдера не затронут — "
                f"заявка на линию не требуется.")
    return ("Устойчивых отклонений от сезонной нормы канала не зафиксировано: "
            "текущие значения укладываются в обычный для этого часа и дня профиль.")


def diagnose(db: Session, school_id: int, at: datetime | None = None,
             topo: Topology | None = None, snap: dict | None = None) -> dict:
    """Вердикт по школе на момент `at` (по умолчанию — сейчас)."""
    at = at or datetime.utcnow()
    topo = topo or Topology(db)
    school = topo.schools.get(school_id)
    if not school:
        return {"error": "школа не найдена"}
    if snap is None:
        snap = baseline.snapshot(db, at)

    vector, ctx = attribution_features(school_id, snap, topo)
    from ..external_network import network_context
    external = network_context(db, at)
    ctx["external_network"] = {
        "ioda_regional_event": external["ioda_regional_event"],
        "ioda_matching_events": external["ioda_matching_events"],
        "interpretation": external["interpretation"],
    }
    net = model()
    if net:
        probs = net.predict_proba(vector)
        cause = max(probs, key=probs.get)
        confidence = probs[cause]
        drivers = net.top_drivers(vector, cause)
        source, version = "model", net.version
    else:
        cause, confidence = _rule_based(vector)
        probs = {cause: confidence}
        drivers, source, version = [], "rules", "правила без обучения"

    return {
        "school_id": school_id,
        "school_name": school.name,
        "provider": school.provider,
        "district": school.region,
        "at": at.isoformat(),
        "cause": cause,
        "cause_label": CAUSE_LABELS[cause],
        "responsible": CAUSE_OWNER[cause],
        "confidence": round(confidence, 3),
        "probabilities": {k: round(v, 3) for k, v in sorted(
            probs.items(), key=lambda kv: kv[1], reverse=True)},
        "evidence": ctx,
        "narrative": _narrative(cause, school, ctx),
        "drivers": drivers,
        "features": dict(zip(ATTRIBUTION_FEATURES, [round(v, 3) for v in vector])),
        "source": source,
        "model_version": version,
        "actionable": cause in ("provider_node", "regional"),
    }


def diagnose_incident(db: Session, incident: Incident, persist: bool = True) -> dict:
    """Вердикт по инциденту на момент его возникновения + запись в карточку."""
    at = incident.start_time or datetime.utcnow()
    verdict = diagnose(db, incident.school_id, at=at)
    if persist and "error" not in verdict:
        incident.root_cause = verdict["cause"]
        incident.root_cause_confidence = verdict["confidence"]
        incident.root_cause_evidence = json.dumps(
            {"narrative": verdict["narrative"], "evidence": verdict["evidence"],
             "probabilities": verdict["probabilities"], "drivers": verdict["drivers"]},
            ensure_ascii=False)
        incident.root_cause_model = verdict["model_version"]
        db.commit()
    return verdict


def live_board(db: Session, school_ids: list[int] | None = None, limit: int = 40) -> list[dict]:
    """Все организации с активными отклонениями — рабочая очередь оператора."""
    now = datetime.utcnow()
    topo = Topology(db)
    snap = baseline.snapshot(db, now, window_min=90, school_ids=school_ids)
    hot = {state["school_id"] for state in snap.values() if state["anomaly"]}
    if school_ids:
        hot &= set(school_ids)

    verdicts = []
    for school_id in hot:
        verdict = diagnose(db, school_id, at=now, topo=topo, snap=snap)
        if "error" in verdict or verdict["cause"] == "none":
            continue
        verdicts.append(verdict)

    order = {"regional": 0, "provider_node": 1, "school_lan": 2, "device": 3}
    verdicts.sort(key=lambda v: (order.get(v["cause"], 9),
                                 -v["evidence"]["avg_depth_pct"]))
    return verdicts[:limit]


def cause_summary(db: Session, school_ids: list[int] | None = None) -> dict:
    """Сводка «кто виноват по области» — для KPI-полосы и питча."""
    board = live_board(db, school_ids=school_ids, limit=500)
    counts: dict[str, int] = {}
    for verdict in board:
        counts[verdict["cause"]] = counts.get(verdict["cause"], 0) + 1
    provider_side = counts.get("provider_node", 0) + counts.get("regional", 0)
    total = sum(counts.values())
    return {
        "total_affected": total,
        "by_cause": [{"cause": c, "label": CAUSE_LABELS[c], "responsible": CAUSE_OWNER[c],
                      "count": n} for c, n in sorted(counts.items(), key=lambda kv: -kv[1])],
        "provider_side": provider_side,
        "school_side": total - provider_side,
        "provider_share_pct": round(100 * provider_side / total, 1) if total else 0.0,
    }
