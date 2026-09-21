import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import { Tag } from './ui';

const time = (v) => v ? new Date(v.endsWith('Z') ? v : `${v}Z`).toLocaleString('ru-RU') : 'нет данных';
const stateLabel = { ok: 'Загружено', no_data: 'Нет данных', not_configured: 'Требуется подключение', error: 'Ошибка обновления' };

export default function PublicDataPage() {
  const [catalog, setCatalog] = useState(null);
  const [connections, setConnections] = useState([]);
  const [network, setNetwork] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [search, setSearch] = useState('');
  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [c, p, n] = await Promise.all([api.publicSchools(), api.publicConnections(), api.externalNetwork()]);
      setCatalog(c); setConnections(p); setNetwork(n); setError('');
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }, []);
  useEffect(() => { const timer = setTimeout(load, 0); return () => clearTimeout(timer); }, [load]);
  const sources = network?.sources || {};
  const atlas = sources.ripe_atlas?.payload || {};
  const mlab = sources.mlab?.payload || {};
  const filtered = (catalog?.items || []).filter((r) => `${r.district} ${r.settlement} ${r.address}`.toLowerCase().includes(search.toLowerCase()));
  return <div className="page">
    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
      <p>Официальные сведения о школах и независимые наблюдения за сетью.</p>
      <button className="btn" onClick={load} disabled={busy}>{busy ? 'Загрузка…' : 'Обновить показания'}</button>
    </div>
    {error && <div role="alert" className="login-error">{error}</div>}
    <div className="page-card" style={{ padding: 18, marginBottom: 16 }}>
      <h3>Независимая проверка сети</h3>
      <p>IODA: {network?.ioda_regional_event === true ? 'найдено совпадающее по времени региональное событие' : network?.ioda_regional_event === false ? 'в полученных данных нет совпадающего события' : 'нет данных для текущего момента'}.</p>
      <p>{network?.interpretation}</p>
      {['ioda', 'ripe_atlas', 'mlab'].map((key) => {
        const s = sources[key];
        return <div key={key} style={{ margin: '12px 0' }}>
          <strong>{key === 'ripe_atlas' ? 'RIPE Atlas' : key.toUpperCase()}</strong>{' '}
          <Tag kind={s?.status === 'ok' && !s?.stale ? 'ok' : 'warn'}>{stateLabel[s?.status || 'no_data']}</Tag>{' '}
          {s?.stale && s?.fetched_at ? <Tag kind="warn">Снимок устарел</Tag> : null}
          <div>Загружено: {time(s?.fetched_at)} {s?.source_url && <a href={s.source_url} target="_blank" rel="noreferrer">Источник</a>}</div>
          <div>{s?.error || s?.payload?.note}</div>
        </div>;
      })}
      <h4>RIPE Atlas: {atlas.scope || 'покрытие не получено'}</h4>
      <div style={{ overflowX: 'auto' }}><table className="data-table"><thead><tr><th>Зонд / ASN</th><th>Цель</th><th>RTT</th><th>Потери</th><th>Время UTC</th><th>Свежесть</th></tr></thead>
        <tbody>{(atlas.observations || []).map((r) => <tr key={`${r.probe_id}-${r.measurement_id}`}>
          <td>{r.probe_id} / {atlas.probes?.find((p) => p.id === r.probe_id)?.asn_v4 || '—'}</td><td>{r.target}</td>
          <td>{r.rtt_ms == null ? 'нет ответа' : `${r.rtt_ms.toFixed(1)} мс`}</td>
          <td>{r.packet_loss_pct == null ? '—' : `${r.packet_loss_pct.toFixed(1)}%`}</td>
          <td>{new Date(r.timestamp * 1000).toISOString()}</td><td>{new Date(`${network.at}Z`).getTime() / 1000 - r.timestamp > 3600 ? 'Устарело' : 'За последний час'}</td>
        </tr>)}</tbody></table></div>
      {!atlas.observations?.length && <p>Измерения RIPE Atlas ещё не получены.</p>}
      <h4>M-Lab: дневные медианы по городам и сетям Казахстана</h4>
      <p>Добровольные тесты пользователей; геолокация по IP. Данные публикуются с задержкой и не описывают отдельную школу.</p>
      {mlab.rows?.length ? <div style={{ maxHeight: 260, overflow: 'auto' }}><table className="data-table"><thead><tr><th>День</th><th>Город / ASN</th><th>Тестов</th><th>Download</th><th>RTT</th></tr></thead>
        <tbody>{mlab.rows.map((r) => <tr key={`${r.day}-${r.city}-${r.asn}`}><td>{r.day}</td><td>{r.city} / {r.asn}</td><td>{r.tests}</td><td>{r.download_mbps?.toFixed(1) ?? '—'} Мбит/с</td><td>{r.rtt_ms?.toFixed(1) ?? '—'} мс</td></tr>)}</tbody></table></div> : <p>Выборка M-Lab не загружена. Требуется подключить Google BigQuery или импортировать выгрузку.</p>}
    </div>
    <div className="page-card" style={{ padding: 18, marginBottom: 16 }}>
      <h3>Опубликованные подключения — {connections.length}</h3>
      <p>Скорость из публикации не подтверждает договорный SLA. Дата публикации источника не установлена; параметры требуют актуализации.</p>
      <div style={{ maxHeight: 340, overflow: 'auto' }}><table className="data-table"><thead><tr><th>Школа</th><th>Технология</th><th>Скорость</th><th>Сопоставление</th><th>Источник</th></tr></thead>
        <tbody>{connections.map((r) => <tr key={r.key}><td>{r.school_name}</td><td>{r.technology}</td><td>{r.speed_down_mbps ?? '—'} Мбит/с</td><td>{r.school_id ? 'Найдена карточка' : 'Требует сверки'}</td><td><a href={r.source_url} target="_blank" rel="noreferrer">gov.kz</a></td></tr>)}</tbody></table></div>
    </div>
    <div className="page-card" style={{ padding: 18 }}>
      <h3>Официальный справочник eGov — {catalog?.total ?? '—'} записей ВКО</h3>
      <p>{catalog?.note}</p>
      <input aria-label="Поиск по официальному справочнику" placeholder="Район, населённый пункт, адрес" value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: '100%', marginBottom: 14 }} />
      <div style={{ maxHeight: 440, overflow: 'auto' }}><table className="data-table"><thead><tr><th>ID eGov</th><th>Район</th><th>Адрес</th><th>Координаты</th><th>Учеников</th></tr></thead>
        <tbody>{filtered.map((r) => <tr key={r.external_id}><td><a href={r.source_url} target="_blank" rel="noreferrer">{r.external_id}</a></td><td>{r.district}</td><td>{r.settlement}, {r.address}</td><td>{r.lat}, {r.lng}</td><td>{r.students ?? '—'}</td></tr>)}</tbody></table></div>
    </div>
  </div>;
}
