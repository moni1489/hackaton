"""Независимые наблюдения: контекст диагностики, а не метки для обучения."""
import asyncio
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import ExternalSourceState

log = logging.getLogger(__name__)
IODA = 'https://api.ioda.inetintel.cc.gatech.edu/v2'
ATLAS = 'https://atlas.ripe.net/api/v2'
MLAB_URL = 'https://www.measurementlab.net/data/'
SQL_PATH = Path(__file__).resolve().parents[2] / 'data' / 'mlab_kz.sql'


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def epoch(dt):
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def get_json(client, url, **kwargs):
    r = client.get(url, **kwargs)
    r.raise_for_status()
    return r.json()


def fetch_ioda(client, now):
    start = now - timedelta(days=365)
    events = []
    for page in range(1, 101):
        data = get_json(client, IODA + '/outages/events', params={
            'entityType': 'region', 'entityCode': settings.IODA_REGION_CODE,
            'from': epoch(start), 'until': epoch(now), 'limit': 100, 'page': page,
        })
        if data.get('error') or not isinstance(data.get('data'), list):
            raise ValueError('Invalid IODA response')
        for row in data['data']:
            if row['location'] != 'region/' + settings.IODA_REGION_CODE:
                raise ValueError('Unexpected IODA region')
            duration = float(row['duration'])
            if not math.isfinite(duration) or duration < 0:
                raise ValueError('Invalid IODA event duration')
            events.append({k: row.get(k) for k in ('location', 'location_name', 'start', 'duration', 'datasource', 'method', 'score')})
        if len(data['data']) < 100:
            break
    else:
        raise ValueError('IODA pagination limit reached')
    # IODA region boundaries are its own geolocation, not school topology.
    return {'events': events, 'scope': 'IODA region/' + settings.IODA_REGION_CODE,
            'note': 'Региональная геолокация IODA; границы могут отличаться от административных. Причина аварии школы не подтверждена.'}, start, now


def fetch_atlas(client, now):
    probe_ids = {int(x.strip()) for x in settings.RIPE_PROBE_IDS.split(',') if x.strip()}
    if not probe_ids:
        raise ValueError('No RIPE probe IDs configured')
    probes = []
    for probe_id in sorted(probe_ids):
        p = get_json(client, f'{ATLAS}/probes/{probe_id}/')
        if p.get('country_code') != 'KZ' or not p.get('is_public'):
            raise ValueError('Expected a public Kazakhstan probe')
        probes.append({k: p.get(k) for k in ('id', 'description', 'asn_v4', 'geometry', 'status')})
    observations = []
    for msm_id in (int(x.strip()) for x in settings.RIPE_MEASUREMENT_IDS.split(',') if x.strip()):
        rows = get_json(client, f'{ATLAS}/measurements/{msm_id}/latest/', params={'probe_ids': ','.join(map(str, sorted(probe_ids)))})
        if not isinstance(rows, list):
            raise ValueError('Invalid Atlas result')
        for row in rows:
            if row.get('prb_id') not in probe_ids or row.get('type') != 'ping':
                continue
            sent, received = row.get('sent', 0), row.get('rcvd', 0)
            observations.append({'probe_id': row['prb_id'], 'measurement_id': msm_id,
                'timestamp': row['timestamp'], 'target': row.get('dst_name'),
                'rtt_ms': row['avg'] if row.get('avg', -1) >= 0 else None,
                'packet_loss_pct': max(0, min(100, 100 * (sent - received) / sent)) if sent > 0 else None,
                'sent': sent, 'received': received})
    return {'probes': probes, 'observations': observations,
            'scope': 'Два публичных зонда в районе Усть-Каменогорска; не вся ВКО',
            'note': 'Ping до внешней цели. Это не измерения школьного канала и не проверка его SLA.'}, now, now


def validate_mlab(rows):
    clean = []
    for row in rows:
        day = datetime.strptime(str(row['day']), '%Y-%m-%d')
        if row['country_code'] != 'KZ' or int(row['tests']) <= 0:
            raise ValueError('Invalid M-Lab country/count')
        values = {}
        for name in ('download_mbps', 'rtt_ms'):
            value = float(row[name]) if row.get(name) is not None else None
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError('Invalid M-Lab metric')
            values[name] = value
        clean.append({'day': day.date().isoformat(), 'country_code': 'KZ',
                      'city': str(row.get('city') or 'Unknown'), 'asn': str(row.get('asn') or ''),
                      'tests': int(row['tests']), **values})
    if len({(r['day'], r['city'], r['asn']) for r in clean}) != len(clean):
        raise ValueError('Duplicate M-Lab aggregation keys')
    return clean


def fetch_mlab(client, now):
    if not settings.MLAB_PROJECT:
        raise RuntimeError('M-Lab: настройте MLAB_PROJECT и Google Application Default Credentials либо импортируйте JSON-выгрузку SQL-запроса')
    # Optional dependency; no credentials or account identifiers in errors/logs.
    from google.cloud import bigquery
    end = now.date() - timedelta(days=1)  # публикация M-Lab запаздывает минимум на сутки
    start = end - timedelta(days=7)
    config = bigquery.QueryJobConfig(
        maximum_bytes_billed=settings.MLAB_MAX_BYTES_BILLED,
        query_parameters=[bigquery.ScalarQueryParameter('start_date', 'DATE', start),
                          bigquery.ScalarQueryParameter('end_date', 'DATE', end)])
    with bigquery.Client(project=settings.MLAB_PROJECT) as bq:
        result = bq.query(SQL_PATH.read_text(), job_config=config).result(timeout=60)
        rows = validate_mlab([dict(r) for r in result])
    return {'rows': rows, 'scope': 'Казахстан, по городам и ASN',
            'note': 'Дневные медианы добровольных NDT-тестов; геолокация по IP, не школьные измерения.'}, datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time())


def save_success(db, source, payload, start, end, url, now=None):
    now = now or utcnow()
    state = db.get(ExternalSourceState, source) or ExternalSourceState(source=source)
    state.status, state.error = 'ok', None
    state.attempted_at = state.fetched_at = now
    state.window_start, state.window_end = start, end
    state.source_url, state.payload_json = url, json.dumps(payload, ensure_ascii=False)
    db.add(state)
    db.commit()


def refresh_sources(sources=('ioda', 'ripe_atlas', 'mlab')):
    adapters = {'ioda': (fetch_ioda, IODA), 'ripe_atlas': (fetch_atlas, ATLAS), 'mlab': (fetch_mlab, MLAB_URL)}
    result = {}
    with httpx.Client(timeout=20, follow_redirects=True) as client, SessionLocal() as db:
        for source in sources:
            fetch, url = adapters[source]
            now = utcnow()
            try:
                payload, start, end = fetch(client, now)
                save_success(db, source, payload, start, end, url, now)
                result[source] = 'ok'
            except Exception as exc:
                db.rollback()
                state = db.get(ExternalSourceState, source) or ExternalSourceState(source=source)
                missing = source == 'mlab' and not settings.MLAB_PROJECT
                state.status = 'not_configured' if missing else 'error'
                state.error = ('Требуются Google BigQuery credentials и MLAB_PROJECT либо JSON-выгрузка.'
                               if missing else f'Обновление не удалось ({type(exc).__name__}); последний успешный снимок сохранён.')
                state.attempted_at, state.source_url = now, url
                db.add(state)
                db.commit()
                result[source] = state.status
                log.warning('External source %s: %s', source, type(exc).__name__)
    return result


def network_context(db: Session, at=None):
    at = at or utcnow()
    if at.tzinfo is not None:
        at = at.astimezone(timezone.utc).replace(tzinfo=None)
    now = utcnow()
    states = {s.source: s for s in db.query(ExternalSourceState).all()}
    output = {}
    for source in ('ioda', 'ripe_atlas', 'mlab'):
        s = states.get(source)
        if s is None:
            output[source] = {'status': 'no_data', 'payload': {}, 'stale': True}
            continue
        stale = not s.fetched_at or now - s.fetched_at > timedelta(hours=48 if source == 'mlab' else 1)
        output[source] = {'status': s.status, 'fetched_at': s.fetched_at,
            'attempted_at': s.attempted_at, 'window_start': s.window_start, 'window_end': s.window_end,
            'stale': stale, 'source_url': s.source_url, 'error': s.error,
            'payload': json.loads(s.payload_json or '{}')}
    ioda = states.get('ioda')
    matches = []
    covered = bool(ioda and ioda.fetched_at and ioda.window_start <= at <= ioda.window_end)
    if covered:
        for e in output['ioda']['payload'].get('events', []):
            if e['start'] <= epoch(at) < e['start'] + e['duration']:
                matches.append(e)
    # For "now", allow a short polling lag; never extend old history indefinitely.
    recent = bool(ioda and ioda.window_end and timedelta(0) <= at - ioda.window_end <= timedelta(minutes=20)
                  and ioda.status == 'ok')
    if recent:
        matches = [e for e in output['ioda']['payload'].get('events', [])
                   if e['start'] <= epoch(at) < e['start'] + e['duration']]
    return {'at': at, 'sources': output,
            'ioda_regional_event': bool(matches) if covered or recent else None,
            'ioda_matching_events': matches,
            'interpretation': 'Внешний региональный признак. Совпадение по времени не доказывает причину школьной аварии; отсутствие события не доказывает исправность сети.'}


async def poll_sources():
    while True:
        try:
            # HTTP/BigQuery never blocks the API event loop.
            await asyncio.to_thread(refresh_sources, ('ioda', 'ripe_atlas'))
        except Exception:
            log.exception('External polling failed')
        await asyncio.sleep(max(60, settings.EXTERNAL_REFRESH_SEC))
