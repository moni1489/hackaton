/* Графики метрик. Одна шкала на график: скорость и задержка разведены
   по разным полотнам, двойных осей нет. Палитра проверена на различимость
   при дальтонизме (ΔE >= 15 для соседних серий при нормальном зрении). */
import {
  Area, AreaChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';

export const SERIES = {
  download: '#2F6BF6',
  upload: '#0E9C8A',
  ping: '#D9730D',
  jitter: '#B23A8E',
};

const AXIS = { fontSize: 10.5, fill: '#8B95AB', fontFamily: 'JetBrains Mono, monospace' };
const GRID = '#EEF0F4';

const fmtTime = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  return `${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:00`;
};

function TipBox({ active, payload, label, unit }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: '#fff', border: '1px solid #E6E9EF', borderRadius: 12,
      boxShadow: '0 12px 40px rgba(16,24,40,.14)', padding: '10px 13px', fontSize: 12,
    }}>
      <div style={{ fontFamily: 'JetBrains Mono, monospace', color: '#8B95AB', marginBottom: 6 }}>
        {label}
      </div>
      {payload.map((p) => (
        <div key={p.dataKey} style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 3 }}>
          <i style={{ width: 8, height: 8, borderRadius: 2, background: p.color, display: 'block' }} />
          <span style={{ color: '#46506A', fontWeight: 600 }}>{p.name}</span>
          <b style={{ marginLeft: 'auto', fontFamily: 'JetBrains Mono, monospace' }}>
            {p.value} {unit}
          </b>
        </div>
      ))}
    </div>
  );
}

const legendStyle = { fontSize: 11, fontWeight: 600, color: '#46506A', paddingTop: 6 };

/** Скорость канала: загрузка и отдача, одна шкала (Мбит/с). */
export function SpeedChart({ measurements, contract, height = 210 }) {
  const data = measurements.map((m) => ({
    t: fmtTime(m.timestamp),
    download: Math.round((m.download_speed || 0) * 10) / 10,
    upload: Math.round((m.upload_speed || 0) * 10) / 10,
  }));
  if (!data.length) return <div className="empty">Нет данных за выбранный период</div>;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 6, right: 10, left: -18, bottom: 0 }}>
        <defs>
          <linearGradient id="gDown" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={SERIES.download} stopOpacity={0.22} />
            <stop offset="100%" stopColor={SERIES.download} stopOpacity={0} />
          </linearGradient>
          <linearGradient id="gUp" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={SERIES.upload} stopOpacity={0.18} />
            <stop offset="100%" stopColor={SERIES.upload} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="t" tick={AXIS} axisLine={false} tickLine={false} minTickGap={44} />
        <YAxis tick={AXIS} axisLine={false} tickLine={false} width={46} />
        {contract ? (
          <ReferenceLine y={contract} stroke="#8B95AB" strokeDasharray="4 4"
            label={{ value: `договор ${contract}`, position: 'insideTopRight',
                     fill: '#8B95AB', fontSize: 10 }} />
        ) : null}
        <Tooltip content={<TipBox unit="Мбит/с" />} />
        <Legend wrapperStyle={legendStyle} iconType="plainline" iconSize={14} />
        <Area type="monotone" dataKey="download" name="Загрузка" stroke={SERIES.download}
              strokeWidth={2} fill="url(#gDown)" dot={false} activeDot={{ r: 4, strokeWidth: 2 }} />
        <Area type="monotone" dataKey="upload" name="Отдача" stroke={SERIES.upload}
              strokeWidth={2} fill="url(#gUp)" dot={false} activeDot={{ r: 4, strokeWidth: 2 }} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/** Задержка: ping и jitter, одна шкала (мс). */
export function LatencyChart({ measurements, height = 190 }) {
  const data = measurements.map((m) => ({
    t: fmtTime(m.timestamp),
    ping: Math.round((m.ping || 0) * 10) / 10,
    jitter: Math.round((m.jitter || 0) * 10) / 10,
  }));
  if (!data.length) return <div className="empty">Нет данных</div>;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 6, right: 10, left: -18, bottom: 0 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="t" tick={AXIS} axisLine={false} tickLine={false} minTickGap={44} />
        <YAxis tick={AXIS} axisLine={false} tickLine={false} width={46} />
        <ReferenceLine y={80} stroke="#8B95AB" strokeDasharray="4 4"
          label={{ value: 'порог 80 мс', position: 'insideTopRight', fill: '#8B95AB', fontSize: 10 }} />
        <Tooltip content={<TipBox unit="мс" />} />
        <Legend wrapperStyle={legendStyle} iconType="plainline" iconSize={14} />
        <Line type="monotone" dataKey="ping" name="Задержка" stroke={SERIES.ping}
              strokeWidth={2} dot={false} activeDot={{ r: 4, strokeWidth: 2 }} />
        <Line type="monotone" dataKey="jitter" name="Джиттер" stroke={SERIES.jitter}
              strokeWidth={2} dot={false} activeDot={{ r: 4, strokeWidth: 2 }} />
      </LineChart>
    </ResponsiveContainer>
  );
}

/** Потери пакетов — одна серия, легенда не нужна: её называет заголовок. */
export function LossChart({ measurements, height = 150 }) {
  const data = measurements.map((m) => ({
    t: fmtTime(m.timestamp),
    loss: Math.round((m.packet_loss || 0) * 100) / 100,
  }));
  if (!data.length) return <div className="empty">Нет данных</div>;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 6, right: 10, left: -18, bottom: 0 }}>
        <defs>
          <linearGradient id="gLoss" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#E0453E" stopOpacity={0.25} />
            <stop offset="100%" stopColor="#E0453E" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="t" tick={AXIS} axisLine={false} tickLine={false} minTickGap={44} />
        <YAxis tick={AXIS} axisLine={false} tickLine={false} width={46} unit="%" />
        <Tooltip content={<TipBox unit="%" />} />
        <Area type="monotone" dataKey="loss" name="Потери пакетов" stroke="#E0453E"
              strokeWidth={2} fill="url(#gLoss)" dot={false} activeDot={{ r: 4, strokeWidth: 2 }} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/** Микро-спарклайн под KPI: без осей, только форма тренда. */
export function Spark({ points, color = '#2F6BF6', height = 34 }) {
  if (!points?.length) return null;
  const data = points.map((value, index) => ({ i: index, value }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id={`sp-${color.slice(1)}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.3} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area type="monotone" dataKey="value" stroke={color} strokeWidth={1.6}
              fill={`url(#sp-${color.slice(1)})`} dot={false} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}
