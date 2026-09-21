"""Offline integration checks: never query production or train on external data."""
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import ExternalSourceState, OfficialSchool, PublishedConnection, School
from app.services.external_network import fetch_atlas, fetch_ioda, network_context, refresh_sources, save_success, validate_mlab
from app.services.public_data import DATA_DIR, download_catalog, import_catalog, import_connections


class PublicDataTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.now = datetime(2026, 9, 21, 12)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_catalog_idempotency_and_no_fake_operational_rows(self):
        snapshot = json.loads((DATA_DIR / 'egov_vko_schools.json').read_text())
        n = import_catalog(self.db, snapshot)
        self.db.commit()
        import_catalog(self.db, snapshot)
        self.db.commit()
        self.assertEqual(n, self.db.query(OfficialSchool).count())
        self.assertEqual(0, self.db.query(School).count())

    def test_reject_bad_catalog_before_write(self):
        snapshot = json.loads((DATA_DIR / 'egov_vko_schools.json').read_text())
        snapshot['records'][-1]['lat'] = 'NaN'
        with self.assertRaises(ValueError):
            import_catalog(self.db, snapshot)
        self.assertEqual(0, self.db.query(OfficialSchool).count())

    def test_catalog_download_rejects_duplicate_page(self):
        row = json.loads((DATA_DIR / 'egov_vko_schools.json').read_text())['records'][0]
        def handler(request):
            self.assertEqual(request.headers['X-Requested-With'], 'XMLHttpRequest')
            return httpx.Response(200, json={'totalCount': 2, 'totalPages': 2, 'elements': [row]})
        with httpx.Client(transport=httpx.MockTransport(handler)) as c:
            with self.assertRaises(ValueError):
                download_catalog(c)

    def test_connections_require_unambiguous_identity_preserve_contract(self):
        data = json.loads((DATA_DIR / 'shemonaiha_connections.json').read_text())
        s = School(name='Школа № 1 имени Н.А. Островского', region='Шемонаихинский район', contract_speed_down=50, connection_type='ADSL')
        self.db.add(s)
        self.db.commit()
        report = import_connections(self.db, data)
        self.db.commit()
        self.assertEqual(1, report['matched'])
        self.assertEqual(50, s.contract_speed_down)
        self.assertEqual('ВОЛС', s.connection_type)
        self.db.add(School(name=s.name, region=s.region))
        self.db.commit()
        self.assertEqual(0, import_connections(self.db, data)['matched'])
        self.assertIsNone(self.db.get(PublishedConnection, 'shemonaiha:ostrovsky').school_id)

    def test_ioda_zero_based_pagination(self):
        def handler(request):
            self.assertEqual(request.url.params['page'], '0')
            return httpx.Response(200, json={'error': None, 'data': [
                {'location': 'region/2083', 'start': 123, 'duration': 300, 'datasource': 'bgp'}]})
        with httpx.Client(transport=httpx.MockTransport(handler)) as c:
            payload, _, _ = fetch_ioda(c, self.now)
        self.assertEqual(1, len(payload['events']))

    def test_ioda_unknown_temporal_overlap_and_no_false_future(self):
        self.assertIsNone(network_context(self.db, self.now)['ioda_regional_event'])
        start = int(self.now.replace(tzinfo=timezone.utc).timestamp())
        save_success(self.db, 'ioda', {'events': [{'start': start, 'duration': 300}]},
                     self.now - timedelta(days=1), self.now + timedelta(hours=1), 'test', self.now)
        self.assertTrue(network_context(self.db, self.now)['ioda_regional_event'])
        self.assertFalse(network_context(self.db, self.now + timedelta(seconds=300))['ioda_regional_event'])
        self.assertIsNone(network_context(self.db, self.now + timedelta(days=1))['ioda_regional_event'])
        aware = self.now.replace(tzinfo=timezone.utc)
        self.assertTrue(network_context(self.db, aware)['ioda_regional_event'])

    def test_source_failure_preserves_last_snapshot(self):
        save_success(self.db, 'ioda', {'events': [{'start': 123, 'duration': 300}]}, self.now, self.now, 'test', self.now)
        with patch('app.services.external_network.SessionLocal', lambda: Session(self.engine)), patch('app.services.external_network.fetch_ioda', side_effect=httpx.ReadTimeout('timeout')):
            self.assertEqual('error', refresh_sources(('ioda',))['ioda'])
        self.db.expire_all()
        state = self.db.get(ExternalSourceState, 'ioda')
        self.assertEqual(1, len(json.loads(state.payload_json)['events']))
        self.assertEqual(self.now, state.fetched_at)

    def test_atlas_loss_and_no_ip_storage(self):
        def handler(request):
            if '/probes/' in str(request.url):
                return httpx.Response(200, json={'country_code': 'KZ', 'is_public': True, 'id': 6753, 'address_v4': '192.0.2.1'})
            return httpx.Response(200, json=[{'type': 'ping', 'prb_id': 6753, 'timestamp': 123, 'sent': 3, 'rcvd': 0, 'avg': -1, 'src_addr': '192.0.2.1'}])
        with patch('app.services.external_network.settings.RIPE_PROBE_IDS', '6753'), patch('app.services.external_network.settings.RIPE_MEASUREMENT_IDS', '1001'), httpx.Client(transport=httpx.MockTransport(handler)) as c:
            payload, _, _ = fetch_atlas(c, self.now)
        self.assertEqual(100, payload['observations'][0]['packet_loss_pct'])
        self.assertIsNone(payload['observations'][0]['rtt_ms'])
        self.assertNotIn('192.0.2.1', json.dumps(payload))

    def test_mlab_validation_and_duplicate_rejection(self):
        r = dict(day='2026-09-18', country_code='KZ', city='Oskemen', asn='9198', tests=10, download_mbps=70, rtt_ms=22)
        self.assertEqual(70, validate_mlab([r])[0]['download_mbps'])
        with self.assertRaises(ValueError): validate_mlab([r, r])
        with self.assertRaises(ValueError): validate_mlab([{**r, 'download_mbps': float('nan')}])
        with self.assertRaises(ValueError): validate_mlab([{**r, 'country_code': 'US'}])


if __name__ == '__main__':
    unittest.main()
