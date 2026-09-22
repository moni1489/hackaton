/* Демонстрация в экранах приложения: сценарий с сервера превращается в те же ответы,
   что отдаёт боевой API. Компоненты (карта, инциденты, диагностика, карточка школы) не знают
   о демо вообще — подмена живёт в одной точке, в api.js.
   Данные синтетические: их источник — backend/app/services/demo/scenario.py. */

// Схема сценария (x, y от 0 до 100) → координаты внутри ВКО, чтобы точки легли на карту.
const lat = (y) => +(50.2 - (y / 100) * 2.6).toFixed(6);
const lng = (x) => +(82.3 + (x / 100) * 2.4).toFixed(6);

const COUNT_KEY = {
  'Норма': 'normal', 'Нестабильно': 'unstable', 'Критично': 'critical',
  'Нет соединения': 'offline', 'Нет свежих данных': 'no_data',
};
const SEVERITY = { 'Критично': 'critical', 'Нет соединения': 'critical', 'Нестабильно': 'warning' };
const CONNECTION = 'ВОЛС (Оптика)';
const mean = (items, key) => (items.length
  ? +(items.reduce((sum, i) => sum + (i[key] || 0), 0) / items.length).toFixed(1) : 0);

/** Модельное время сценария как ISO-строка: приложение печатает его в «Последний замер». */
function stamp(view, minutesAgo = 0) {
  const [day, month, year] = (view.clock.label || '01.01.2026 00:00').split(' ')[0].split('.');
  const time = (view.clock.label || '').split(' ')[1] || '00:00';
  const at = new Date(`${year}-${month}-${day}T${time}:00`);
  at.setMinutes(at.getMinutes() - minutesAgo);
  return at.toISOString().slice(0, 19);
}

const code = (s) => `DEMO-${String(s.id).padStart(3, '0')}`;

function row(s, view) {
  const known = s.status != null;
  return {
    id: s.id, school_id_code: code(s), name: s.name, region: s.district, address: s.district,
    lat: lat(s.y), lng: lng(s.x), provider: s.provider, connection_type: CONNECTION,
    contract_speed_down: s.contract, contract_speed_up: s.contract,
    status: known ? s.status : 'Нет свежих данных', last_known_status: s.status || 'Норма',
    current_download: s.download, current_upload: s.upload, current_ping: s.ping,
    current_jitter: s.jitter, current_packet_loss: s.loss,
    last_measurement: known ? stamp(view, 2) : null,
    age_min: known ? 2 : null, is_stale: false,
  };
}

/** ПК-агенты школы: шлюз и рабочие места с теми же показателями, что у школы. */
function devicesOf(s, view) {
  if (s.status == null) return [];
  const bad = Boolean(s.affected);
  const make = (n) => ({
    id: s.id * 10 + (n ?? 0),
    device_id: n == null ? `${code(s)}-GW` : `${code(s)}-PC${n}`,
    school_id: s.id, school_name: s.name, region: s.district, provider: s.provider,
    name: n == null ? 'Шлюз-агент (основной)' : `Рабочее место №${n}`,
    room: n == null ? 'Серверная / Шлюз' : `Кабинет ${100 + n}`,
    ip_address: `10.10.${s.id}.${10 + (n ?? 0)}`, device_type: n == null ? 'Шлюз' : 'ПК',
    role: n == null ? 'monitor' : 'workstation', status: bad ? 'warning' : 'online',
    os_name: 'Astra Linux SE 1.7', agent_version: '1.9.4', uptime_hours: 412,
    link_mode: n == null ? 'Ethernet 1 Гбит/с' : 'Ethernet 100 Мбит/с', wifi_signal_dbm: null,
    current_download: s.download, current_upload: s.upload, current_ping: s.ping,
    current_jitter: s.jitter, current_packet_loss: s.loss,
    sla_compliance_pct: bad ? 12 : 98, last_measurement: stamp(view, 2), line_id: s.id,
  });
  return [make(null), make(1), make(2)];
}

function incidentOf(s, view) {
  const inc = view.incident;
  return {
    id: s.id, incident_number: `${inc.number}-${String(s.id).padStart(2, '0')}`,
    school_id: s.id, school_name: s.name, region: s.district, provider: s.provider,
    device_id: `${code(s)}-GW`, line_id: s.id, status: 'В работе',
    severity: SEVERITY[s.status] || 'warning',
    start_time: stamp(view, 32),
    description: `Скорость ${s.download} Мбит/с при договорных ${s.contract} Мбит/с · `
      + `Ping ${s.ping} мс · Потери ${s.loss}%`,
    root_cause: view.ml ? view.ml.cause : 'undetermined',
    root_cause_label: view.ml ? view.ml.cause_label : 'Идёт диагностика',
    responsible: view.ml ? view.ml.responsible : '—',
    root_cause_confidence: view.ml ? view.ml.confidence : 0,
    operator_verdict: view.decision ? view.decision.cause : null,
    has_claim: Boolean(view.report),
  };
}

/** Вердикт модели по школе в форме /api/ml/attribution/{id}. */
function verdictOf(s, view) {
  const ml = view.ml;
  const own = ml.per_school.find((p) => p.school === s.name);
  const cause = own ? own.cause : ml.cause;
  const confidence = own ? own.confidence : ml.confidence;
  return {
    school_id: s.id, school_name: s.name, provider: s.provider, district: s.district,
    at: stamp(view), cause,
    cause_label: own ? own.cause_label : ml.cause_label,
    responsible: ml.responsible, confidence,
    probabilities: Object.fromEntries(ml.alternatives.map((a) => [a.cause, a.p])),
    evidence: { ...ml.evidence, affected: [] },
    narrative: ml.narrative, data_quality: ml.data_quality,
    drivers: ml.drivers, features: {}, source: ml.source, model_version: ml.model_version,
    last_measurement: stamp(view, 2), hypothesis: true, actionable: cause === 'provider_node',
  };
}

function forecastOf(s, view) {
  const f = view.ml?.forecast;
  return {
    school_id: s.id, school_name: s.name, district: s.district, provider: s.provider,
    horizon_hours: f ? f.horizon_hours : 6,
    probability: f ? f.probability : null,
    band: f ? f.band : 'нет данных',
    stale: false, last_measurement: stamp(view, 2),
    source: view.ml ? view.ml.source : 'нет данных',
    model_version: f ? f.model_version : null,
    drivers: f ? f.drivers : [],
    recommendation: f ? f.recommendation : '',
  };
}

/** Ответы боевого API, собранные из текущего состояния демонстрации. */
export default function demoSource(view, sid) {
  const shown = view.schools.filter((s) => s.status != null);
  const affected = shown.filter((s) => s.affected);
  const counts = { normal: 0, unstable: 0, critical: 0, offline: 0, no_data: 0 };
  view.schools.forEach((s) => {
    counts[s.status ? COUNT_KEY[s.status] || 'no_data' : 'no_data'] += 1;
  });
  const incidents = view.incident ? affected.map((s) => incidentOf(s, view)) : [];
  const byId = (id) => view.schools.find((s) => s.id === Number(id));

  return {
    sid,
    hero: view.ml?.seasonal_norm?.school || affected[0]?.name || null,
    heroId: view.schools.find((s) => s.name === view.ml?.seasonal_norm?.school)?.id
      || affected[0]?.id || null,

    // Претензия провайдеру: текст берётся из акта сценария, когда он уже сформирован.
    claim: view.report ? {
      claim_text: view.report.claim_text, source: 'юридический шаблон', claim_advised: true,
      advisory: 'Демонстрационный документ на синтетических данных.',
    } : null,

    overview: {
      total_schools: view.schools.length, total_devices: view.schools.length * 3,
      devices_online: shown.length * 3, active_incidents: incidents.length,
      avg_download: mean(shown, 'download'), avg_upload: mean(shown, 'upload'),
      avg_ping: mean(shown, 'ping'), avg_loss: mean(shown, 'loss'),
      sla_compliance: null, status_counts: counts,
      freshness: {
        last_measurement: shown.length ? stamp(view, 2) : null, age_min: shown.length ? 2 : null,
        stale_after_min: 90, is_stale: false, mode: 'live', as_of: stamp(view),
        schools_with_data: shown.length, schools_without_data: view.schools.length - shown.length,
      },
      filters: {
        regions: ['Все районы', ...new Set(view.schools.map((s) => s.district))],
        providers: ['Все провайдеры', ...new Set(view.schools.map((s) => s.provider))],
        connection_types: ['Все типы', CONNECTION],
        statuses: ['Все статусы', 'Норма', 'Нестабильно', 'Критично', 'Нет соединения'],
      },
    },

    schools: ({ region, provider, status, search } = {}) => view.schools
      .map((s) => row(s, view))
      .filter((s) => (!region || region.startsWith('Все') || s.region === region)
        && (!provider || provider.startsWith('Все') || s.provider === provider)
        && (!status || status.startsWith('Все') || s.status === status)
        && (!search || `${s.name} ${s.school_id_code}`.toLowerCase().includes(search.toLowerCase()))),

    school: (id) => {
      const s = byId(id);
      if (!s) throw new Error('Школа не найдена');
      const base = row(s, view);
      return {
        ...base, published_connections: [],
        contact_name: 'Демонстрационный контакт', contact_phone: '+7(700)000-00-00',
        contact_email: `admin${s.id}@example.local`, provider_phone: '+7(800)000-00-00',
        lines: [{
          id: s.id, code: `${code(s)}-L1`, role: 'main', provider: s.provider,
          connection_type: CONNECTION, contract_speed_down: s.contract, contract_speed_up: s.contract,
          status: base.status, current_download: s.download, current_upload: s.upload,
          current_ping: s.ping, current_jitter: s.jitter, current_packet_loss: s.loss,
          last_measurement: base.last_measurement,
        }],
        workstations: 3, devices: devicesOf(s, view),
        incidents: view.incident && s.affected ? [incidentOf(s, view)] : [],
      };
    },

    // График карточки: ряд сценария (% от договорной) в Мбит/с этой школы.
    schoolMeasurements: (id) => {
      const s = byId(id);
      if (!s || s.status == null) return [];
      const key = s.affected ? 'group' : 'rest';
      return view.series.map((point, i) => ({
        timestamp: stamp(view, (view.series.length - 1 - i) * 5),
        devices: 3, download_speed: +(point[key] * s.contract / 100).toFixed(1),
        upload_speed: +(point[key] * s.contract / 120).toFixed(1),
        ping: s.affected && i > view.series.length / 2 ? s.ping : 18,
        jitter: s.jitter, packet_loss: s.loss, is_offline: false, source: 'live',
      }));
    },

    schoolAnalytics: (id) => {
      const s = byId(id);
      const f = view.ml?.forecast;
      const bad = Boolean(s?.affected);
      return {
        school_id: Number(id), school_name: s?.name, provider: s?.provider,
        device_id: s ? `${code(s)}-GW` : null, window_days: 1, samples: view.series.length * 3,
        data_state: s?.status == null ? 'no_data' : 'ok', last_measurement: stamp(view, 2),
        contract_speed: s?.contract ?? null, avg_speed: s?.download ?? null,
        recent_avg_speed: s?.download ?? null, stability: bad ? 41 : 96,
        sla_compliance_pct: bad ? 12 : 98, availability_pct: bad ? 74 : 100,
        availability_norm_pct: 99, availability_ok: !bad,
        below_contract_pct: bad ? 88 : 4, violations: bad ? 1 : 0, trend_pct: bad ? -72 : 0,
        patterns: bad ? [{ text: 'Резкая просадка у всех ПК школы одновременно' }] : [],
        risk_score: f && bad ? Math.round(f.probability * 100) : 8,
        risk_level: bad ? (f?.band || 'высокая') : 'низкая',
        forecast: f && bad ? { probability: f.probability, band: f.band,
          horizon_hours: f.horizon_hours, recommendation: f.recommendation } : null,
        worst_weekday: null, worst_hour: null,
      };
    },

    incidents,
    trend: shown.length ? view.series.map((point) => ({
      hour: point.t, download: +(point.group).toFixed(1),
      ping: point.group < 60 ? view.metrics?.focus?.ping ?? 0 : 18,
    })) : [],

    riskQueue: (limit = 6) => (view.ml ? affected.slice(0, limit).map((s) => ({
      school_id: s.id, school_name: s.name, provider: s.provider, district: s.district,
      risk_score: Math.round((view.ml.forecast?.probability ?? 0.5) * 100),
      risk_level: view.ml.forecast?.band || 'высокая',
      sla_compliance_pct: 12, avg_speed: s.download, contract_speed: s.contract,
      patterns: [{ text: 'Просадка у всех ПК школы одновременно' }],
    })) : []),

    devices: () => view.schools.flatMap((s) => devicesOf(s, view)),

    mlSummary: {
      total_affected: view.ml ? affected.length : 0,
      by_cause: view.ml ? [{ cause: view.ml.cause, label: view.ml.cause_label, count: affected.length }] : [],
      provider_side: view.ml && view.ml.cause === 'provider_node' ? affected.length : 0,
      school_side: view.ml && ['school_lan', 'device'].includes(view.ml.cause) ? affected.length : 0,
      undetermined: 0,
      provider_share_pct: view.ml && view.ml.cause === 'provider_node' ? 100 : 0,
      freshness: { last_measurement: stamp(view, 2), age_min: 2, stale_after_min: 90,
        is_stale: false, mode: 'live' },
    },
    mlBoard: (limit = 40) => (view.ml ? affected.slice(0, limit).map((s) => verdictOf(s, view)) : []),
    mlForecast: (limit = 12) => (view.ml?.forecast
      ? affected.slice(0, limit).map((s) => forecastOf(s, view)) : []),
    mlAttribution: (id) => {
      const s = byId(id);
      if (!view.ml || !s) throw new Error('Вердикт ещё не построен');
      return verdictOf(s, view);
    },
    mlSchoolForecast: (id) => {
      const s = byId(id);
      if (!s) throw new Error('Школа не найдена');
      return forecastOf(s, view);
    },
  };
}
