/* Карточка школы: состояние канала, ПК-агенты, инциденты, претензии и акт SLA. */
import { useEffect, useState } from 'react';
import { api } from './api';
import { LatencyChart, SpeedChart } from './Charts';
import { QUALITY_TAG, causeUi } from './Diagnostics';
import {
  IcoAlert, IcoClose, IcoDoc, IcoDown, IcoSpark, IcoTrend,
} from './icons';
import {
  Bar, Metric, Pair, RISK_CLASS, Section, StatusCell, Tag,
  deviceMeta, fmtAge, fmtDateTime, fmtStamp, statusMeta,
} from './ui';

const LINE_ROLE = { main: 'основная', backup: 'резервная', disabled: 'отключена' };

/* Причины, которые принимает ML API (features.CAUSES). Оператор подтверждает
   вердикт модели или исправляет его — метка идёт в журнал аварий и дообучение. */
const VERDICT_CAUSES = ['none', 'device', 'school_lan', 'provider_node', 'regional'];

export default function SchoolDrawer({ schoolId, role, onOpenDevice, onClose }) {
  const [school, setSchool] = useState(null);
  const [measurements, setMeasurements] = useState([]);
  const [analytics, setAnalytics] = useState(null);
  const [verdict, setVerdict] = useState(null);
  const [prediction, setPrediction] = useState(null);
  const [operVerdict, setOperVerdict] = useState({ cause: '', note: '', busy: false });
  const [claim, setClaim] = useState(null);
  const [claimBusy, setClaimBusy] = useState(false);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [exportBusy, setExportBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    Promise.all([
      api.school(schoolId),
      api.schoolMeasurements(schoolId),
      api.schoolAnalytics(schoolId),
      api.mlAttribution(schoolId).catch(() => null),
      api.mlSchoolForecast(schoolId).catch(() => null),
    ]).then(([s, m, a, v, f]) => {
      if (!alive) return;
      setSchool(s); setMeasurements(m); setAnalytics(a); setVerdict(v); setPrediction(f);
    }).catch((e) => alive && setError(e.message));
    return () => { alive = false; };
  }, [schoolId]);

  const saveVerdict = async (incidentId, cause) => {
    setOperVerdict((v) => ({ ...v, busy: true }));
    try {
      const res = await api.mlVerdict(incidentId, cause);
      setOperVerdict({ cause, busy: false,
        note: res.agreement ? 'Вердикт модели подтверждён' : 'Вердикт исправлен — метка учтена' });
    } catch (e) {
      setOperVerdict((v) => ({ ...v, busy: false, note: `Не сохранено: ${e.message}` }));
    }
  };

  const makeClaim = async (incident) => {
    setClaim({ incident, text: '' });
    setClaimBusy(true);
    try {
      const data = await api.generateClaim(incident.id);
      setClaim({ incident, text: data.claim_text, source: data.source,
        advised: data.claim_advised, advisory: data.advisory });
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

  const exportData = async (format) => {
    setExportBusy(true);
    try {
      await api.exportMeasurements({ school_id: schoolId, format },
        `${school.school_id_code}_measurements.${format}`);
    } catch (e) { setError(e.message); }
    setExportBusy(false);
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
  const live = !school.is_stale;   // устаревший замер не выдаём за текущие показатели
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
              <>
                {claim.advisory ? (
                  <div className="insight" style={{
                    background: claim.advised ? 'var(--ok-soft)' : 'var(--warn-soft)',
                    cursor: 'default', marginBottom: 12,
                  }}>
                    <div className="insight-icon">
                      <IcoAlert size={17} style={{
                        color: claim.advised ? 'var(--ok)' : 'var(--warn)',
                      }} />
                    </div>
                    <div className="insight-body">
                      <div className="eyebrow">Проверка обоснованности</div>
                      <p className="insight-note" style={{ marginTop: 5 }}>{claim.advisory}</p>
                    </div>
                  </div>
                ) : null}
                <textarea className="claim-area" value={claim.text}
                  onChange={(e) => setClaim({ ...claim, text: e.target.value })} />
              </>
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
              <Tag kind={meta.key}>{meta.short}</Tag>
              <Tag kind="off">{school.connection_type}</Tag>
            </div>
            <h2>{school.name}</h2>
            <p>{school.region} · {school.address} · {live
              ? `замер ${fmtAge(school.age_min)} назад`
              : `последний замер ${fmtStamp(school.last_measurement)}`}</p>
          </div>
          <button className="close-btn" onClick={onClose}><IcoClose size={17} /></button>
        </div>

        <div className="drawer-body">
          <div className="metric-row">
            <Metric label="Загрузка" value={live ? school.current_download : '—'} unit={live ? ' Мбит/с' : ''}
              sub={live ? `${ratio}% от договорной` : ''}
              color={live ? (ratio < 60 ? '#E0453E' : '#17A65B') : undefined} />
            <Metric label="Отдача" value={live ? school.current_upload : '—'} unit={live ? ' Мбит/с' : ''}
              sub={`договор ${school.contract_speed_up} Мбит/с`} />
            <Metric label="Задержка" value={live ? school.current_ping : '—'} unit={live ? ' мс' : ''}
              sub={live ? `джиттер ${school.current_jitter} мс` : ''} />
            <Metric label="Потери пакетов" value={live ? school.current_packet_loss : '—'} unit={live ? ' %' : ''}
              sub={`ПК-агентов: ${school.devices.length}`} />
          </div>

          <Section title="Линии связи и рабочие места" />
          <div className="block">
            {school.lines.map((line) => (
              <div key={line.id} style={{
                display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', padding: '7px 0',
                borderTop: '1px solid var(--surface-3)',
              }}>
                <Tag kind={line.role === 'main' ? 'info' : 'off'}>{LINE_ROLE[line.role] || line.role}</Tag>
                <code>{line.code}</code>
                <span style={{ fontSize: 12, color: 'var(--ink-2)', flex: 1 }}>
                  {line.provider} · {line.connection_type} · договор {line.contract_speed_down}/{line.contract_speed_up} Мбит/с
                </span>
                <StatusCell status={line.status} />
              </div>
            ))}
            <p className="insight-note" style={{ marginBottom: 0 }}>
              Статус школы определяет основная линия. Рабочих мест: {school.workstations.total}
              , со свежими данными {school.workstations.with_fresh_data}, с отклонениями{' '}
              {school.workstations.degraded}, не в сети {school.workstations.offline}. Они оцениваются
              отдельно и на статус школы не влияют.
            </p>
          </div>

          {school.published_connections?.length > 0 && <div className="insight" style={{ display: 'block', marginBottom: 16 }}>
            <strong>Опубликованные параметры подключения</strong>
            {school.published_connections.map((c) => <p key={c.key}>
              {c.technology} · {c.speed_down_mbps} Мбит/с · <a href={c.source_url} target="_blank" rel="noreferrer">Источник gov.kz</a><br />
              Дата публикации не установлена. {c.note}
            </p>)}
          </div>}
          <Section title="Качество канала за период" />
          <div className="block">
            <SpeedChart measurements={measurements} contract={school.contract_speed_down} />
          </div>
          <div className="block"><LatencyChart measurements={measurements} /></div>

          {/* --- ML: заключение об источнике деградации --- */}
          {verdict && !verdict.error ? (
            <>
              <Section title="Предполагаемый источник">
                {verdict.cause !== 'no_data' ? (
                  <Tag kind={causeUi(verdict.cause).tag}>
                    оценка модели {Math.round(verdict.confidence * 100)}%
                  </Tag>
                ) : null}
                {verdict.data_quality ? (
                  <Tag kind={QUALITY_TAG[verdict.data_quality.level]}>
                    данные: {verdict.data_quality.label}
                  </Tag>
                ) : null}
              </Section>
              <div className="block">
                <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
                  <div className="insight-icon">
                    {(() => { const I = causeUi(verdict.cause).Icon; return <I size={17} />; })()}
                  </div>
                  <div style={{ flex: 1 }}>
                    <div className="eyebrow">{verdict.cause_label}</div>
                    <div style={{ fontSize: 13, fontWeight: 700, margin: '4px 0 0' }}>
                      Зона ответственности: {verdict.responsible}
                    </div>
                  </div>
                  {verdict.actionable ? <Tag kind="danger">основание для претензии</Tag> : null}
                </div>
                <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.55, color: 'var(--ink-2)' }}>
                  {verdict.narrative}
                </p>
                {verdict.data_quality?.reasons?.length ? (
                  <p style={{ margin: '8px 0 0', fontSize: 12, lineHeight: 1.5, color: 'var(--ink-3)' }}>
                    <b>Ограничения данных:</b> {verdict.data_quality.reasons.join('; ')}.
                  </p>
                ) : null}
                <div className="mini-stats" style={{ marginTop: 12 }}>
                  <div className="mini-stat">
                    <div className="k">ПК в отклонении</div>
                    <div className="v">
                      {verdict.evidence.devices_affected}/{verdict.evidence.devices_total}
                    </div>
                  </div>
                  <div className="mini-stat">
                    <div className="k">Сопоставимые школы провайдера</div>
                    <div className="v">
                      {verdict.evidence.peers_same_provider_district_affected}/
                      {verdict.evidence.peers_same_provider_district}
                    </div>
                  </div>
                  <div className="mini-stat">
                    <div className="k">Просадка к норме</div>
                    <div className="v">{verdict.evidence.avg_depth_pct}%</div>
                  </div>
                  <div className="mini-stat">
                    <div className="k">Модель</div>
                    <div className="v" style={{ fontSize: 12 }}>{verdict.model_version}</div>
                  </div>
                </div>
                {(() => {
                  const incident = school.incidents.find((i) => i.status !== 'Устранён')
                    || school.incidents[0];
                  if (!incident || !['admin', 'operator'].includes(role)) return null;
                  const model = VERDICT_CAUSES.includes(verdict.cause) ? verdict.cause : 'none';
                  const chosen = operVerdict.cause || model;
                  return (
                    <div style={{ marginTop: 14, paddingTop: 12,
                      borderTop: '1px solid var(--surface-3)' }}>
                      <div className="eyebrow" style={{ marginBottom: 8 }}>
                        Вердикт оператора · {incident.incident_number}
                      </div>
                      <div className="form-grid" style={{ padding: 0,
                        gridTemplateColumns: '1fr auto', alignItems: 'end' }}>
                        <label>Источник
                          <select value={chosen} disabled={operVerdict.busy}
                            onChange={(e) => setOperVerdict({ cause: e.target.value,
                              note: '', busy: false })}>
                            {VERDICT_CAUSES.map((c) => (
                              <option key={c} value={c}>{causeUi(c).short}</option>
                            ))}
                          </select>
                        </label>
                        <button className="btn solid" style={{ padding: '10px 16px' }}
                          disabled={operVerdict.busy}
                          onClick={() => saveVerdict(incident.id, chosen)}>
                          {operVerdict.busy ? 'Сохраняю…'
                            : chosen === model ? 'Подтвердить' : 'Изменить'}
                        </button>
                      </div>
                      <p className="insight-note">
                        {operVerdict.note
                          || 'Метка ложится в журнал аварий и учитывается при следующем '
                          + 'переобучении модели.'}
                      </p>
                    </div>
                  );
                })()}
                {prediction && prediction.probability != null ? (
                  <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 14,
                    paddingTop: 12, borderTop: '1px solid var(--surface-3)' }}>
                    <IcoAlert size={15} style={{ color: 'var(--danger)', flex: '0 0 auto' }} />
                    <span style={{ fontSize: 12.5, flex: 1 }}>
                      Вероятность выхода за SLA в ближайшие {prediction.horizon_hours} ч —{' '}
                      <b>{Math.round(prediction.probability * 100)}%</b> ({prediction.band}).{' '}
                      {prediction.recommendation}
                    </span>
                  </div>
                ) : prediction?.stale ? (
                  <p className="insight-note" style={{ marginTop: 12, marginBottom: 0 }}>
                    Прогноз не строится.
                  </p>
                ) : null}
              </div>
            </>
          ) : null}

          {analytics && !analytics.samples ? (
            <>
              <Section title="Анализ соответствия SLA" />
              <div className="block">
                <div className="empty" style={{ padding: 18 }}>
                  Нет замеров основной линии за период
                  {analytics.last_measurement ? `; последний — ${fmtStamp(analytics.last_measurement)}` : ''}.
                  Показатели SLA не рассчитываются.
                </div>
              </div>
            </>
          ) : null}

          {analytics?.samples ? (
            <>
              <Section title="Анализ соответствия SLA">
                <Tag kind={RISK_CLASS[analytics.risk_level] || 'off'}>
                  риск {analytics.risk_score}/100 · {analytics.risk_level}
                </Tag>
              </Section>
              <div className="block">
                <div style={{ display: 'flex', gap: 12, marginBottom: 14 }}>
                  <div className="insight-icon"><IcoTrend size={17} style={{ color: "var(--violet)" }} /></div>
                  <div style={{ flex: 1 }}>
                    <div className="eyebrow">Оценка по истории замеров</div>
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
                  <div>
                    <div className="eyebrow" style={{ marginBottom: 8 }}>
                      Доступность (норматив ≥ {analytics.availability_norm_pct}%)
                    </div>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
                      <b className="mono" style={{ fontSize: 22 }}>{analytics.availability_pct}%</b>
                      <span style={{ fontSize: 11.5, color: 'var(--ink-3)', fontWeight: 600 }}>
                        ниже договорной доли: {analytics.below_contract_pct}% замеров
                      </span>
                    </div>
                    <Bar value={analytics.availability_pct}
                      color={analytics.availability_ok ? '#17A65B' : '#E0453E'} />
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

          <Section title={`ПК-агенты (${school.devices.length})`}>
            <Tag kind="info">нажмите строку для ПК-уровня</Tag>
          </Section>
          <div className="page-card">
            <table className="grid">
              <thead>
                <tr>
                  <th>Device ID</th><th>Роль</th><th>Расположение</th><th>Подключение</th>
                  <th>↓ Мбит/с</th><th>Ping</th><th>SLA</th><th>Статус</th>
                </tr>
              </thead>
              <tbody>
                {school.devices.map((device) => {
                  const dm = deviceMeta(device.status);
                  return (
                    <tr key={device.device_id} onClick={() => onOpenDevice(device.device_id)}>
                      <td><code>{device.device_id}</code></td>
                      <td style={{ fontSize: 12 }}>
                        {device.role === 'monitor' ? 'точка мониторинга' : 'рабочее место'}
                      </td>
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
          <button className="btn" onClick={() => exportData('csv')} disabled={exportBusy}>CSV</button>
          <button className="btn" onClick={() => exportData('xlsx')} disabled={exportBusy}>XLSX</button>
          <button className="btn accent" onClick={downloadPdf} disabled={pdfBusy}>
            <IcoDown size={13} style={{ verticalAlign: -2, marginRight: 6 }} />
            {pdfBusy ? 'Формирую…' : 'Акт SLA (PDF)'}
          </button>
        </div>
      </div>
    </div>
  );
}
