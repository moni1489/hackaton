"""Слой 2: гипотеза «кто виноват» с доказательной базой и оценкой достаточности данных.

Граница просадки (один ПК / вся школа / школы провайдера / все провайдеры) сужает круг
причин, но конкретную причину не доказывает: одинаковое ухудшение во всей школе даёт и
неисправная ЛВС, и роутер, и индивидуальная линия провайдера. Поэтому вывод всегда
«предполагаемый источник», а при нехватке сопоставимых школ — «источник не определён».
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from ...config import settings
from ...models import Incident, School
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


MIN_PEERS = 2   # меньше сопоставимых школ провайдера в районе — «узел» от «линии школы» не отличить
# Потолок оценки модели по достаточности данных: softmax-вероятность не калибрована,
# «99%» на обучающей синтетике не должен выглядеть как доказанный факт.
CONFIDENCE_CAP = {2: 0.9, 1: 0.6, 0: 0.3}
QUALITY_LABEL = {2: "достаточно", 1: "ограниченно", 0: "недостаточно"}


def assess(cause: str, ctx: dict) -> dict:
    """Достаточность данных для ЭТОГО вывода: чего не хватает, чтобы отделить причину от соседних."""
    devices = ctx.get("devices_total", 0)
    peers = ctx.get("peers_same_provider_district", 0)
    others = ctx.get("peers_other_providers_district", 0)
    level, reasons = 2, []

    def need(ok: bool, reason: str, hard: bool = False) -> None:
        nonlocal level
        if not ok:
            level = min(level, 0 if hard else 1)
            reasons.append(reason)

    if cause == "device":
        need(devices >= 2, "данные только с одного устройства: «один ПК» и «вся школа» не различить",
             hard=True)
    elif cause == "school_lan":
        need(peers >= 1, "нет сопоставимых школ этого провайдера в районе: неисправность сети школы "
                         "нельзя отделить от проблемы индивидуальной линии или узла провайдера",
             hard=True)
        need(peers == 0 or peers >= MIN_PEERS, f"сопоставимых школ провайдера в районе мало ({peers})")
        need(devices >= 2, "данные только с одного устройства")
    elif cause == "provider_node":
        need(peers >= 1, "нет сопоставимых школ этого провайдера в районе", hard=True)
        need(peers == 0 or peers >= MIN_PEERS, f"сопоставимых школ провайдера в районе мало ({peers})")
        need(others >= 1, "в районе нет сопоставимых школ других провайдеров: "
                          "магистральную причину исключить нельзя")
    elif cause == "regional":
        need(peers >= 1 and others >= 1, "для магистральной аварии нужны сопоставимые школы "
                                          "и своего, и других провайдеров", hard=True)
    registered = ctx.get("devices_registered", devices)
    need(devices * 2 >= registered, f"замеры получены менее чем с половины ПК ({devices} из {registered})")
    return {"level": level, "label": QUALITY_LABEL[level], "reasons": reasons,
            "comparable_peers": peers, "comparable_other_providers": others,
            "devices_with_data": devices}


def _narrative(cause: str, school: School, ctx: dict, quality: dict, hint: str | None = None) -> str:
    """Формулировка для оператора и для приложения к претензии. Везде — предположение."""
    total    = ctx.get("devices_total", 1)
    hit      = ctx.get("devices_affected", 0)
    peers    = ctx.get("peers_same_provider_district", 0)
    peers_hit = ctx.get("peers_same_provider_district_affected", 0)
    others   = ctx.get("peers_other_providers_district", 0)
    depth    = ctx.get("avg_depth_pct", 0)
    lead = f"Предполагаемый источник — {CAUSE_LABELS[cause][0].lower()}{CAUSE_LABELS[cause][1:]}. "

    if cause == "provider_node":
        other_text = (f"У сопоставимых школ других провайдеров района отклонения у "
                      f"{ctx['peers_other_providers_district_affected_pct']}% ({others} школ). "
                      if others else
                      "Сопоставимых школ других провайдеров в районе нет — сравнить не с чем. ")
        return (lead + f"Просадка на {depth}% ниже нормы одновременно на {hit} из {total} ПК "
                f"организации и на {peers_hit} из {peers} сопоставимых школ провайдера "
                f"{_quoted(school.provider)} в районе {_quoted(school.region)}. {other_text}"
                f"Совпадение границы отказа с зоной провайдера указывает на общий узел или линию, "
                f"но не доказывает конкретную причину — нужно подтверждение провайдера.")
    if cause == "regional":
        return (lead + f"Отклонения охватывают школы всех провайдеров района {_quoted(school.region)} "
                f"({ctx['peers_other_providers_district_affected_pct']}% сопоставимых школ других "
                f"операторов). Это признак магистральной аварии или перебоя энергоснабжения узла "
                f"связи, но не доказательство: конкретную причину подтверждает оператор.")
    if cause == "school_lan":
        return (lead + f"Просадка на {depth}% охватывает {hit} из {total} ПК организации "
                f"{'включая шлюз' if ctx['gateway_affected'] else 'при исправном шлюзе'}, тогда как "
                f"{peers - peers_hit} из {peers} сопоставимых школ провайдера "
                f"{_quoted(school.provider)} в районе работают штатно. Так выглядит и неисправная "
                f"ЛВС или роутер школы, и индивидуальная линия провайдера до школы: сравнение с "
                f"соседями эти причины не разделяет. Сначала проверить оборудование школы; если оно "
                f"исправно — запросить у провайдера диагностику линии.")
    if cause == "device":
        worst = ctx["affected"][0] if ctx["affected"] else {}
        link = worst.get("link_mode") or "—"
        signal = (f", уровень сигнала {worst['wifi_signal_dbm']} дБм"
                  if worst.get("wifi_signal_dbm") else "")
        return (lead + f"Отклонение локализовано на ПК «{worst.get('name', '—')}» "
                f"({worst.get('room', '—')}, {link}{signal}): просадка "
                f"{worst.get('depth_pct', depth)}% при исправных остальных {total - hit} ПК "
                f"организации. Проверить сам ПК и его подключение (Wi-Fi, кабель, сетевая карта); "
                f"признаков проблемы линии провайдера по остальным ПК нет.")
    if cause == "chronic":
        return (lead + f"Фактическая скорость — {ctx.get('contract_ratio_pct', 0)}% договорной, "
                f"и так держится постоянно, поэтому отклонения от собственной нормы канала нет: "
                f"норма этой организации сама ниже договора. Это не авария, а устойчивое "
                f"несоответствие услуги договору — повод для претензии к провайдеру "
                f"{_quoted(school.provider)}, а не для поиска сбоя.")
    if cause == "undetermined":
        why = "; ".join(quality["reasons"]) or "данных недостаточно"
        guess = (f" Предварительная гипотеза модели — {CAUSE_LABELS[hint][0].lower()}"
                 f"{CAUSE_LABELS[hint][1:]} — не подтверждена и в претензии использоваться не должна."
                 if hint else "")
        return f"Источник не определён: отклонение есть, но {why}.{guess}"
    return ("Устойчивых отклонений от сезонной нормы канала по замерам за последний час не "
            "зафиксировано: текущие значения укладываются в обычный для этого часа и дня профиль.")


def _no_data(school: School, at: datetime) -> dict:
    """Замеров в окне нет — это не «всё в норме», а отсутствие оснований для вывода."""
    last = school.last_measurement
    when = f"последний замер {last:%d.%m.%Y %H:%M}" if last else "замеров не было"
    return {
        "school_id": school.id, "school_name": school.name, "provider": school.provider,
        "district": school.region, "at": at.isoformat(), "cause": "no_data",
        "cause_label": CAUSE_LABELS["no_data"], "responsible": CAUSE_OWNER["no_data"],
        "confidence": 0.0, "probabilities": {}, "evidence": {"devices_total": 0,
                                                              "devices_affected": 0, "affected": []},
        "narrative": f"Нет свежих данных ({when}). Вывод об источнике не выносится; это не означает, "
                     f"что канал исправен.",
        "data_quality": {"level": 0, "label": QUALITY_LABEL[0],
                         "reasons": ["в окне анализа нет замеров"]},
        "drivers": [], "features": {}, "source": "нет данных", "model_version": None,
        "last_measurement": last.isoformat() if last else None,
        "hypothesis": False, "actionable": False,
    }


def diagnose(db: Session, school_id: int, at: datetime | None = None,
             topo: Topology | None = None, snap: dict | None = None) -> dict:
    """Гипотеза об источнике по школе на момент `at` (по умолчанию — сейчас)."""
    at = at or datetime.utcnow()
    topo = topo or Topology(db)
    school = topo.schools.get(school_id)
    if not school:
        return {"error": "школа не найдена"}
    if snap is None:
        snap = baseline.snapshot(db, at)

    vector, ctx = attribution_features(school_id, snap, topo)
    if not ctx.get("devices_total"):
        return _no_data(school, at)
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

    # Слой достаточности: без сопоставимых школ модель отвечает «школа», потому что
    # отсутствие соседей неотличимо от «соседи здоровы». Такой вывод не выносится.
    quality = assess(cause, ctx)
    # Ровно плохой канал аномалией не выглядит — отклоняться ему не от чего.
    # Но недобор договорной скорости это именно нарушение, и вешать на него
    # «аномалия не подтверждена» значит прятать реальный повод для претензии.
    if cause == "none" and ctx.get("contract_ratio_pct", 100.0) < settings.SLA_SPEED_RATIO * 100:
        cause = "chronic"
    model_confidence, hint = confidence, None
    if cause != "none" and quality["level"] == 0:
        hint, cause = cause, "undetermined"
    confidence = min(confidence, CONFIDENCE_CAP[quality["level"]]) if cause != "undetermined" \
        else min(confidence, CONFIDENCE_CAP[0])

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
        "model_confidence": round(model_confidence, 3),
        "model_hint": hint,
        "data_quality": quality,
        "probabilities": {k: round(v, 3) for k, v in sorted(
            probs.items(), key=lambda kv: kv[1], reverse=True)},
        "evidence": ctx,
        "narrative": _narrative(cause, school, ctx, quality, hint),
        "drivers": drivers,
        "features": dict(zip(ATTRIBUTION_FEATURES, [round(v, 3) for v in vector])),
        "source": source,
        "model_version": version,
        "hypothesis": cause not in ("none",),
        # Основание для претензии — только при достаточных данных и стороне провайдера.
        "actionable": (cause in ("provider_node", "regional") and quality["level"] == 2)
                      or cause == "chronic",
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


def live_board(db: Session, school_ids: list[int] | None = None, limit: int = 40,
               now: datetime | None = None) -> list[dict]:
    """Все организации с активными отклонениями — рабочая очередь оператора.

    Снимок строится по ВСЕЙ области: соседи нужны для сравнения. Ограничение по роли
    (school_ids) применяется только к списку результатов. school_ids=None — без ограничения.
    """
    now = now or datetime.utcnow()
    topo = Topology(db)
    snap = baseline.snapshot(db, now, window_min=90)
    hot = {state["school_id"] for state in snap.values() if state["anomaly"]}
    if school_ids is not None:
        hot &= set(school_ids)

    verdicts = []
    for school_id in hot:
        verdict = diagnose(db, school_id, at=now, topo=topo, snap=snap)
        if "error" in verdict or verdict["cause"] in ("none", "no_data"):
            continue
        verdicts.append(verdict)

    order = {"regional": 0, "provider_node": 1, "school_lan": 2, "device": 3, "undetermined": 4}
    verdicts.sort(key=lambda v: (order.get(v["cause"], 9),
                                 -v["evidence"]["avg_depth_pct"]))
    return verdicts[:limit]


def cause_summary(db: Session, school_ids: list[int] | None = None,
                  now: datetime | None = None) -> dict:
    """Сводка предполагаемых источников по области — для KPI-полосы."""
    board = live_board(db, school_ids=school_ids, limit=500, now=now)
    counts: dict[str, int] = {}
    for verdict in board:
        counts[verdict["cause"]] = counts.get(verdict["cause"], 0) + 1
    provider_side = counts.get("provider_node", 0) + counts.get("regional", 0)
    school_side = counts.get("school_lan", 0) + counts.get("device", 0)
    undetermined = counts.get("undetermined", 0)
    total = provider_side + school_side + undetermined
    return {
        "total_affected": total,
        "by_cause": [{"cause": c, "label": CAUSE_LABELS[c], "responsible": CAUSE_OWNER[c],
                      "count": n} for c, n in sorted(counts.items(), key=lambda kv: -kv[1])],
        "provider_side": provider_side,
        "school_side": school_side,
        "undetermined": undetermined,
        "provider_share_pct": round(100 * provider_side / total, 1) if total else 0.0,
    }
