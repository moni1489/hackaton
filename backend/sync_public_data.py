"""python backend/sync_public_data.py --catalog --connections --network"""
import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from app.database import Base, SessionLocal, engine
from app.services.public_data import DATA_DIR, download_catalog, import_catalog, import_connections
from app.services.external_network import MLAB_URL, refresh_sources, save_success, validate_mlab


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog', action='store_true', help='Download complete eGov VKO catalog')
    p.add_argument('--catalog-file', type=Path, help='Import previously downloaded snapshot')
    p.add_argument('--connections', action='store_true')
    p.add_argument('--network', action='store_true', help='Refresh IODA and RIPE Atlas')
    p.add_argument('--mlab', action='store_true', help='Query M-Lab via configured BigQuery')
    p.add_argument('--mlab-file', type=Path, help='Import JSON array from the supplied SQL query')
    args = p.parse_args()
    if not any(vars(args).values()):
        p.error('Select at least one source')
    Base.metadata.create_all(engine)
    result = {}
    with SessionLocal() as db:
        if args.catalog or args.catalog_file:
            if args.catalog:
                with httpx.Client(timeout=30, follow_redirects=True, transport=httpx.HTTPTransport(retries=2)) as client:
                    snapshot = download_catalog(client)
            else:
                snapshot = json.loads(args.catalog_file.read_text())
            result['official_schools'] = import_catalog(db, snapshot)
            db.commit()
            if args.catalog:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                target = DATA_DIR / 'egov_vko_schools.json'
                tmp = target.with_suffix('.tmp')
                tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
                tmp.replace(target)
        if args.connections:
            result['connections'] = import_connections(db, json.loads((DATA_DIR / 'shemonaiha_connections.json').read_text()))
            db.commit()
        if args.mlab_file:
            rows = validate_mlab(json.loads(args.mlab_file.read_text()))
            if not rows:
                p.error('M-Lab export is empty; cannot infer coverage window')
            days = [datetime.strptime(r['day'], '%Y-%m-%d') for r in rows]
            save_success(db, 'mlab', {'rows': rows, 'scope': 'Казахстан, по городам и ASN',
                         'note': 'Импорт агрегатов SQL; геолокация по IP, не школьные измерения.'},
                         min(days), max(days) + timedelta(days=1), MLAB_URL)
            result['mlab_rows'] = len(rows)
    if args.network:
        result.update(refresh_sources(('ioda', 'ripe_atlas')))
    if args.mlab:
        result.update(refresh_sources(('mlab',)))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if any(v in ('error', 'not_configured') for v in result.values() if isinstance(v, str)):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
