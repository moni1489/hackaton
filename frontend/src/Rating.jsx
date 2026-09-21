/* Рейтинг организаций: лучшие и проблемные, сравнение с прошлым периодом, динамика за квартал. */
import { useEffect, useState } from 'react';
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { api } from './api';
import { IcoTrend } from './icons';
import { Bar, Tag } from './ui';

const PERIODS = [[7, 'Неделя'], [30, 'Месяц'], [90, 'Квартал']];
const GRADE = {
  'Отлично': { kind: 'ok', color: '#17A65B' },
  'Хорошо': { kind: 'info', color: '#2F6BF6' },
  'Удовлетворительно': { kind: 'warn', color: '#E4962A' },
  'Проблемная': { kind: 'danger', color: '#E0453E' },
};

function Delta({ item }) {
  if (item.delta == null) return <span style={{ color: 'var(--ink-3)' }}>—</span>;
  const kind = item.delta > 0.5 ? 'up' : item.delta < -0.5 ? 'down' : 'flat';
  const move = item.rank_change ? ` · ${item.rank_change > 0 ? '▲' : '▼'}${Math.abs(item.rank_change)}` : '';
  return (
    <span className={`delta ${kind}`} title="Оценка и место относительно предыдущего периода такой же длины">
      {item.delta > 0 ? '+' : ''}{item.delta}{move}
    </span>
  );
}

function Leaders({ title, items, onOpenSchool }) {
  return (
    <div className="page-card" style={{ flex: 1, minWidth: 280 }}>
      <div className="page-card-head"><h3>{title}</h3></div>
      {items.map((item) => (
        <div key={item.school_id} onClick={() => onOpenSchool(item.school_id)}
          style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '9px 16px', cursor: 'pointer',
            borderBottom: '1px solid var(--line)' }}>
          <b style={{ width: 30, fontFamily: 'var(--mono)' }}>#{item.rank}</b>
          <span style={{ flex: 1, fontSize: 13, fontWeight: 600 }}>{item.name}</span>
          <b style={{ fontFamily: 'var(--mono)', color: GRADE[item.grade].color }}>{item.score}</b>
        </div>
      ))}
    </div>
  );
}

export default function RatingPage({ onOpenSchool }) {
  const [days, setDays] = useState(30);
  const [region, setRegion] = useState('');
  const [data, setData] = useState(null);
  const [history, setHistory] = useState([]);
  const [error, setError] = useState('');

  useEffect(() => {
    api.rating(days).then(setData).catch((e) => setError(e.message));
    api.ratingHistory(days).then(setHistory).catch(() => setHistory([]));
  }, [days]);

  if (error) return <div className="page"><div className="empty">{error}</div></div>;
  if (!data) return <div className="page"><div className="empty">Расчёт рейтинга…</div></div>;

  const rated = data.items.filter((i) => i.rank);
  const regions = [...new Set(data.items.map((i) => i.region).filter(Boolean))].sort();
  const shown = data.items.filter((i) => !region || i.region === region);

  return (
    <div className="page">
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <div className="segmented">
          {PERIODS.map(([value, label]) => (
            <button key={value} className={days === value ? 'active' : ''}
              onClick={() => { setData(null); setError(''); setDays(value); }}>{label}</button>
          ))}
        </div>
        <div className="tool-select">
          <select value={region} onChange={(e) => setRegion(e.target.value)}>
            <option value="">Все районы</option>
            {regions.map((r) => <option key={r}>{r}</option>)}
          </select>
        </div>
        <span style={{ fontSize: 12, color: 'var(--ink-2)' }}>
          Оценка 0–100 = 50% соответствие SLA + 25% доступность + 25% скорость к договору.
          Средняя по области: <b>{data.avg_score ?? '—'}</b> · оценено {data.rated}
          {data.unrated ? ` · без оценки (мало замеров): ${data.unrated}` : ''}
        </span>
      </div>

      {rated.length ? (
        <div style={{ display: 'flex', gap: 14, marginBottom: 14, flexWrap: 'wrap' }}>
          <Leaders title="Лучшие" items={rated.slice(0, 5)} onOpenSchool={onOpenSchool} />
          <Leaders title="Проблемные" items={rated.slice(-5).reverse()} onOpenSchool={onOpenSchool} />
        </div>
      ) : null}

      <div className="page-card" style={{ marginBottom: 14 }}>
        <div className="page-card-head">
          <IcoTrend size={17} style={{ color: 'var(--accent)' }} />
          <h3>Динамика по области (среднее по школам, по дням)</h3>
        </div>
        <div style={{ padding: 14 }}>
          {history.length < 2 ? <div className="empty">Недостаточно дней для графика</div> : (
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={history}>
                <CartesianGrid stroke="#EEF0F4" vertical={false} />
                <XAxis dataKey="day" tick={{ fontSize: 10.5 }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 10.5 }} width={32} />
                <Tooltip />
                <Legend />
                <Line dataKey="score" name="Оценка" stroke="#2F6BF6" dot={false} strokeWidth={2} />
                <Line dataKey="sla_pct" name="SLA, %" stroke="#0E9C8A" dot={false} />
                <Line dataKey="availability_pct" name="Доступность, %" stroke="#D9730D" dot={false} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      <div className="page-card">
        <div className="page-card-head">
          <h3>Все организации</h3>
          <Tag kind="off" className="tag ml">{shown.length} записей</Tag>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table className="grid">
            <thead>
              <tr>
                <th>Место</th><th>Наименование</th><th>Район</th><th>Поставщик</th>
                <th style={{ minWidth: 150 }}>Оценка</th><th>SLA</th><th>Доступн.</th>
                <th>Скорость / договор</th><th>К прошлому периоду</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((item) => (
                <tr key={item.school_id} onClick={() => onOpenSchool(item.school_id)}>
                  <td className="num">{item.rank ?? '—'}</td>
                  <td style={{ fontWeight: 600 }}>{item.name}</td>
                  <td>{item.region}</td>
                  <td>{item.provider}</td>
                  {item.rank ? (
                    <>
                      <td>
                        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                          <b style={{ fontFamily: 'var(--mono)', width: 38 }}>{item.score}</b>
                          <Bar value={item.score} color={GRADE[item.grade].color} />
                        </div>
                        <Tag kind={GRADE[item.grade].kind}>{item.grade}</Tag>
                      </td>
                      <td className="num">{item.sla_pct}%</td>
                      <td className="num">{item.availability_pct}%</td>
                      <td className="num">{item.avg_download} / {item.contract_speed_down ?? '—'}</td>
                      <td><Delta item={item} /></td>
                    </>
                  ) : (
                    <td colSpan={5} style={{ color: 'var(--ink-3)' }}>
                      мало замеров за период ({item.samples})
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
