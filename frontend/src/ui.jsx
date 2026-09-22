/* Общие представления статусов и мелкие визуальные примитивы. */
export const STATUS = {
  'Норма': { key: 'ok', color: '#17A65B', short: 'Норма', weight: 0 },
  'Нестабильно': { key: 'warn', color: '#E4962A', short: 'Нестабильно', weight: 1 },
  'Критично': { key: 'danger', color: '#E0453E', short: 'Критично', weight: 2 },
  'Нет соединения': { key: 'off', color: '#64748B', short: 'Нет связи', weight: 3 },
  // Замеров нет — о канале ничего не известно. Не путать с «Нет соединения» (замер был, связи нет).
  'Нет свежих данных': { key: 'stale', color: '#9AA5B5', short: 'Нет данных', weight: 4 },
};
export const statusMeta = (status) => STATUS[status] || STATUS['Нет свежих данных'];

export const DEVICE_STATUS = {
  online: { label: 'В сети', cls: 'ok', color: '#17A65B' },
  warning: { label: 'Отклонения', cls: 'warn', color: '#E4962A' },
  offline: { label: 'Не в сети', cls: 'off', color: '#64748B' },
  stale: { label: 'Нет данных', cls: 'stale', color: '#9AA5B5' },
};
export const deviceMeta = (status) => DEVICE_STATUS[status] || DEVICE_STATUS.offline;

export const RISK_CLASS = {
  'низкий': 'ok', 'средний': 'warn', 'высокий': 'danger',
  'критический': 'danger', 'нет данных': 'off',
};

export const fmtDateTime = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
};

/* Время замера в UTC: так оно хранится на сервере, поэтому без неоднозначности часового пояса. */
export const fmtStamp = (iso) => {
  if (!iso) return '—';
  const utc = /Z$|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`;
  return `${new Date(utc).toLocaleString('ru-RU', {
    timeZone: 'UTC', day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })} UTC`;
};

/* Возраст в минутах (с сервера) → «5 мин», «3 ч», «4 сут». */
export const fmtAge = (minutes) => {
  if (minutes == null) return '—';
  if (minutes < 60) return `${minutes} мин`;
  if (minutes < 60 * 24) return `${Math.round(minutes / 60)} ч`;
  return `${Math.round(minutes / 1440)} сут`;
};

export const fmtAgo = (iso) => {
  if (!iso) return '—';
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return 'только что';
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} ч назад`;
  return `${Math.round(hours / 24)} сут назад`;
};

export const fmtInterval = (seconds) => {
  if (!seconds) return '—';
  return seconds >= 60 ? `${Math.round(seconds / 60)} мин` : `${seconds} с`;
};

export const Tag = ({ kind = 'info', children, className = '' }) => (
  <span className={`tag ${kind} ${className}`}>{children}</span>
);

export const Bar = ({ value, color = '#2F6BF6' }) => (
  <div className="bar"><i style={{ width: `${Math.max(0, Math.min(100, value))}%`, background: color }} /></div>
);

export const StatusCell = ({ status }) => {
  const meta = statusMeta(status);
  return (
    <span className="status-cell">
      <i className="dot" style={{ background: meta.color }} />{meta.short}
    </span>
  );
};

export const Metric = ({ label, value, unit, sub, color }) => (
  <div className="metric">
    <div className="eyebrow">{label}</div>
    <div className="v" style={color ? { color } : undefined}>
      {value}{unit ? <small>{unit}</small> : null}
    </div>
    {sub ? <div className="s">{sub}</div> : null}
  </div>
);

export const Section = ({ title, children }) => (
  <div className="section"><h3>{title}</h3><span className="line" />{children}</div>
);

export const Pair = ({ label, children }) => (
  <div className="pair"><span>{label}</span><b>{children ?? '—'}</b></div>
);
