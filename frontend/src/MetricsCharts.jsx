/**
 * MetricsCharts.jsx
 * Компонент метрик — используется на главной (региональные) и в drilldown (школа).
 */
import React from 'react';
import {
  AreaChart, Area, LineChart, Line,
  BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine
} from 'recharts';

// ── КАСТОМНЫЙ ТУЛТИП ──────────────────────────────────────────────────────────
function CustomTooltip({ active, payload, label, unit }) {
  if (!active || !payload || !payload.length) return null;
  return (
    <div style={{
      background: '#0D1B2A',
      border: '1px solid #1E3A56',
      borderRadius: 6,
      padding: '0.5rem 0.75rem',
      fontSize: '0.78rem',
      color: '#E2EAF4',
    }}>
      <div style={{ color: '#7B95B4', marginBottom: 3 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color }}>
          {p.name}: <strong>{typeof p.value === 'number' ? p.value.toFixed(1) : p.value} {unit || ''}</strong>
        </div>
      ))}
    </div>
  );
}

// ── МИНИ КАРТОЧКА ГРАФИКА ─────────────────────────────────────────────────────
export function MetricCard({ title, value, unit, sub, color, children }) {
  return (
    <div style={{
      background: '#13243A',
      border: '1px solid #1E3A56',
      borderRadius: 10,
      padding: '0.85rem 1rem',
      display: 'flex',
      flexDirection: 'column',
      gap: '0.5rem',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span style={{ fontSize: '0.72rem', color: '#7B95B4', textTransform: 'uppercase', letterSpacing: '0.07em', fontWeight: 700 }}>
          {title}
        </span>
        {sub && <span style={{ fontSize: '0.7rem', color: '#4A6782' }}>{sub}</span>}
      </div>
      <div style={{ fontSize: '1.6rem', fontWeight: 800, color: color || '#E2EAF4', lineHeight: 1 }}>
        {value}<span style={{ fontSize: '0.85rem', fontWeight: 500, color: '#7B95B4', marginLeft: 4 }}>{unit}</span>
      </div>
      <div style={{ height: 60 }}>
        {children}
      </div>
    </div>
  );
}

// ── РЕГИОНАЛЬНЫЕ МЕТРИКИ (главная страница) ────────────────────────────────────
export function RegionalMetrics({ schools }) {
  if (!schools || schools.length === 0) return null;

  // Построим данные по диапазонам скоростей для гистограммы
  const ranges = [
    { label: '0–10', min: 0,   max: 10  },
    { label: '10–20', min: 10, max: 20  },
    { label: '20–50', min: 20, max: 50  },
    { label: '50–100', min: 50, max: 100 },
    { label: '100+',  min: 100, max: Infinity },
  ];
  const speedDist = ranges.map(r => ({
    name: r.label,
    count: schools.filter(s => s.current_download >= r.min && s.current_download < r.max).length,
  }));

  // Топ-провайдеры по среднему качеству
  const byProvider = {};
  schools.forEach(s => {
    if (!byProvider[s.provider]) byProvider[s.provider] = { sum: 0, cnt: 0 };
    byProvider[s.provider].sum += s.current_download;
    byProvider[s.provider].cnt++;
  });
  const providerData = Object.entries(byProvider).map(([name, d]) => ({
    name: name.replace('АО «', '').replace('»', '').split(' ')[0],
    avg: Math.round(d.sum / d.cnt * 10) / 10,
  })).sort((a, b) => b.avg - a.avg);

  // Данные Ping по регионам (берём среднее)
  const byRegion = {};
  schools.forEach(s => {
    if (!byRegion[s.region]) byRegion[s.region] = { pingSum: 0, cnt: 0 };
    byRegion[s.region].pingSum += s.current_ping;
    byRegion[s.region].cnt++;
  });
  const pingData = Object.entries(byRegion).map(([region, d]) => ({
    name: region.replace('район', 'р-н').replace('г. ', ''),
    ping: Math.round(d.pingSum / d.cnt),
  })).sort((a, b) => a.ping - b.ping);

  // Fake real-time last 10 минут (симуляция, т.к. агент пишет раз в минуту)
  const onlineSchools = schools.filter(s => s.status !== 'Нет соединения');
  const avgDown = onlineSchools.length
    ? Math.round(onlineSchools.reduce((a, s) => a + s.current_download, 0) / onlineSchools.length * 10) / 10
    : 0;
  const avgPing = onlineSchools.length
    ? Math.round(onlineSchools.reduce((a, s) => a + s.current_ping, 0) / onlineSchools.length)
    : 0;
  const avgLoss = onlineSchools.length
    ? Math.round(onlineSchools.reduce((a, s) => a + s.current_packet_loss, 0) / onlineSchools.length * 10) / 10
    : 0;

  return (
    <div>
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(3, 1fr)',
        gap: '1.25rem',
        marginBottom: '1.25rem',
      }}>
        {/* 1. Распределение скоростей Download */}
        <div style={{
          background: '#13243A',
          border: '1px solid #1E3A56',
          borderRadius: 10,
          padding: '0.85rem 1rem',
        }}>
          <div style={{ fontSize: '0.72rem', color: '#7B95B4', textTransform: 'uppercase', letterSpacing: '0.07em', fontWeight: 700, marginBottom: '0.75rem' }}>
            Распределение Download по школам (Мбит/с)
          </div>
          <ResponsiveContainer width="100%" height={150}>
            <BarChart data={speedDist} barSize={28}>
              <CartesianGrid vertical={false} stroke="#1E3A56" />
              <XAxis dataKey="name" tick={{ fill: '#4A6782', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: '#4A6782', fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip content={<CustomTooltip unit="школ" />} />
              <Bar dataKey="count" name="Кол-во школ" fill="#2563EB" radius={[4, 4, 0, 0]}>
              </Bar>
              <ReferenceLine y={0} stroke="#1E3A56" />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* 2. Средний Download по провайдерам */}
        <div style={{
          background: '#13243A',
          border: '1px solid #1E3A56',
          borderRadius: 10,
          padding: '0.85rem 1rem',
        }}>
          <div style={{ fontSize: '0.72rem', color: '#7B95B4', textTransform: 'uppercase', letterSpacing: '0.07em', fontWeight: 700, marginBottom: '0.75rem' }}>
            Средний Download по провайдерам (Мбит/с)
          </div>
          <ResponsiveContainer width="100%" height={150}>
            <BarChart data={providerData} layout="vertical" barSize={14}>
              <CartesianGrid horizontal={false} stroke="#1E3A56" />
              <XAxis type="number" tick={{ fill: '#4A6782', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis dataKey="name" type="category" tick={{ fill: '#7B95B4', fontSize: 11 }} axisLine={false} tickLine={false} width={80} />
              <Tooltip content={<CustomTooltip unit="Мбит/с" />} />
              <ReferenceLine x={20} stroke="#DC2626" strokeDasharray="4 2" label={{ value: 'мин.20', fill: '#DC2626', fontSize: 10 }} />
              <Bar dataKey="avg" name="Ср. скорость" fill="#16A34A" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* 3. Средний Ping по районам */}
        <div style={{
          background: '#13243A',
          border: '1px solid #1E3A56',
          borderRadius: 10,
          padding: '0.85rem 1rem',
        }}>
          <div style={{ fontSize: '0.72rem', color: '#7B95B4', textTransform: 'uppercase', letterSpacing: '0.07em', fontWeight: 700, marginBottom: '0.75rem' }}>
            Средний Ping по районам (мс)
          </div>
          <ResponsiveContainer width="100%" height={150}>
            <BarChart data={pingData} layout="vertical" barSize={12}>
              <CartesianGrid horizontal={false} stroke="#1E3A56" />
              <XAxis type="number" tick={{ fill: '#4A6782', fontSize: 11 }} axisLine={false} tickLine={false} />
              <YAxis dataKey="name" type="category" tick={{ fill: '#7B95B4', fontSize: 11 }} axisLine={false} tickLine={false} width={80} />
              <Tooltip content={<CustomTooltip unit="мс" />} />
              <ReferenceLine x={80} stroke="#D97706" strokeDasharray="4 2" />
              <Bar dataKey="ping" name="Ping" fill="#D97706" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Сводные KPI под графиками */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(4, 1fr)',
        gap: '1px',
        background: '#1E3A56',
        border: '1px solid #1E3A56',
        borderRadius: 8,
        overflow: 'hidden',
        marginBottom: '1.25rem',
      }}>
        {[
          { label: 'Ср. Download (онлайн)', value: avgDown, unit: 'Мбит/с', color: '#86EFAC' },
          { label: 'Ср. Ping (онлайн)',     value: avgPing,  unit: 'мс',     color: avgPing > 80 ? '#FCD34D' : '#86EFAC' },
          { label: 'Ср. Packet Loss',        value: avgLoss,  unit: '%',      color: avgLoss > 2 ? '#F87171' : '#86EFAC' },
          { label: 'Школ без связи',          value: schools.filter(s => s.status === 'Нет соединения').length, unit: 'шк.', color: '#F87171' },
        ].map(item => (
          <div key={item.label} style={{ background: '#0D1B2A', padding: '0.75rem 1rem' }}>
            <div style={{ fontSize: '0.68rem', color: '#4A6782', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 700, marginBottom: '0.35rem' }}>
              {item.label}
            </div>
            <div style={{ fontSize: '1.4rem', fontWeight: 800, color: item.color }}>
              {item.value}<span style={{ fontSize: '0.75rem', color: '#7B95B4', marginLeft: 4 }}>{item.unit}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── ГРАФИК ШКОЛЫ (drilldown modal) ────────────────────────────────────────────
export function SchoolMetricsChart({ measurements }) {
  if (!measurements || measurements.length === 0) {
    return (
      <div style={{ color: '#4A6782', fontSize: '0.82rem', padding: '1rem 0' }}>
        История измерений отсутствует (агент ещё не отправлял данные).
      </div>
    );
  }

  const data = measurements.map(m => ({
    time: new Date(m.timestamp).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }),
    down: m.download_speed,
    up: m.upload_speed,
    ping: m.ping,
    loss: m.packet_loss,
  }));

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
      {/* Download / Upload */}
      <div style={{ background: '#0D1B2A', border: '1px solid #1E3A56', borderRadius: 8, padding: '0.75rem 1rem' }}>
        <div style={{ fontSize: '0.7rem', color: '#7B95B4', textTransform: 'uppercase', fontWeight: 700, marginBottom: '0.6rem', letterSpacing: '0.06em' }}>
          Download / Upload (Мбит/с)
        </div>
        <ResponsiveContainer width="100%" height={130}>
          <AreaChart data={data}>
            <defs>
              <linearGradient id="gradDown" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor="#2563EB" stopOpacity={0.25} />
                <stop offset="95%" stopColor="#2563EB" stopOpacity={0}    />
              </linearGradient>
              <linearGradient id="gradUp" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor="#16A34A" stopOpacity={0.2} />
                <stop offset="95%" stopColor="#16A34A" stopOpacity={0}   />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#1E3A56" strokeDasharray="3 3" />
            <XAxis dataKey="time" tick={{ fill: '#4A6782', fontSize: 10 }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fill: '#4A6782', fontSize: 10 }} axisLine={false} tickLine={false} />
            <Tooltip content={<CustomTooltip unit="Мбит/с" />} />
            <ReferenceLine y={20} stroke="#DC2626" strokeDasharray="4 2" />
            <Area type="monotone" dataKey="down" name="Download" stroke="#3B82F6" fill="url(#gradDown)" strokeWidth={2} dot={false} />
            <Area type="monotone" dataKey="up"   name="Upload"   stroke="#16A34A" fill="url(#gradUp)"   strokeWidth={2} dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Ping */}
      <div style={{ background: '#0D1B2A', border: '1px solid #1E3A56', borderRadius: 8, padding: '0.75rem 1rem' }}>
        <div style={{ fontSize: '0.7rem', color: '#7B95B4', textTransform: 'uppercase', fontWeight: 700, marginBottom: '0.6rem', letterSpacing: '0.06em' }}>
          Ping (мс)
        </div>
        <ResponsiveContainer width="100%" height={130}>
          <LineChart data={data}>
            <CartesianGrid stroke="#1E3A56" strokeDasharray="3 3" />
            <XAxis dataKey="time" tick={{ fill: '#4A6782', fontSize: 10 }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fill: '#4A6782', fontSize: 10 }} axisLine={false} tickLine={false} />
            <Tooltip content={<CustomTooltip unit="мс" />} />
            <ReferenceLine y={80} stroke="#D97706" strokeDasharray="4 2" />
            <Line type="monotone" dataKey="ping" name="Ping" stroke="#F59E0B" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Packet Loss */}
      <div style={{ background: '#0D1B2A', border: '1px solid #1E3A56', borderRadius: 8, padding: '0.75rem 1rem' }}>
        <div style={{ fontSize: '0.7rem', color: '#7B95B4', textTransform: 'uppercase', fontWeight: 700, marginBottom: '0.6rem', letterSpacing: '0.06em' }}>
          Packet Loss (%)
        </div>
        <ResponsiveContainer width="100%" height={130}>
          <AreaChart data={data}>
            <defs>
              <linearGradient id="gradLoss" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor="#DC2626" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#DC2626" stopOpacity={0}   />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#1E3A56" strokeDasharray="3 3" />
            <XAxis dataKey="time" tick={{ fill: '#4A6782', fontSize: 10 }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fill: '#4A6782', fontSize: 10 }} axisLine={false} tickLine={false} />
            <Tooltip content={<CustomTooltip unit="%" />} />
            <ReferenceLine y={2} stroke="#D97706" strokeDasharray="4 2" />
            <Area type="monotone" dataKey="loss" name="Loss" stroke="#DC2626" fill="url(#gradLoss)" strokeWidth={2} dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {/* Gauge-style текущие значения */}
      <div style={{ background: '#0D1B2A', border: '1px solid #1E3A56', borderRadius: 8, padding: '0.75rem 1rem' }}>
        <div style={{ fontSize: '0.7rem', color: '#7B95B4', textTransform: 'uppercase', fontWeight: 700, marginBottom: '0.6rem', letterSpacing: '0.06em' }}>
          Последнее измерение
        </div>
        {(() => {
          const last = data[data.length - 1];
          return (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem', marginTop: '0.5rem' }}>
              {[
                { label: '↓ Download', v: last?.down,  unit: 'Мбит/с', bad: v => v < 20 },
                { label: '↑ Upload',   v: last?.up,    unit: 'Мбит/с', bad: v => v < 10 },
                { label: '⏱ Ping',     v: last?.ping,  unit: 'мс',     bad: v => v > 80  },
                { label: '📉 Loss',    v: last?.loss,   unit: '%',      bad: v => v > 2   },
              ].map(item => (
                <div key={item.label} style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: '0.68rem', color: '#4A6782', marginBottom: 2 }}>{item.label}</div>
                  <div style={{
                    fontSize: '1.35rem',
                    fontWeight: 800,
                    color: item.bad(item.v) ? '#F87171' : '#86EFAC',
                  }}>
                    {typeof item.v === 'number' ? item.v.toFixed(1) : '—'}
                    <span style={{ fontSize: '0.7rem', color: '#7B95B4', marginLeft: 3 }}>{item.unit}</span>
                  </div>
                </div>
              ))}
            </div>
          );
        })()}
      </div>
    </div>
  );
}
