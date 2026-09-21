/* Панель ведущего демонстрации. Маршрут /demo. Доступ — только operator/admin.
   Все действия идут через API и попадают в журнал аудита; зрители получают изменение с сервера. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, api, clearSession, getToken, getUser, request } from './api';
import { LiveView } from './Live';
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

function OperatorLogin({ onDone }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api.login(email.trim(), password);
      onDone();
    } catch (e) { setError(e.status === 401 ? 'Неверный логин или пароль' : e.message); } finally { setBusy(false); }
  };
  // Намеренно без подсказок с учётными записями: страница доступна всему залу.
  return (
    <form className="dc-card dc-login" onSubmit={submit}>
      <h2>Панель ведущего</h2>
      <p className="dc-note">Вход только для ролей operator и admin.</p>
      {error ? <div className="dc-err">{error}</div> : null}
      <label className="dc-field">Электронная почта
        <input type="email" value={email} autoComplete="username" onChange={(e) => setEmail(e.target.value)} required />
      </label>
      <label className="dc-field">Пароль
        <input type="password" value={password} autoComplete="current-password"
          onChange={(e) => setPassword(e.target.value)} required />
      </label>
      <button type="submit" className="btn accent" disabled={busy}>{busy ? 'Проверка…' : 'Войти'}</button>
    </form>
  );
}

function Setup({ onCreated, onOpen, sessions, fail }) {
  const [title, setTitle] = useState('Демонстрация САМ ВКО');
  const [mode, setMode] = useState('manual');
  const [publicUrl, setPublicUrl] = useState(window.location.origin);
  const [busy, setBusy] = useState(false);
  const create = async () => {
    setBusy(true);
    try { onCreated(await call('/sessions', 'POST', { title, mode, public_url: publicUrl })); } catch (e) { fail(e); } finally { setBusy(false); }
  };
  return (
    <div className="dc-cols">
      <section className="dc-card">
        <h2>Новая демонстрационная сессия</h2>
        <label className="dc-field">Название
          <input value={title} maxLength={80} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="dc-field">Режим переходов
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="manual">Ручной: этапы переключает ведущий</option>
            <option value="auto">Автоматический: по таймеру (интервалы — в панели сессии)</option>
          </select>
        </label>
        <label className="dc-field">Адрес для зрителей (попадёт в QR-код)
          <input value={publicUrl} onChange={(e) => setPublicUrl(e.target.value)} placeholder="http://192.168.1.20:8000" />
        </label>
        {isLocal(window.location.hostname) && isLocal(hostOf(publicUrl)) ? (
          <div className="dc-warn">Телефоны не откроют «localhost». Укажите адрес компьютера в сети зала
            (его печатает <code>python demo.py up</code>) или публичный адрес туннеля.</div>
        ) : null}
        <button type="button" className="btn accent" onClick={create} disabled={busy}>
          {busy ? 'Создаём (считаем модели один раз)…' : 'Создать сессию'}
        </button>
      </section>
      <section className="dc-card">
        <h2>Активные сессии</h2>
        {sessions.length === 0 ? <p className="dc-note">Активных сессий нет.</p> : sessions.map((s) => (
          <div key={s.id} className="dc-row">
            <span className="dc-note" style={{ flex: 1 }}><b>{s.title}</b> · этап {s.stage} · запуск №{s.run}</span>
            <button type="button" className="btn" onClick={() => onOpen(s.id)}>Открыть</button>
          </div>
        ))}
      </section>
    </div>
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
        <h2>Сценарий <span className="lv-tag">{view.stage.label}{control.paused ? ' · пауза' : ''}</span></h2>
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

function Panel({ initial, onLogout }) {
  const [sid, setSid] = useState(initial?.session_id || null);
  const [state, setState] = useState(initial?.state || null);
  const [link, setLink] = useState(initial?.link || null);
  const [sessions, setSessions] = useState([]);
  const [error, setError] = useState('');
  const timer = useRef(null);

  const fail = useCallback((e) => {
    if (e instanceof ApiError && e.status === 401) onLogout();
    else setError(e.message || 'Ошибка запроса');
  }, [onLogout]);

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
    <div className="dc">
      <header className="dc-top">
        <h1>Панель ведущего · демонстрация</h1>
        <div className="dc-row">
          {sid ? <button type="button" className="btn danger" onClick={close}>Закрыть сессию</button> : null}
          {sid ? <button type="button" className="btn" onClick={() => { setSid(null); setState(null); }}>К списку сессий</button> : null}
          <button type="button" className="btn" onClick={onLogout}>Выйти</button>
        </div>
      </header>
      <div className="dc-wrap">
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
              <h2 style={{ margin: '0 0 8px' }}>Предварительный просмотр: то, что видят зрители</h2>
              <div className="dc-preview"><LiveView view={state.view} conn="live" /></div>
            </section>
          </div>
        )}
      </div>
    </div>
  );
}

export default function DemoControl() {
  const [authed, setAuthed] = useState(Boolean(getToken()));
  const [enabled, setEnabled] = useState(null);
  useEffect(() => { document.title = 'Панель ведущего · САМ ВКО'; }, []);
  useEffect(() => {
    request('/api/demo/config').then(() => setEnabled(true)).catch(() => setEnabled(false));
    const drop = () => setAuthed(false);
    window.addEventListener('vko:unauthorized', drop);
    return () => window.removeEventListener('vko:unauthorized', drop);
  }, []);

  const role = getUser()?.role;
  const logout = () => { clearSession(); setAuthed(false); };

  if (enabled === null) return null;
  if (!enabled) {
    return (
      <div className="dc"><div className="dc-wrap"><section className="dc-card dc-login">
        <h2>Демонстрационный режим отключён</h2>
        <p className="dc-note">На сервере не включён DEMO_MODE.</p>
      </section></div></div>
    );
  }
  if (!authed) return <div className="dc"><div className="dc-wrap"><OperatorLogin onDone={() => setAuthed(true)} /></div></div>;
  if (role !== 'operator' && role !== 'admin') {
    return (
      <div className="dc"><div className="dc-wrap"><section className="dc-card dc-login">
        <h2>Доступ запрещён</h2>
        <p className="dc-note">Панель доступна только ролям operator и admin.</p>
        <button type="button" className="btn" onClick={logout}>Выйти</button>
      </section></div></div>
    );
  }
  return <Panel onLogout={logout} />;
}
