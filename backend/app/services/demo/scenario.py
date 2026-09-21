"""Воспроизводимый демонстрационный сценарий «авария на узле провайдера».

Что здесь синтетическое, а что настоящее:
  • школы, провайдеры и замеры — СИНТЕТИЧЕСКИЕ: датчик с фиксированным seed, вымышленные названия;
  • сезонная норма (baseline), атрибуция источника (attribution) и прогноз риска SLA (forecast) —
    НАСТОЯЩИЕ модели проекта, применённые к этим замерам в отдельной in-memory БД;
  • боевая БД не читается и не меняется; ответ модели заранее не написан — он считается.

compute() выполняется ОДИН раз (кэш по seed) при создании сессии. Зрители получают готовый
результат и не запускают ни симуляцию, ни модель. build_view() — чистая функция: из документа
сессии строит то, что в данный момент показывается зрителям (и только это: будущие этапы
на клиент не уходят).
"""
from __future__ import annotations

import random
import threading
from datetime import datetime, timedelta
from functools import lru_cache
from types import SimpleNamespace

from sqlalchemy import create_engine, insert
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ...database import Base
from ...models import Device, Line, Measurement, School
from ..ml import attribution, baseline, forecast
from ..ml.attribution import CONFIDENCE_CAP, QUALITY_LABEL
from ..ml.features import CAUSE_LABELS, CAUSE_OWNER, Topology
from ..status import Thresholds, classify, freshness

SEED = 20260921

# --- Этапы --------------------------------------------------------------------

STAGES = [
    ("waiting", "Ожидание начала", "Зрители подключились и ждут начала демонстрации."),
    ("normal", "Штатная работа", "Школы работают нормально, данные свежие."),
    ("incident_started", "Авария",
     "У нескольких школ одного провайдера ухудшились скорость, ping, jitter и потери пакетов."),
    ("diagnostics", "Диагностика",
     "Система по очереди проверяет компьютеры, шлюз школы, соседние школы и школы того же провайдера."),
    ("ml_result", "Вывод модели",
     "Модель предполагает источник сбоя и показывает, на чём основан вывод."),
    ("operator_review", "Решение оператора",
     "Результат ожидает решения человека. Модель ничего не подтверждает сама."),
    ("operator_confirmed", "Оператор решил", "Оператор подтвердил или изменил вердикт модели."),
    ("report_ready", "Обращение и акт SLA", "Сформированы обращение провайдеру и PDF-акт SLA."),
    ("reset", "Сброс", "Демонстрация возвращена в исходное состояние и готова к повтору."),
]
STAGE_KEYS = [s[0] for s in STAGES]
IDX = {key: i for i, key in enumerate(STAGE_KEYS)}
HOLD_STAGES = ("waiting", "reset", "operator_review")   # сами не переходят дальше: ждут ведущего/оператора

# --- Синтетическая область ------------------------------------------------------

# Модельное время: вторник 10:20. Дата фиксирована, чтобы сезонный бакет («будни, 10 ч»)
# и весь результат не зависели от дня, когда идёт показ.
T0 = datetime(2026, 3, 10, 10, 20)
ONSET = T0 + timedelta(minutes=10)          # начало деградации
LAST_ROW = T0 + timedelta(minutes=30)       # последний замер на этапе аварии
NOW_NORMAL = T0 + timedelta(minutes=2)      # «сейчас» на этапе штатной работы
NOW_INCIDENT = LAST_ROW + timedelta(minutes=2)
HISTORY_DAYS = 14
RAMP = [0.55, 0.25, 0.12, 0.10, 0.10]       # доля нормы на 0, 5, 10, 15, 20 минут аварии

N_DIST, R_DIST, O_DIST = "Северный район (демо)", "Речной район (демо)", "Озёрный район (демо)"
NORD, STEP, REKA = "НордЛинк (демо)", "СтепьТелеком (демо)", "РекаНет (демо)"
FAILED = (NORD, N_DIST)                     # провайдер и район, где «ломается» узел

# код, район, провайдер, x, y (условная схема 0..100), договор Мбит/с, рабочих мест
SCHOOLS = [
    ("D01", N_DIST, NORD, 22, 20, 100, 3), ("D02", N_DIST, NORD, 34, 28, 100, 3),
    ("D03", N_DIST, NORD, 24, 38, 200, 2), ("D04", N_DIST, NORD, 42, 17, 100, 2),
    ("D05", N_DIST, STEP, 47, 35, 100, 3), ("D06", N_DIST, STEP, 12, 30, 50, 2),
    ("D07", R_DIST, NORD, 62, 58, 100, 2), ("D08", R_DIST, REKA, 76, 62, 100, 3),
    ("D09", R_DIST, REKA, 68, 76, 100, 2), ("D10", O_DIST, STEP, 22, 70, 100, 3),
    ("D11", O_DIST, STEP, 36, 84, 50, 2), ("D12", O_DIST, REKA, 46, 68, 100, 2),
]
ANCHOR = "D01"

FEATURE_LABELS = {
    "dev_frac": "Доля ПК школы ниже своей нормы",
    "dev_all": "Просели все ПК школы",
    "dev_single": "Просел только один ПК",
    "gw_anom": "Шлюз школы ниже своей нормы",
    "depth_mean": "Глубина просадки",
    "sync": "Просадка одинакова на всех ПК",
    "peer_prov_dist": "Доля школ того же провайдера в районе с просадкой",
    "peer_other_prov_dist": "Доля школ других провайдеров в районе с просадкой",
    "peer_prov_other_dist": "Доля школ провайдера в других районах с просадкой",
    "wifi_frac": "Доля ПК на Wi-Fi среди просевших",
    "wifi_weak": "Слабый сигнал Wi-Fi",
    "n_dev": "Число ПК с данными",
    "peer_ctx": "Число соседей для сравнения",
}
NOTICE = ("Демонстрационные синтетические данные. Модели обучены на симуляторе отказов и не "
          "подтверждены на реальных авариях школ. Вывод — гипотеза; решение принимает оператор.")

ML_RUNS = 0                 # сколько раз реально считались модели (тесты: не растёт от зрителей)
_LOCK = threading.RLock()


def _device_id(code: str, n: int | None) -> str:
    return f"DEMO-{code}-GW" if n is None else f"DEMO-{code}-PC{n}"


def _norm_ratio(ts: datetime) -> float:
    """Доля договорной скорости в норме: зависит от часа и типа дня — то, что ловит baseline."""
    hour = ts.hour + ts.minute / 60
    if ts.weekday() >= 5:
        return 0.93 if 10 <= hour < 20 else 0.96
    if 8 <= hour < 15:
        return 0.82            # уроки: канал загружен
    return 0.90 if hour < 20 else 0.96


def _timeline() -> list[datetime]:
    """Замеры: 14 суток каждые 30 мин, затем последние 2 часа каждые 5 мин до LAST_ROW."""
    start = T0 - timedelta(days=HISTORY_DAYS)
    dense = T0 - timedelta(hours=2)
    stamps, ts = [], start
    while ts < dense:
        stamps.append(ts)
        ts += timedelta(minutes=30)
    while ts <= LAST_ROW:
        stamps.append(ts)
        ts += timedelta(minutes=5)
    return stamps


def _rows_for(device_id: str, school_id: int, contract: float, affected: bool, seed: int,
              gateway: bool) -> list[dict]:
    """Замеры одного устройства. Шум ограничен (равномерный): ложных аномалий быть не может,
    а любая аномалия в результате — следствие заданной аварии, а не случайного выброса."""
    rnd = random.Random(f"{seed}:{device_id}")
    factor = 1.0 if gateway else rnd.uniform(0.93, 0.99)
    ping_base = rnd.uniform(9, 20)
    out = []
    for ts in _timeline():
        minutes = (ts - ONSET).total_seconds() / 60
        ratio = _norm_ratio(ts) * factor + rnd.uniform(-0.035, 0.035)
        ping, jitter, loss = ping_base + rnd.uniform(0, 4), 1.5 + rnd.uniform(0, 2.5), rnd.uniform(0, 0.15)
        if affected and minutes >= 0:
            ratio *= RAMP[min(len(RAMP) - 1, int(minutes // 5))]
            ping, jitter = ping_base * rnd.uniform(11, 16), rnd.uniform(48, 95)
            loss = rnd.uniform(7, 14)
        out.append({"device_id": device_id, "school_id": school_id, "timestamp": ts,
                    "download_speed": round(ratio * contract, 2),
                    "upload_speed": round(ratio * contract * 0.95, 2), "ping": round(ping, 1),
                    "jitter": round(jitter, 1), "packet_loss": round(loss, 2),
                    "is_offline": False, "source": "demo"})
    return out


class _Fixture:
    """Отдельная in-memory БД + профили сезонной нормы. Боевая БД не затрагивается."""

    def __init__(self, seed: int):
        self.seed = seed
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
        Base.metadata.create_all(bind=engine)
        self.db = sessionmaker(bind=engine, autoflush=False)()
        self.school_ids: dict[str, int] = {}
        self.rows: dict[str, list[dict]] = {}
        contracts: dict[int, float] = {}
        for n, (code, district, provider, x, y, contract, workstations) in enumerate(SCHOOLS, 1):
            school = School(school_id_code=f"DEMO-{n:03d}", name=f"Демо-школа №{n}", region=district,
                            address="Условный адрес (демо)", lat=x, lng=y, provider=provider,
                            connection_type="Оптика", contract_speed_down=contract,
                            contract_speed_up=contract, contact_name="Демо-контакт",
                            contact_phone="+7 000 000-00-00", provider_phone="—")
            self.db.add(school)
            self.db.flush()
            self.school_ids[code] = school.id
            contracts[school.id] = contract
            line = Line(school_id=school.id, code=f"DEMO-{n:03d}-L1", role="main", provider=provider,
                        connection_type="Оптика", contract_speed_down=contract,
                        contract_speed_up=contract)
            self.db.add(line)
            self.db.flush()
            affected = (provider, district) == FAILED
            for k in [None, *range(1, workstations + 1)]:
                did = _device_id(code, k)
                self.db.add(Device(
                    device_id=did, school_id=school.id, line_id=line.id if k is None else None,
                    name="Шлюз школы" if k is None else f"ПК-агент №{k}",
                    room="Серверная" if k is None else f"Кабинет {k}",
                    device_type="Шлюз" if k is None else "Рабочая станция",
                    link_mode="Ethernet 1 Гбит/с", revoked=False))
                self.rows[did] = _rows_for(did, school.id, contract, affected, seed, k is None)
        self.db.flush()
        for rows in self.rows.values():
            self.db.execute(insert(Measurement), rows)
        self.db.commit()
        self.store = {"built_at": None, "devices": 0, "profiles": baseline.profiles_from_rows(
            ((r["device_id"], r["school_id"], r["timestamp"], r["download_speed"], False)
             for rows in self.rows.values() for r in rows), contracts)}
        self.store["devices"] = len(self.store["profiles"])
        self.contracts = contracts


@lru_cache(maxsize=2)
def fixture(seed: int = SEED) -> _Fixture:
    with _LOCK:
        return _Fixture(seed)


# --- Расчёт ---------------------------------------------------------------------------

def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _gateway_at(fx: _Fixture, code: str, at: datetime) -> dict:
    """Последний замер шлюза (точки мониторинга основной линии) не позже `at`."""
    rows = [r for r in fx.rows[_device_id(code, None)] if r["timestamp"] <= at]
    return rows[-1]


def _metrics(row: dict, contract: float, th: Thresholds) -> dict:
    return {"download": row["download_speed"], "upload": row["upload_speed"], "ping": row["ping"],
            "jitter": row["jitter"], "loss": row["packet_loss"],
            "status": classify(row["download_speed"], row["ping"], row["packet_loss"], contract,
                               False, upload=row["upload_speed"], jitter=row["jitter"],
                               contract_up=contract, th=th)}


def _steps(v: dict) -> list[dict]:
    """Ход диагностики — из доказательной базы модели, а не из шаблона."""
    ev = v["evidence"]
    total, hit = ev.get("devices_total", 0), ev.get("devices_affected", 0)
    peers, peers_hit = ev.get("peers_same_provider_district", 0), ev.get("peers_same_provider_district_affected", 0)
    others, pct = ev.get("peers_other_providers_district", 0), ev.get("peers_other_providers_district_affected_pct", 0)
    gateway = bool(ev.get("gateway_affected"))
    depth = ev.get("avg_depth_pct", 0)
    return [
        {"key": "devices", "title": "Компьютеры школы",
         "result": f"{hit} из {total} ПК ниже собственной нормы для этого часа и типа дня (просадка {depth}%)",
         "conclusion": "Отдельный компьютер просадку не объясняет" if hit >= max(2, total / 2)
         else "Отклонение локализовано на отдельных ПК", "flagged": hit >= max(2, total / 2)},
        {"key": "gateway", "title": "Шлюз школы",
         "result": "Шлюз ниже нормы — канал просел на входе в школу" if gateway
         else "Шлюз в пределах нормы",
         "conclusion": "Проблема выше уровня рабочих мест" if gateway
         else "Канал до школы работает штатно", "flagged": gateway},
        {"key": "neighbors", "title": "Соседние школы района (другие провайдеры)",
         "result": f"{pct:g}% из {others} школ в отклонении" if others
         else "Сопоставимых школ других провайдеров в районе нет",
         "conclusion": "Общерайонная авария маловероятна" if others and pct < 25
         else "Есть признаки общерайонной аварии" if others and pct >= 40 else "Сравнить не с чем",
         "flagged": bool(others and pct >= 40)},
        {"key": "provider", "title": "Школы того же провайдера в районе",
         "result": f"{peers_hit} из {peers} в отклонении" if peers
         else "Сопоставимых школ этого провайдера нет",
         "conclusion": "Общая точка отказа на стороне провайдера вероятна" if peers and peers_hit / peers >= 0.4
         else "Соседи провайдера работают штатно" if peers else "Сравнить не с чем",
         "flagged": bool(peers and peers_hit / peers >= 0.4)},
    ]


def compute(seed: int = SEED) -> dict:
    """Единственный расчёт сценария: метрики, диагностика и вывод ML. Кэшируется по seed."""
    return _compute(seed)


@lru_cache(maxsize=4)
def _compute(seed: int) -> dict:
    global ML_RUNS
    with _LOCK:
        ML_RUNS += 1
        fx = fixture(seed)
        th = Thresholds.from_settings()
        db, ids = fx.db, fx.school_ids
        topo = Topology(db)
        snap = baseline.snapshot(db, NOW_INCIDENT, window_min=60, store=fx.store)
        anchor_id = ids[ANCHOR]

        # --- метрики школ на двух моментах ------------------------------------------
        schools = []
        for n, (code, district, provider, x, y, contract, _) in enumerate(SCHOOLS, 1):
            school = topo.schools[ids[code]]
            schools.append({
                "id": n, "name": school.name, "district": district, "provider": provider,
                "x": x, "y": y, "contract": contract, "affected": (provider, district) == FAILED,
                "normal": _metrics(_gateway_at(fx, code, T0), contract, th),
                "incident": _metrics(_gateway_at(fx, code, LAST_ROW), contract, th)})

        # --- настоящие модели -------------------------------------------------------
        verdict = attribution.diagnose(db, anchor_id, at=NOW_INCIDENT, topo=topo, snap=snap)
        prediction = forecast.predict(db, anchor_id, at=NOW_INCIDENT, topo=topo, snap=snap)
        per_school = []
        for sid in sorted(topo.anomalous(snap)):
            v = attribution.diagnose(db, sid, at=NOW_INCIDENT, topo=topo, snap=snap)
            per_school.append({"school": topo.schools[sid].name, "cause": v["cause"],
                               "cause_label": v["cause_label"], "confidence": v["confidence"]})

        gw_id = _device_id(ANCHOR, None)
        gw_row = _gateway_at(fx, ANCHOR, LAST_ROW)
        contract = fx.contracts[anchor_id]
        got = baseline.score(gw_id, gw_row["timestamp"], gw_row["download_speed"] / contract,
                             False, fx.store)
        seasonal = {
            "device": "Шлюз школы", "school": topo.schools[anchor_id].name,
            "bucket": "будни" if gw_row["timestamp"].weekday() < 5 else "выходные",
            "hour": gw_row["timestamp"].hour,
            "expected_mbps": round(got["expected"] * contract, 1),
            "actual_mbps": gw_row["download_speed"], "drop_pct": round(got["depth"] * 100, 1),
            "z": got["z"], "history_days": HISTORY_DAYS,
            "samples": fx.store["profiles"][gw_id]["samples"]}

        quality = verdict["data_quality"]
        probs = sorted(verdict["probabilities"].items(), key=lambda kv: -kv[1])[:3]
        ml = {
            "cause": verdict["cause"], "cause_label": verdict["cause_label"],
            "responsible": verdict["responsible"], "confidence": verdict["confidence"],
            "model_confidence": verdict.get("model_confidence", verdict["confidence"]),
            "confidence_cap": CONFIDENCE_CAP[quality["level"]],
            "cap_table": [{"level": k, "label": QUALITY_LABEL[k], "cap": CONFIDENCE_CAP[k]}
                          for k in (2, 1, 0)],
            "data_quality": quality,
            "alternatives": [{"cause": c, "label": CAUSE_LABELS.get(c, c), "p": round(p, 3)}
                             for c, p in probs],
            "drivers": [{**d, "label": FEATURE_LABELS.get(d["feature"], d["feature"])}
                        for d in verdict["drivers"]],
            "evidence": {k: verdict["evidence"].get(k) for k in (
                "devices_total", "devices_affected", "gateway_affected", "avg_depth_pct",
                "peers_same_provider_district", "peers_same_provider_district_affected",
                "peers_other_providers_district",
                "peers_other_providers_district_affected_pct")},
            "narrative": verdict["narrative"], "model_version": verdict["model_version"],
            "source": verdict["source"], "seasonal_norm": seasonal, "per_school": per_school,
            "forecast": None if prediction.get("probability") is None else {
                "probability": prediction["probability"], "band": prediction["band"],
                "horizon_hours": prediction["horizon_hours"],
                "recommendation": prediction["recommendation"],
                "drivers": prediction["drivers"], "model_version": prediction["model_version"]},
            "notice": NOTICE,
        }

        # --- ряд для графика: среднее по группе «провайдер узла» и по остальным ---------
        group = [c for c, d, p, *_ in SCHOOLS if (p, d) == FAILED]
        rest = [c for c, d, p, *_ in SCHOOLS if (p, d) != FAILED]
        contract_of = {c: contract for c, *_, contract, _ in SCHOOLS}
        series = []
        for i, row in enumerate(fx.rows[_device_id(ANCHOR, None)]):
            ts = row["timestamp"]
            if ts < T0 - timedelta(minutes=60):
                continue
            def pick(codes):   # среднее по группе, Мбит/с в пересчёте на договор 100
                return _mean(fx.rows[_device_id(c, None)][i]["download_speed"] / contract_of[c] * 100
                             for c in codes)
            series.append({"t": ts.strftime("%H:%M"), "group": round(pick(group), 1),
                           "rest": round(pick(rest), 1)})

        # --- материалы для обращения и PDF (чистые данные — строится в любом процессе) ---
        from ..predictive import analyze
        analysis = analyze(db, anchor_id, days=1, now=NOW_INCIDENT)
        anchor = topo.schools[anchor_id]
        report_ctx = {
            "analysis": analysis,
            "school": {"name": anchor.name, "school_id_code": anchor.school_id_code,
                       "region": anchor.region, "address": anchor.address, "provider": anchor.provider,
                       "connection_type": anchor.connection_type,
                       "contract_speed_down": anchor.contract_speed_down,
                       "contact_name": anchor.contact_name, "contact_phone": anchor.contact_phone},
            "incident": {"incident_number": "INC-DEMO-0001", "start_time": ONSET.isoformat(),
                         "status": "Открыт", "provider": anchor.provider,
                         "description": f"Массовое ухудшение связи у провайдера {anchor.provider} "
                                        f"в районе «{anchor.region}»: скорость, ping, jitter, потери."},
            "verdict": verdict, "forecast_text": None if not ml["forecast"] else (
                f"Вероятность выхода за SLA в ближайшие {ml['forecast']['horizon_hours']} ч — "
                f"{round(ml['forecast']['probability'] * 100)}% ({ml['forecast']['band']}). "
                f"{ml['forecast']['recommendation']}"),
        }
        affected = [s["id"] for s in schools if s["affected"]]
        return {
            "seed": seed, "synthetic": True, "schools": schools, "series": series, "ml": ml,
            "steps": _steps(verdict), "report": report_ctx,
            "incident": {"number": "INC-DEMO-0001", "provider": FAILED[0], "district": FAILED[1],
                         "started": ONSET.strftime("%H:%M"), "affected": affected},
            "clock": {"normal": NOW_NORMAL.strftime("%H:%M"), "incident": NOW_INCIDENT.strftime("%H:%M"),
                      "date": T0.strftime("%d.%m.%Y")},
            "fresh": {"normal": freshness(T0, NOW_NORMAL, th), "incident": freshness(LAST_ROW, NOW_INCIDENT, th)},
        }


# --- Обращение и PDF ---------------------------------------------------------------------

BANNER = ("ДЕМОНСТРАЦИОННЫЙ ДОКУМЕНТ · синтетические данные · вымышленные организации · "
          "юридической силы не имеет")


def decision_verdict(scn: dict, decision: dict | None) -> dict:
    """Вердикт для документов: если оператор изменил его — в документе указано именно это,
    а оценка модели остаётся оценкой модели."""
    verdict = dict(scn["report"]["verdict"])
    if decision and decision["cause"] != verdict["cause"]:
        verdict["narrative"] = (
            f"Модель предполагала: {verdict['cause_label']} (оценка {round(verdict['confidence'] * 100)}%). "
            f"Оператор изменил вердикт на: {CAUSE_LABELS[decision['cause']]}. "
            f"Далее указан вердикт оператора.")
        verdict.update(cause=decision["cause"], cause_label=CAUSE_LABELS[decision["cause"]],
                       responsible=CAUSE_OWNER[decision["cause"]], actionable=False)
    elif decision:
        verdict["narrative"] = "Вердикт модели подтверждён оператором. " + verdict["narrative"]
    return verdict


def claim_text(scn: dict, decision: dict | None) -> str:
    """Обращение провайдеру — детерминированный шаблон проекта (без внешнего AI)."""
    from ...routers.ai import _fallback_claim
    ctx = scn["report"]
    school = SimpleNamespace(**ctx["school"])
    incident = SimpleNamespace(**{**ctx["incident"],
                                  "start_time": datetime.fromisoformat(ctx["incident"]["start_time"])})
    text = _fallback_claim(incident, school, ctx["analysis"], decision_verdict(scn, decision))
    return f"[{BANNER}]\n\n{text}"


def report_pdf(scn: dict, decision: dict | None) -> bytes:
    from ..reports import sla_report
    ctx = scn["report"]
    verdict = decision_verdict(scn, decision)
    if ctx["forecast_text"]:
        verdict["forecast_text"] = ctx["forecast_text"]
    return sla_report(ctx["analysis"], ctx["school"], [ctx["incident"]], verdict=verdict,
                      banner=BANNER)


# --- Представление для зрителей ----------------------------------------------------------

def _agg(items: list[dict]) -> dict | None:
    if not items:
        return None
    return {k: round(_mean(i[k] for i in items), 1) for k in ("download", "upload", "ping", "jitter", "loss")}


def build_view(doc: dict) -> dict:
    """Что видят зрители на текущем этапе. Данные будущих этапов сюда не попадают."""
    scn, stage = doc["scn"], doc["stage"]
    idx, cfg = IDX[stage], doc["settings"]
    label, blurb = next((s[1], s[2]) for s in STAGES if s[0] == stage)
    started = idx >= IDX["normal"] and stage != "reset"
    incident = idx >= IDX["incident_started"] and stage != "reset"
    moment = "incident" if incident else "normal"

    schools = []
    for s in scn["schools"]:
        row = {k: s[k] for k in ("id", "name", "district", "provider", "x", "y", "contract")}
        row.update(affected=incident and s["affected"], **(s[moment] if started else
                   {"download": None, "upload": None, "ping": None, "jitter": None, "loss": None,
                    "status": None}))
        schools.append(row)

    metrics = None
    if started:
        live = [s[moment] for s in scn["schools"]]
        focus = [s[moment] for s in scn["schools"] if s["affected"]] if incident else live
        rest = [s[moment] for s in scn["schools"] if not s["affected"]] if incident else []
        metrics = {"scope": "affected" if incident else "all", "focus": _agg(focus), "rest": _agg(rest),
                   "norm_download": scn["ml"]["seasonal_norm"]["expected_mbps"] if incident else None}

    view = {
        "session": {"id": doc["id"], "title": doc["title"], "run": doc["run"], "version": doc["version"]},
        "synthetic": True,
        "banner": "Демонстрационные синтетические данные",
        "notice": NOTICE,
        "stage": {"key": stage, "index": idx, "label": label, "description": blurb,
                  "started_at": doc["stage_started"], "paused": doc["paused_at"] is not None,
                  "auto": cfg["mode"] == "auto",
                  "next_at": _next_at(doc)},
        "stages": [{"key": k, "label": l} for k, l, _ in STAGES],
        "clock": {"label": f"{scn['clock']['date']} {scn['clock'][moment]}" if started else None,
                  "note": "модельное время сценария"},
        "freshness": {**scn["fresh"][moment], "state": "fresh" if not scn["fresh"][moment]["is_stale"] else "stale"}
        if started else None,
        "schools": schools, "metrics": metrics,
        "series": [p for p in scn["series"] if incident or p["t"] <= scn["clock"]["normal"]] if started else [],
        "provider": {"name": scn["incident"]["provider"], "district": scn["incident"]["district"],
                     "affected": len(scn["incident"]["affected"]),
                     "in_district": sum(1 for s in scn["schools"] if s["district"] == scn["incident"]["district"]
                                        and s["provider"] == scn["incident"]["provider"])} if incident else None,
        "incident": {"number": scn["incident"]["number"], "started": scn["incident"]["started"],
                     "affected": scn["incident"]["affected"]} if incident else None,
        "diagnostics": None, "ml": None, "review": None, "decision": None, "report": None,
    }

    if idx >= IDX["diagnostics"] and stage != "reset":
        done = doc["substep"] if stage == "diagnostics" else len(scn["steps"])
        view["diagnostics"] = [
            {"key": s["key"], "title": s["title"],
             "state": "done" if i < done else "running" if i == done else "pending",
             **({"result": s["result"], "conclusion": s["conclusion"], "flagged": s["flagged"]}
                if i < done else {})}
            for i, s in enumerate(scn["steps"])]
    if idx >= IDX["ml_result"] and stage != "reset":
        view["ml"] = scn["ml"]
    if stage == "operator_review":
        view["review"] = {"status": "pending", "text": "Ожидает решения оператора"}
    if idx >= IDX["operator_confirmed"] and stage != "reset" and doc["decision"]:
        d = doc["decision"]
        view["decision"] = {"kind": d["kind"], "model_cause": scn["ml"]["cause"],
                            "model_label": scn["ml"]["cause_label"], "cause": d["cause"],
                            "label": CAUSE_LABELS[d["cause"]], "responsible": CAUSE_OWNER[d["cause"]],
                            "decided_at": d["at"], "by": "оператор"}
    if stage == "report_ready" and doc["report"]:
        view["report"] = {"generated_at": doc["report"]["at"], "claim_text": doc["report"]["claim_text"],
                          "pdf_name": "SLA_DEMO_act.pdf"}
    return view


def _next_at(doc: dict) -> float | None:
    """Когда сработает автопереход (эпоха, с). None — переход только вручную/по решению оператора."""
    cfg = doc["settings"]
    if cfg["mode"] != "auto" or doc["paused_at"] is not None or doc["stage"] in HOLD_STAGES:
        return None
    seconds = cfg["intervals"].get(doc["stage"], 0)
    return doc["armed_at"] + seconds if seconds else None
