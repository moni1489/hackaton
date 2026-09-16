/* Оболочка панели: карта области, карточка школы и ПК-уровень прослеживания. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, clearSession, getToken, getUser } from './api';
import { Spark } from './Charts';
import DeviceDrawer from './DeviceDrawer';
import Login from './Login';
import MapView from './MapView';
import { AdminPage, DevicesPage, IncidentsPage, SchoolsPage } from './Pages';
import Rail from './Rail';
import SchoolDrawer from './SchoolDrawer';
import {
  IcoAlert, IcoBell, IcoCal, IcoChevron, IcoClock, IcoDown, IcoGear, IcoLayers, IcoLeaf,
  IcoLogout, IcoMap, IcoPc, IcoPulse, IcoSchool, IcoSearch, IcoShield,
} from './icons';
import { Bar, Tag } from './ui';

const NAV = [
  { key: 'map', label: 'Карта области', Icon: IcoMap },
  { key: 'schools', label: 'Школы', Icon: IcoSchool },
  { key: 'devices', label: 'ПК-агенты', Icon: IcoPc },
  { key: 'incidents', label: 'Инциденты', Icon: IcoAlert },
  { key: 'admin', label: 'Управление', Icon: IcoGear },
];

const TITLES = {
  map: 'Мониторинг', schools: 'Школы', devices: 'ПК-агенты',
  incidents: 'Инциденты', admin: 'Управление',
};

const ROLE_LABEL = {
  admin: 'Администратор', operator: 'Оператор',
  school: 'Ответственный школы', provider: 'Поставщик связи',
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
  const [view, setView] = useState('map');
  const [overview, setOverview] = useState(null);
  const [schools, setSchools] = useState([]);
  const [incidents, setIncidents] = useState([]);
  const [trend, setTrend] = useState([]);

  const [region, setRegion] = useState('Все районы');
  const [provider, setProvider] = useState('Все провайдеры');
  const [status, setStatus] = useState('Все статусы');
  const [search, setSearch] = useState('');
  const [mapMode, setMapMode] = useState('heat');
  const [slaLayer, setSlaLayer] = useState(false);

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

  useEffect(() => { loadCore(); }, [loadCore]);

  useEffect(() => {
    api.schools({ region, provider, status, search }).then(setSchools).catch(() => setSchools([]));
  }, [region, provider, status, search]);

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
  const total = overview?.total_schools || 1;
  const healthy = Math.round(100 * (counts.normal || 0) / total);
  const selected = schools.find((s) => s.id === selectedId);

  const sparks = useMemo(() => ({
    download: trend.map((point) => point.download),
    ping: trend.map((point) => point.ping),
  }), [trend]);

  const openSchool = (id) => { setSelectedId(id); setSchoolDrawer(id); };

  return (
    <div className="shell">
      {/* ---------- Боковая навигация ---------- */}
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><IcoLeaf /></div>
          <div className="brand-text">
            <div className="brand-name">САМ ВКО</div>
            <div className="brand-sub">МОНИТОРИНГ СВЯЗИ</div>
          </div>
        </div>

        <nav className="nav">
          {NAV.map(({ key, label, Icon }) => (
            <button key={key} className={`nav-item ${view === key ? 'active' : ''}`}
              onClick={() => setView(key)}>
              <Icon size={17} /><span>{label}</span>
              {key === 'incidents' && incidents.length
                ? <span className="nav-badge">{incidents.length}</span> : null}
            </button>
          ))}
        </nav>

        <div className="sidebar-foot">
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
      <div className="main">
        <header className="topbar">
          <h1>{TITLES[view]}</h1>
          <span className="vdiv" />
          <span className="sys-state">
            <i className="dot pulse" />
            <span className="eyebrow">Система активна</span>
          </span>

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
                {[['heat', 'Хитмап'], ['points', 'Точки'], ['satellite', 'Спутник']].map(([key, label]) => (
                  <button key={key} className={mapMode === key ? 'active' : ''}
                    onClick={() => setMapMode(key)}>{label}</button>
                ))}
              </div>

              <button className={`pill-toggle ${slaLayer ? 'on' : ''}`}
                onClick={() => { setSlaLayer(!slaLayer); setStatus(slaLayer ? 'Все статусы' : 'Нестабильно'); }}>
                <IcoPulse size={15} />SLA-фокус
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
                    <div className="chip-value">{overview?.avg_download ?? '—'} <small>Мбит/с</small></div>
                  </div>
                </div>
                <div className="chip">
                  <IcoClock size={15} style={{ color: 'var(--warn)' }} />
                  <div>
                    <div className="chip-label">Задержка</div>
                    <div className="chip-value">{overview?.avg_ping ?? '—'} <small>мс</small></div>
                  </div>
                </div>
                <div className="mini-stats">
                  {[['Норма', counts.normal, '#17A65B'], ['Нестаб.', counts.unstable, '#E4962A'],
                    ['Авария', (counts.critical || 0) + (counts.offline || 0), '#E0453E']].map(
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
                badge={{ text: `${healthy}% норма`, kind: healthy >= 70 ? 'up' : 'down' }} />
              <Kpi label="ПК-агентов" value={overview?.total_devices ?? '—'}
                sub={`в сети ${overview?.devices_online ?? 0}`}
                badge={{ text: 'ПК-уровень', kind: 'flat' }} />
              <Kpi label="Средняя скорость" value={overview?.avg_download ?? '—'} unit="Мбит/с"
                sub="загрузка по области" spark={sparks.download} sparkColor="#2F6BF6"
                badge={{ text: `↑ ${overview?.avg_upload ?? 0}`, kind: 'up' }} />
              <Kpi label="Задержка" value={overview?.avg_ping ?? '—'} unit="мс"
                sub={`потери ${overview?.avg_loss ?? 0}%`} spark={sparks.ping} sparkColor="#D9730D"
                badge={{ text: (overview?.avg_ping ?? 0) < 80 ? 'в норме' : 'выше порога',
                         kind: (overview?.avg_ping ?? 0) < 80 ? 'up' : 'down' }} />
              <Kpi label="Инцидентов" value={overview?.active_incidents ?? '—'}
                sub="требуют устранения"
                badge={{ text: 'SLA', kind: (overview?.active_incidents ?? 0) ? 'down' : 'up' }} />
            </div>

            <div className="work">
              <div className="stage">
                <MapView schools={schools} mode={mapMode} selectedId={selectedId}
                  onSelect={setSelectedId} onOpenSchool={openSchool} />

                <div className="float tl panel-card">
                  <div className="panel-card-head">
                    <IcoShield size={17} style={{ color: 'var(--accent)' }} />
                    <h4>ОБЩИЙ СТАТУС СЕТИ</h4>
                    <Tag kind={healthy >= 70 ? 'ok' : healthy >= 45 ? 'warn' : 'danger'} className="tag ml">
                      {healthy >= 70 ? 'Стабильно' : healthy >= 45 ? 'Внимание' : 'Напряжённо'}
                    </Tag>
                  </div>
                  <div className="duo">
                    <div>
                      <div className="eyebrow">Доступность</div>
                      <div className="v">{healthy}%</div>
                    </div>
                    <div>
                      <div className="eyebrow">Соответствие SLA</div>
                      <div className="v word" style={{
                        color: (overview?.sla_compliance ?? 0) >= 70 ? 'var(--ok)' : 'var(--warn)',
                      }}>
                        {(overview?.sla_compliance ?? 0) >= 70 ? 'Оптимально' : 'Ниже нормы'}
                      </div>
                    </div>
                  </div>
                  <Bar value={healthy} color={healthy >= 70 ? '#17A65B' : healthy >= 45 ? '#E4962A' : '#E0453E'} />
                  <div className="btn-row">
                    <button className="btn solid">Уровень школ</button>
                    <button className="btn" onClick={() => setView('devices')}>Уровень ПК</button>
                  </div>
                </div>

                <div className="float bl legend-card">
                  <div className="eyebrow">Состояние канала</div>
                  <div className="legend-gradient" />
                  <div className="legend-ends"><span>Оптимально</span><span>Критично</span></div>
                </div>

                <div className="float bc">
                  <button className="fab" disabled={!selected}
                    onClick={() => selected && openSchool(selected.id)}>
                    <IcoSchool size={16} />
                    {selected ? `Карточка: ${selected.name.slice(0, 34)}` : 'Выберите школу на карте'}
                  </button>
                </div>
              </div>

              <Rail overview={overview} incidents={incidents} schools={schools}
                onOpenSchool={openSchool} onOpenDevice={setDeviceDrawer} onReload={loadCore} />
            </div>
          </>
        ) : null}

        {view === 'schools' ? <SchoolsPage schools={schools} onOpenSchool={openSchool} /> : null}
        {view === 'devices' ? <DevicesPage onOpenDevice={setDeviceDrawer} /> : null}
        {view === 'incidents' ? (
          <IncidentsPage incidents={incidents} role={user?.role}
            onOpenSchool={openSchool} onReload={loadCore} />
        ) : null}
        {view === 'admin' ? <AdminPage role={user?.role} /> : null}
      </div>

      {schoolDrawer ? (
        <SchoolDrawer schoolId={schoolDrawer} role={user?.role}
          onOpenDevice={(id) => { setSchoolDrawer(null); setDeviceDrawer(id); }}
          onClose={() => setSchoolDrawer(null)} />
      ) : null}

      {deviceDrawer ? (
        <DeviceDrawer deviceId={deviceDrawer} role={user?.role}
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
