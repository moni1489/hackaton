"""Демо-дашборд ML-моделей САМ ВКО: «Виновник» (атрибуция) и прогноз SLA.

Грузит уже обученные веса из backend/models/*.json через ту же
SoftmaxRegression, которой пользуется бэкенд — никакой отдельной
реализации модели здесь нет.
"""
import math
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ML_DIR = Path(__file__).resolve().parent.parent / "backend" / "app" / "services" / "ml"
sys.path.insert(0, str(ML_DIR))
from linmodel import SoftmaxRegression  # noqa: E402

CAUSE_LABELS = {
    "none": "Аномалия не подтверждена",
    "device": "Отдельный ПК или его линк",
    "school_lan": "Шлюз или ЛВС школы",
    "provider_node": "Узел провайдера в районе",
    "regional": "Магистраль или энергоснабжение района",
}
CAUSE_OWNER = {
    "none": "—",
    "device": "Школа · системный администратор",
    "school_lan": "Школа · обслуживающая организация",
    "provider_node": "Провайдер",
    "regional": "Провайдер / магистральный оператор",
}

# слайдеры: (подпись, min, max, default)
ATTRIBUTION_SLIDERS = {
    "dev_frac": ("Доля аномальных ПК школы", 0.0, 1.0, 0.6),
    "dev_all": ("Аномальны все ПК школы", 0.0, 1.0, 0.0),
    "dev_single": ("Аномален ровно один ПК", 0.0, 1.0, 0.0),
    "gw_anom": ("Аномален шлюз школы", 0.0, 1.0, 0.0),
    "depth_mean": ("Средняя глубина просадки", 0.0, 1.0, 0.4),
    "sync": ("Согласованность просадки между ПК", 0.0, 1.0, 0.5),
    "peer_prov_dist": ("Доля школ того же провайдера в районе — в аномалии", 0.0, 1.0, 0.2),
    "peer_other_prov_dist": ("Доля школ других провайдеров в районе — в аномалии", 0.0, 1.0, 0.1),
    "peer_prov_other_dist": ("Доля школ того же провайдера в других районах — в аномалии", 0.0, 1.0, 0.1),
    "wifi_frac": ("Доля аномальных ПК на Wi-Fi", 0.0, 1.0, 0.3),
    "wifi_weak": ("Слабость Wi-Fi-сигнала аномальных ПК", 0.0, 1.0, 0.2),
    "n_dev": ("ПК в школе (нормировано)", 0.0, 1.0, 0.6),
    "peer_ctx": ("Школ-соседей для сравнения (нормировано)", 0.0, 1.0, 0.5),
}
FORECAST_SLIDERS = {
    "compliance_6h": ("Соответствие SLA за 6 ч", 0.0, 1.0, 0.9),
    "compliance_24h": ("Соответствие SLA за 24 ч", 0.0, 1.0, 0.9),
    "depth_now": ("Текущая глубина просадки", 0.0, 1.0, 0.2),
    "anom_frac_now": ("Доля ПК школы в аномалии сейчас", 0.0, 1.0, 0.1),
    "trend_1h_6h": ("Тренд скорости: 1ч относительно 6ч", -1.0, 1.0, 0.0),
    "is_weekend": ("Выходной день", 0.0, 1.0, 0.0),
    "peer_anom_frac": ("Доля соседних школ провайдера в аномалии", 0.0, 1.0, 0.1),
    "loss_6h": ("Потери пакетов за 6 ч (доля от порога SLA)", 0.0, 1.0, 0.2),
    "grade": ("Средняя скорость за 24 ч (доля от тарифа)", 0.0, 1.5, 0.9),
}


@st.cache_resource
def load_model(name: str) -> SoftmaxRegression | None:
    return SoftmaxRegression.load(name)


def band(probability: float) -> str:
    if probability < 0.2:
        return "низкая"
    if probability < 0.45:
        return "умеренная"
    if probability < 0.7:
        return "высокая"
    return "критическая"


st.set_page_config(page_title="САМ ВКО · ML-демо", layout="wide")
st.title("САМ ВКО — демо ML-моделей")
st.caption("«Виновник» деградации канала и прогноз выхода за SLA на 6 часов вперёд.")

with st.sidebar:
    st.header("Настройки")
    task = st.radio("Модель", ["Атрибуция причины («кто виноват»)", "Прогноз пробоя SLA"])
    is_attribution = task.startswith("Атрибуция")
    model_name = "attribution" if is_attribution else "forecast"
    sliders = ATTRIBUTION_SLIDERS if is_attribution else FORECAST_SLIDERS

    st.divider()
    st.subheader("Входные признаки")
    values = {}
    if is_attribution:
        for key, (label, lo, hi, default) in sliders.items():
            values[key] = st.slider(label, lo, hi, default, 0.05, key=key)
    else:
        for key, (label, lo, hi, default) in sliders.items():
            values[key] = st.slider(label, lo, hi, default, 0.05, key=key)
        hour = st.slider("Час суток", 0, 23, 12)
        values["hour_sin"] = math.sin(2 * math.pi * hour / 24)
        values["hour_cos"] = math.cos(2 * math.pi * hour / 24)

    run = st.button("Запустить предсказание", use_container_width=True, type="primary")

model = load_model(model_name)
if model is None:
    st.error(f"Файл модели backend/models/{model_name}.json не найден.")
    st.stop()

vector = [values[f] for f in model.features]

col_main, col_stats = st.columns([3, 2])

with col_main:
    st.subheader("Результат")
    if run:
        probs = model.predict_proba(vector)
        label = max(probs, key=probs.get)
        confidence = probs[label]

        if is_attribution:
            st.markdown(f"## {CAUSE_LABELS[label]}")
            st.markdown(f"**Ответственный:** {CAUSE_OWNER[label]}")
        else:
            breach_p = probs["breach"]
            st.markdown(f"## Вероятность пробоя SLA: {breach_p:.0%}")
            st.markdown(f"**Уровень риска:** {band(breach_p)}")

        st.metric("Уверенность модели", f"{confidence:.0%}")

        probs_df = pd.Series(probs, name="Вероятность").sort_values(ascending=False)
        st.bar_chart(probs_df)

        st.markdown("**Вклад признаков в вердикт**")
        drivers = model.top_drivers(vector, label, limit=5)
        drivers_df = pd.DataFrame(drivers).set_index("feature")["contribution"]
        st.bar_chart(drivers_df)
    else:
        st.info("Настройте признаки слева и нажмите «Запустить предсказание».")

with col_stats:
    st.subheader("Качество модели")
    metrics = model.metrics
    st.caption(f"Версия: `{model.version}`")
    m1, m2, m3 = st.columns(3)
    m1.metric("Accuracy", f"{metrics.get('accuracy', 0):.1%}")
    m2.metric("Macro F1", f"{metrics.get('macro_f1', 0):.1%}")
    m3.metric("Выборка", metrics.get("samples", "—"))

    per_class = metrics.get("per_class", {})
    if per_class:
        st.markdown("**F1 по классам**")
        f1_df = pd.Series({k: v["f1"] for k, v in per_class.items()}, name="F1")
        st.line_chart(f1_df)
