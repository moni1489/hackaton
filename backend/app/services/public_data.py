"""Официальный каталог и проверенные публикации. Повторные импорты идемпотентны."""
import json
import math
import re
from datetime import datetime
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from ..models import OfficialSchool, PublishedConnection, School

DATA_DIR = Path(__file__).resolve().parents[2] / 'data' / 'public'
EGOV_URL = 'https://data.egov.kz/datasets/view?index=state_schools'
EGOV_DATA = 'https://data.egov.kz/datasets/getdata'
VKO = 'Восточно-Казахстанская область'


def download_catalog(client: httpx.Client) -> dict:
    rows, seen, total = [], set(), None
    # Витрина ограничивает страницы 20 строками. Поиск по полному названию
    # региона не работает; широкий поиск дополняем точным локальным фильтром.
    for page in range(1, 501):
        response = client.get(EGOV_DATA, params={
            'index': 'state_schools', 'version': 'v1', 'page': page,
            'count': 20, 'text': 'Восточно', 'column': 'id', 'order': 'ascending',
        }, headers={'X-Requested-With': 'XMLHttpRequest', 'Referer': EGOV_URL})
        response.raise_for_status()
        data = response.json()
        if total is None:
            total = int(data['totalCount'])
        if total != int(data['totalCount']):
            raise ValueError('eGov changed during pagination; retry import')
        for row in data['elements']:
            if row['id'] in seen:
                raise ValueError('eGov returned duplicate page/ID')
            seen.add(row['id'])
            if row.get('region_nam') == VKO:
                rows.append(row)
        if page >= int(data['totalPages']):
            break
    if len(seen) != total or not rows:
        raise ValueError('Incomplete or empty eGov response')
    return {'source_url': EGOV_URL, 'retrieved_at': datetime.utcnow().isoformat(),
            'source_updated_at': '2024-04-27', 'source_actuality': False,
            'note': 'Источник помечен как неактуальный; названия школ отсутствуют.',
            'records': rows}


def import_catalog(db: Session, snapshot: dict) -> int:
    if snapshot.get('source_url') != EGOV_URL or not snapshot.get('records'):
        raise ValueError('Invalid eGov snapshot')
    fetched = datetime.fromisoformat(snapshot['retrieved_at'])
    validated = []
    ids = set()
    for row in snapshot['records']:
        if row.get('region_nam') != VKO or str(row['id']) in ids:
            raise ValueError('Wrong region or duplicate school ID')
        ids.add(str(row['id']))
        lat, lng = float(row['lat']), float(row['long'])
        if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError('Invalid coordinates')
        validated.append((row, lat, lng))
    for row, lat, lng in validated:
        item = db.get(OfficialSchool, str(row['id'])) or OfficialSchool(external_id=str(row['id']))
        item.district, item.settlement, item.address = row.get('district_n'), row.get('area_name'), row.get('address')
        item.lat, item.lng = lat, lng
        item.students = int(row['student_co']) if row.get('student_co') else None
        item.source_url, item.fetched_at = EGOV_URL, fetched
        item.raw_json = json.dumps(row, ensure_ascii=False)
        db.add(item)
    db.flush()
    return len(validated)


def normalize_name(value: str) -> str:
    return re.sub(r'[^\w]+', ' ', value.casefold().replace('ё', 'е')).strip()


def import_connections(db: Session, snapshot: dict) -> dict:
    matched = 0
    schools = db.query(School).all()
    for row in snapshot['records']:
        item = db.get(PublishedConnection, row['key']) or PublishedConnection(key=row['key'])
        # Только перечисленные и проверенные варианты названий + точный район.
        aliases = {normalize_name(n) for n in row.get('verified_aliases', [])}
        candidates = [s for s in schools if s.region == row['district'] and normalize_name(s.name or '') in aliases]
        item.school_id = candidates[0].id if len(candidates) == 1 else None
        matched += item.school_id is not None
        for key in ('school_name', 'district', 'technology', 'speed_down_mbps', 'note'):
            setattr(item, key, row.get(key))
        item.source_url = snapshot['source_url']
        item.source_date = snapshot.get('source_date')
        item.retrieved_at = datetime.fromisoformat(snapshot['retrieved_at'])
        db.add(item)
    db.flush()
    return {'published': len(snapshot['records']), 'matched': matched}


def connection_dict(row: PublishedConnection) -> dict:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def school_connections(db: Session, school_id: int) -> list[dict]:
    return [connection_dict(r) for r in db.query(PublishedConnection).filter_by(school_id=school_id).all()]
