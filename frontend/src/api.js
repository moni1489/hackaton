/* Клиент API: токен, обработка ошибок, скачивание файлов. */
export const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const TOKEN_KEY = 'vko.token';
const USER_KEY = 'vko.user';

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const getUser = () => {
  try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch { return null; }
};
export const clearSession = () => {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
};

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function request(path, { method = 'GET', body, raw = false } = {}) {
  const headers = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  const response = await fetch(`${API_URL}${path}`, {
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

export const api = {
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
  slaReport: async (schoolId, filename) => {
    const response = await request(`/api/ai/sla-report/${schoolId}`, { raw: true });
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  },
};
