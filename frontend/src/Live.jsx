/* Дашборд зрителя демонстрации: только чтение.
   Маршрут /live/{session_id}#t=<временный токен>. Состояние приходит с сервера целиком
   (SSE; при недоступности потока — опрос каждые 1,5 с), клиент ничего не считает и не запускает.
   /live/replay — резервное воспроизведение записи того же сценария без сервера и интернета. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { API_URL } from './api';
import { statusMeta } from './ui';
import './demo.css';

const POLL_MS = 1500;               // худший случай задержки при опросе ≈ интервал + запрос < 2 с
const STREAM_SILENCE_MS = 15000;   // сервер шлёт «пульс» каждые 5 с; тишина дольше — поток мёртв
const FIRST_MESSAGE_MS = 6000;
const PROVIDER_COLORS = ['#7C5CF0', '#0E9C8A', '#D9730D', '#B23A8E'];

const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

/* ---------- Токен и идентификатор вкладки ---------- */

let memoryToken = null;
function readToken(sid) {
  // Ссылка вида /live/{id}#t=<токен>: забираем токен из фрагмента и убираем его из адресной строки.
  const key = `demo.viewer.${sid}`;
  const match = /(?:^|&)t=([^&]+)/.exec(window.location.hash.slice(1));
  if (match) {
    memoryToken = match[1];
    try { sessionStorage.setItem(key, match[1]); } catch { /* приватный режим */ }
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
  }
  try { return sessionStorage.getItem(key) || memoryToken; } catch { return memoryToken; }
}

function clientId() {
  const key = 'demo.cid';
  try {
    const known = sessionStorage.getItem(key);
    if (known) return known;
    const fresh = Math.random().toString(36).slice(2, 12);
    sessionStorage.setItem(key, fresh);
    return fresh;
  } catch { return Math.random().toString(36).slice(2, 12); }
}

class HttpError extends Error {
  constructor(status) { super(`HTTP ${status}`); this.status = status; }
}

/* ---------- Поток состояния: SSE → переподключение → опрос ---------- */

async function readStream(url, token, onView, signal, onOpen) {
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' },
    cache: 'no-store', signal,
  });
  if (!response.ok) throw new HttpError(response.status);
  onOpen();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let gotFirst = false;
  for (;;) {
    let timer;
    const silence = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('silence')),
        gotFirst ? STREAM_SILENCE_MS : FIRST_MESSAGE_MS);
    });
    let chunk;
    try { chunk = await Promise.race([reader.read(), silence]); } finally { clearTimeout(timer); }
    if (chunk.done) return;
    buffer += decoder.decode(chunk.value, { stream: true });
    let cut = buffer.indexOf('\n\n');
    while (cut >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      cut = buffer.indexOf('\n\n');
      const event = /^event: (.+)$/m.exec(block)?.[1];
      const data = /^data: (.*)$/m.exec(block)?.[1];
      if (event === 'closed') throw new HttpError(404);
      if (event === 'state' && data) { gotFirst = true; onView(JSON.parse(data)); }
    }
  }
}

async function pollOnce(url, token, signal) {
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` }, cache: 'no-store', signal,
  });
  if (!response.ok) throw new HttpError(response.status);
  return response.json();
}

function useLive(sid) {
  const [view, setView] = useState(null);
  const [state, setConn] = useState('connecting');
  const offset = useRef(0);   // расхождение часов клиента и сервера — для обратного отсчёта
  const token = useMemo(() => readToken(sid), [sid]);
  const conn = token ? state : 'expired';

  useEffect(() => {
    if (!token) return undefined;
    const cid = clientId();
    const base = `${API_URL}/api/demo/live/${encodeURIComponent(sid)}`;
    const query = `?cid=${encodeURIComponent(cid)}`;
    const control = new AbortController();
    let alive = true;

    const apply = (next) => {
      offset.current = next.server_time * 1000 - Date.now();
      // Старое состояние (запоздавший опрос) не затирает более новое.
      setView((current) => (!current || next.session.version >= current.session.version ? next : current));
    };
    const fatal = (error) => {
      if (error.status === 401 || error.status === 403) { setConn('expired'); return true; }
      if (error.status === 404) { setConn('ended'); return true; }
      return false;
    };
    const poll = async () => {
      try {
        apply(await pollOnce(`${base}/state${query}`, token, control.signal));
        return true;
      } catch (error) { if (alive) fatal(error); return false; }
    };

    (async () => {
      let failures = 0;
      let nextStream = 0;
      while (alive) {
        if (Date.now() >= nextStream) {
          try {
            await readStream(`${base}/stream${query}`, token, (next) => { failures = 0; apply(next); },
              control.signal, () => setConn('live'));
          } catch (error) {
            if (!alive || fatal(error)) return;
            failures += 1;
            nextStream = failures >= 2 ? Date.now() + 30000 : 0;   // поток не идёт — пробуем реже
          }
        }
        if (!alive) return;
        if (failures >= 2) {
          const ok = await poll();
          if (!alive) return;
          setConn(ok ? 'polling' : 'offline');
          await sleep(POLL_MS);
        } else {
          setConn((current) => (current === 'live' ? 'offline' : current));
          await sleep(Math.min(4000, 500 * 2 ** failures));   // автопереподключение с нарастающей паузой
        }
      }
    })();

    const wake = () => { if (!document.hidden) poll(); };     // телефон «проснулся» — сразу свежее состояние
    document.addEventListener('visibilitychange', wake);
    window.addEventListener('online', wake);
    return () => {
      alive = false;
      control.abort();
      document.removeEventListener('visibilitychange', wake);
      window.removeEventListener('online', wake);
    };
  }, [sid, token]);

  return { view, conn, offset };
}

/* ---------- Мелкие представления ---------- */

const num = (value, digits = 1) => (value == null ? '—' : Number(value).toFixed(digits).replace(/\.0+$/, ''));
const pct = (value) => `${Math.round(value * 100)}%`;

function useNow(active) {   // «сейчас» для обратного отсчёта; тикает только пока он нужен
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

const CONN_LABEL = {
  connecting: ['off', 'Подключение…'],
  live: ['ok', 'Онлайн · синхронно'],
  polling: ['warn', 'Резервный режим: обновление каждые 1,5 с'],
  offline: ['danger', 'Нет связи · переподключаемся'],
  replay: ['warn', 'Запись · без сервера'],
};

function Pill({ kind, children }) {
  return <span className={`lv-pill ${kind}`}><i />{children}</span>;
}

function Fatal({ conn }) {
  return (
    <div className="lv-fatal">
      <div className="lv-fatal-card">
        <h1>{conn === 'ended' ? 'Демонстрация завершена' : 'Ссылка недействительна'}</h1>
        <p>{conn === 'ended'
          ? 'Ведущий закрыл сессию. Спасибо, что были с нами.'
          : 'Срок действия ссылки истёк или она открыта не полностью. Попросите ведущего показать QR-код ещё раз.'}
        </p>
      </div>
    </div>
  );
}

function Stepper({ stage, stages }) {
  const shown = stages.slice(0, -1);   // «Сброс» — служебный конец сценария
  const index = Math.min(stage.index, shown.length - 1);
  return (
    <nav className="lv-steps" aria-label="Этапы демонстрации">
      <ol>
        {shown.map((item, i) => (
          <li key={item.key} className={i < stage.index ? 'done' : i === stage.index ? 'now' : ''}
            aria-current={i === index && stage.key !== 'reset' ? 'step' : undefined}>
            <i /><span>{item.label}</span>
          </li>
        ))}
      </ol>
    </nav>
  );
}

/* ---------- Карта: схема без тайлов — работает офлайн ---------- */

function SchoolMap({ schools, selected, onSelect }) {
  const providers = [...new Set(schools.map((s) => s.provider))].sort();
  const color = (name) => PROVIDER_COLORS[providers.indexOf(name) % PROVIDER_COLORS.length];
  const districts = [...new Set(schools.map((s) => s.district))].map((name) => {
    const group = schools.filter((s) => s.district === name);
    const xs = group.map((s) => s.x);
    const ys = group.map((s) => s.y);
    return { name, x: Math.min(...xs) - 7, y: Math.min(...ys) - 9, w: Math.max(...xs) - Math.min(...xs) + 14,
      h: Math.max(...ys) - Math.min(...ys) + 17 };
  });
  return (
    <div>
      <svg className="lv-map" viewBox="0 0 100 100" role="img"
        aria-label="Схема демонстрационной области: школы, окрашенные по статусу связи">
        {districts.map((d) => (
          <g key={d.name}>
            <rect x={d.x} y={d.y} width={d.w} height={d.h} rx="4" className="lv-district" />
            <text x={d.x + 2} y={d.y + 4.4} className="lv-district-name">{d.name.replace(' (демо)', '')}</text>
          </g>
        ))}
        {schools.map((s) => {
          const meta = s.status ? statusMeta(s.status) : null;
          return (
            <g key={s.id} className={`lv-school ${s.affected ? 'hit' : ''} ${selected === s.id ? 'sel' : ''}`}
              onClick={() => onSelect(selected === s.id ? null : s.id)} tabIndex={0} role="button"
              aria-label={`${s.name}, ${s.status || 'ожидание данных'}`}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onSelect(selected === s.id ? null : s.id); }}>
              {s.affected ? <circle cx={s.x} cy={s.y} r="4.6" className="lv-pulse" /> : null}
              <circle cx={s.x} cy={s.y} r="2.7" fill={meta ? meta.color : '#C9D0DC'}
                stroke={color(s.provider)} strokeWidth="1.1" />
              <text x={s.x} y={s.y + 6.4} textAnchor="middle" className="lv-school-name">№{s.id}</text>
            </g>
          );
        })}
      </svg>
      <ul className="lv-legend">
        {providers.map((name) => (
          <li key={name}><i style={{ borderColor: color(name) }} />{name}</li>
        ))}
        <li className="sep">Заливка — статус связи</li>
      </ul>
    </div>
  );
}

function SchoolInfo({ school }) {
  if (!school) return null;
  const meta = school.status ? statusMeta(school.status) : null;
  return (
    <div className="lv-school-info">
      <b>{school.name}</b>
      <span>{school.provider} · {school.district}</span>
      <span className="lv-status"><i style={{ background: meta ? meta.color : '#C9D0DC' }} />
        {school.status || 'Ожидание данных'}</span>
      {school.status ? (
        <span className="mono">
          {num(school.download)} Мбит/с · {num(school.ping)} мс · джиттер {num(school.jitter)} мс · потери {num(school.loss)}%
        </span>
      ) : null}
    </div>
  );
}

/* ---------- Метрики и график ---------- */

function Metrics({ metrics }) {
  if (!metrics?.focus) return null;
  const { focus, rest, scope, norm_download: norm, status } = metrics;
  const bad = scope === 'affected';
  const tiles = [
    ['Скорость', num(focus.download), 'Мбит/с', rest ? `остальные школы: ${num(rest.download)}` : null,
      norm ? `норма для этого часа: ${num(norm)}` : null],
    ['Ping', num(focus.ping), 'мс', rest ? `остальные школы: ${num(rest.ping)}` : null, null],
    ['Jitter', num(focus.jitter), 'мс', rest ? `остальные школы: ${num(rest.jitter)}` : null, null],
    ['Потери пакетов', num(focus.loss), '%', rest ? `остальные школы: ${num(rest.loss)}` : null, null],
  ];
  return (
    <section className="lv-card" aria-label="Показатели связи">
      <h2>{bad ? 'Затронутые школы: средние показатели' : 'Все школы: средние показатели'}
        {status ? <span className="lv-tag" style={{ background: statusMeta(status).color }}>{status}</span> : null}
      </h2>
      <div className="lv-tiles">
        {tiles.map(([label, value, unit, sub, sub2]) => (
          <div key={label} className={`lv-tile ${bad ? 'bad' : ''}`}>
            <span className="eyebrow">{label}</span>
            <b className="mono">{value}<small>{unit}</small></b>
            {sub ? <em>{sub}</em> : null}
            {sub2 ? <em>{sub2}</em> : null}
          </div>
        ))}
      </div>
    </section>
  );
}

function Trend({ series, started }) {
  if (series.length < 2) return null;
  const W = 300;
  const H = 110;
  const x = (i) => 6 + (i * (W - 12)) / (series.length - 1);
  const y = (v) => H - 14 - (Math.max(0, Math.min(110, v)) / 110) * (H - 24);
  const line = (key) => series.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(p[key]).toFixed(1)}`).join(' ');
  const onset = started ? series.findIndex((p) => p.t >= started) : -1;
  return (
    <section className="lv-card" aria-label="Динамика скорости">
      <h2>Скорость загрузки, % от договорной</h2>
      <svg viewBox={`0 0 ${W} ${H}`} className="lv-trend" role="img"
        aria-label="График скорости: школы затронутого провайдера и остальные школы">
        {[0, 50, 100].map((v) => (
          <g key={v}>
            <line x1="6" x2={W - 6} y1={y(v)} y2={y(v)} className="grid" />
            <text x="8" y={y(v) - 2} className="tick">{v}</text>
          </g>
        ))}
        {onset > 0 ? <line x1={x(onset)} x2={x(onset)} y1="4" y2={H - 14} className="onset" /> : null}
        <path d={line('rest')} className="rest" />
        <path d={line('group')} className="group" />
        <text x="6" y={H - 2} className="tick">{series[0].t}</text>
        <text x={W - 6} y={H - 2} textAnchor="end" className="tick">{series[series.length - 1].t}</text>
      </svg>
      <ul className="lv-legend">
        <li><i className="line group" />Школы одного провайдера (Северный район)</li>
        <li><i className="line rest" />Остальные школы</li>
        {onset > 0 ? <li className="sep">Вертикаль — начало аварии</li> : null}
      </ul>
    </section>
  );
}

/* ---------- Диагностика, ML, решение, акт ---------- */

function Diagnostics({ steps }) {
  return (
    <section className="lv-card" aria-label="Ход диагностики" aria-live="polite">
      <h2>Ход диагностики</h2>
      <ol className="lv-diag">
        {steps.map((s) => (
          <li key={s.key} className={`${s.state} ${s.flagged ? 'flag' : ''}`}>
            <span className="mark" aria-hidden="true">{s.state === 'done' ? '✓' : s.state === 'running' ? '' : '○'}</span>
            <div>
              <b>{s.title}</b>
              {s.state === 'done'
                ? <><span>{s.result}</span><em>{s.conclusion}</em></>
                : <span className="muted">{s.state === 'running' ? 'Проверяем…' : 'Ожидает очереди'}</span>}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function Confidence({ ml }) {
  const capped = ml.model_confidence > ml.confidence + 0.001;
  return (
    <div className="lv-conf">
      <div className="lv-conf-num mono">{pct(ml.confidence)}</div>
      <div className="lv-conf-body">
        <div className="lv-bar"><i style={{ width: pct(ml.confidence) }} /><u style={{ left: pct(ml.confidence_cap) }} /></div>
        <span>Уверенность модели · потолок при таком объёме данных {pct(ml.confidence_cap)}</span>
        {capped ? (
          <em>Сырая оценка модели {(ml.model_confidence * 100).toFixed(1)}% ограничена: она не калибрована и не равна вероятности ошибки.</em>
        ) : null}
      </div>
    </div>
  );
}

function MlCard({ ml }) {
  const top = Math.max(...ml.drivers.map((d) => Math.abs(d.contribution)), 0.001);
  const ev = ml.evidence;
  const norm = ml.seasonal_norm;
  return (
    <section className="lv-card lv-ml" aria-label="Результат ML-модели">
      <div className="eyebrow">Предполагаемый источник сбоя</div>
      <h2 className="lv-cause">{ml.cause_label}</h2>
      <p className="lv-owner">Зона ответственности: {ml.responsible}</p>
      <Confidence ml={ml} />

      <h3>Достаточность данных: {ml.data_quality.label}</h3>
      <ul className="lv-plain">
        <li>Сопоставимых школ этого провайдера в районе: {ml.data_quality.comparable_peers};
          школ других провайдеров: {ml.data_quality.comparable_other_providers}.</li>
        {ml.data_quality.reasons.length
          ? ml.data_quality.reasons.map((r) => <li key={r} className="warn">Ограничение: {r}</li>)
          : <li>Ограничений по данным нет.</li>}
        <li className="muted">При нехватке данных уверенность ограничивается:{' '}
          {ml.cap_table.map((c) => `${c.label} — до ${pct(c.cap)}`).join(' · ')}.</li>
      </ul>

      <h3>Что повлияло на вывод</h3>
      <ul className="lv-drivers">
        {ml.drivers.map((d) => (
          <li key={d.feature}>
            <span>{d.label}<small className="mono"> = {num(d.value, 2)}</small></span>
            <div className="lv-bar sm"><i className={d.contribution < 0 ? 'neg' : ''}
              style={{ width: `${Math.round((Math.abs(d.contribution) / top) * 100)}%` }} /></div>
          </li>
        ))}
      </ul>

      <h3>Доказательная база</h3>
      <ul className="lv-plain">
        <li>ПК школы ниже своей нормы: {ev.devices_affected} из {ev.devices_total}
          {ev.gateway_affected ? ', включая шлюз' : ''} (просадка {num(ev.avg_depth_pct)}%).</li>
        <li>Школы того же провайдера в районе в отклонении: {ev.peers_same_provider_district_affected} из {ev.peers_same_provider_district}.</li>
        <li>Школы других провайдеров в районе в отклонении: {num(ev.peers_other_providers_district_affected_pct)}% ({ev.peers_other_providers_district} школ).</li>
        <li>Сезонная норма: {norm.device.toLowerCase()} {norm.school} в {norm.bucket}, {norm.hour}:00 — {num(norm.expected_mbps)} Мбит/с
          (по {norm.history_days} суткам истории); сейчас {num(norm.actual_mbps)} Мбит/с, ниже нормы на {num(norm.drop_pct)}%.</li>
        {ml.alternatives.slice(1).map((a) => (
          <li key={a.cause} className="muted">Другая гипотеза: {a.label} — {(a.p * 100).toFixed(1)}%.</li>
        ))}
      </ul>

      {ml.forecast ? (
        <p className="lv-forecast">Прогноз на {ml.forecast.horizon_hours} ч: риск нарушения SLA{' '}
          <b>{pct(ml.forecast.probability)}</b> ({ml.forecast.band}).</p>
      ) : null}
      <p className="lv-fine">Модель {ml.source === 'model' ? 'обученная' : 'правила без обучения'} · версия {ml.model_version}.
        {' '}{ml.notice}</p>
    </section>
  );
}

function Review({ review }) {
  if (!review) return null;
  return (
    <section className="lv-card lv-wait" role="status" aria-live="polite">
      <span className="lv-spin" aria-hidden="true" />
      <div>
        <h2>{review.text}</h2>
        <p>Вывод модели — гипотеза. Окончательное решение принимает человек-оператор; система его не подменяет.</p>
      </div>
    </section>
  );
}

function Decision({ decision }) {
  if (!decision) return null;
  const changed = decision.kind === 'change';
  return (
    <section className={`lv-card lv-decision ${changed ? 'chg' : 'ok'}`} aria-live="polite">
      <div className="eyebrow">Решение оператора</div>
      <h2>{changed ? 'Вердикт изменён оператором' : 'Вердикт подтверждён оператором'}</h2>
      <p><b>{decision.label}</b><br />Зона ответственности: {decision.responsible}</p>
      {changed ? <p className="muted">Модель предполагала: {decision.model_label}.</p> : null}
    </section>
  );
}

function Report({ report, pdf }) {
  if (!report) return null;
  return (
    <section className="lv-card lv-report" aria-live="polite">
      <div className="eyebrow">Документы сформированы</div>
      <h2>Обращение провайдеру и акт SLA</h2>
      <p>Акт содержит показатели, доказательную базу и оговорки. Это демонстрационный документ на синтетических данных.</p>
      {pdf}
      <details>
        <summary>Текст обращения</summary>
        <pre>{report.claim_text}</pre>
      </details>
    </section>
  );
}

/* ---------- Экран целиком ---------- */

export function LiveView({ view, conn, offset = 0, pdf = null }) {
  const [selected, setSelected] = useState(null);
  const stage = view.stage;
  const now = useNow(stage.next_at != null);
  const countdown = stage.next_at ? Math.max(0, Math.ceil(stage.next_at - (now + offset) / 1000)) : null;
  const [connKind, connText] = CONN_LABEL[conn] || CONN_LABEL.connecting;
  const school = view.schools.find((s) => s.id === selected);
  const ordered = [...view.schools].sort((a, b) => Number(b.affected) - Number(a.affected) || a.id - b.id);
  const waiting = stage.key === 'waiting' || stage.key === 'reset';

  return (
    <div className="lv">
      <div className="lv-banner" role="note">
        <b>{view.banner.toUpperCase()}</b>
        <span>Вымышленные школы и провайдеры. Модели не подтверждены на реальных авариях школ.</span>
      </div>
      <header className="lv-head">
        <div>
          <div className="eyebrow">САМ ВКО · демонстрация</div>
          <h1>{view.session.title}</h1>
        </div>
        <Pill kind={connKind}>{connText}</Pill>
      </header>

      <Stepper stage={stage} stages={view.stages} />

      <section className={`lv-stage ${stage.key}`} aria-live="polite">
        <div className="eyebrow">
          Этап {Math.min(stage.index, view.stages.length - 2) + 1} из {view.stages.length - 1}
          {view.session.run > 1 ? ` · повтор №${view.session.run}` : ''}
        </div>
        <h2>{stage.label}</h2>
        <p>{stage.description}</p>
        <div className="lv-badges">
          {stage.paused ? <span className="lv-tag paused">Пауза</span> : null}
          {countdown != null && !stage.paused ? <span className="lv-tag auto">Автопереход через {countdown} с</span> : null}
          {view.clock.label ? <span className="lv-tag ghost">{view.clock.label} · {view.clock.note}</span> : null}
        </div>
      </section>

      {waiting ? (
        <section className="lv-card lv-hello">
          <span className="lv-spin" aria-hidden="true" />
          <p>Вы подключены. Экран обновляется сам — ничего нажимать не нужно.</p>
        </section>
      ) : null}

      <div className="lv-grid">
        <div className="lv-story">
          <Review review={view.review} />
          <Decision decision={view.decision} />
          <Report report={view.report} pdf={pdf} />
          {view.ml ? <MlCard ml={view.ml} /> : null}
          {view.diagnostics ? <Diagnostics steps={view.diagnostics} /> : null}
        </div>
        <div className="lv-data">
          {view.freshness ? (
            <section className={`lv-card lv-fresh ${view.freshness.state}`}>
              <div className="eyebrow">Свежесть данных</div>
              <b>{view.freshness.state === 'fresh' ? 'Данные свежие' : 'Данные устарели'}</b>
              <span>Последний замер {view.freshness.age_min} мин назад (порог {view.freshness.stale_after_min} мин)</span>
            </section>
          ) : null}
          {view.incident ? (
            <section className="lv-card lv-incident">
              <div className="eyebrow">Инцидент {view.incident.number}</div>
              <b>Затронуто школ: {view.incident.affected.length} из {view.schools.length}</b>
              <span>Провайдер: {view.provider.name} · {view.provider.district}</span>
              <span>Начало (модельное время): {view.incident.started}</span>
            </section>
          ) : null}
          <Metrics metrics={view.metrics} />
          <section className="lv-card" aria-label="Карта школ">
            <h2>Карта школ</h2>
            <SchoolMap schools={view.schools} selected={selected} onSelect={setSelected} />
            <SchoolInfo school={school} />
            <details className="lv-list">
              <summary>Список школ ({view.schools.length})</summary>
              <ul>
                {ordered.map((s) => (
                  <li key={s.id}>
                    <i style={{ background: s.status ? statusMeta(s.status).color : '#C9D0DC' }} />
                    <span>{s.name}{s.affected ? ' · затронута' : ''}</span>
                    <small className="mono">{s.status ? `${num(s.download)} Мбит/с` : '—'}</small>
                  </li>
                ))}
              </ul>
            </details>
          </section>
          <Trend series={view.series} started={view.incident?.started} />
        </div>
      </div>
      <footer className="lv-foot">{view.notice}</footer>
    </div>
  );
}

/* ---------- Режим зрителя и режим воспроизведения записи ---------- */

async function downloadPdf(sid, token, setBusy) {
  setBusy(true);
  try {
    const response = await fetch(`${API_URL}/api/demo/live/${encodeURIComponent(sid)}/report.pdf`,
      { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) throw new Error('pdf');
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = url;
    link.download = 'SLA_DEMO_act.pdf';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch { window.alert('Не удалось скачать акт. Попробуйте ещё раз.'); } finally { setBusy(false); }
}

function Viewer({ sid }) {
  const { view, conn, offset } = useLive(sid);
  const [busy, setBusy] = useState(false);
  if (conn === 'expired' || conn === 'ended') return <Fatal conn={conn} />;
  if (!view) return <div className="lv-fatal"><div className="lv-fatal-card"><p>Подключаемся к демонстрации…</p></div></div>;
  const pdf = (
    <button type="button" className="btn accent lv-pdf" disabled={busy}
      onClick={() => downloadPdf(sid, readToken(sid), setBusy)}>
      {busy ? 'Скачиваем…' : 'Скачать PDF-акт SLA'}
    </button>
  );
  return <LiveView view={view} conn={conn} offset={offset.current} pdf={pdf} />;
}

function Replay() {
  const [frames, setFrames] = useState(null);
  const [index, setIndex] = useState(0);
  const [auto, setAuto] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    fetch('/demo-replay.json', { cache: 'no-store' }).then((r) => r.json()).then(setFrames).catch(() => setFailed(true));
  }, []);
  const last = (frames?.length || 1) - 1;
  const step = useCallback((delta) => setIndex((i) => Math.max(0, Math.min(last, i + delta))), [last]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'ArrowRight' || e.key === ' ' || e.key === 'PageDown') { e.preventDefault(); step(1); }
      if (e.key === 'ArrowLeft' || e.key === 'PageUp') { e.preventDefault(); step(-1); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [step]);
  useEffect(() => {
    if (!auto) return undefined;
    const id = setInterval(() => setIndex((i) => { if (i >= last) { setAuto(false); return i; } return i + 1; }), 3500);
    return () => clearInterval(id);
  }, [auto, last]);

  if (failed) return <Fatal conn="expired" />;
  if (!frames) return null;
  const frame = frames[index];
  return (
    <>
      <div className="lv-replay-bar">
        <b>ВОСПРОИЗВЕДЕНИЕ ЗАПИСИ</b> · резервный режим без сервера и интернета · кадр {index + 1} из {frames.length}: {frame.label}
        <span>
          <button type="button" onClick={() => step(-1)} disabled={index === 0} aria-label="Назад">←</button>
          <button type="button" onClick={() => setAuto((a) => !a)}>{auto ? 'Пауза' : 'Авто'}</button>
          <button type="button" onClick={() => step(1)} disabled={index === last} aria-label="Вперёд">→</button>
        </span>
      </div>
      <LiveView view={frame.view} conn="replay"
        pdf={frame.view.report ? <a className="btn accent lv-pdf" href="/demo-report.pdf" download>Скачать PDF-акт SLA</a> : null} />
    </>
  );
}

export default function Live() {
  const sid = decodeURIComponent(window.location.pathname.split('/')[2] || '');
  useEffect(() => { document.title = 'Демонстрация · САМ ВКО'; }, []);
  return sid === 'replay' ? <Replay /> : <Viewer sid={sid} />;
}
