/* ПК-уровень прослеживания: карточка отдельного компьютера-агента. */
import { useEffect, useState } from 'react';
import { api } from './api';
import { LatencyChart, LossChart, SpeedChart } from './Charts';
import { IcoClose, IcoPc, IcoPulse, IcoRouter, IcoSpark, IcoWifi } from './icons';
import { Bar, Metric, Pair, RISK_CLASS, Section, Tag, deviceMeta, fmtAgo, fmtDateTime, fmtInterval } from './ui';

export default function DeviceDrawer({ deviceId, role, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const load = () => api.device(deviceId).then(setData).catch((e) => setError(e.message));
  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [deviceId]);

  const toggleMode = async () => {
    setBusy(true);
    try {
      const next = data.device.diagnostic_mode === 'deep' ? 'standard' : 'deep';
      await api.setDiagnosticMode(deviceId, next);
      await load();
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  if (error) {
    return (
      <div className="overlay" onClick={onClose}>
        <div className="drawer" onClick={(e) => e.stopPropagation()}>
          <div className="drawer-body"><div className="empty">{error}</div></div>
        </div>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="overlay" onClick={onClose}>
        <div className="drawer" onClick={(e) => e.stopPropagation()}>
          <div className="spinner-wrap"><div className="spinner" />Загрузка данных ПК-агента…</div>
        </div>
      </div>
    );
  }

  const { device, school, measurements, summary, analytics, sync_batches: batches } = data;
  const meta = deviceMeta(device.status);
  const canManage = role === 'admin' || role === 'operator';

  return (
    <div className="overlay" onClick={onClose}>
      <div className="drawer wide" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
              <span className="eyebrow mono">{device.device_id}</span>
              <Tag kind={meta.cls}>{meta.label}</Tag>
              <Tag kind={device.diagnostic_mode === 'deep' ? 'violet' : 'info'}>
                {device.diagnostic_mode === 'deep' ? 'углублённая диагностика' : 'штатный режим'}
              </Tag>
            </div>
            <h2>{device.name}</h2>
            <p>{device.room} · {school.name} · {school.region} · последний выход на связь {fmtAgo(device.last_seen)}</p>
          </div>
          <button className="close-btn" onClick={onClose}><IcoClose size={17} /></button>
        </div>

        <div className="drawer-body">
          <div className="metric-row">
            <Metric label="Загрузка" value={device.current_download ?? 0} unit=" Мбит/с"
              sub={`договор школы ${school.contract_speed_down} Мбит/с`}
              color={(device.current_download ?? 0) < school.contract_speed_down * 0.6 ? '#E0453E' : '#17A65B'} />
            <Metric label="Отдача" value={device.current_upload ?? 0} unit=" Мбит/с"
              sub={device.link_mode} />
            <Metric label="Задержка" value={device.current_ping ?? 0} unit=" мс"
              sub={`джиттер ${device.current_jitter ?? 0} мс`} />
            <Metric label="Потери" value={device.current_packet_loss ?? 0} unit=" %"
              sub={`замеров: ${summary.samples}`} />
          </div>

          <div className="pair-grid">
            <div className="block">
              <div className="eyebrow" style={{ marginBottom: 10 }}>Доступность агента за период</div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 9 }}>
                <b className="mono" style={{ fontSize: 24 }}>{device.availability_pct ?? 100}%</b>
                <span style={{ fontSize: 11.5, color: 'var(--ink-3)', fontWeight: 600 }}>
                  офлайн-замеров: {summary.offline_samples}
                </span>
              </div>
              <Bar value={device.availability_pct ?? 100} color="#2F6BF6" />
            </div>
            <div className="block">
              <div className="eyebrow" style={{ marginBottom: 10 }}>Соответствие SLA на этом ПК</div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 9 }}>
                <b className="mono" style={{ fontSize: 24 }}>{device.sla_compliance_pct ?? 100}%</b>
                <span style={{ fontSize: 11.5, color: 'var(--ink-3)', fontWeight: 600 }}>
                  норматив 95%
                </span>
              </div>
              <Bar value={device.sla_compliance_pct ?? 100}
                color={(device.sla_compliance_pct ?? 100) >= 95 ? '#17A65B' : '#E4962A'} />
            </div>
          </div>

          <Section title="Скорость канала на этом ПК" />
          <div className="block">
            <SpeedChart measurements={measurements} contract={school.contract_speed_down} />
          </div>

          <Section title="Задержка и потери" />
          <div className="block"><LatencyChart measurements={measurements} /></div>
          <div className="block"><LossChart measurements={measurements} /></div>

          <Section title="Анализ канала этого ПК">
            <Tag kind={RISK_CLASS[analytics.risk_level] || 'off'}>
              риск {analytics.risk_score}/100 · {analytics.risk_level}
            </Tag>
          </Section>
          <div className="block">
            <p style={{ margin: '0 0 12px', fontSize: 13, lineHeight: 1.55 }}>{analytics.forecast}</p>
            {analytics.patterns.length ? analytics.patterns.map((pattern) => (
              <div key={pattern.type + pattern.key} style={{
                display: 'flex', gap: 10, alignItems: 'center', padding: '9px 0',
                borderTop: '1px solid var(--surface-3)',
              }}>
                <IcoSpark size={15} style={{ color: 'var(--warn)', flex: '0 0 auto' }} />
                <span style={{ fontSize: 12.5, flex: 1 }}>{pattern.text}</span>
                <b className="mono" style={{ fontSize: 12, color: 'var(--danger)' }}>−{pattern.drop_pct}%</b>
              </div>
            )) : (
              <div style={{ fontSize: 12.5, color: 'var(--ink-3)' }}>
                Повторяющихся паттернов деградации не выявлено.
              </div>
            )}
          </div>

          <Section title="Инвентарные данные и идентичность" />
          <div className="pair-grid">
            <div className="block">
              <div className="eyebrow" style={{ marginBottom: 8 }}>
                <IcoPc size={13} style={{ verticalAlign: -2, marginRight: 5 }} />Оборудование
              </div>
              <Pair label="Тип">{device.device_type}</Pair>
              <Pair label="Операционная система">{device.os_name}</Pair>
              <Pair label="Процессор">{device.cpu_model}</Pair>
              <Pair label="Оперативная память">{device.ram_gb ? `${device.ram_gb} ГБ` : '—'}</Pair>
              <Pair label="Версия агента">{device.agent_version}</Pair>
            </div>
            <div className="block">
              <div className="eyebrow" style={{ marginBottom: 8 }}>
                <IcoWifi size={13} style={{ verticalAlign: -2, marginRight: 5 }} />Сеть и доверие
              </div>
              <Pair label="IP-адрес"><code className="mono">{device.ip_address}</code></Pair>
              <Pair label="MAC-адрес"><code className="mono">{device.mac_address}</code></Pair>
              <Pair label="Тип линии">{device.link_mode}</Pair>
              {device.wifi_signal_dbm ? <Pair label="Сигнал Wi-Fi">{device.wifi_signal_dbm} дБм</Pair> : null}
              <Pair label="Отпечаток железа"><code className="mono">{device.hardware_fingerprint}…</code></Pair>
              <Pair label="Зарегистрирован">{fmtDateTime(device.enrolled_at)}</Pair>
            </div>
          </div>

          <Section title="Динамическая конфигурация агента" />
          <div className="block">
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
              <div className="insight-icon"><IcoRouter size={17} style={{ color: "var(--accent)" }} /></div>
              <div style={{ flex: 1, fontSize: 12.5, lineHeight: 1.5, color: 'var(--ink-2)' }}>
                Сервер сам управляет частотой тестирования: при деградации канала интервал
                сокращается, а агент переходит в углублённую диагностику.
              </div>
            </div>
            <div className="pair-grid">
              <div>
                <Pair label="Интервал тестирования">{fmtInterval(device.test_interval_sec)}</Pair>
                <Pair label="Режим">{device.diagnostic_mode === 'deep' ? 'углублённый' : 'штатный'}</Pair>
              </div>
              <div>
                <Pair label="Версия конфигурации">№ {device.config_version}</Pair>
                <Pair label="Доступ">{device.revoked ? 'отозван' : 'активен'}</Pair>
              </div>
            </div>
            {canManage ? (
              <div className="btn-row">
                <button className="btn solid" onClick={toggleMode} disabled={busy}>
                  {busy ? 'Применяю…'
                    : device.diagnostic_mode === 'deep'
                      ? 'Вернуть штатный режим' : 'Включить углублённую диагностику'}
                </button>
              </div>
            ) : null}
          </div>

          <Section title="Офлайн-догрузка (Smart Sync)">
            <Tag kind="info">{summary.backfilled} из {summary.samples} замеров догружено</Tag>
          </Section>
          <div className="block">
            {batches.length ? (
              <table className="grid">
                <thead>
                  <tr><th>Пакет</th><th>Замеров</th><th>Принят</th><th>Обработан</th><th>Статус</th></tr>
                </thead>
                <tbody>
                  {batches.map((batch) => (
                    <tr key={batch.id} style={{ cursor: 'default' }}>
                      <td className="num">#{batch.id}</td>
                      <td className="num">{batch.items}</td>
                      <td className="num">{fmtDateTime(batch.received_at)}</td>
                      <td className="num">{fmtDateTime(batch.processed_at)}</td>
                      <td><Tag kind={batch.status === 'done' ? 'ok' : batch.status === 'failed' ? 'danger' : 'warn'}>
                        {batch.status}</Tag></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div style={{ fontSize: 12.5, color: 'var(--ink-3)', display: 'flex', gap: 9, alignItems: 'center' }}>
                <IcoPulse size={15} />Агент работал без разрывов связи — офлайн-пакетов не было.
              </div>
            )}
          </div>
        </div>

        <div className="drawer-foot">
          <button className="btn" onClick={onClose}>Закрыть</button>
        </div>
      </div>
    </div>
  );
}
