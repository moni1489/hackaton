/* Страницы-реестры: школы, ПК-агенты, инциденты, управление системой. */
import { useEffect, useState } from 'react';
import { api } from './api';
import { causeUi } from './Diagnostics';
import { IcoDoc, IcoPc, IcoQueue, IcoRefresh, IcoSchool, IcoShield, IcoUsers } from './icons';
import {
  Pair, Section, StatusCell, Tag, deviceMeta, fmtDateTime, fmtInterval,
} from './ui';

/* ---------- Школы ------------------------------------------------- */
export function SchoolsPage({ schools, onOpenSchool }) {
  return (
    <div className="page">
      <div className="page-card">
        <div className="page-card-head">
          <IcoSchool size={17} style={{ color: 'var(--accent)' }} />
          <h3>Организации образования</h3>
          <Tag kind="off" className="tag ml">{schools.length} записей</Tag>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table className="grid">
            <thead>
              <tr>
                <th>Код</th><th>Наименование</th><th>Район</th><th>Поставщик</th>
                <th>Линия</th><th>↓ факт</th><th>Договор</th><th>Ping</th><th>Статус</th>
              </tr>
            </thead>
            <tbody>
              {schools.map((school) => (
                <tr key={school.id} onClick={() => onOpenSchool(school.id)}>
                  <td><code>{school.school_id_code}</code></td>
                  <td style={{ fontWeight: 600 }}>{school.name}</td>
                  <td>{school.region}</td>
                  <td>{school.provider}</td>
                  <td style={{ fontSize: 12, color: 'var(--ink-2)' }}>{school.connection_type}</td>
                  <td className="num">{school.current_download}</td>
                  <td className="num" style={{ color: 'var(--ink-3)' }}>{school.contract_speed_down}</td>
                  <td className="num">{school.current_ping}</td>
                  <td><StatusCell status={school.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ---------- ПК-агенты --------------------------------------------- */
export function DevicesPage({ onOpenDevice }) {
  const [devices, setDevices] = useState([]);
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');

  useEffect(() => { api.devices().then(setDevices).catch((e) => setError(e.message)); }, []);

  const filtered = devices.filter((device) => {
    if (!query) return true;
    const needle = query.toLowerCase();
    return device.device_id.toLowerCase().includes(needle)
      || (device.school_name || '').toLowerCase().includes(needle)
      || (device.room || '').toLowerCase().includes(needle);
  });

  const problem = devices.filter((d) => d.status !== 'online').length;

  return (
    <div className="page">
      <div className="page-card">
        <div className="page-card-head">
          <IcoPc size={17} style={{ color: 'var(--accent)' }} />
          <h3>ПК-уровень прослеживания</h3>
          <Tag kind="off">{devices.length} агентов</Tag>
          {problem ? <Tag kind="warn">{problem} с отклонениями</Tag> : null}
          <div className="search" style={{ marginLeft: 'auto', width: 260, height: 34 }}>
            <input placeholder="ПК, школа или кабинет…" value={query}
              onChange={(e) => setQuery(e.target.value)} />
          </div>
        </div>
        {error ? <div className="empty">{error}</div> : null}
        <div style={{ overflowX: 'auto' }}>
          <table className="grid">
            <thead>
              <tr>
                <th>Device ID</th><th>Школа</th><th>Расположение</th><th>Линия</th>
                <th>↓ Мбит/с</th><th>Ping</th><th>Доступность</th><th>SLA</th>
                <th>Интервал</th><th>Статус</th>
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, 400).map((device) => {
                const meta = deviceMeta(device.status);
                return (
                  <tr key={device.device_id} onClick={() => onOpenDevice(device.device_id)}>
                    <td><code>{device.device_id}</code></td>
                    <td style={{ fontWeight: 600 }}>{device.school_name}</td>
                    <td style={{ fontSize: 12 }}>{device.room}</td>
                    <td style={{ fontSize: 12, color: 'var(--ink-2)' }}>{device.link_mode}</td>
                    <td className="num">{device.current_download ?? 0}</td>
                    <td className="num">{device.current_ping ?? 0}</td>
                    <td className="num">{device.availability_pct ?? 100}%</td>
                    <td className="num" style={{
                      color: (device.sla_compliance_pct ?? 100) >= 95 ? 'var(--ok)' : 'var(--warn)',
                    }}>{device.sla_compliance_pct ?? 100}%</td>
                    <td className="num" style={{ fontSize: 12 }}>
                      {fmtInterval(device.test_interval_sec)}
                      {device.diagnostic_mode === 'deep'
                        ? <span style={{ color: 'var(--violet)' }}> · deep</span> : null}
                    </td>
                    <td>
                      <span className="status-cell">
                        <i className="dot" style={{ background: meta.color }} />{meta.label}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {filtered.length > 400 ? (
          <div className="empty">Показаны первые 400 из {filtered.length} — уточните поиск.</div>
        ) : null}
      </div>
    </div>
  );
}

/* ---------- Инциденты --------------------------------------------- */
export function IncidentsPage({ incidents, role, onOpenSchool, onReload }) {
  const [busyId, setBusyId] = useState(null);

  const advance = async (incident, next) => {
    setBusyId(incident.id);
    try { await api.updateIncident(incident.id, next); await onReload(); }
    finally { setBusyId(null); }
  };

  const canManage = role === 'admin' || role === 'operator';

  return (
    <div className="page">
      <div className="page-card">
        <div className="page-card-head">
          <IcoDoc size={17} style={{ color: 'var(--danger)' }} />
          <h3>Лента инцидентов</h3>
          <Tag kind="danger">{incidents.length} записей</Tag>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table className="grid">
            <thead>
              <tr>
                <th>Номер</th><th>Школа</th><th>Район</th><th>Поставщик</th>
                <th>Вердикт «Виновника»</th><th>ПК-источник</th><th>Начало</th>
                <th>Описание</th><th>Статус</th>
                {canManage ? <th>Действие</th> : null}
              </tr>
            </thead>
            <tbody>
              {incidents.map((incident) => (
                <tr key={incident.id} onClick={() => onOpenSchool(incident.school_id)}>
                  <td className="num" style={{ fontWeight: 700 }}>{incident.incident_number}</td>
                  <td style={{ fontWeight: 600 }}>{incident.school_name}</td>
                  <td>{incident.region}</td>
                  <td>{incident.provider}</td>
                  <td>
                    {incident.root_cause ? (
                      <span title={`Ответственность: ${incident.responsible}`}>
                        <Tag kind={causeUi(incident.root_cause).tag}>
                          {causeUi(incident.root_cause).short}
                        </Tag>
                        {incident.root_cause_confidence ? (
                          <small style={{ marginLeft: 6, color: 'var(--ink-3)' }}>
                            {Math.round(incident.root_cause_confidence * 100)}%
                          </small>
                        ) : null}
                        {incident.operator_verdict
                          && incident.operator_verdict !== incident.root_cause ? (
                            <small style={{ marginLeft: 6, color: 'var(--warn)' }}
                              title="Оператор исправил вердикт — метка пойдёт в дообучение">
                              исправлен
                            </small>
                          ) : null}
                      </span>
                    ) : <span style={{ color: 'var(--ink-3)' }}>—</span>}
                  </td>
                  <td>{incident.device_id ? <code>{incident.device_id}</code> : '—'}</td>
                  <td className="num" style={{ fontSize: 12 }}>{fmtDateTime(incident.start_time)}</td>
                  <td style={{ fontSize: 12, maxWidth: 320, color: 'var(--ink-2)' }}>
                    {incident.description}
                  </td>
                  <td>
                    <Tag kind={incident.severity === 'critical' ? 'danger' : 'warn'}>
                      {incident.status}
                    </Tag>
                  </td>
                  {canManage ? (
                    <td onClick={(e) => e.stopPropagation()}>
                      <button className="btn" style={{ padding: '7px 11px', fontSize: 10 }}
                        disabled={busyId === incident.id || incident.status === 'Устранён'}
                        onClick={() => advance(incident,
                          incident.status === 'Новый' ? 'В работе' : 'Устранён')}>
                        {incident.status === 'Новый' ? 'В работу' : 'Устранён'}
                      </button>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!incidents.length ? <div className="empty">Открытых инцидентов нет.</div> : null}
      </div>
    </div>
  );
}

/* ---------- Управление системой ----------------------------------- */
export function AdminPage({ role }) {
  const [system, setSystem] = useState(null);
  const [sync, setSync] = useState(null);
  const [audit, setAudit] = useState(null);
  const [policy, setPolicy] = useState(null);
  const [users, setUsers] = useState([]);
  const [error, setError] = useState('');

  const load = () => {
    api.system().then(setSystem).catch((e) => setError(e.message));
    api.policy().then(setPolicy).catch(() => {});
    if (role === 'admin' || role === 'operator') api.syncMonitor().then(setSync).catch(() => {});
    if (role === 'admin') {
      api.audit().then(setAudit).catch(() => {});
      api.users().then(setUsers).catch(() => {});
    }
  };
  useEffect(load, [role]);

  return (
    <div className="page">
      {error ? <div className="empty">{error}</div> : null}

      <Section title="Состояние подсистем">
        <button className="btn" style={{ flex: 'none', padding: '7px 13px' }} onClick={load}>
          <IcoRefresh size={13} style={{ verticalAlign: -2, marginRight: 6 }} />Обновить
        </button>
      </Section>
      <div className="pair-grid">
        <div className="block">
          <div className="eyebrow" style={{ marginBottom: 8 }}>Приложение и хранилище</div>
          <Pair label="Фреймворк">{system?.api?.framework} {system?.api?.version}</Pair>
          <Pair label="СУБД">{system?.database?.dialect}</Pair>
          <Pair label="Школ / ПК-агентов">
            {system?.database?.schools} / {system?.database?.devices}
          </Pair>
          <Pair label="Замеров в базе">{system?.database?.measurements?.toLocaleString('ru-RU')}</Pair>
          <Pair label="Кэш">{system?.cache?.backend} · TTL {system?.cache?.ttl_sec} с</Pair>
        </div>
        <div className="block">
          <div className="eyebrow" style={{ marginBottom: 8 }}>
            <IcoQueue size={13} style={{ verticalAlign: -2, marginRight: 5 }} />
            Smart Sync — защита от лавины офлайн-догрузок
          </div>
          <Pair label="Брокер">{system?.smart_sync?.broker}</Pair>
          <Pair label="Воркеров">{system?.smart_sync?.workers}</Pair>
          <Pair label="Глубина очереди">{system?.smart_sync?.queue_depth}</Pair>
          <Pair label="Пакетов обработано">{system?.smart_sync?.processed}</Pair>
          <Pair label="Замеров записано">{system?.smart_sync?.rows?.toLocaleString('ru-RU')}</Pair>
          <Pair label="Отклонено по перегрузке">{system?.smart_sync?.rejected}</Pair>
        </div>
      </div>

      <Section title="Политики безопасности" />
      <div className="block">
        <Pair label="Транспорт">{policy?.transport}</Pair>
        <Pair label="Аутентификация агента">{policy?.device_auth}</Pair>
        <Pair label="Роли RBAC">{policy?.rbac_roles?.join(' · ')}</Pair>
        <Pair label="Лимиты запросов">
          агент {policy?.rate_limits?.agent_per_min}/мин · панель {policy?.rate_limits?.web_per_min}/мин
        </Pair>
        <Pair label="Аудит">{policy?.audit}</Pair>
        <Pair label="Минимизация данных">{policy?.data_minimization}</Pair>
      </div>

      {sync ? (
        <>
          <Section title="Последние офлайн-пакеты" />
          <div className="page-card">
            <table className="grid">
              <thead>
                <tr><th>Пакет</th><th>ПК-агент</th><th>Замеров</th><th>Принят</th><th>Обработан</th><th>Статус</th></tr>
              </thead>
              <tbody>
                {sync.recent.length ? sync.recent.map((batch) => (
                  <tr key={batch.id} style={{ cursor: 'default' }}>
                    <td className="num">#{batch.id}</td>
                    <td><code>{batch.device_id}</code></td>
                    <td className="num">{batch.items}</td>
                    <td className="num" style={{ fontSize: 12 }}>{fmtDateTime(batch.received_at)}</td>
                    <td className="num" style={{ fontSize: 12 }}>{fmtDateTime(batch.processed_at)}</td>
                    <td><Tag kind={batch.status === 'done' ? 'ok' : batch.status === 'failed' ? 'danger' : 'warn'}>
                      {batch.status}</Tag></td>
                  </tr>
                )) : (
                  <tr><td colSpan={6} className="empty">Офлайн-догрузок пока не было.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {audit ? (
        <>
          <Section title="Журнал действий">
            <Tag kind={audit.integrity.valid ? 'ok' : 'danger'}>
              <IcoShield size={11} style={{ verticalAlign: -1, marginRight: 4 }} />
              цепочка хэшей {audit.integrity.valid ? 'целостна' : 'нарушена'} · проверено {audit.integrity.checked}
            </Tag>
          </Section>
          <div className="page-card">
            <table className="grid">
              <thead>
                <tr><th>Время</th><th>Субъект</th><th>Роль</th><th>Действие</th><th>Объект</th><th>Хэш</th></tr>
              </thead>
              <tbody>
                {audit.entries.map((entry) => (
                  <tr key={entry.id} style={{ cursor: 'default' }}>
                    <td className="num" style={{ fontSize: 12 }}>{fmtDateTime(entry.timestamp)}</td>
                    <td>{entry.actor}</td>
                    <td><Tag kind="off">{entry.role}</Tag></td>
                    <td style={{ fontWeight: 600 }}>{entry.action}</td>
                    <td>{entry.target || '—'}</td>
                    <td><code>{entry.hash}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {users.length ? (
        <>
          <Section title="Пользователи и область видимости">
            <IcoUsers size={15} style={{ color: 'var(--ink-3)' }} />
          </Section>
          <div className="page-card">
            <table className="grid">
              <thead>
                <tr><th>E-mail</th><th>ФИО</th><th>Роль</th><th>Ограничение</th><th>Активен</th></tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr key={user.id} style={{ cursor: 'default' }}>
                    <td>{user.email}</td>
                    <td style={{ fontWeight: 600 }}>{user.full_name}</td>
                    <td><Tag kind={user.role === 'admin' ? 'info' : 'off'}>{user.role}</Tag></td>
                    <td style={{ fontSize: 12, color: 'var(--ink-2)' }}>
                      {user.school_id ? `школа #${user.school_id}`
                        : user.provider_name || 'вся область'}
                    </td>
                    <td>{user.is_active ? 'да' : 'нет'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </div>
  );
}
