/* Вкладка «Демонстрация» в панели оператора: одна кнопка запуска, ссылка с QR-кодом для зала
   и управление сценарием. Доступ — только operator/admin (вкладка скрыта у остальных ролей).
   Все действия идут через API и попадают в журнал аудита; зрители получают изменение с сервера. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, request } from './api';
import { LiveView } from './MlDash';
import './demo.css';

const INTERVAL_STAGES = [
  ['normal', 'Штатная работа'], ['incident_started', 'Авария'], ['diagnostics', 'Диагностика'],
  ['ml_result', 'Вывод модели'], ['operator_confirmed', 'Оператор решил'], ['report_ready', 'Акт (0 — ждать)'],
];
const STAGE_NAMES = [
  ['waiting', 'waiting · ожидание'], ['normal', 'normal · штатная работа'],
  ['incident_started', 'incident_started · авария'], ['diagnostics', 'diagnostics · диагностика'],
  ['ml_result', 'ml_result · вывод модели'], ['operator_review', 'operator_review · решение'],
  ['operator_confirmed', 'operator_confirmed · решено'], ['report_ready', 'report_ready · акт'],
  ['reset', 'reset · сброс'],
];
const isLocal = (host) => ['localhost', '127.0.0.1', '::1', '[::1]'].includes(host);
const hostOf = (url) => { try { return new URL(url).hostname; } catch { return ''; } };

async function saveBlob(response, filename) {
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

const call = (path, method = 'GET', body) => request(`/api/demo${path}`, { method, body });

function Setup({ onCreated, onOpen, sessions, fail }) {
  const [busy, setBusy] = useState(false);
  const create = async () => {
    setBusy(true);
    try {
      onCreated(await call('/sessions', 'POST',
        { title: 'Демонстрация САМ ВКО', mode: 'manual', public_url: window.location.origin }));
    } catch (e) { fail(e); } finally { setBusy(false); }
  };
  return (
    <>
      <section className="dc-card dc-start">
        <h2>Демонстрация для зала</h2>
        <p className="dc-note">Одна кнопка: сервер посчитает модели, выдаст ссылку и QR-код.
          На телефонах зрителей откроется ML-дашборд в реальном времени — тот же, что в предпросмотре ниже.</p>
        <button type="button" className="btn accent" onClick={create} disabled={busy}>
          {busy ? 'Готовим (считаем модели)…' : '▶ Запустить демо'}
        </button>
      </section>
      {sessions.length ? (
        <section className="dc-card dc-start">
          <h2>Активные сессии</h2>
          {sessions.map((s) => (
            <div key={s.id} className="dc-row">
              <span className="dc-note" style={{ flex: 1 }}><b>{s.title}</b> · этап {s.stage} · запуск №{s.run}</span>
              <button type="button" className="btn" onClick={() => onOpen(s.id)}>Открыть</button>
            </div>
          ))}
        </section>
      ) : null}
    </>
  );
}

function Controls({ sid, state, act, setError }) {
  const { control, view } = state;
  const [cause, setCause] = useState('');
  const [note, setNote] = useState('');
  const [changing, setChanging] = useState(false);
  const [draft, setDraft] = useState(null);
  const cfg = draft || control.settings;
  const stage = view.stage.key;
  const alternatives = control.causes.filter((c) => c.key !== control.model_cause);

  const applySettings = () => act(() => call(`/sessions/${sid}/settings`, 'PUT', draft)).then(() => setDraft(null));
  const savePdf = async () => {
    try { await saveBlob(await request(`/api/demo/sessions/${sid}/report.pdf`, { raw: true }), 'SLA_DEMO_act.pdf'); } catch (e) { setError(e.message); }
  };

  return (
    <div className="dc-cols" style={{ gridTemplateColumns: 'minmax(0,1fr)' }}>
      <section className="dc-card">
        <h2>Сценарий <span className="tag info">{view.stage.label}{control.paused ? ' · пауза' : ''}</span></h2>
        <div className="dc-row">
          <button type="button" className="btn accent" disabled={!control.can.start}
            onClick={() => act(() => call(`/sessions/${sid}/control`, 'POST', { action: 'start' }))}>▶ Запуск</button>
          <button type="button" className="btn" disabled={!control.can.next}
            onClick={() => act(() => call(`/sessions/${sid}/control`, 'POST', { action: 'next' }))}>Следующий этап →</button>
        </div>
        <div className="dc-row">
          <button type="button" className="btn" disabled={!(control.paused ? control.can.resume : control.can.pause)}
            onClick={() => act(() => call(`/sessions/${sid}/control`, 'POST', { action: control.paused ? 'resume' : 'pause' }))}>
            {control.paused ? '⏵ Продолжить' : '⏸ Пауза'}</button>
          <button type="button" className="btn danger"
            onClick={() => { if (window.confirm('Сбросить демонстрацию в исходное состояние? Зрители увидят экран ожидания.')) act(() => call(`/sessions/${sid}/control`, 'POST', { action: 'reset' })); }}>
            ↺ Сброс</button>
        </div>
        <label className="dc-field">Перейти к этапу вручную
          <select value={stage} onChange={(e) => act(() => call(`/sessions/${sid}/control`, 'POST', { action: 'goto', stage: e.target.value }))}>
            {STAGE_NAMES.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </label>
        {control.awaiting_operator ? (
          <p className="dc-note">Автоматика и «Следующий этап» остановлены: нужно ваше решение по вердикту модели.</p>
        ) : null}
      </section>

      <section className={control.can.decide ? 'dc-card dc-await' : 'dc-card'}>
        <h2>Решение оператора</h2>
        {control.can.decide ? (
          <>
            <p className="dc-note">Модель предполагает: <b>{control.causes.find((c) => c.key === control.model_cause)?.label}</b>.
              Зрители видят «Ожидает решения оператора».</p>
            {!changing ? (
              <div className="dc-row">
                <button type="button" className="btn accent"
                  onClick={() => act(() => call(`/sessions/${sid}/verdict`, 'POST', { decision: 'confirm', note }))}>Подтвердить вердикт</button>
                <button type="button" className="btn" onClick={() => { setChanging(true); setCause(alternatives[0]?.key || ''); }}>Изменить…</button>
              </div>
            ) : (
              <>
                <label className="dc-field">Другой источник
                  <select value={cause} onChange={(e) => setCause(e.target.value)}>
                    {alternatives.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
                  </select>
                </label>
                <div className="dc-row">
                  <button type="button" className="btn accent"
                    onClick={() => act(() => call(`/sessions/${sid}/verdict`, 'POST', { decision: 'change', cause, note })).then(() => setChanging(false))}>
                    Сохранить изменённый вердикт</button>
                  <button type="button" className="btn" onClick={() => setChanging(false)}>Отмена</button>
                </div>
              </>
            )}
            <label className="dc-field">Заметка (только в журнал, зрителям не показывается)
              <textarea rows={2} maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} />
            </label>
          </>
        ) : (
          <p className="dc-note">{view.decision
            ? `Принято: ${view.decision.kind === 'change' ? 'изменено на' : 'подтверждено'} «${view.decision.label}».`
            : 'Появится на этапе operator_review.'}</p>
        )}
      </section>

      <section className="dc-card">
        <h2>PDF-акт и обращение</h2>
        <div className="dc-row">
          <button type="button" className="btn accent" disabled={!control.can.report}
            onClick={() => act(() => call(`/sessions/${sid}/report`, 'POST'))}>Сформировать акт</button>
          <button type="button" className="btn" disabled={!view.report} onClick={savePdf}>Скачать PDF</button>
        </div>
        <p className="dc-note">{view.report ? 'Акт сформирован; зрители видят ссылку.' : 'Доступно после решения оператора.'}</p>
      </section>

      <section className="dc-card">
        <h2>Автоматический режим</h2>
        <label className="dc-field">Режим
          <select value={cfg.mode} onChange={(e) => setDraft({ ...cfg, mode: e.target.value })}>
            <option value="manual">Ручной</option>
            <option value="auto">Автоматический (по таймеру)</option>
          </select>
        </label>
        <div className="dc-intervals">
          {INTERVAL_STAGES.map(([key, label]) => (
            <label key={key} className="dc-field">{label}, с
              <input type="number" min="0" max="600" value={cfg.intervals[key] ?? 0}
                onChange={(e) => setDraft({ ...cfg, intervals: { ...cfg.intervals, [key]: Number(e.target.value) } })} />
            </label>
          ))}
          <label className="dc-field">Шаг диагностики, с
            <input type="number" min="1" max="30" value={cfg.diag_step_sec}
              onChange={(e) => setDraft({ ...cfg, diag_step_sec: Number(e.target.value) })} />
          </label>
        </div>
        <p className="dc-note">0 — ждать ведущего. Этап решения оператора таймером не проходится никогда.</p>
        <button type="button" className="btn" disabled={!draft} onClick={applySettings}>Применить</button>
      </section>
    </div>
  );
}

function LinkPanel({ sid, link, setLink, control, fail }) {
  const [copied, setCopied] = useState(false);
  const [publicUrl, setPublicUrl] = useState(() => (link ? new URL(link.viewer_url).origin : window.location.origin));
  const refresh = async () => {
    try { setLink(await call(`/sessions/${sid}/link`, 'POST', { public_url: publicUrl })); } catch (e) { fail(e); }
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(link.viewer_url); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* нет доступа к буферу */ }
  };
  const local = link && isLocal(hostOf(link.viewer_url));
  return (
    <section className="dc-card dc-link">
      <h2>Ссылка и QR-код для зрителей</h2>
      <div className="dc-stats">
        <div className="dc-stat"><span className="eyebrow">Зрителей</span><b>{control.viewers}</b></div>
        <div className="dc-stat"><span className="eyebrow">Хранилище</span><b>{control.backend === 'redis' ? 'Redis' : 'Память'}</b></div>
        <div className="dc-stat"><span className="eyebrow">Запуск №</span><b>{control.run}</b></div>
      </div>
      {link ? (
        <>
          {link.qr_svg
            ? <div className="dc-qr" role="img" aria-label="QR-код ссылки для зрителей" dangerouslySetInnerHTML={{ __html: link.qr_svg }} />
            : <div className="dc-warn">QR не построен (нет библиотеки segno) — используйте ссылку.</div>}
          <input readOnly value={link.viewer_url} onFocus={(e) => e.target.select()} aria-label="Ссылка для зрителей" />
          <div className="dc-row">
            <button type="button" className="btn" onClick={copy}>{copied ? 'Скопировано' : 'Копировать ссылку'}</button>
            <a className="btn" href={link.viewer_url} target="_blank" rel="noreferrer">Открыть как зритель</a>
          </div>
          <p className="dc-note">Ссылка действует до {new Date(link.expires_at).toLocaleTimeString('ru-RU')} и открывает только эту сессию. Токен не показывается зрителям.</p>
          {local ? <div className="dc-warn">В ссылке «localhost»: телефоны её не откроют. Укажите адрес ниже.</div> : null}
        </>
      ) : <p className="dc-note">Нажмите «Обновить ссылку», чтобы получить новый QR-код.</p>}
      <label className="dc-field">Адрес для зрителей
        <input value={publicUrl} onChange={(e) => setPublicUrl(e.target.value)} />
      </label>
      <button type="button" className="btn" onClick={refresh}>Обновить ссылку и QR</button>
    </section>
  );
}

function Panel() {
  const [sid, setSid] = useState(null);
  const [state, setState] = useState(null);
  const [link, setLink] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [error, setError] = useState('');
  const timer = useRef(null);

  const fail = useCallback((e) => {
    // 401 разбирает сам api.js: он шлёт vko:unauthorized и приложение возвращает на вход.
    if (!(e instanceof ApiError && e.status === 401)) setError(e.message || 'Ошибка запроса');
  }, []);

  const refreshList = useCallback(() => call('/sessions').then(setSessions).catch(fail), [fail]);
  useEffect(() => { if (!sid) refreshList(); }, [sid, refreshList]);

  // Панель опрашивает состояние: это её «зеркало» того, что видят зрители.
  useEffect(() => {
    if (!sid) return undefined;
    let alive = true;
    const tick = () => call(`/sessions/${sid}`).then((s) => { if (alive) { setState(s); setError(''); } })
      .catch((e) => { if (alive) { if (e.status === 404) { setSid(null); setState(null); } else fail(e); } });
    tick();
    timer.current = setInterval(tick, 1500);
    return () => { alive = false; clearInterval(timer.current); };
  }, [sid, fail]);

  const act = useCallback(async (fn) => {
    setError('');
    try { setState(await fn()); } catch (e) { fail(e); }
  }, [fail]);

  const open = async (id) => {
    setSid(id);
    setState(null);
    try { setLink(await call(`/sessions/${id}/link`, 'POST', { public_url: window.location.origin })); } catch (e) { fail(e); }
  };
  const close = async () => {
    if (!window.confirm('Закрыть сессию? Ссылки зрителей перестанут работать.')) return;
    try { await call(`/sessions/${sid}`, 'DELETE'); setSid(null); setState(null); setLink(null); } catch (e) { fail(e); }
  };

  return (
    <div className="page dc-page">
      {sid ? (
        <div className="dc-row">
          <button type="button" className="btn danger" onClick={close}>Закрыть сессию</button>
          <button type="button" className="btn" onClick={() => { setSid(null); setState(null); }}>К списку сессий</button>
        </div>
      ) : null}
      {error ? <div className="dc-err" role="alert">{error}</div> : null}
        {!sid ? (
          <Setup sessions={sessions} fail={fail} onOpen={open}
            onCreated={(data) => { setLink(data.link); setState(data.state); setSid(data.session_id); }} />
        ) : !state ? <p className="dc-note">Загрузка…</p> : (
          <div className="dc-cols">
            <div style={{ display: 'grid', gap: 16 }}>
              <Controls sid={sid} state={state} act={act} setError={setError} />
              <LinkPanel sid={sid} link={link} setLink={setLink} control={state.control} fail={fail} />
            </div>
            <section aria-label="Предварительный просмотр">
              <h2 style={{ margin: '0 0 8px' }}>Предпросмотр: ML-дашборд на телефонах зрителей</h2>
              <div className="dc-preview"><LiveView view={state.view} conn="live" /></div>
            </section>
          </div>
        )}
    </div>
  );
}

export default function DemoPanel({ role }) {
  const [enabled, setEnabled] = useState(null);
  useEffect(() => { request('/api/demo/config').then(() => setEnabled(true)).catch(() => setEnabled(false)); }, []);

  if (enabled === null) return <div className="page dc-page"><p className="dc-note">Загрузка…</p></div>;
  if (!enabled) {
    return (
      <div className="page dc-page"><section className="dc-card dc-start">
        <h2>Демонстрационный режим отключён</h2>
        <p className="dc-note">На сервере не включён DEMO_MODE.</p>
      </section></div>
    );
  }
  if (role !== 'operator' && role !== 'admin') {
    return (
      <div className="page dc-page"><section className="dc-card dc-start">
        <h2>Доступ запрещён</h2>
        <p className="dc-note">Демонстрацию запускают только роли operator и admin.</p>
      </section></div>
    );
  }
  return <Panel />;
}
