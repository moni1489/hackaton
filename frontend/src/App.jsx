/* Оболочка панели: карта области, карточка школы и ПК-уровень прослеживания. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, clearSession, getToken, getUser, setAsOf } from './api';
import { Spark } from './Charts';
import DeviceDrawer from './DeviceDrawer';
import Login from './Login';
import MapView from './MapView';
import DemoPanel from './DemoControl';
import DiagnosticsPage from './Diagnostics';
import PublicDataPage from './PublicData';
import RatingPage from './Rating';
import { AdminPage, DevicesPage, IncidentsPage, SchoolsPage } from './Pages';
import Rail from './Rail';
import SchoolDrawer from './SchoolDrawer';
import {
  IcoAlert, IcoBell, IcoCal, IcoChevron, IcoClock, IcoDown, IcoGear, IcoLayers,
  IcoLogout, IcoMap, IcoNodes, IcoPc, IcoPulse, IcoSchool, IcoSearch, IcoTrend,
} from './icons';
import { STATUS, fmtAge, fmtStamp } from './ui';

const NAV = [
  { key: 'map', label: 'Карта области', Icon: IcoMap },
  { key: 'schools', label: 'Школы', Icon: IcoSchool },
  { key: 'rating', label: 'Рейтинг', Icon: IcoTrend },
  { key: 'devices', label: 'ПК-агенты', Icon: IcoPc },
  { key: 'diagnostics', label: 'Диагностика', Icon: IcoNodes },
  { key: 'public-data', label: 'Открытые данные', Icon: IcoLayers },
  { key: 'incidents', label: 'Инциденты', Icon: IcoAlert },
  { key: 'admin', label: 'Управление', Icon: IcoGear },
  { key: 'demo', label: 'Демонстрация', Icon: IcoPulse },
];
const OPERATOR_ONLY = ['admin', 'demo'];

const TITLES = {
  'public-data': 'Открытые данные и внешняя проверка',
  map: 'Мониторинг', schools: 'Школы', rating: 'Рейтинг организаций', devices: 'ПК-агенты',
  diagnostics: 'Диагностика · кто виноват', incidents: 'Инциденты', admin: 'Управление',
  demo: 'Демонстрация для зала',
};

const ROLE_LABEL = {
  admin: 'Администратор', operator: 'Оператор',
  school: 'Ответственный школы', provider: 'Поставщик связи', district: 'Районный отдел',
};

export default function App() {
  const [authed, setAuthed] = useState(Boolean(getToken()));
  const [user, setUser] = useState(getUser());

  useEffect(() => {
    const drop = () => { setAuthed(false); setUser(null); };
    window.addEventListener('vko:unauthorized', drop);
    return () => window.removeEventListener('vko:unauthorized', drop);
  }, []);

  if (!authed) {
    return <Login onSuccess={() => { setAuthed(true); setUser(getUser()); }} />;
  }
  return <Dashboard user={user} onLogout={() => { clearSession(); setAuthed(false); }} />;
}

function Dashboard({ user, onLogout }) {
  // /demo открывает то же приложение сразу на вкладке демонстрации.
  const [view, setView] = useState(() => (window.location.pathname === '/demo' ? 'demo' : 'map'));
  const [overview, setOverview] = useState(null);
  const [schools, setSchools] = useState([]);
  const [incidents, setIncidents] = useState([]);
  const [trend, setTrend] = useState([]);

  const [region, setRegion] = useState('Все районы');
  const [provider, setProvider] = useState('Все провайдеры');
  const [status, setStatus] = useState('Все статусы');
  const [search, setSearch] = useState('');
  const [mapMode, setMapMode] = useState('points');
  const [slaLayer, setSlaLayer] = useState(false);
  const [mapCollapsed, setMapCollapsed] = useState(false);
  const [navCollapsed, setNavCollapsed] = useState(false);

  const [demo, setDemo] = useState(false);   // режим демонстрации истории
  const [selectedId, setSelectedId] = useState(null);
  const [schoolDrawer, setSchoolDrawer] = useState(null);
  const [deviceDrawer, setDeviceDrawer] = useState(null);
  const searchRef = useRef(null);

  const loadCore = useCallback(async () => {
    const [ov, inc, tr] = await Promise.all([
      api.overview().catch(() => null),
      api.incidents().catch(() => []),
      api.trend(24).catch(() => []),
    ]);
    setOverview(ov); setIncidents(inc); setTrend(tr);
  }, []);

  useEffect(() => { loadCore(); }, [loadCore, demo]);

  useEffect(() => {
    api.schools({ region, provider, status, search }).then(setSchools).catch(() => setSchools([]));
  }, [region, provider, status, search, demo]);

  // ⌘K / Ctrl+K — фокус в поиск
  useEffect(() => {
    const onKey = (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        searchRef.current?.focus();
      }
      if (event.key === 'Escape') { setSchoolDrawer(null); setDeviceDrawer(null); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const counts = overview?.status_counts || {};
  const fresh = overview?.freshness;
  // Без свежих замеров средние и «доля нормы» не показываем: 0 выглядел бы как показатель.
  const withData = (overview?.total_schools || 0) - (counts.no_data || 0);
  const healthy = withData > 0 ? Math.round(100 * (counts.normal || 0) / withData) : null;
  const val = (v) => (withData > 0 ? v : '—');

  const toggleDemo = () => {
    if (demo) { setAsOf(null); setDemo(false); return; }
    const anchor = overview?.freshness?.last_measurement;   // конец истории в базе
    if (anchor) { setAsOf(anchor); setDemo(true); }
  };
  const selected = schools.find((s) => s.id === selectedId);

  const sparks = useMemo(() => ({
    download: trend.map((point) => point.download),
    ping: trend.map((point) => point.ping),
  }), [trend]);

  const openSchool = (id) => { setSelectedId(id); setSchoolDrawer(id); };

  return (
    <div className={`shell ${navCollapsed ? 'nav-min' : ''}`}>
      {/* ---------- Боковая навигация ---------- */}
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <img src="/logos/akimat.png" alt="Акимат ВКО" />
          </div>
          <div className="brand-text">
            <div className="brand-name">САМ ВКО</div>
            <div className="brand-sub">МОНИТОРИНГ СВЯЗИ</div>
          </div>
        </div>

        <button className="nav-collapse" onClick={() => setNavCollapsed((v) => !v)}
          aria-expanded={!navCollapsed}
          title={navCollapsed ? 'Развернуть меню' : 'Свернуть меню'}>
          <IcoChevron style={{ transform: `rotate(${navCollapsed ? -90 : 90}deg)` }} />
          <span>Свернуть меню</span>
        </button>

        <nav className="nav">
          {NAV.filter(({ key }) => !OPERATOR_ONLY.includes(key) || ['admin', 'operator'].includes(user?.role))
            .map(({ key, label, Icon }) => (
            <button key={key} className={`nav-item ${view === key ? 'active' : ''}`}
              title={label} onClick={() => setView(key)}>
              <Icon size={17} /><span>{label}</span>
              {key === 'incidents' && incidents.length
                ? <span className="nav-badge">{incidents.length}</span> : null}
            </button>
          ))}
        </nav>

        <div className="sidebar-foot">
          <div className="partner-logos">
            <img src="/logos/akimat.png" alt="Акимат ВКО" title="Акимат ВКО" />
            <img src="/logos/oskemen.png" alt="Oskemen Hub" title="Oskemen Hub" />
            <img src="/logos/uct.png" alt="Цифровой ВКО" title="Цифровой ВКО" />
          </div>
          <div className="user-chip">
            <div className="avatar">{(user?.full_name || '?').trim()[0]}</div>
            <div className="user-meta">
              <div className="user-name">{(user?.full_name || '').split(':')[0]}</div>
              <div className="user-role">{ROLE_LABEL[user?.role] || user?.role}</div>
            </div>
            <button className="logout-btn" onClick={onLogout} title="Выйти">
              <IcoLogout size={16} />
            </button>
          </div>
        </div>
      </aside>

      {/* ---------- Основная область ---------- */}
      <div className="main" key={demo ? 'history' : 'live'}>
        <header className="topbar">
          <h1>{TITLES[view]}</h1>
          <span className="vdiv" />
          <span className={`sys-state ${fresh?.is_stale ? 'stale' : ''}`}
            title={fresh?.last_measurement ? `Последний замер: ${fmtStamp(fresh.last_measurement)}` : ''}>
            <i className={`dot ${fresh && !fresh.is_stale ? 'pulse' : ''}`}
              style={fresh?.is_stale ? { background: 'var(--warn)' } : undefined} />
            <span className="eyebrow">
              {!fresh ? 'Загрузка…'
                : demo ? `Демо истории · ${fmtStamp(fresh.as_of)}`
                  : fresh.is_stale ? `Нет свежих данных · замер ${fmtStamp(fresh.last_measurement)}`
                    : `Замер ${fmtAge(fresh.age_min)} назад`}
            </span>
          </span>
          <button className={`pill-toggle ${demo ? 'on' : ''}`} onClick={toggleDemo}
            disabled={!demo && !fresh?.last_measurement}
            title="Показать состояние на момент последнего замера в базе. Это не текущие данные.">
            <IcoClock size={15} />Демо истории
          </button>

          {['admin', 'operator'].includes(user?.role) ? (
            <button className="btn accent" style={{ flex: 'none' }} onClick={() => setView('demo')}
              title="Создать сессию, показать QR-код залу и вести сценарий">
              ▶ Запустить демо
            </button>
          ) : null}

          <div className="search">
            <IcoSearch size={15} style={{ color: 'var(--ink-3)' }} />
            <input ref={searchRef} placeholder="Поиск школы или кода…" value={search}
              onChange={(e) => setSearch(e.target.value)} />
            <span className="kbd">⌘K</span>
          </div>
          <button className="icon-btn" title="Уведомления">
            <IcoBell size={18} />
            {incidents.length ? <i className="pip" /> : null}
          </button>
        </header>

        {demo ? (
          <div className="banner demo">
            <span>
              <b>Режим демонстрации истории.</b> Показано состояние на {fmtStamp(fresh?.as_of)};
              это не текущие данные.
            </span>
            <button className="btn" onClick={toggleDemo}>Выйти из демо</button>
          </div>
        ) : fresh?.is_stale ? (
          <div className="banner stale">
            <span>
              <b>Нет свежих данных.</b> Последний замер: {fmtStamp(fresh.last_measurement)}
              {' '}({fmtAge(fresh.age_min)} назад). Статусы школ не подтверждены; пустые списки и
              графики не означают, что связь в норме.
            </span>
            <button className="btn" onClick={toggleDemo} disabled={!fresh.last_measurement}>
              Показать историю (демо)
            </button>
          </div>
        ) : null}

        {view === 'map' ? (
          <>
            <div className="toolbar">
              <div className="tool-select">
                <IcoMap size={16} />
                <select value={region} onChange={(e) => setRegion(e.target.value)}>
                  {(overview?.filters?.regions || ['Все районы']).map((item) => (
                    <option key={item}>{item}</option>
                  ))}
                </select>
                <IcoChevron className="chev" />
              </div>

              <div className="tool-select">
                <IcoLayers size={16} />
                <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                  {(overview?.filters?.providers || ['Все провайдеры']).map((item) => (
                    <option key={item}>{item}</option>
                  ))}
                </select>
                <IcoChevron className="chev" />
              </div>

              <span className="vdiv" />

              <div className="segmented">
                {[['points', 'Точки'], ['satellite', 'Спутник']].map(([key, label]) => (
                  <button key={key} className={mapMode === key ? 'active' : ''}
                    onClick={() => setMapMode(key)}>{label}</button>
                ))}
              </div>

              <button className={`pill-toggle ${slaLayer ? 'on' : ''}`}
                onClick={() => { setSlaLayer(!slaLayer); setStatus(slaLayer ? 'Все статусы' : 'Нестабильно'); }}>
                <IcoPulse size={15} />SLA-фокус
              </button>

              <button className={`pill-toggle ${mapCollapsed ? 'on' : ''}`}
                onClick={() => setMapCollapsed((v) => !v)}
                title={mapCollapsed ? 'Показать карту' : 'Свернуть карту'}>
                <IcoChevron style={{ transform: `rotate(${mapCollapsed ? -90 : 90}deg)` }} />
                {mapCollapsed ? 'Показать карту' : 'Свернуть карту'}
              </button>

              <div className="tool-right">
                <div className="chip">
                  <IcoCal size={15} style={{ color: 'var(--ink-3)' }} />
                  <span className="chip-value" style={{ fontSize: 12.5 }}>
                    {new Date().toLocaleDateString('ru-RU', { weekday: 'short', day: 'numeric', month: 'short' })}
                  </span>
                </div>
                <div className="chip">
                  <IcoDown size={15} style={{ color: 'var(--accent)' }} />
                  <div>
                    <div className="chip-label">Средняя ↓</div>
                    <div className="chip-value">{val(overview?.avg_download) ?? '—'} <small>Мбит/с</small></div>
                  </div>
                </div>
                <div className="chip">
                  <IcoClock size={15} style={{ color: 'var(--warn)' }} />
                  <div>
                    <div className="chip-label">Задержка</div>
                    <div className="chip-value">{val(overview?.avg_ping) ?? '—'} <small>мс</small></div>
                  </div>
                </div>
                <div className="mini-stats">
                  {[['Норма', counts.normal, '#17A65B'], ['Нестаб.', counts.unstable, '#E4962A'],
                    ['Авария', (counts.critical || 0) + (counts.offline || 0), '#E0453E'],
                    ['Нет данных', counts.no_data, '#8492A6']].map(
                    ([label, value, color]) => (
                      <div className="mini-stat" key={label}>
                        <div className="k">{label}</div>
                        <div className="v" style={{ color }}>{value ?? '—'}</div>
                      </div>
                    ))}
                </div>
              </div>
            </div>

            <div className="kpi-strip">
              <Kpi label="Организаций" value={overview?.total_schools ?? '—'}
                sub="в системе мониторинга"
                badge={healthy === null ? { text: 'нет данных', kind: 'flat' }
                  : { text: `${healthy}% норма`, kind: healthy >= 70 ? 'up' : 'down' }} />
              <Kpi label="ПК-агентов" value={overview?.total_devices ?? '—'}
                sub={`в сети ${overview?.devices_online ?? 0}`}
                badge={{ text: 'ПК-уровень', kind: 'flat' }} />
              <Kpi label="Средняя скорость" value={val(overview?.avg_download) ?? '—'} unit="Мбит/с"
                sub="основные линии, свежие замеры" spark={sparks.download} sparkColor="#2F6BF6"
                badge={withData > 0 ? { text: `↑ ${overview?.avg_upload ?? 0}`, kind: 'up' }
                  : { text: 'нет данных', kind: 'flat' }} />
              <Kpi label="Задержка" value={val(overview?.avg_ping) ?? '—'} unit="мс"
                sub={withData > 0 ? `потери ${overview?.avg_loss ?? 0}%` : 'нет свежих замеров'}
                spark={sparks.ping} sparkColor="#D9730D"
                badge={withData <= 0 ? { text: 'нет данных', kind: 'flat' }
                  : { text: (overview?.avg_ping ?? 0) <= 100 ? 'в норме' : 'выше порога',
                      kind: (overview?.avg_ping ?? 0) <= 100 ? 'up' : 'down' }} />
              <Kpi label="Инцидентов" value={overview?.active_incidents ?? '—'}
                sub="требуют устранения"
                badge={{ text: 'SLA', kind: (overview?.active_incidents ?? 0) ? 'down' : 'up' }} />
            </div>

            <div className={`work ${mapCollapsed ? 'no-map' : ''}`}>
              {mapCollapsed ? null : (
                <div className="stage">
                  <MapView schools={schools} mode={mapMode} selectedId={selectedId}
                    onSelect={setSelectedId} onOpenSchool={openSchool} />

                  <div className="float bl legend-card">
                    <div className="eyebrow">Статус канала</div>
                    {Object.entries(STATUS).map(([label, meta]) => (
                      <div className="legend-row" key={label}>
                        <span className="legend-dot" style={{ background: meta.color }} />
                        {label}
                      </div>
                    ))}
                  </div>

                  <div className="float bc">
                    <button className="fab" disabled={!selected}
                      onClick={() => selected && openSchool(selected.id)}>
                      <IcoSchool size={16} />
                      {selected ? `Карточка: ${selected.name.slice(0, 34)}` : 'Выберите школу на карте'}
                    </button>
                  </div>
                </div>
              )}

              <Rail overview={overview} incidents={incidents} schools={schools}
                onOpenSchool={openSchool} onOpenDevice={setDeviceDrawer} onReload={loadCore} />
            </div>
          </>
        ) : null}

        {view === 'schools' ? <SchoolsPage schools={schools} onOpenSchool={openSchool} /> : null}
        {view === 'rating' ? <RatingPage onOpenSchool={openSchool} /> : null}
        {view === 'public-data' ? <PublicDataPage /> : null}
        {view === 'devices' ? <DevicesPage onOpenDevice={setDeviceDrawer} /> : null}
        {view === 'diagnostics' ? (
          <DiagnosticsPage role={user?.role} onOpenSchool={openSchool} />
        ) : null}
        {view === 'incidents' ? (
          <IncidentsPage incidents={incidents} role={user?.role}
            onOpenSchool={openSchool} onReload={loadCore} />
        ) : null}
        {view === 'admin' ? <AdminPage role={user?.role} /> : null}
        {view === 'demo' ? <DemoPanel role={user?.role} /> : null}
      </div>

      {schoolDrawer ? (
        <SchoolDrawer key={demo ? 'history' : 'live'} schoolId={schoolDrawer} role={user?.role}
          onOpenDevice={(id) => { setSchoolDrawer(null); setDeviceDrawer(id); }}
          onClose={() => setSchoolDrawer(null)} />
      ) : null}

      {deviceDrawer ? (
        <DeviceDrawer key={demo ? 'history' : 'live'} deviceId={deviceDrawer} role={user?.role}
          onClose={() => setDeviceDrawer(null)} />
      ) : null}
    </div>
  );
}

function Kpi({ label, value, unit, sub, badge, spark, sparkColor }) {
  return (
    <div className="kpi">
      <div className="kpi-head">
        <span className="eyebrow">{label}</span>
        {badge ? <span className={`delta ${badge.kind}`}>{badge.text}</span> : null}
      </div>
      <div className="kpi-value">
        <b>{value}</b>
        {unit ? <small>{unit}</small> : null}
      </div>
      <div className="eyebrow" style={{ marginTop: 4, letterSpacing: '.02em', fontWeight: 600 }}>
        {sub}
      </div>
      {spark?.length ? (
        <div className="kpi-spark"><Spark points={spark} color={sparkColor} /></div>
      ) : null}
    </div>
  );
}
