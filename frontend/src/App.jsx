import React, { useState, useEffect, useRef, useCallback } from 'react';
import mapboxgl from 'mapbox-gl';
import 'mapbox-gl/dist/mapbox-gl.css';
import './index.css';
import { RegionalMetrics, SchoolMetricsChart } from './MetricsCharts';

const API_URL = 'http://localhost:8000';
const MAPBOX_TOKEN = 'pk.eyJ1IjoiYmVicnVzZDMyIiwiYSI6ImNtbXozZTEzZTA0M3oycG93M3R5NHBranQifQ.pc5OgxomRXUl5pRDVktXuA';

mapboxgl.accessToken = MAPBOX_TOKEN;

// Status -> colour mapping
const STATUS_COLOR = {
  'Норма':          '#16A34A',
  'Нестабильно':    '#D97706',
  'Критично':       '#DC2626',
  'Нет соединения': '#475569',
};

function getStatusClass(status) {
  if (status === 'Норма')          return 'ok';
  if (status === 'Нестабильно')    return 'warn';
  if (status === 'Критично')       return 'danger';
  return 'off';
}

function getIncTag(status) {
  if (status === 'Новый')             return 'tag-new';
  if (status === 'В работе')          return 'tag-inwork';
  if (status === 'Передан поставщику') return 'tag-sent';
  if (status === 'Ожидает информации') return 'tag-waiting';
  return 'tag-resolved';
}

// Live clock
function LiveClock() {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <span className="header-time">
      {time.toLocaleDateString('ru-KZ', { day: '2-digit', month: '2-digit', year: 'numeric' })}
      {' · '}
      {time.toLocaleTimeString('ru-KZ', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
    </span>
  );
}

export default function App() {
  const [schools,   setSchools]   = useState([]);
  const [analytics, setAnalytics] = useState(null);
  const [incidents, setIncidents] = useState([]);

  // Filters
  const [fRegion,   setFRegion]   = useState('Все районы');
  const [fProvider, setFProvider] = useState('Все провайдеры');
  const [fConn,     setFConn]     = useState('Все типы');
  const [fStatus,   setFStatus]   = useState('Все статусы');
  const [search,    setSearch]    = useState('');

  // School measurements for charts
  const [schoolMeasurements, setSchoolMeasurements] = useState([]);

  // Modals
  const [schoolModal,    setSchoolModal]    = useState(null); // school detail object
  const [claimModal,     setClaimModal]     = useState(null); // incident object
  const [claimText,      setClaimText]      = useState('');
  const [claimLoading,   setClaimLoading]   = useState(false);
  const [generatingId,   setGeneratingId]   = useState(null);

  // Map
  const mapContainer = useRef(null);
  const mapRef       = useRef(null);
  const markersRef   = useRef([]);

  // Fetch data
  const fetchData = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (fRegion !== 'Все районы')       params.append('region', fRegion);
      if (fProvider !== 'Все провайдеры') params.append('provider', fProvider);
      if (fConn !== 'Все типы')           params.append('connection_type', fConn);
      if (fStatus !== 'Все статусы')      params.append('status', fStatus);

      const [sRes, aRes, iRes] = await Promise.all([
        fetch(`${API_URL}/api/schools?${params}`),
        fetch(`${API_URL}/api/analytics`),
        fetch(`${API_URL}/api/incidents`),
      ]);
      const [s, a, i] = await Promise.all([sRes.json(), aRes.json(), iRes.json()]);
      setSchools(s);
      setAnalytics(a);
      setIncidents(i);
    } catch (e) {
      console.error('Fetch error:', e);
    }
  }, [fRegion, fProvider, fConn, fStatus]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Init Mapbox
  useEffect(() => {
    if (mapRef.current) return;
    const map = new mapboxgl.Map({
      container: mapContainer.current,
      style: 'mapbox://styles/mapbox/dark-v11',
      center: [83.2, 49.7],
      zoom: 6.5,
    });
    map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), 'bottom-right');
    mapRef.current = map;
    return () => { map.remove(); mapRef.current = null; };
  }, []);

  // Update markers
  useEffect(() => {
    if (!mapRef.current) return;

    // Remove old markers
    markersRef.current.forEach(m => m.remove());
    markersRef.current = [];

    const visible = schools.filter(s => {
      if (!search) return true;
      const q = search.toLowerCase();
      return s.name.toLowerCase().includes(q) || s.school_id_code.toLowerCase().includes(q);
    });

    visible.forEach(school => {
      if (!school.lat || !school.lng) return;

      const color = STATUS_COLOR[school.status] || '#475569';

      // SVG pin shapes по ТЗ
      let svgInner = '';
      if (school.status === 'Норма') {
        svgInner = `<circle cx="10" cy="10" r="7" fill="${color}" stroke="#0D1B2A" stroke-width="1.5"/>`;
      } else if (school.status === 'Нестабильно') {
        svgInner = `<polygon points="10,3 18,17 2,17" fill="${color}" stroke="#0D1B2A" stroke-width="1.5"/>`;
      } else if (school.status === 'Критично') {
        svgInner = `<rect x="3" y="3" width="14" height="14" rx="1" transform="rotate(45 10 10)" fill="${color}" stroke="#0D1B2A" stroke-width="1.5"/>`;
      } else {
        svgInner = `<rect x="3" y="3" width="14" height="14" rx="2" fill="${color}" stroke="#0D1B2A" stroke-width="1.5"/>`;
      }

      const el = document.createElement('div');
      el.innerHTML = `<svg width="20" height="20" viewBox="0 0 20 20">${svgInner}</svg>`;
      el.style.cursor = 'pointer';
      el.style.filter = `drop-shadow(0 0 4px ${color}80)`;

      // Popup
      const popupEl = document.createElement('div');
      popupEl.className = 'map-popup';
      const sc = getStatusClass(school.status);
      popupEl.innerHTML = `
        <div class="popup-code-row">
          <span class="popup-code">${school.school_id_code}</span>
          <span class="popup-status ${sc}">${school.status}</span>
        </div>
        <div class="popup-name">${school.name}</div>
        <div class="popup-grid">
          <div class="popup-grid-item"><span>Провайдер:</span><br/><strong>${school.provider}</strong></div>
          <div class="popup-grid-item"><span>Тип линии:</span><br/><strong>${school.connection_type}</strong></div>
          <div class="popup-grid-item"><span>Факт Download:</span><br/><strong style="color:${school.current_download < 20 ? '#F87171' : '#86EFAC'}">${school.current_download} Мбит/с</strong></div>
          <div class="popup-grid-item"><span>Ping:</span><br/><strong>${school.current_ping} мс</strong></div>
        </div>
        <button class="popup-btn" id="popup-btn-${school.id}">Подробнее →</button>
      `;
      popupEl.querySelector(`#popup-btn-${school.id}`).addEventListener('click', () => {
        openSchoolDetail(school.id);
      });

      const popup = new mapboxgl.Popup({ offset: 12, maxWidth: '280px', closeButton: false })
        .setDOMContent(popupEl);

      const marker = new mapboxgl.Marker(el)
        .setLngLat([school.lng, school.lat])
        .setPopup(popup)
        .addTo(mapRef.current);

      markersRef.current.push(marker);
    });
  }, [schools, search]);

  // Open school detail + measurements
  const openSchoolDetail = async (id) => {
    try {
      const [res, mRes] = await Promise.all([
        fetch(`${API_URL}/api/schools/${id}`),
        fetch(`${API_URL}/api/schools/${id}/measurements`),
      ]);
      const [data, mData] = await Promise.all([res.json(), mRes.json()]);
      setSchoolModal(data);
      setSchoolMeasurements(mData);
    } catch (e) { console.error(e); }
  };

  // Generate AI claim
  const handleGenerateClaim = async (incident) => {
    setClaimModal(incident);
    setClaimText('');
    setClaimLoading(true);
    setGeneratingId(incident.id);
    try {
      const res  = await fetch(`${API_URL}/api/generate-claim/${incident.id}`, { method: 'POST' });
      const data = await res.json();
      setClaimText(data.claim_text || 'Ошибка получения текста');
    } catch (e) {
      setClaimText('Ошибка связи с AI-сервисом. Проверьте API-токен Gemini.');
    }
    setClaimLoading(false);
    setGeneratingId(null);
  };

  // Derived stats
  const st = analytics?.status_counts || {};

  return (
    <>
      {/* ───── HEADER ───── */}
      <header className="app-header">
        <div className="header-brand">
          <div className="header-logo-group">
            <img src="/logos/akimat.png"  alt="Акимат ВКО" />
            <div className="header-divider" />
            <img src="/logos/oskemen.png" alt="Oskemen Hub" />
            <div className="header-divider" />
            <img src="/logos/uct.png"     alt="УЦТ" />
          </div>
          <div className="header-divider" />
          <div className="header-title-block">
            <h1>САМ ВКО · Мониторинг качества интернет-соединения</h1>
            <p>Система автономного мониторинга организаций образования — Восточно-Казахстанская область</p>
          </div>
        </div>

        <div className="header-right">
          <div className="live-badge">
            <span className="live-dot" />
            Система онлайн
          </div>
          <LiveClock />
        </div>
      </header>

      {/* ───── BODY ───── */}
      <div className="app-body">

        {/* KPI СТРИПА */}
        <div className="kpi-strip">
          <div className="kpi-item">
            <span className="kpi-label">Школ в системе</span>
            <span className="kpi-value">{analytics?.total_schools ?? schools.length}</span>
            <span className="kpi-sub">организаций ВКО</span>
          </div>
          <div className="kpi-item">
            <span className="kpi-label">Точек мониторинга</span>
            <span className="kpi-value blue">{analytics?.total_devices ?? '—'}</span>
            <span className="kpi-sub">edge-агентов (ПК)</span>
          </div>
          <div className="kpi-item">
            <span className="kpi-label">Средняя скорость ↓</span>
            <span className="kpi-value green">{analytics?.avg_download ?? '—'}<small> Мбит/с</small></span>
            <span className="kpi-sub">по всему региону</span>
          </div>
          <div className="kpi-item">
            <span className="kpi-label">Активных инцидентов</span>
            <span className="kpi-value red">{analytics?.active_incidents ?? incidents.length}</span>
            <span className="kpi-sub">требуют устранения</span>
          </div>
          <div className="kpi-item">
            <span className="kpi-label">Средний Ping</span>
            <span className="kpi-value">{analytics?.avg_ping ?? '—'}<small> мс</small></span>
            <span className="kpi-sub">задержка в сети</span>
          </div>
        </div>

        {/* ФИЛЬТРЫ */}
        <div className="filter-bar">
          <h3>Фильтры</h3>

          <div className="filter-group search-box">
            <label>Поиск</label>
            <input
              type="text"
              placeholder="Название или код школы..."
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
          </div>

          <div className="filter-group">
            <label>Район / Город</label>
            <select value={fRegion} onChange={e => setFRegion(e.target.value)}>
              {analytics?.filters?.regions?.map(r => <option key={r}>{r}</option>)
                || <option>Все районы</option>}
            </select>
          </div>

          <div className="filter-group">
            <label>Поставщик</label>
            <select value={fProvider} onChange={e => setFProvider(e.target.value)}>
              {analytics?.filters?.providers?.map(p => <option key={p}>{p}</option>)
                || <option>Все провайдеры</option>}
            </select>
          </div>

          <div className="filter-group">
            <label>Тип подключения</label>
            <select value={fConn} onChange={e => setFConn(e.target.value)}>
              {analytics?.filters?.connection_types?.map(c => <option key={c}>{c}</option>)
                || <option>Все типы</option>}
            </select>
          </div>

          <div className="filter-group">
            <label>Статус</label>
            <select value={fStatus} onChange={e => setFStatus(e.target.value)}>
              <option>Все статусы</option>
              <option>Норма</option>
              <option>Нестабильно</option>
              <option>Критично</option>
              <option>Нет соединения</option>
            </select>
          </div>
        </div>

        {/* КАРТА + ПРАВАЯ КОЛОНКА */}
        <div className="content-grid">

          {/* КАРТА */}
          <div className="map-card">
            <div className="card-head">
              <div>
                <h2>Интерактивная карта Восточно-Казахстанской области</h2>
                <span className="card-head-meta">
                  {schools.length} организаций · Нажмите на маркер для просмотра параметров
                </span>
              </div>
              <div className="map-legend">
                <div className="legend-item">
                  <span className="legend-shape l-circle"  style={{ background: '#16A34A' }} />
                  Норма
                </div>
                <div className="legend-item">
                  <span className="legend-shape l-diamond" style={{ background: '#D97706' }} />
                  Нестабильно
                </div>
                <div className="legend-item">
                  <span className="legend-shape l-diamond" style={{ background: '#DC2626' }} />
                  Критично
                </div>
                <div className="legend-item">
                  <span className="legend-shape l-square"  style={{ background: '#475569' }} />
                  Нет связи
                </div>
              </div>
            </div>

            <div className="map-wrapper">
              <div ref={mapContainer} id="mapbox-map" style={{ width: '100%', height: '100%' }} />
            </div>
          </div>

          {/* ПРАВАЯ КОЛОНКА */}
          <div className="right-column">

            {/* Статусы */}
            <div className="status-breakdown">
              <h3>Распределение по статусу</h3>
              {[
                { label: 'Норма',          count: st.normal   || 0, color: '#16A34A', total: analytics?.total_schools || 1 },
                { label: 'Нестабильно',    count: st.unstable || 0, color: '#D97706', total: analytics?.total_schools || 1 },
                { label: 'Критично',       count: st.critical || 0, color: '#DC2626', total: analytics?.total_schools || 1 },
                { label: 'Нет соединения', count: st.offline  || 0, color: '#475569', total: analytics?.total_schools || 1 },
              ].map(item => (
                <div key={item.label}>
                  <div className="status-row">
                    <div className="status-left">
                      <span className="status-dot" style={{ background: item.color }} />
                      {item.label}
                    </div>
                    <span className="status-count" style={{ color: item.color }}>{item.count}</span>
                  </div>
                  <div className="status-bar-wrap">
                    <div
                      className="status-bar-fill"
                      style={{ background: item.color, width: `${Math.round(item.count / item.total * 100)}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>

            {/* Инциденты */}
            <div className="incidents-card">
              <div className="card-head">
                <h2>Лента инцидентов</h2>
                <span className="badge">{incidents.length}</span>
              </div>

              <div className="incidents-list">
                {incidents.length === 0
                  ? <p className="no-incidents">Все школы работают в штатном режиме.</p>
                  : incidents.map(inc => (
                    <div key={inc.id} className="incident-item">
                      <div className="inc-header">
                        <span className="inc-number">{inc.incident_number}</span>
                        <span className={`inc-tag ${getIncTag(inc.status)}`}>{inc.status}</span>
                      </div>
                      <div className="inc-school">{inc.school_name}</div>
                      <p className="inc-desc">{inc.description}</p>
                      <div className="inc-footer">
                        <span className="inc-provider">Провайдер: {inc.provider}</span>
                        <button
                          className="btn-claim"
                          onClick={() => handleGenerateClaim(inc)}
                          disabled={generatingId === inc.id}
                        >
                          {generatingId === inc.id ? 'Генерация...' : '✦ Претензия (AI)'}
                        </button>
                      </div>
                    </div>
                  ))
                }
              </div>
            </div>
          </div>
        </div>

        {/* FOOTER */}
        <div className="footer-logos">
          <img src="/logos/akimat.png"  alt="Акимат ВКО" />
          <img src="/logos/oskemen.png" alt="Oskemen Hub" />
          <img src="/logos/uct.png"     alt="УЦТ" />
        </div>
        <p className="footer-copy">
          © 2026 Система автономного мониторинга качества интернет-соединения ВКО · Hackathon Oskemen Hub
        </p>
      </div>

      {/* ───── MODAL: ШКОЛА ───── */}
      {schoolModal && (
        <div className="modal-overlay" onClick={() => setSchoolModal(null)}>
          <div className="modal-box" onClick={e => e.stopPropagation()}>
            <div className="modal-head">
              <div className="modal-head-left">
                <span className="modal-school-code">{schoolModal.school_id_code}</span>
                <h2>{schoolModal.name}</h2>
                <p>📍 {schoolModal.address}</p>
              </div>
              <button className="btn-close-modal" onClick={() => setSchoolModal(null)}>×</button>
            </div>

            <div className="modal-body-scroll">
              {/* KPI */}
              <div className="school-kpi-row">
                <div className="skpi-box">
                  <div className="skpi-label">Download (факт)</div>
                  <div className="skpi-val" style={{ color: schoolModal.current_download < 20 ? '#F87171' : '#86EFAC' }}>
                    {schoolModal.current_download}<small> Мбит/с</small>
                  </div>
                  <div className="skpi-sub">Договор: {schoolModal.contract_speed_down} Мбит/с</div>
                </div>
                <div className="skpi-box">
                  <div className="skpi-label">Upload (факт)</div>
                  <div className="skpi-val">{schoolModal.current_upload}<small> Мбит/с</small></div>
                  <div className="skpi-sub">Тип: {schoolModal.connection_type}</div>
                </div>
                <div className="skpi-box">
                  <div className="skpi-label">Ping / Jitter</div>
                  <div className="skpi-val">{schoolModal.current_ping}<small> мс</small></div>
                  <div className="skpi-sub">Jitter: {schoolModal.current_jitter} мс</div>
                </div>
                <div className="skpi-box">
                  <div className="skpi-label">Статус подключения</div>
                  <div className="skpi-val" style={{ fontSize: '1rem', marginTop: '0.35rem' }}>
                    {schoolModal.status}
                  </div>
                  <div className="skpi-sub">Loss: {schoolModal.current_packet_loss}%</div>
                </div>
              </div>

              {/* Контакты */}
              <div className="section-title">Ответственные лица и провайдер</div>
              <div className="info-row">
                <div className="info-block">
                  <h4>Ответственное лицо за интернет</h4>
                  <p><span>ФИО:</span> {schoolModal.contact_name}</p>
                  <p><span>Телефон:</span> {schoolModal.contact_phone}</p>
                  <p><span>E-mail:</span> {schoolModal.contact_email}</p>
                </div>
                <div className="info-block">
                  <h4>Поставщик услуг связи</h4>
                  <p><span>Провайдер:</span> {schoolModal.provider}</p>
                  <p><span>Тип линии:</span> {schoolModal.connection_type}</p>
                  <p><span>Техподдержка:</span> {schoolModal.provider_phone}</p>
                </div>
              </div>

              {/* Устройства */}
              <div className="section-title">Точки мониторинга в здании школы ({schoolModal.devices?.length} устройств)</div>
              <div className="data-table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Device ID</th>
                      <th>Название</th>
                      <th>Расположение</th>
                      <th>IP-адрес</th>
                      <th>Статус</th>
                    </tr>
                  </thead>
                  <tbody>
                    {schoolModal.devices?.map(d => (
                      <tr key={d.id}>
                        <td><code>{d.device_id}</code></td>
                        <td>{d.name}</td>
                        <td>{d.room}</td>
                        <td>{d.ip_address}</td>
                        <td>
                          <span className={`status-pill ${d.status === 'online' ? 'pill-online' : d.status === 'warning' ? 'pill-warning' : 'pill-offline'}`}>
                            {d.status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="modal-foot">
              <button className="btn btn-secondary" onClick={() => setSchoolModal(null)}>Закрыть</button>
            </div>
          </div>
        </div>
      )}

      {/* ───── MODAL: AI ПРЕТЕНЗИЯ ───── */}
      {claimModal && (
        <div className="modal-overlay" onClick={() => setClaimModal(null)}>
          <div className="modal-box claim-box" onClick={e => e.stopPropagation()}>
            <div className="modal-head">
              <div className="modal-head-left">
                <span className="modal-school-code">✦ Gemini AI · Генератор досудебных претензий</span>
                <h2>Официальная претензия провайдеру</h2>
                <p>Инцидент: {claimModal.incident_number} · {claimModal.school_name}</p>
              </div>
              <button className="btn-close-modal" onClick={() => setClaimModal(null)}>×</button>
            </div>

            <div className="modal-body-scroll">
              {claimLoading
                ? (
                  <div className="ai-spinner">
                    <div className="spinner-ring" />
                    <span>ИИ анализирует параметры нарушения и формирует официальный черновик...</span>
                  </div>
                )
                : (
                  <textarea
                    className="claim-textarea"
                    value={claimText}
                    onChange={e => setClaimText(e.target.value)}
                  />
                )
              }
            </div>

            <div className="modal-foot">
              <button className="btn btn-secondary" onClick={() => setClaimModal(null)}>Отмена</button>
              <button
                className="btn btn-primary"
                disabled={!claimText}
                onClick={() => {
                  navigator.clipboard.writeText(claimText);
                  alert('Текст претензии скопирован в буфер обмена!');
                }}
              >
                📋 Копировать претензию
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
