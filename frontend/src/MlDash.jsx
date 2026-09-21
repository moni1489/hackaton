/* Экран зрителя: дашборд ML-моделей в реальном времени.
   Оформление повторяет Streamlit-дашборд проекта (streamlit_app/app.py): те же блоки —
   метрики, бары вероятностей, вклад признаков, качество данных, прогноз SLA.
   Данные приходят с сервера целиком, клиент ничего не считает. */
import { useEffect, useState } from 'react';
import './streamlit.css';

const num = (value, digits = 1) => (value == null ? '—' : Number(value).toFixed(digits).replace(/\.0+$/, ''));
const pct = (value) => (value == null ? '—' : `${Math.round(value * 100)}%`);

const CONN = {
  connecting: ['', 'Подключение…'],
  live: ['ok', 'Онлайн · синхронно'],
  polling: ['warn', 'Резервный режим · опрос'],
  offline: ['danger', 'Нет связи · переподключаемся'],
  replay: ['warn', 'Запись · без сервера'],
};

function useNow(active) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

function Metric({ label, value, unit, delta, kind = 'off' }) {
  return (
    <div className="st-metric">
      <div className="lbl">{label}</div>
      <div className="val">{value}{unit ? <small>{unit}</small> : null}</div>
      <div className={`dlt ${kind}`}>{delta || ' '}</div>
    </div>
  );
}

function Alert({ kind = 'info', icon = 'ℹ️', children }) {
  return <div className={`st-alert ${kind}`}><span className="ico" aria-hidden="true">{icon}</span><div>{children}</div></div>;
}

/* st.bar_chart(horizontal=True): бары долей с подписью значения */
function Bars({ rows }) {
  const top = Math.max(...rows.map((r) => Math.abs(r.value)), 1e-6);
  return (
    <div className="st-bars">
      {rows.map((row, i) => (
        <div className="st-bar" key={row.label}>
          <div className="cap"><span>{row.label}</span><b className="st-mono">{row.text}</b></div>
          <div className="track">
            <i className={row.value < 0 ? 'neg' : i === 0 ? 'top' : ''}
              style={{ width: `${Math.max(2, (Math.abs(row.value) / top) * 100)}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

/* st.line_chart: скорость группы провайдера против остальных школ */
function LineChart({ series, started }) {
  if (series.length < 2) return null;
  const W = 300;
  const H = 110;
  const x = (i) => 10 + (i * (W - 16)) / (series.length - 1);
  const y = (v) => H - 14 - (Math.max(0, Math.min(110, v)) / 110) * (H - 24);
  const line = (key) => series.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(p[key]).toFixed(1)}`).join(' ');
  const onset = started ? series.findIndex((p) => p.t >= started) : -1;
  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} className="st-chart" role="img"
        aria-label="Скорость загрузки: школы затронутого провайдера и остальные школы">
        {[0, 50, 100].map((v) => (
          <g key={v}>
            <line x1="10" x2={W - 6} y1={y(v)} y2={y(v)} className="grid" />
            <text x="0" y={y(v) + 2} className="tick">{v}</text>
          </g>
        ))}
        {onset > 0 ? <line x1={x(onset)} x2={x(onset)} y1="4" y2={H - 14} className="onset" /> : null}
        <path d={line('rest')} className="rest" />
        <path d={line('group')} className="group" />
        <text x="10" y={H - 2} className="tick">{series[0].t}</text>
        <text x={W - 6} y={H - 2} textAnchor="end" className="tick">{series[series.length - 1].t}</text>
      </svg>
      <ul className="st-legend">
        <li style={{ color: 'var(--st-blue)' }}><i />Школы провайдера в аварийном районе</li>
        <li style={{ color: 'var(--st-blue-2)' }}><i />Остальные школы</li>
        {onset > 0 ? <li>┆ начало аварии</li> : null}
      </ul>
    </>
  );
}

function Telemetry({ view }) {
  const m = view.metrics;
  if (!m?.focus) {
    return <Alert>Замеры ещё не поступили — дашборд обновится сам, как только пойдут данные.</Alert>;
  }
  const bad = m.scope === 'affected';
  const d = (focus, rest) => (rest == null ? '' : `${focus - rest >= 0 ? '+' : ''}${num(focus - rest)} к остальным школам`);
  return (
    <>
      <div className="st-metrics">
        <Metric label="Скорость загрузки" value={num(m.focus.download)} unit="Мбит/с"
          delta={m.norm_download ? `норма часа: ${num(m.norm_download)}` : d(m.focus.download, m.rest?.download)}
          kind={bad ? 'down' : 'up'} />
        <Metric label="Ping" value={num(m.focus.ping)} unit="мс" delta={d(m.focus.ping, m.rest?.ping)}
          kind={bad ? 'down' : 'off'} />
        <Metric label="Jitter" value={num(m.focus.jitter)} unit="мс" delta={d(m.focus.jitter, m.rest?.jitter)}
          kind={bad ? 'down' : 'off'} />
        <Metric label="Потери пакетов" value={num(m.focus.loss)} unit="%" delta={d(m.focus.loss, m.rest?.loss)}
          kind={bad ? 'down' : 'off'} />
      </div>
      <p className="st-caption">
        {bad ? `Показаны средние по затронутым школам (${view.incident?.affected.length} из ${view.schools.length}).`
          : 'Показаны средние по всем школам демонстрационной области.'}
        {view.freshness ? ` Последний замер ${view.freshness.age_min} мин назад.` : ''}
      </p>
    </>
  );
}

function Diagnostics({ steps }) {
  const done = steps.filter((s) => s.state === 'done').length;
  return (
    <>
      <div className="st-progress"><i style={{ width: `${(done / steps.length) * 100}%` }} /></div>
      <p className="st-caption">Сбор признаков для модели: {done} из {steps.length}</p>
      <table className="st-table">
        <tbody>
          {steps.map((s) => (
            <tr key={s.key}>
              <td>{s.state === 'done' ? '✅' : s.state === 'running' ? '⏳' : '⬜'}</td>
              <td>{s.title}</td>
              <td className="num">{s.state === 'done' ? s.result : s.state === 'running' ? 'считаем…' : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function Attribution({ ml }) {
  const ev = ml.evidence;
  const norm = ml.seasonal_norm;
  return (
    <>
      <h3>{ml.cause_label}</h3>
      <div className="st-metrics">
        <Metric label="Уверенность модели" value={pct(ml.confidence)}
          delta={`потолок при таких данных ${pct(ml.confidence_cap)}`} />
        <Metric label="Достаточность данных" value={ml.data_quality.label}
          delta={`сопоставимых школ: ${ml.data_quality.comparable_peers}`} />
        <Metric label="Зона ответственности" value={ml.responsible} delta={`версия ${ml.model_version}`} />
      </div>

      <h3>Вероятности классов</h3>
      <Bars rows={ml.alternatives.map((a) => ({ label: a.label, value: a.p, text: `${(a.p * 100).toFixed(1)}%` }))} />

      <h3>Вклад признаков в вердикт</h3>
      <Bars rows={ml.drivers.map((d) => ({
        label: d.label, value: d.contribution, text: `${d.contribution >= 0 ? '+' : ''}${num(d.contribution, 2)}`,
      }))} />

      <details className="st-exp">
        <summary>Доказательная база</summary>
        <ul>
          <li>ПК школы ниже своей нормы: {ev.devices_affected} из {ev.devices_total}
            {ev.gateway_affected ? ', включая шлюз' : ''} (просадка {num(ev.avg_depth_pct)}%).</li>
          <li>Школы того же провайдера в районе в отклонении: {ev.peers_same_provider_district_affected} из {ev.peers_same_provider_district}.</li>
          <li>Школы других провайдеров в районе в отклонении: {num(ev.peers_other_providers_district_affected_pct)}%
            ({ev.peers_other_providers_district} школ).</li>
          <li>Сезонная норма: {norm.bucket}, {norm.hour}:00 — {num(norm.expected_mbps)} Мбит/с по {norm.history_days} суткам
            истории; сейчас {num(norm.actual_mbps)} Мбит/с, ниже нормы на {num(norm.drop_pct)}%.</li>
          {ml.data_quality.reasons.map((r) => <li key={r}>Ограничение данных: {r}.</li>)}
        </ul>
      </details>

      {ml.per_school?.length ? (
        <>
          <h3>Вердикт по школам в аномалии</h3>
          <div className="st-scroll">
            <table className="st-table">
              <thead><tr><th>Школа</th><th>Причина</th><th>Уверенность</th></tr></thead>
              <tbody>
                {ml.per_school.map((row) => (
                  <tr key={row.school}>
                    <td>{row.school}</td><td>{row.cause_label}</td>
                    <td className="num">{pct(row.confidence)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
      <p className="st-caption">Модель {ml.source === 'model' ? 'обученная' : 'правила без обучения'} ·
        сырая оценка {(ml.model_confidence * 100).toFixed(1)}% ограничена калибровкой. {ml.notice}</p>
    </>
  );
}

function Forecast({ forecast }) {
  if (!forecast) return <Alert>Прогноз появится вместе с вердиктом модели.</Alert>;
  const p = forecast.probability;
  return (
    <>
      <div className="st-metrics">
        <Metric label={`Риск нарушения SLA за ${forecast.horizon_hours} ч`} value={pct(p)}
          delta={forecast.band} kind={p >= 0.45 ? 'down' : 'up'} />
        <Metric label="Версия модели прогноза" value={forecast.model_version} />
      </div>
      <div className="st-progress"><i style={{ width: pct(p) }} /></div>
      <p className="st-caption">{forecast.recommendation}</p>
      {forecast.drivers?.length ? (
        <Bars rows={forecast.drivers.slice(0, 5).map((d) => ({
          label: d.label || d.feature, value: d.contribution,
          text: `${d.contribution >= 0 ? '+' : ''}${num(d.contribution, 2)}`,
        }))} />
      ) : null}
    </>
  );
}

export function LiveView({ view, conn, offset = 0, pdf = null }) {
  const stage = view.stage;
  const now = useNow(stage.next_at != null);
  const countdown = stage.next_at ? Math.max(0, Math.ceil(stage.next_at - (now + offset) / 1000)) : null;
  const [connKind, connText] = CONN[conn] || CONN.connecting;
  const total = view.stages.length - 1;
  const step = Math.min(stage.index, total - 1) + 1;
  const waiting = stage.key === 'waiting' || stage.key === 'reset';

  return (
    <div className="st">
      <div className="st-top">Демонстрационные синтетические данные · вымышленные школы и провайдеры ·
        модели не подтверждены на реальных авариях</div>
      <div className="st-main">
        <h1>САМ ВКО — ML в реальном времени</h1>
        <p className="st-caption">Атрибуция причины деградации канала и прогноз выхода за SLA.
          Экран обновляется сам — ничего нажимать не нужно.</p>
        <div className="st-row" style={{ margin: '.75rem 0 .5rem' }}>
          <span className={`st-badge ${connKind}`}><i />{connText}</span>
          <span className="st-badge">Этап {step} из {total}: {stage.label}</span>
          {stage.paused ? <span className="st-badge warn">Пауза</span> : null}
          {countdown != null && !stage.paused ? <span className="st-badge">Далее через {countdown} с</span> : null}
          {view.clock.label ? <span className="st-badge">{view.clock.label} · {view.clock.note}</span> : null}
        </div>
        <div className="st-progress"><i style={{ width: `${(step / total) * 100}%` }} /></div>

        {waiting ? (
          <>
            <hr className="st-divider" />
            <div className="st-alert info"><span className="st-spin" aria-hidden="true" />
              <div>Вы подключены. Дашборд оживёт, как только ведущий запустит демонстрацию.</div></div>
          </>
        ) : null}

        <h2>Телеметрия сети</h2>
        <Telemetry view={view} />
        {view.series.length > 1 ? (
          <>
            <h3>Скорость загрузки, % от договорной</h3>
            <LineChart series={view.series} started={view.incident?.started} />
          </>
        ) : null}

        <h2>Модель 1 · Атрибуция причины</h2>
        {view.ml ? <Attribution ml={view.ml} />
          : view.diagnostics ? <Diagnostics steps={view.diagnostics} />
            : <Alert>Модель ждёт признаков: идёт сбор замеров по ПК, шлюзам и соседним школам.</Alert>}

        <h2>Модель 2 · Прогноз пробоя SLA</h2>
        <Forecast forecast={view.ml?.forecast} />

        {view.review ? (
          <>
            <h2>Решение оператора</h2>
            <div className="st-alert warning"><span className="st-spin" aria-hidden="true" />
              <div>{view.review.text}. Вывод модели — гипотеза; окончательное решение принимает человек.</div></div>
          </>
        ) : null}
        {view.decision ? (
          <>
            <h2>Решение оператора</h2>
            <Alert kind={view.decision.kind === 'change' ? 'warning' : 'success'}
              icon={view.decision.kind === 'change' ? '✏️' : '✅'}>
              {view.decision.kind === 'change' ? 'Вердикт изменён оператором' : 'Вердикт подтверждён оператором'}:{' '}
              <b>{view.decision.label}</b>. Зона ответственности: {view.decision.responsible}.
              {view.decision.kind === 'change' ? ` Модель предполагала: ${view.decision.model_label}.` : ''}
            </Alert>
          </>
        ) : null}
        {view.report ? (
          <>
            <h2>Документы</h2>
            <Alert kind="success" icon="📄">Сформированы обращение провайдеру и акт SLA на синтетических данных.</Alert>
            {pdf}
            <details className="st-exp"><summary>Текст обращения</summary>
              <pre>{view.report.claim_text}</pre></details>
          </>
        ) : null}

        <hr className="st-divider" />
        <p className="st-caption">{view.notice}</p>
      </div>
    </div>
  );
}

export default LiveView;
