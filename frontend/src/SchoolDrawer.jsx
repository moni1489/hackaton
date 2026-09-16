/* Карточка школы: состояние канала, ПК-агенты, инциденты, претензии и акт SLA. */
import { useEffect, useState } from 'react';
import { api } from './api';
import { LatencyChart, SpeedChart } from './Charts';
import {
  IcoBrain, IcoClose, IcoDoc, IcoDown, IcoPc, IcoSpark, IcoTrend,
} from './icons';
import {
  Bar, Metric, Pair, RISK_CLASS, Section, StatusCell, Tag,
  deviceMeta, fmtAgo, fmtDateTime, statusMeta,
} from './ui';

export default function SchoolDrawer({ schoolId, role, onOpenDevice, onClose }) {
  const [school, setSchool] = useState(null);
  const [measurements, setMeasurements] = useState([]);
  const [analytics, setAnalytics] = useState(null);
  const [claim, setClaim] = useState(null);
  const [claimBusy, setClaimBusy] = useState(false);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    Promise.all([
      api.school(schoolId),
      api.schoolMeasurements(schoolId),
      api.schoolAnalytics(schoolId),
    ]).then(([s, m, a]) => {
      if (!alive) return;
      setSchool(s); setMeasurements(m); setAnalytics(a);
    }).catch((e) => alive && setError(e.message));
    return () => { alive = false; };
  }, [schoolId]);

  const makeClaim = async (incident) => {
    setClaim({ incident, text: '' });
    setClaimBusy(true);
    try {
      const data = await api.generateClaim(incident.id);
      setClaim({ incident, text: data.claim_text, source: data.source });
    } catch (e) {
      setClaim({ incident, text: `Не удалось сформировать претензию: ${e.message}` });
    }
    setClaimBusy(false);
  };

  const downloadPdf = async () => {
    setPdfBusy(true);
    try { await api.slaReport(schoolId, `SLA_${school.school_id_code}.pdf`); }
    catch (e) { setError(e.message); }
    setPdfBusy(false);
  };

  if (!school) {
    return (
      <div className="overlay" onClick={onClose}>
        <div className="drawer" onClick={(e) => e.stopPropagation()}>
          <div className="spinner-wrap">
            <div className="spinner" />{error || 'Загрузка карточки школы…'}
          </div>
        </div>
      </div>
    );
  }

  const meta = statusMeta(school.status);
  const ratio = school.contract_speed_down
    ? Math.round(100 * (school.current_download || 0) / school.contract_speed_down) : 0;

  /* --- окно претензии ---------------------------------------------- */
  if (claim) {
    return (
      <div className="overlay" onClick={() => setClaim(null)}>
        <div className="drawer wide" onClick={(e) => e.stopPropagation()}>
          <div className="drawer-head">
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <span className="eyebrow">Досудебная претензия</span>
                {claim.source ? (
                  <Tag kind={claim.source === 'gemini' ? 'violet' : 'info'}>
                    {claim.source === 'gemini' ? 'Gemini AI' : 'юридический шаблон'}
                  </Tag>
                ) : null}
              </div>
              <h2>{claim.incident.incident_number}</h2>
              <p>{school.name} · поставщик {school.provider}</p>
            </div>
            <button className="close-btn" onClick={() => setClaim(null)}><IcoClose size={17} /></button>
          </div>
          <div className="drawer-body">
            {claimBusy ? (
              <div className="spinner-wrap">
                <div className="spinner" />
                Формирую претензию на основании зафиксированных нарушений SLA…
              </div>
            ) : (
              <textarea className="claim-area" value={claim.text}
                onChange={(e) => setClaim({ ...claim, text: e.target.value })} />
            )}
          </div>
          <div className="drawer-foot">
            <button className="btn" onClick={() => setClaim(null)}>Назад</button>
            <button className="btn accent" disabled={!claim.text}
              onClick={() => navigator.clipboard.writeText(claim.text)}>
              Скопировать текст
            </button>
          </div>
        </div>
      </div>
    );
  }

  /* --- карточка школы ---------------------------------------------- */
  return (
    <div className="overlay" onClick={onClose}>
      <div className="drawer wide" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
              <span className="eyebrow mono">{school.school_id_code}</span>
              <Tag kind={meta.key}>{school.status}</Tag>
              <Tag kind="off">{school.connection_type}</Tag>
            </div>
            <h2>{school.name}</h2>
            <p>{school.region} · {school.address} · обновлено {fmtAgo(school.last_measurement)}</p>
          </div>
          <button className="close-btn" onClick={onClose}><IcoClose size={17} /></button>
        </div>

        <div className="drawer-body">
          <div className="metric-row">
            <Metric label="Загрузка" value={school.current_download} unit=" Мбит/с"
              sub={`${ratio}% от договорной`} color={ratio < 60 ? '#E0453E' : '#17A65B'} />
            <Metric label="Отдача" value={school.current_upload} unit=" Мбит/с"
              sub={`договор ${school.contract_speed_up} Мбит/с`} />
            <Metric label="Задержка" value={school.current_ping} unit=" мс"
              sub={`джиттер ${school.current_jitter} мс`} />
            <Metric label="Потери пакетов" value={school.current_packet_loss} unit=" %"
              sub={`ПК-агентов: ${school.devices.length}`} />
          </div>

          <Section title="Качество канала за период" />
          <div className="block">
            <SpeedChart measurements={measurements} contract={school.contract_speed_down} />
          </div>
          <div className="block"><LatencyChart measurements={measurements} /></div>

          {analytics ? (
            <>
              <Section title="Предиктивная аналитика SLA">
                <Tag kind={RISK_CLASS[analytics.risk_level] || 'off'}>
                  риск {analytics.risk_score}/100 · {analytics.risk_level}
                </Tag>
              </Section>
              <div className="block">
                <div style={{ display: 'flex', gap: 12, marginBottom: 14 }}>
                  <div className="insight-icon"><IcoBrain size={17} style={{ color: 'var(--violet)' }} /></div>
                  <div style={{ flex: 1 }}>
                    <div className="eyebrow">Прогноз системы</div>
                    <p style={{ margin: '6px 0 0', fontSize: 13, lineHeight: 1.55 }}>
                      {analytics.forecast}
                    </p>
                  </div>
                </div>
                <div className="pair-grid">
                  <div>
                    <div className="eyebrow" style={{ marginBottom: 8 }}>Соответствие SLA</div>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
                      <b className="mono" style={{ fontSize: 22 }}>{analytics.sla_compliance_pct}%</b>
                      <span style={{ fontSize: 11.5, color: 'var(--ink-3)', fontWeight: 600 }}>
                        нарушений: {analytics.violations} из {analytics.samples}
                      </span>
                    </div>
                    <Bar value={analytics.sla_compliance_pct}
                      color={analytics.sla_compliance_pct >= 95 ? '#17A65B' : '#E4962A'} />
                  </div>
                  <div>
                    <div className="eyebrow" style={{ marginBottom: 8 }}>Стабильность канала</div>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
                      <b className="mono" style={{ fontSize: 22 }}>{analytics.stability}%</b>
                      <span style={{ fontSize: 11.5, color: 'var(--ink-3)', fontWeight: 600 }}>
                        тренд {analytics.trend_pct > 0 ? '+' : ''}{analytics.trend_pct}%
                      </span>
                    </div>
                    <Bar value={analytics.stability} color="#2F6BF6" />
                  </div>
                </div>
                {analytics.patterns.length ? (
                  <div style={{ marginTop: 14 }}>
                    <div className="eyebrow" style={{ marginBottom: 6 }}>Повторяющиеся паттерны деградации</div>
                    {analytics.patterns.map((pattern) => (
                      <div key={pattern.type + pattern.key} style={{
                        display: 'flex', gap: 10, alignItems: 'center', padding: '9px 0',
                        borderTop: '1px solid var(--surface-3)',
                      }}>
                        <IcoSpark size={15} style={{ color: 'var(--warn)', flex: '0 0 auto' }} />
                        <span style={{ fontSize: 12.5, flex: 1 }}>{pattern.text}</span>
                        <b className="mono" style={{ fontSize: 12, color: 'var(--danger)' }}>
                          −{pattern.drop_pct}%
                        </b>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            </>
          ) : null}

          <Section title={`Точки мониторинга — ПК-агенты (${school.devices.length})`}>
            <Tag kind="info">нажмите строку для ПК-уровня</Tag>
          </Section>
          <div className="page-card">
            <table className="grid">
              <thead>
                <tr>
                  <th>Device ID</th><th>Расположение</th><th>Линия</th>
                  <th>↓ Мбит/с</th><th>Ping</th><th>SLA</th><th>Статус</th>
                </tr>
              </thead>
              <tbody>
                {school.devices.map((device) => {
                  const dm = deviceMeta(device.status);
                  return (
                    <tr key={device.device_id} onClick={() => onOpenDevice(device.device_id)}>
                      <td><code>{device.device_id}</code></td>
                      <td>{device.room}</td>
                      <td style={{ fontSize: 12, color: 'var(--ink-2)' }}>{device.link_mode}</td>
                      <td className="num">{device.current_download ?? 0}</td>
                      <td className="num">{device.current_ping ?? 0}</td>
                      <td className="num" style={{
                        color: (device.sla_compliance_pct ?? 100) >= 95 ? 'var(--ok)' : 'var(--warn)',
                      }}>{device.sla_compliance_pct ?? 100}%</td>
                      <td>
                        <span className="status-cell">
                          <i className="dot" style={{ background: dm.color }} />{dm.label}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <Section title="Ответственные лица и поставщик" />
          <div className="pair-grid">
            <div className="block">
              <div className="eyebrow" style={{ marginBottom: 8 }}>Ответственный за интернет</div>
              <Pair label="ФИО">{school.contact_name}</Pair>
              <Pair label="Телефон">{school.contact_phone}</Pair>
              <Pair label="E-mail">{school.contact_email}</Pair>
            </div>
            <div className="block">
              <div className="eyebrow" style={{ marginBottom: 8 }}>Поставщик услуг связи</div>
              <Pair label="Провайдер">{school.provider}</Pair>
              <Pair label="Тип линии">{school.connection_type}</Pair>
              <Pair label="Техподдержка">{school.provider_phone}</Pair>
            </div>
          </div>

          <Section title={`Инциденты (${school.incidents.length})`} />
          {school.incidents.length ? school.incidents.map((incident) => (
            <div className="block" key={incident.id}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 8 }}>
                <span className="mono" style={{ fontSize: 12, fontWeight: 700 }}>
                  {incident.incident_number}
                </span>
                <Tag kind={incident.severity === 'critical' ? 'danger' : 'warn'}>{incident.status}</Tag>
                <span style={{ marginLeft: 'auto', fontSize: 11.5, color: 'var(--ink-3)' }}>
                  {fmtDateTime(incident.start_time)}
                </span>
              </div>
              <p style={{ margin: '0 0 12px', fontSize: 12.5, lineHeight: 1.5, color: 'var(--ink-2)' }}>
                {incident.description}
              </p>
              {['admin', 'operator', 'school'].includes(role) ? (
                <button className="btn solid" style={{ flex: 'none', padding: '10px 16px' }}
                  onClick={() => makeClaim(incident)}>
                  <IcoDoc size={13} style={{ verticalAlign: -2, marginRight: 6 }} />
                  Сформировать претензию
                </button>
              ) : null}
            </div>
          )) : (
            <div className="block" style={{ color: 'var(--ink-3)', fontSize: 12.5 }}>
              Инцидентов не зарегистрировано — канал работает в штатном режиме.
            </div>
          )}
        </div>

        <div className="drawer-foot">
          {error ? <span style={{ color: 'var(--danger)', fontSize: 12, marginRight: 'auto' }}>{error}</span> : null}
          <button className="btn" onClick={onClose}>Закрыть</button>
          <button className="btn accent" onClick={downloadPdf} disabled={pdfBusy}>
            <IcoDown size={13} style={{ verticalAlign: -2, marginRight: 6 }} />
            {pdfBusy ? 'Формирую…' : 'Акт SLA (PDF)'}
          </button>
        </div>
      </div>
    </div>
  );
}
