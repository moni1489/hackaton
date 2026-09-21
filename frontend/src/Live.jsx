/* Подключение зрителя к демонстрации: только чтение. Сам дашборд — MlDash.jsx.
   Маршрут /live/{session_id}#t=<временный токен>. Состояние приходит с сервера целиком
   (SSE; при недоступности потока — опрос каждые 1,5 с), клиент ничего не считает и не запускает.
   /live/replay и /#replay — резервное воспроизведение записи того же сценария без сервера и интернета. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { API_URL } from './api';
import { LiveView } from './MlDash';

const POLL_MS = 1500;               // худший случай задержки при опросе ≈ интервал + запрос < 2 с
const STREAM_SILENCE_MS = 15000;   // сервер шлёт «пульс» каждые 5 с; тишина дольше — поток мёртв
const FIRST_MESSAGE_MS = 6000;

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

/* ---------- Экраны ---------- */

function Fatal({ conn }) {
  return (
    <div className="st">
      <div className="st-fatal">
        <div>
          <h1>{conn === 'ended' ? 'Демонстрация завершена' : 'Ссылка недействительна'}</h1>
          <p className="st-caption">{conn === 'ended'
            ? 'Ведущий закрыл сессию. Спасибо, что были с нами.'
            : 'Срок действия ссылки истёк или она открыта не полностью. Попросите ведущего показать QR-код ещё раз.'}
          </p>
        </div>
      </div>
    </div>
  );
}

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
  if (!view) return <div className="st"><div className="st-fatal"><p>Подключаемся к демонстрации…</p></div></div>;
  const pdf = (
    <button type="button" className="st-btn primary st-wide" disabled={busy}
      onClick={() => downloadPdf(sid, readToken(sid), setBusy)}>
      {busy ? 'Скачиваем…' : 'Скачать PDF-акт SLA'}
    </button>
  );
  return <LiveView view={view} conn={conn} offset={offset.current} pdf={pdf} />;
}

/* Резервное воспроизведение записи сценария: без сервера и интернета. */
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
      <div className="st-top">
        ВОСПРОИЗВЕДЕНИЕ ЗАПИСИ · без сервера и интернета · кадр {index + 1} из {frames.length}: {frame.label}
        <span className="st-row" style={{ justifyContent: 'center', marginTop: '.25rem' }}>
          <button type="button" className="st-btn" onClick={() => step(-1)} disabled={index === 0} aria-label="Назад">←</button>
          <button type="button" className="st-btn" onClick={() => setAuto((a) => !a)}>{auto ? 'Пауза' : 'Авто'}</button>
          <button type="button" className="st-btn" onClick={() => step(1)} disabled={index === last} aria-label="Вперёд">→</button>
        </span>
      </div>
      <LiveView view={frame.view} conn="replay"
        pdf={frame.view.report ? <a className="st-btn primary st-wide" href="/demo-report.pdf" download>Скачать PDF-акт SLA</a> : null} />
    </>
  );
}

export default function Live() {
  const sid = window.location.hash === '#replay' ? 'replay'
    : decodeURIComponent(window.location.pathname.split('/')[2] || '');
  useEffect(() => { document.title = 'ML-дашборд · САМ ВКО'; }, []);
  return sid === 'replay' ? <Replay /> : <Viewer sid={sid} />;
}
