"""Признаки моделей: радиус поражения (атрибуция) и предвестники пробоя SLA.

Главная мысль атрибуции — источник сбоя выдаёт не глубина просадки, а её ГРАНИЦА.
Просела одна машина при здоровых соседях — виновата машина. Просела вся школа,
а соседние школы того же провайдера в порядке — виноват шлюз школы. Просели все
школы провайдера в районе — виноват его узел. Просели все провайдеры района —
магистраль. Признаки ниже измеряют ровно эти четыре границы.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ...config import settings
from ...models import Device, Line, Measurement, School
from ..status import is_sla_violation

CAUSES = ["none", "device", "school_lan", "provider_node", "regional"]

# Метки — это ПРЕДПОЛАГАЕМЫЙ источник: сравнение соседей сужает круг, но причину не доказывает.
# no_data и undetermined модель не выдаёт — это состояния слоя достаточности данных.
CAUSE_LABELS = {
    "none": "Аномалия не подтверждена",
    "device": "Отдельный ПК или его подключение",
    "school_lan": "Уровень школы: ЛВС, роутер или линия провайдера",
    "provider_node": "Узел провайдера в районе",
    "regional": "Магистраль или энергоснабжение района",
    "undetermined": "Источник не определён: данных недостаточно",
    "no_data": "Нет свежих данных",
    # Не класс модели: канал ровно плохой, отклоняться ему не от чего, поэтому
    # аномалии нет, а нарушение договора есть. Ставится поверх вердикта «none».
    "chronic": "Канал стабильно ниже договора",
}

CAUSE_OWNER = {
    "none": "—",
    "device": "Школа · системный администратор",
    "school_lan": "Школа и провайдер · нужна проверка на месте",
    "provider_node": "Провайдер",
    "regional": "Провайдер / магистральный оператор",
    "undetermined": "Требуется ручная проверка",
    "no_data": "—",
    "chronic": "Провайдер · несоответствие договорной скорости",
}

ATTRIBUTION_FEATURES = [
    "dev_frac",             # доля ПК школы в аномалии
    "dev_all",              # аномальны все ПК школы
    "dev_single",           # аномален ровно один ПК из нескольких
    "gw_anom",              # аномален шлюз школы
    "depth_mean",           # средняя глубина просадки
    "sync",                 # согласованность просадки между ПК школы
    "peer_prov_dist",       # доля школ того же провайдера в районе в аномалии
    "peer_other_prov_dist", # доля школ ДРУГИХ провайдеров в районе в аномалии
    "peer_prov_other_dist", # доля школ того же провайдера в других районах
    "wifi_frac",            # доля аномальных ПК школы на Wi-Fi
    "wifi_weak",            # слабость Wi-Fi-сигнала у аномальных ПК
    "n_dev",                # сколько ПК в школе (надёжность внутришкольной статистики)
    "peer_ctx",             # сколько школ-соседей есть для сравнения
]

FORECAST_FEATURES = [
    "compliance_6h", "compliance_24h", "depth_now", "anom_frac_now", "trend_1h_6h",
    "hour_sin", "hour_cos", "is_weekend", "peer_anom_frac", "loss_6h", "grade",
]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    return math.sqrt(sum((v - avg) ** 2 for v in values) / len(values))


class Topology:
    """Справочник связей школа↔район↔провайдер↔ПК. Строится один раз на запрос."""

    def __init__(self, db: Session):
        self.schools = {s.id: s for s in db.query(School).all()}
        self.devices: dict[int, list[Device]] = {}
        # Резервная и отключённая линии в атрибуции основной линии не участвуют.
        off_main = {line.id for line in db.query(Line).filter(Line.role != "main")}
        for device in db.query(Device).all():
            if device.line_id in off_main:
                continue
            self.devices.setdefault(device.school_id, []).append(device)
        self.by_district: dict[str, list[int]] = {}
        self.by_pair: dict[tuple, list[int]] = {}
        self.by_provider: dict[str, list[int]] = {}
        for school in self.schools.values():
            self.by_district.setdefault(school.region, []).append(school.id)
            self.by_pair.setdefault((school.provider, school.region), []).append(school.id)
            self.by_provider.setdefault(school.provider, []).append(school.id)
        self._anom_key: int | None = None
        self._anom: set[int] = set()
        self._with_data: set[int] = set()

    def anomalous(self, snap: dict) -> set[int]:
        """Множество поражённых школ на снимке. Считается один раз на снимок —
        иначе перебор соседей стал бы квадратичным по числу организаций.
        Заодно запоминает, у каких школ на снимке вообще есть замеры."""
        if self._anom_key != id(snap):
            self._anom_key = id(snap)
            self._with_data = {sid for sid in self.schools
                               if any(d.device_id in snap for d in self.devices.get(sid, []))}
            self._anom = {sid for sid in self._with_data if self.school_anomalous(sid, snap)}
        return self._anom

    def school_anomalous(self, school_id: int, snap: dict) -> bool:
        """Школа считается поражённой, если сломана половина её ПК или шлюз."""
        devices = self.devices.get(school_id, [])
        if not devices:
            return False
        states = [snap.get(d.device_id) for d in devices]
        present = [s for s in states if s]
        if not present:
            return False
        bad = sum(1 for s in present if s["anomaly"])
        gateway = next((snap.get(d.device_id) for d in devices
                        if d.device_type == "Шлюз" and snap.get(d.device_id)), None)
        return bad / len(present) >= 0.5 or bool(gateway and gateway["anomaly"])

    def _peer_fraction(self, ids: list[int], snap: dict, exclude: int) -> tuple[float, int]:
        """Доля поражённых среди СОПОСТАВИМЫХ соседей и их число. Сосед без замеров на
        снимке не сопоставим: он не «здоровый», о нём просто ничего не известно.
        Нет сопоставимых — (0.0, 0); отличать это от «все здоровы» нужно по числу."""
        hot = self.anomalous(snap)
        peers = [i for i in ids if i != exclude and i in self._with_data]
        if not peers:
            return 0.0, 0
        return sum(1 for i in peers if i in hot) / len(peers), len(peers)


def attribution_features(school_id: int, snap: dict, topo: Topology) -> tuple[list[float], dict]:
    """Вектор признаков + человекочитаемый контекст для доказательной базы."""
    school = topo.schools[school_id]
    devices = topo.devices.get(school_id, [])
    states = [(d, snap[d.device_id]) for d in devices if d.device_id in snap]

    if not states:
        # devices_total — ПК С ДАННЫМИ: ноль означает «замеров нет», а не «ПК нет».
        return [0.0] * len(ATTRIBUTION_FEATURES), {"devices_total": 0,
                                                   "devices_registered": len(devices),
                                                   "devices_affected": 0, "affected": []}

    affected = [(d, s) for d, s in states if s["anomaly"]]
    dev_frac = len(affected) / len(states)
    depths = [s["depth"] for _, s in affected]
    gateway = next((s for d, s in states if d.device_type == "Шлюз"), None)

    # Согласованность: одинаково просевшие ПК = общая причина выше по стеку.
    sync = max(0.0, 1.0 - _stdev(depths) * 2.5) if len(depths) > 1 else (1.0 if depths else 0.0)

    wifi_affected = [(d, s) for d, s in affected if "Wi-Fi" in (d.link_mode or "")]
    wifi_frac = len(wifi_affected) / len(affected) if affected else 0.0
    # −42 дБм — отличный сигнал, −85 — на грани разрыва.
    wifi_weak = _mean([min(1.0, max(0.0, (-(d.wifi_signal_dbm or -50) - 55) / 30))
                       for d, _ in wifi_affected])

    same_pair, ctx = topo._peer_fraction(topo.by_pair.get((school.provider, school.region), []),
                                         snap, school_id)
    district_ids = topo.by_district.get(school.region, [])
    other_prov_ids = [i for i in district_ids
                      if topo.schools[i].provider != school.provider]
    other_prov, other_n = topo._peer_fraction(other_prov_ids, snap, school_id)
    prov_other_dist_ids = [i for i in topo.by_provider.get(school.provider, [])
                           if topo.schools[i].region != school.region]
    prov_other_dist, _ = topo._peer_fraction(prov_other_dist_ids, snap, school_id)

    vector = [
        dev_frac,
        1.0 if affected and len(affected) == len(states) else 0.0,
        1.0 if len(affected) == 1 and len(states) > 1 else 0.0,
        1.0 if gateway and gateway["anomaly"] else 0.0,
        _mean(depths),
        sync,
        same_pair,
        other_prov,
        prov_other_dist,
        wifi_frac,
        wifi_weak,
        min(1.0, len(states) / 5.0),
        min(1.0, ctx / 20.0),
    ]
    # Доля от договорной скорости: аномалия считается от собственной нормы ПК,
    # а нарушение договора — от договора, и одно без другого картину не даёт.
    ratios = [s["ratio"] for _, s in states]
    context = {
        "contract_ratio_pct": round(((gateway or min(states, key=lambda ds: ds[1]["ratio"])[1])
                                     ["ratio"] if ratios else 0.0) * 100, 1),
        "devices_total": len(states),
        "devices_registered": len(devices),
        "devices_affected": len(affected),
        "gateway_affected": bool(gateway and gateway["anomaly"]),
        "avg_depth_pct": round(_mean(depths) * 100, 1),
        "peers_same_provider_district": ctx,
        "peers_same_provider_district_affected": round(same_pair * ctx),
        "peers_other_providers_district": other_n,
        "peers_other_providers_district_affected_pct": round(other_prov * 100, 1),
        "peers_same_provider_other_districts_affected_pct": round(prov_other_dist * 100, 1),
        "affected": [{"device_id": d.device_id, "name": d.name, "room": d.room,
                      "link_mode": d.link_mode, "wifi_signal_dbm": d.wifi_signal_dbm,
                      "depth_pct": round(s["depth"] * 100, 1), "z": s["z"],
                      "offline": s["offline"]}
                     for d, s in sorted(affected, key=lambda ds: ds[1]["z"])],
    }
    return vector, context


def forecast_features(school_id: int, at: datetime, rows: list[Measurement],
                      snap: dict, topo: Topology) -> list[float] | None:
    """Предвестники пробоя SLA: состояние канала за 1/6/24 ч плюс контекст района."""
    school = topo.schools.get(school_id)
    if not school or not rows:
        return None
    contract = school.contract_speed_down or 100.0
    contract_up = school.contract_speed_up

    def window(hours: float) -> list[Measurement]:
        edge = at - timedelta(hours=hours)
        return [r for r in rows if edge <= r.timestamp <= at]

    last_6h, last_24h, last_1h = window(6), window(24), window(1)
    if not last_6h or not last_24h:
        return None

    def compliance(items: list[Measurement]) -> float:
        if not items:
            return 1.0
        bad = sum(1 for r in items
                  if is_sla_violation(r.download_speed or 0, r.ping or 0, r.packet_loss or 0,
                                      contract, bool(r.is_offline), upload=r.upload_speed or 0,
                                      jitter=r.jitter or 0, contract_up=contract_up))
        return 1.0 - bad / len(items)

    avg_6h = _mean([(r.download_speed or 0) / contract for r in last_6h])
    avg_1h = _mean([(r.download_speed or 0) / contract for r in last_1h]) if last_1h else avg_6h
    grade = _mean([(r.download_speed or 0) / contract for r in last_24h])

    own = [snap[d.device_id] for d in topo.devices.get(school_id, []) if d.device_id in snap]
    anom_frac = sum(1 for s in own if s["anomaly"]) / len(own) if own else 0.0
    depth_now = _mean([s["depth"] for s in own]) if own else 0.0
    peer_frac, _ = topo._peer_fraction(topo.by_pair.get((school.provider, school.region), []),
                                       snap, school_id)

    return [
        compliance(last_6h),
        compliance(last_24h),
        depth_now,
        anom_frac,
        (avg_1h - avg_6h) / avg_6h if avg_6h else 0.0,
        math.sin(2 * math.pi * at.hour / 24),
        math.cos(2 * math.pi * at.hour / 24),
        1.0 if at.weekday() >= 5 else 0.0,
        peer_frac,
        min(1.0, _mean([r.packet_loss or 0 for r in last_6h]) / max(settings.SLA_LOSS_PCT, 0.1)),
        min(1.5, grade),
    ]


BREACH_SHARE = 0.25   # устойчивое нарушение, а не единичный плохой замер


def breach_in_window(rows: list[Measurement], start: datetime, end: datetime,
                     contract: float, contract_up: float | None = None) -> bool:
    """Метка прогноза: был ли УСТОЙЧИВЫЙ выход за SLA в интервале.

    Единичный просевший замер нарушением не считается — иначе меткой становился
    бы любой всплеск, и прогнозировать было бы нечего.
    """
    window = [r for r in rows if start < r.timestamp <= end]
    if not window:
        return False
    bad = sum(1 for r in window
              if is_sla_violation(r.download_speed or 0, r.ping or 0, r.packet_loss or 0,
                                  contract, bool(r.is_offline), upload=r.upload_speed or 0,
                                  jitter=r.jitter or 0, contract_up=contract_up))
    return bad / len(window) >= BREACH_SHARE
