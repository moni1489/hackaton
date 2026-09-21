/* Клиент API: токен, обработка ошибок, скачивание файлов. */
// По умолчанию (и в dev, и в prod) — бэкенд на Render; для локального бэка: VITE_API_URL=http://localhost:8000
// Пустая VITE_API_URL= — тот же адрес, что у страницы (демо-сборка, которую раздаёт сам бэкенд).
export const API_URL = import.meta.env.VITE_API_URL ?? 'https://codemasters1.onrender.com';

const TOKEN_KEY = 'vko.token';
const USER_KEY = 'vko.user';

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const getUser = () => {
  try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch { return null; }
};
export const clearSession = () => {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  asOf = null;
};

// Режим демонстрации истории: пока задан, все GET-запросы считают «сейчас» = asOf.
let asOf = null;
export const setAsOf = (value) => { asOf = value || null; };

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export async function request(path, { method = 'GET', body, raw = false } = {}) {
  const headers = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  const url = method === 'GET' && asOf
    ? `${path}${path.includes('?') ? '&' : '?'}as_of=${encodeURIComponent(asOf)}` : path;
  const response = await fetch(`${API_URL}${url}`, {
    method, headers, body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (response.status === 401) {
    clearSession();
    window.dispatchEvent(new Event('vko:unauthorized'));
    throw new ApiError(401, 'Сессия истекла — войдите заново');
  }
  if (!response.ok) {
    let detail = `Ошибка ${response.status}`;
    try { detail = (await response.json()).detail || detail; } catch { /* тело не JSON */ }
    throw new ApiError(response.status, detail);
  }
  return raw ? response : response.json();
}

async function saveBlob(response, filename) {
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export const api = {
  publicSchools: () => request('/api/public-data/schools?limit=500'),
  publicConnections: () => request('/api/public-data/connections'),
  externalNetwork: () => request('/api/public-data/network'),
  login: async (email, password) => {
    const data = await request('/api/auth/login', { method: 'POST', body: { email, password } });
    localStorage.setItem(TOKEN_KEY, data.access_token);
    localStorage.setItem(USER_KEY, JSON.stringify({
      role: data.role, full_name: data.full_name,
      school_id: data.school_id, provider_name: data.provider_name, email,
    }));
    return data;
  },
  overview: () => request('/api/web/overview'),
  schools: (params = {}) => request(`/api/web/schools?${new URLSearchParams(
    Object.entries(params).filter(([, v]) => v && !String(v).startsWith('Все')))}`),
  school: (id) => request(`/api/web/schools/${id}`),
  schoolMeasurements: (id) => request(`/api/web/schools/${id}/measurements`),
  schoolAnalytics: (id) => request(`/api/web/schools/${id}/analytics`),
  devices: (params = {}) => request(`/api/web/devices?${new URLSearchParams(
    Object.entries(params).filter(([, v]) => v))}`),
  device: (deviceId) => request(`/api/web/devices/${encodeURIComponent(deviceId)}`),
  incidents: () => request('/api/web/incidents'),
  updateIncident: (id, status) =>
    request(`/api/web/incidents/${id}?new_status=${encodeURIComponent(status)}`, { method: 'PATCH' }),
  riskQueue: (limit = 6) => request(`/api/web/risk-queue?limit=${limit}`),
  trend: (hours = 24) => request(`/api/web/trend?hours=${hours}`),
  rating: (days = 30) => request(`/api/web/rating?days=${days}`),
  ratingHistory: (days = 90, schoolId) =>
    request(`/api/web/rating/history?days=${days}${schoolId ? `&school_id=${schoolId}` : ''}`),
  system: () => request('/api/web/system'),
  audit: () => request('/api/auth/audit?limit=60'),
  policy: () => request('/api/auth/policy'),
  syncMonitor: () => request('/api/admin/sync'),
  users: () => request('/api/admin/users'),
  enrollmentCode: (code) => request(`/api/admin/enrollment-code/${encodeURIComponent(code)}`),
  setDiagnosticMode: (deviceId, mode) =>
    request(`/api/admin/devices/${encodeURIComponent(deviceId)}/diagnostic-mode?mode=${mode}`,
      { method: 'POST' }),
  generateClaim: (incidentId) => request(`/api/ai/claim/${incidentId}`, { method: 'POST' }),

  // --- ML: атрибуция причины и прогноз пробоя SLA ---
  mlBoard: (limit = 40) => request(`/api/ml/board?limit=${limit}`),
  mlSummary: () => request('/api/ml/summary'),
  mlAttribution: (schoolId) => request(`/api/ml/attribution/${schoolId}`),
  mlIncident: (incidentId) => request(`/api/ml/incident/${incidentId}`),
  mlVerdict: (incidentId, cause) =>
    request(`/api/ml/verdict/${incidentId}?cause=${encodeURIComponent(cause)}`, { method: 'POST' }),
  mlForecast: (limit = 12) => request(`/api/ml/forecast?limit=${limit}`),
  mlSchoolForecast: (schoolId) => request(`/api/ml/forecast/${schoolId}`),
  mlTimeline: (deviceId, hours = 24) =>
    request(`/api/ml/timeline/${encodeURIComponent(deviceId)}?hours=${hours}`),
  mlModelInfo: () => request('/api/ml/model-info'),
  mlRetrain: () => request('/api/ml/retrain', { method: 'POST' }),
  slaReport: async (schoolId, filename) => {
    await saveBlob(await request(`/api/ai/sla-report/${schoolId}`, { raw: true }), filename);
  },

  // --- Экспорт (ТЗ п.9), пороги и линии ---
  exportMeasurements: async (params, filename) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (Array.isArray(value)) value.forEach((item) => query.append(key, item));
      else if (value !== '' && value != null && value !== false) query.set(key, value);
    });
    await saveBlob(await request(`/api/web/export?${query}`, { raw: true }), filename);
  },
  thresholds: () => request('/api/admin/thresholds'),
  setThresholds: (body) => request('/api/admin/thresholds', { method: 'PUT', body }),
};
