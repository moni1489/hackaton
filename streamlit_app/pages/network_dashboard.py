"""Дашборд качества связи школ: данные из backend/hackathon.db (таблицы measurements, schools)."""
import sqlite3
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Качество связи", page_icon="📡", layout="wide")

DB = Path(__file__).resolve().parent.parent.parent / "backend" / "hackathon.db"
# пороги SLA — зеркало backend/app/config.py (SLA_*)
SPEED_RATIO, PING_MS, LOSS_PCT = 0.6, 80.0, 2.0

# нижняя граница доли замеров в SLA -> (категория, пояснение)
BANDS = [
    (95, "Отлично", "Связь стабильна, нарушений SLA практически нет."),
    (85, "Норма", "Единичные нарушения SLA, риск для учебного процесса низкий."),
    (70, "Нестабильно", "Заметная доля замеров вне SLA, возможны сбои занятий."),
    (0, "Критично", "Массовые нарушения SLA, нужна реакция оператора и провайдера."),
]

# CASE-выражение «замер нарушает SLA»
VIOLATION = f"""(m.is_offline = 1 OR m.ping > {PING_MS} OR m.packet_loss > {LOSS_PCT}
    OR m.download_speed < {SPEED_RATIO} * s.contract_speed_down)"""


def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as con:
        return pd.read_sql_query(sql, con, params=params)


@st.cache_data(ttl=300)
def schools() -> pd.DataFrame:
    return query("SELECT id, name, region FROM schools ORDER BY region, name")


@st.cache_data(ttl=300)
def latest_ts() -> pd.Timestamp:
    return pd.Timestamp(query("SELECT MAX(timestamp) t FROM measurements")["t"][0])


@st.cache_data(ttl=300, show_spinner="Загружаю замеры…")
def hourly(school_id: int | None, region: str | None, since: str) -> pd.DataFrame:
    where, params = ["m.timestamp >= ?"], [since]
    if school_id:
        where.append("s.id = ?"); params.append(school_id)
    elif region:
        where.append("s.region = ?"); params.append(region)
    df = query(f"""
        SELECT strftime('%Y-%m-%d %H:00', m.timestamp) AS hour,
               AVG(m.download_speed) AS download, AVG(m.ping) AS ping,
               AVG(m.packet_loss) AS loss, AVG(m.jitter) AS jitter,
               AVG(m.download_speed / NULLIF(s.contract_speed_down, 0)) * 100 AS speed_pct,
               100.0 * AVG({VIOLATION}) AS violation_pct,
               SUM(m.is_offline) AS offline
        FROM measurements m JOIN schools s ON s.id = m.school_id
        WHERE {' AND '.join(where)} GROUP BY hour ORDER BY hour""", tuple(params))
    df["hour"] = pd.to_datetime(df["hour"])
    return df.set_index("hour")


@st.cache_data(ttl=300)
def worst_schools(region: str | None, since: str) -> pd.DataFrame:
    where, params = ["m.timestamp >= ?"], [since]
    if region:
        where.append("s.region = ?"); params.append(region)
    return query(f"""
        SELECT s.name AS Школа, s.region AS Район, s.provider AS Провайдер,
               ROUND(100.0 * AVG({VIOLATION}), 1) AS "Нарушений SLA, %"
        FROM measurements m JOIN schools s ON s.id = m.school_id
        WHERE {' AND '.join(where)} GROUP BY s.id ORDER BY 4 DESC LIMIT 5""", tuple(params))


def chart(series: pd.Series, title: str, color: str, rule: float | None = None):
    data = series.rename("v").reset_index()
    base = alt.Chart(data).encode(x=alt.X("hour:T", title=None),
                                  y=alt.Y("v:Q", title=None),
                                  tooltip=[alt.Tooltip("hour:T"), alt.Tooltip("v:Q", format=".1f")])
    layers = base.mark_line(color=color, point=alt.OverlayMarkDef(color=color, size=12))
    if rule is not None:
        layers += alt.Chart(pd.DataFrame({"y": [rule]})).mark_rule(
            color="#ef4444", strokeDash=[6, 4]).encode(y="y:Q")
    st.altair_chart(layers.properties(height=230, title=alt.TitleParams(title, anchor="middle")),
                    use_container_width=True)


if not DB.exists():
    st.error(f"Не найдена база {DB}"); st.stop()

sch = schools()
with st.sidebar:
    st.header("Фильтры")
    region = st.selectbox("Район", ["Все"] + sorted(sch["region"].dropna().unique())) 
    pool = sch if region == "Все" else sch[sch["region"] == region]
    school = st.selectbox("Школа", ["Все школы"] + list(pool["name"] + " · " + pool["region"]))
    days = st.slider("Дней истории", 1, 14, 7)
    st.caption(f"Последний замер в БД: {latest_ts():%d.%m.%Y %H:%M}")

region_f = None if region == "Все" else region
school_id = None if school == "Все школы" else int(
    pool.iloc[list(pool["name"] + " · " + pool["region"]).index(school)]["id"])
since = (latest_ts() - pd.Timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

df = hourly(school_id, region_f, since)
if df.empty:
    st.warning("Нет замеров за выбранный период."); st.stop()

sla_pct = round(100 - df["violation_pct"].mean())
band, note = next((b, n) for lo, b, n in BANDS if sla_pct >= lo)

left, right = st.columns([3, 2])

with left:
    chart(df["download"], "Средняя скорость загрузки, Мбит/с", "#f5c542")
    chart(df["ping"], "Пинг, мс (красная линия — порог SLA)", "#8b5cf6", PING_MS)
    chart(df["loss"], "Потери пакетов, % (красная линия — порог SLA)", "#38bdf8", LOSS_PCT)

with right:
    with st.container(border=True):
        st.caption("Состояние связи")
        st.markdown(f"<h1 style='text-align:center;margin:0;font-size:4rem'>{sla_pct}%</h1>"
                    f"<h2 style='text-align:center;margin:0'>{band}</h2>", unsafe_allow_html=True)
        st.caption(f"{note} Показатель — доля замеров в рамках SLA за период.")

    with st.container(border=True):
        st.subheader("Быстрая проверка SLA")
        checks = [  # (метрика, пик/худшее значение, порог, превышен ли, подпись порога)
            ("Пинг", df["ping"].max(), PING_MS, df["ping"].max() > PING_MS, f"{PING_MS:g} мс", "макс."),
            ("Потери пакетов", df["loss"].max(), LOSS_PCT, df["loss"].max() > LOSS_PCT, f"{LOSS_PCT:g} %", "макс."),
            ("Скорость / договор", df["speed_pct"].min(), SPEED_RATIO * 100,
             df["speed_pct"].min() < SPEED_RATIO * 100, f"{SPEED_RATIO * 100:.0f} %", "мин."),
            ("Замеры вне SLA", df["violation_pct"].max(), 15, df["violation_pct"].max() > 15, "15 %", "макс."),
        ]
        rows = [{"Метрика": n, "Худший час": f"{v:.1f} ({k})", "Порог SLA": t,
                 "Статус": "🔴 Превышено" if bad else "🟢 В норме"}
                for n, v, _, bad, t, k in checks]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        st.markdown("**Выявленные проблемы:**")
        issues = [f"{n}: {v:.1f} — порог {t}" for n, v, _, bad, t, _ in checks if bad]
        offline = int(df["offline"].sum())
        if offline:
            issues.append(f"Замеров «нет связи»: {offline}")
        for i in issues or ["Не обнаружено"]:
            st.write(f"- {i}")

    if school_id is None:
        with st.container(border=True):
            st.subheader("Проблемные школы")
            st.dataframe(worst_schools(region_f, since), hide_index=True, use_container_width=True)

st.caption("Источник: backend/hackathon.db. Графики — среднее по выбранным школам за час; "
           "пороги SLA как в backend/app/config.py.")
