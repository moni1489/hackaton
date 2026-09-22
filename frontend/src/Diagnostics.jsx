/* ML-диагностика: кто ответственен за деградацию и где рванёт дальше. */
import { useCallback, useEffect, useState } from 'react';
import { api } from './api';
import {
  IcoAlert, IcoClock, IcoLayers, IcoNodes, IcoPc, IcoRefresh, IcoRouter, IcoShield, IcoTrend, IcoWifi,
} from './icons';
import { Pair, Section, Tag, fmtAge, fmtDateTime, fmtStamp } from './ui';

/* Оформление вердикта: свой цвет и знак на каждый уровень поражения. */
export const CAUSE_UI = {
  regional: { tag: 'danger', Icon: IcoLayers, short: 'Магистраль района' },
  provider_node: { tag: 'danger', Icon: IcoNodes, short: 'Узел провайдера' },
  school_lan: { tag: 'warn', Icon: IcoRouter, short: 'Уровень школы' },
  device: { tag: 'info', Icon: IcoWifi, short: 'Отдельный ПК' },
  undetermined: { tag: 'off', Icon: IcoShield, short: 'Не определён' },
  no_data: { tag: 'stale', Icon: IcoClock, short: 'Нет данных' },
  none: { tag: 'off', Icon: IcoShield, short: 'Не подтверждено' },
};
/* Достаточность данных для вывода: 2 — достаточно, 1 — ограниченно, 0 — недостаточно. */
export const QUALITY_TAG = { 2: 'ok', 1: 'warn', 0: 'danger' };
export const causeUi = (cause) => CAUSE_UI[cause] || CAUSE_UI.none;

const BAND_TAG = {
  'критическая': 'danger', 'высокая': 'danger', 'умеренная': 'warn',
  'низкая': 'ok', 'нет данных': 'off',
};

export default function DiagnosticsPage({ role, onOpenSchool }) {
  const [summary, setSummary] = useState(null);
  const [board, setBoard] = useState([]);
  const [forecast, setForecast] = useState([]);
  const [model, setModel] = useState(null);
  const [open, setOpen] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [s, b, f, m] = await Promise.all([
        api.mlSummary(), api.mlBoard(40), api.mlForecast(10), api.mlModelInfo().catch(() => null),
      ]);
      setSummary(s); setBoard(b); setForecast(f); setModel(m); setError('');
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const retrain = async () => {
    setBusy(true);
    try { await api.mlRetrain(); await load(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const attr = model?.attribution;
  const fcst = model?.forecast;
  const fr = summary?.freshness;
  const stale = Boolean(fr?.is_stale);

  return (
    <div className="page">
      {error ? <div className="login-error" style={{ marginBottom: 14 }}>{error}</div> : null}

      {/* ---------- Предполагаемые источники текущих отклонений ---------- */}
      <div className="kpi-strip" style={{ marginBottom: 14 }}>
        <div className="kpi">
          <div className="kpi-head"><span className="eyebrow">Организаций в отклонении</span></div>
          <div className="kpi-value">{summary?.total_affected ?? '—'}</div>
        </div>
        <div className="kpi">
          <div className="kpi-head"><span className="eyebrow">Предположительно провайдер</span></div>
          <div className="kpi-value" style={{ color: 'var(--danger)' }}>
            {summary?.provider_side ?? '—'}
            <small style={{ marginLeft: 6 }}>{summary ? `${summary.provider_share_pct}%` : ''}</small>
          </div>
        </div>
        <div className="kpi">
          <div className="kpi-head"><span className="eyebrow">Предположительно организация</span></div>
          <div className="kpi-value" style={{ color: 'var(--warn)' }}>
            {summary?.school_side ?? '—'}
          </div>
        </div>
        <div className="kpi">
          <div className="kpi-head"><span className="eyebrow">Источник не определён</span></div>
          <div className="kpi-value">{summary?.undetermined ?? '—'}</div>
        </div>
        <div className="kpi">
          <div className="kpi-head"><span className="eyebrow">Прогноз пробоя SLA</span></div>
          <div className="kpi-value">{forecast.length}<small> школ</small></div>
        </div>
      </div>

      <div className="pair-grid" style={{ gridTemplateColumns: 'minmax(0, 1.55fr) minmax(0, 1fr)',
        display: 'grid', gap: 14, alignItems: 'start' }}>

        {/* ---------- Доска вердиктов ---------- */}
        <div className="page-card">
          <div className="page-card-head">
            <IcoNodes size={17} style={{ color: 'var(--accent)' }} />
            <h3>Предполагаемые источники по текущим отклонениям</h3>
            <Tag kind="info" className="tag ml">{board.length}</Tag>
          </div>
          <div style={{ padding: 14, maxHeight: 620, overflowY: 'auto' }}>
            {board.map((v) => {
              const ui = causeUi(v.cause);
              const isOpen = open === v.school_id;
              return (
                <div key={v.school_id} className={`insight ${ui.tag === 'info' ? '' : ui.tag}`}
                  style={{ display: 'block', cursor: 'pointer' }}
                  onClick={() => setOpen(isOpen ? null : v.school_id)}>
                  <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                    <div className="insight-icon"><ui.Icon size={18} /></div>
                    <div className="insight-body">
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                        <Tag kind={ui.tag}>{v.cause_label}</Tag>
                        <Tag kind="off">оценка модели {Math.round(v.confidence * 100)}%</Tag>
                        {v.data_quality ? (
                          <Tag kind={QUALITY_TAG[v.data_quality.level]}>
                            данные: {v.data_quality.label}
                          </Tag>
                        ) : null}
                        {v.actionable ? <Tag kind="danger">основание для претензии</Tag> : null}
                      </div>
                      <div className="insight-title sm">{v.school_name}</div>
                      <div className="insight-note" style={{ margin: 0 }}>
                        {v.district} · {v.provider} · ответственность: <b>{v.responsible}</b>
                      </div>
                    </div>
                  </div>

                  {isOpen ? (
                    <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--line)' }}
                      onClick={(e) => e.stopPropagation()}>
                      <p className="insight-note" style={{ marginTop: 0 }}>{v.narrative}</p>
                      {v.data_quality?.reasons?.length ? (
                        <p className="insight-note">
                          <b>Ограничения данных:</b> {v.data_quality.reasons.join('; ')}.
                        </p>
                      ) : null}
                      <div className="mini-stats" style={{ marginTop: 10 }}>
                        <div className="mini-stat">
                          <div className="k">ПК в отклонении</div>
                          <div className="v">{v.evidence.devices_affected}/{v.evidence.devices_total}</div>
                        </div>
                        <div className="mini-stat">
                          <div className="k">Сопоставимых школ провайдера</div>
                          <div className="v">
                            {v.evidence.peers_same_provider_district_affected}/
                            {v.evidence.peers_same_provider_district}
                          </div>
                        </div>
                        <div className="mini-stat">
                          <div className="k">Другие провайдеры района</div>
                          <div className="v">{v.evidence.peers_other_providers_district_affected_pct}%</div>
                        </div>
                        <div className="mini-stat">
                          <div className="k">Просадка к норме</div>
                          <div className="v">{v.evidence.avg_depth_pct}%</div>
                        </div>
                      </div>

                      {v.evidence.affected?.length ? (
                        <>
                        <Section title="Поражённые ПК" />
                        <div style={{ overflowX: 'auto' }}>
                          <table className="grid">
                            <thead>
                              <tr><th>ПК</th><th>Кабинет</th><th>Линк</th><th>Просадка</th><th>z</th></tr>
                            </thead>
                            <tbody>
                              {v.evidence.affected.slice(0, 6).map((d) => (
                                <tr key={d.device_id}>
                                  <td><code>{d.device_id}</code></td>
                                  <td>{d.room}</td>
                                  <td style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>
                                    {d.link_mode}{d.wifi_signal_dbm ? ` · ${d.wifi_signal_dbm} дБм` : ''}
                                  </td>
                                  <td className="num">{d.depth_pct}%</td>
                                  <td className="num">{d.offline ? 'обрыв' : d.z}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        </>
                      ) : null}

                      {v.drivers?.length ? (
                        <p className="insight-note">
                          Решающие признаки модели:{' '}
                          {v.drivers.map((d) => `${d.feature} (${d.contribution > 0 ? '+' : ''}${d.contribution})`)
                            .join(', ')}
                        </p>
                      ) : null}

                      <div className="btn-row" style={{ marginTop: 10 }}>
                        <button className="btn" onClick={() => onOpenSchool(v.school_id)}>
                          Открыть карточку организации
                        </button>
                      </div>
                    </div>
                  ) : null}
                </div>
              );
            })}
            {!board.length && !busy
              ? <div className="empty">
                {'Отклонений от сезонной нормы не зафиксировано.'}
              </div> : null}
          </div>
        </div>

        {/* ---------- Правая колонка: прогноз и модель ---------- */}
        <div style={{ display: 'grid', gap: 14 }}>
          <div className="page-card">
            <div className="page-card-head">
              <IcoTrend size={17} style={{ color: 'var(--danger)' }} />
              <h3>Прогноз пробоя SLA · {fcst?.horizon_hours || 6} ч</h3>
            </div>
            <div style={{ padding: 14 }}>
              {forecast.map((f) => (
                <div key={f.school_id} className="insight neutral" style={{ marginBottom: 8 }}
                  onClick={() => onOpenSchool(f.school_id)}>
                  <div className="insight-icon"><IcoAlert size={17} /></div>
                  <div className="insight-body">
                    <div className="insight-title sm" style={{ margin: '2px 0 6px' }}>
                      {f.school_name}
                    </div>
                    <div className="insight-meter">
                      <div className="bar">
                        <i style={{
                          width: `${Math.round(f.probability * 100)}%`,
                          background: f.probability >= 0.7 ? 'var(--danger)'
                            : f.probability >= 0.45 ? 'var(--warn)' : 'var(--accent)',
                        }} />
                      </div>
                      <span>{Math.round(f.probability * 100)}%</span>
                    </div>
                    <div className="insight-note" style={{ marginTop: 6 }}>
                      {f.district} · {f.provider} · риск <Tag kind={BAND_TAG[f.band] || 'off'}>{f.band}</Tag>
                    </div>
                  </div>
                </div>
              ))}
              {!forecast.length && !busy
                ? <div className="empty">
                  {'Школ с повышенным риском нет.'}
                </div> : null}
            </div>
          </div>

          <div className="page-card">
            <div className="page-card-head">
              <IcoPc size={17} style={{ color: 'var(--violet, var(--accent))' }} />
              <h3>Модели</h3>
              <button className="icon-btn ml" style={{ marginLeft: 'auto' }} title="Обновить"
                disabled={busy} onClick={load}><IcoRefresh size={16} /></button>
            </div>
            <div style={{ padding: 14 }}>
              <Section title="Атрибуция причины" />
              <div>
                <Pair label="Версия">{attr?.version || 'не обучена'}</Pair>
                <Pair label="Accuracy (hold-out)">{attr?.metrics?.accuracy ?? '—'}</Pair>
                <Pair label="Macro-F1">{attr?.metrics?.macro_f1 ?? '—'}</Pair>
                <Pair label="Обучающих примеров">{attr?.metrics?.train_samples ?? '—'}</Pair>
                <Pair label="Классов">{attr?.classes?.length ?? '—'}</Pair>
              </div>
              <Section title={`Прогноз ${fcst?.horizon_hours || 6} ч`} />
              <div>
                <Pair label="Версия">{fcst?.version || 'не обучена'}</Pair>
                <Pair label="Accuracy (hold-out)">{fcst?.metrics?.accuracy ?? '—'}</Pair>
                <Pair label="Macro-F1">{fcst?.metrics?.macro_f1 ?? '—'}</Pair>
                <Pair label="Полнота по классу «пробой»">
                  {fcst?.metrics?.per_class?.breach?.recall ?? '—'}
                </Pair>
              </div>
              <Section title="Данные обучения" />
              <div>
                <Pair label="Меток всего">{model?.labels?.total ?? '—'}</Pair>
                <Pair label="От операторов">{model?.labels?.from_operators ?? '—'}</Pair>
                <Pair label="От симулятора">{model?.labels?.from_simulator ?? '—'}</Pair>
                <Pair label="Сезонный базис">
                  {model?.baseline?.devices ? `${model.baseline.devices} ПК` : '—'}
                </Pair>
                <Pair label="Построен">{fmtDateTime(model?.baseline?.built_at)}</Pair>
                <Pair label="Среда">{model?.runtime}</Pair>
              </div>
              {role === 'admin' ? (
                <button className="rail-cta" disabled={busy} onClick={retrain}>
                  <IcoRefresh size={15} />
                  {busy ? 'Переобучение…' : 'Переобучить на текущих данных'}
                </button>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
