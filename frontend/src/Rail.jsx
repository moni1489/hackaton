/* Правая колонка: сводка, рейтинг риска SLA, инциденты и проблемные ПК. */
import { useEffect, useState } from 'react';
import { api } from './api';
import {
  IcoAlert, IcoDown, IcoGauge, IcoPc, IcoPulse, IcoRefresh, IcoShield, IcoSpark, IcoTrend,
} from './icons';
import { Bar, RISK_CLASS, Tag, deviceMeta, fmtAgo } from './ui';

const TABS = [
  { key: 'insights', label: 'Инсайты', Icon: IcoSpark },
  { key: 'risk', label: 'Риски', Icon: IcoGauge },
  { key: 'incidents', label: 'Инциденты', Icon: IcoAlert },
  { key: 'devices', label: 'ПК', Icon: IcoPc },
];

const HEADS = {
  insights: { title: 'Сводка по состоянию сети', Icon: IcoPulse },
  risk: { title: 'Рейтинг риска нарушения SLA', Icon: IcoGauge },
  incidents: { title: 'Активные инциденты', Icon: IcoAlert },
  devices: { title: 'ПК-агенты с отклонениями', Icon: IcoPc },
};

export default function Rail({ overview, incidents, schools, onOpenSchool, onOpenDevice, onReload }) {
  const [tab, setTab] = useState('insights');
  const [risks, setRisks] = useState([]);
  const [devices, setDevices] = useState([]);
  const [stamp, setStamp] = useState(new Date());

  useEffect(() => {
    api.riskQueue(6).then(setRisks).catch(() => setRisks([]));
    api.devices().then((rows) => setDevices(rows.filter((d) => d.status !== 'online')))
      .catch(() => setDevices([]));
  }, [overview]);

  const refresh = async () => {
    setStamp(new Date());
    await onReload();
    api.riskQueue(6).then(setRisks).catch(() => {});
  };

  const counts = overview?.status_counts || {};
  const head = HEADS[tab];

  return (
    <aside className="rail">
      <div className="rail-tabs">
        {TABS.map(({ key, label, Icon }) => (
          <button key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>
            <Icon size={14} />{label}
          </button>
        ))}
      </div>

      <div className="rail-head">
        <head.Icon size={17} style={{ color: 'var(--accent)' }} />
        <h3>{head.title}</h3>
        <span className="t">{stamp.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}</span>
        <button className="icon-btn" style={{ width: 30, height: 30 }} onClick={refresh} title="Обновить">
          <IcoRefresh size={15} />
        </button>
      </div>

      <div className="rail-body">
        {tab === 'insights' ? (
          <Insights overview={overview} counts={counts} risks={risks} schools={schools}
            onOpenSchool={onOpenSchool} />
        ) : null}

        {tab === 'risk' ? (
          risks.length ? risks.map((risk) => (
            <button key={risk.school_id} className={`insight ${RISK_CLASS[risk.risk_level] === 'ok' ? '' : RISK_CLASS[risk.risk_level]}`}
              onClick={() => onOpenSchool(risk.school_id)} style={{ width: '100%', textAlign: 'left' }}>
              <div className="insight-icon"><IcoGauge size={17} style={{ color: 'var(--violet)' }} /></div>
              <div className="insight-body">
                <Tag kind={RISK_CLASS[risk.risk_level] || 'off'}>риск {risk.risk_level}</Tag>
                <div className="insight-title sm">{risk.school_name}</div>
                <div className="insight-meter">
                  <Bar value={risk.risk_score}
                    color={risk.risk_score > 60 ? '#E0453E' : risk.risk_score > 30 ? '#E4962A' : '#17A65B'} />
                  <span>{risk.risk_score}/100</span>
                </div>
                <p className="insight-note">
                  SLA {risk.sla_compliance_pct}% · средняя {risk.avg_speed} из {risk.contract_speed} Мбит/с
                  {risk.patterns[0] ? ` · ${risk.patterns[0].text}` : ''}
                </p>
              </div>
            </button>
          )) : <div className="empty">Школ с повышенным риском нарушения SLA нет.</div>
        ) : null}

        {tab === 'incidents' ? (
          incidents.length ? incidents.slice(0, 20).map((incident) => (
            <button key={incident.id} className="insight danger" style={{ width: '100%', textAlign: 'left' }}
              onClick={() => onOpenSchool(incident.school_id)}>
              <div className="insight-icon"><IcoAlert size={17} style={{ color: 'var(--danger)' }} /></div>
              <div className="insight-body">
                <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                  <Tag kind={incident.severity === 'critical' ? 'danger' : 'warn'}>{incident.status}</Tag>
                  <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)', fontWeight: 700 }}>
                    {incident.incident_number}
                  </span>
                </div>
                <div className="insight-title sm">{incident.school_name}</div>
                <p className="insight-note">{incident.description}</p>
                <p className="insight-note" style={{ color: 'var(--ink-3)' }}>
                  {incident.provider} · {fmtAgo(incident.start_time)}
                </p>
              </div>
            </button>
          )) : <div className="empty">Открытых инцидентов нет — все каналы в норме.</div>
        ) : null}

        {tab === 'devices' ? (
          devices.length ? devices.slice(0, 25).map((device) => {
            const meta = deviceMeta(device.status);
            return (
              <button key={device.device_id} className={`insight ${device.status === 'offline' ? 'neutral' : 'warn'}`}
                style={{ width: '100%', textAlign: 'left' }}
                onClick={() => onOpenDevice(device.device_id)}>
                <div className="insight-icon"><IcoPc size={17} style={{ color: meta.color }} /></div>
                <div className="insight-body">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                    <Tag kind={meta.cls}>{meta.label}</Tag>
                    <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)', fontWeight: 700 }}>
                      {device.device_id}
                    </span>
                  </div>
                  <div className="insight-title sm">{device.school_name}</div>
                  <div className="insight-meter">
                    <Bar value={device.sla_compliance_pct ?? 0}
                      color={(device.sla_compliance_pct ?? 0) >= 95 ? '#17A65B' : '#E4962A'} />
                    <span>SLA {device.sla_compliance_pct ?? 0}%</span>
                  </div>
                  <p className="insight-note">
                    {device.room} · {device.link_mode} · ↓{device.current_download ?? 0} Мбит/с
                  </p>
                </div>
              </button>
            );
          }) : <div className="empty">Все ПК-агенты работают штатно.</div>
        ) : null}
      </div>
    </aside>
  );
}

function Insights({ overview, counts, risks, schools, onOpenSchool }) {
  if (!overview) return <div className="empty">Загрузка сводки…</div>;

  const total = overview.total_schools || 1;
  const healthy = Math.round(100 * (counts.normal || 0) / total);
  const worst = risks[0];
  const slowest = [...schools]
    .filter((s) => s.contract_speed_down)
    .sort((a, b) => (a.current_download / a.contract_speed_down) - (b.current_download / b.contract_speed_down))[0];

  return (
    <>
      <div className="insight">
        <div className="insight-icon"><IcoShield size={17} style={{ color: 'var(--accent)' }} /></div>
        <div className="insight-body">
          <Tag kind={healthy >= 70 ? 'ok' : healthy >= 45 ? 'warn' : 'danger'}>Состояние сети</Tag>
          <div className="insight-title">
            {healthy >= 70 ? 'Стабильное' : healthy >= 45 ? 'Требует внимания' : 'Напряжённое'}
          </div>
          <div className="insight-meter">
            <Bar value={healthy} color={healthy >= 70 ? '#17A65B' : healthy >= 45 ? '#E4962A' : '#E0453E'} />
            <span>{healthy}%</span>
          </div>
          <p className="insight-note">
            {counts.normal} школ в норме, {counts.unstable} нестабильны,
            {' '}{counts.critical} критичны, {counts.offline} без связи.
          </p>
        </div>
      </div>

      <div className="insight neutral">
        <div className="insight-icon"><IcoTrend size={17} style={{ color: 'var(--ok)' }} /></div>
        <div className="insight-body">
          <Tag kind="info">Средние показатели</Tag>
          <div className="insight-title">
            <span className="mono">{overview.avg_download}</span> Мбит/с по области
          </div>
          <p className="insight-note">
            Отдача {overview.avg_upload} Мбит/с · задержка {overview.avg_ping} мс ·
            потери {overview.avg_loss}% · ПК-агентов в сети {overview.devices_online}
            {' '}из {overview.total_devices}.
          </p>
        </div>
      </div>

      {worst ? (
        <button className="insight danger" style={{ width: '100%', textAlign: 'left' }}
          onClick={() => onOpenSchool(worst.school_id)}>
          <div className="insight-icon"><IcoAlert size={17} style={{ color: "var(--danger)" }} /></div>
          <div className="insight-body">
            <Tag kind="danger">Приоритет вмешательства</Tag>
            <div className="insight-title sm">{worst.school_name}</div>
            <div className="insight-meter">
              <Bar value={worst.risk_score} color="#E0453E" />
              <span>{worst.risk_score}/100</span>
            </div>
            <p className="insight-note">{worst.forecast}</p>
          </div>
        </button>
      ) : null}

      {slowest ? (
        <button className="insight warn" style={{ width: '100%', textAlign: 'left' }}
          onClick={() => onOpenSchool(slowest.id)}>
          <div className="insight-icon"><IcoDown size={17} style={{ color: "var(--warn)" }} /></div>
          <div className="insight-body">
            <Tag kind="warn">Наибольшее отставание от договора</Tag>
            <div className="insight-title sm">{slowest.name}</div>
            <p className="insight-note">
              Фактически {slowest.current_download} Мбит/с при договорных
              {' '}{slowest.contract_speed_down} Мбит/с · поставщик {slowest.provider}.
              Основание для перерасчёта абонентской платы.
            </p>
          </div>
        </button>
      ) : null}

      <div className="insight neutral">
        <div className="insight-icon"><IcoPulse size={17} style={{ color: 'var(--violet)' }} /></div>
        <div className="insight-body">
          <Tag kind="violet">Источник данных</Tag>
          <div className="insight-title sm">Агрегация по ПК-агентам</div>
          <p className="insight-note">
            Показатели школы усредняются по всем её компьютерам-агентам. Откройте карточку
            школы и выберите конкретный ПК, чтобы увидеть индивидуальную историю канала.
          </p>
        </div>
      </div>
    </>
  );
}
