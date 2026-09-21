"""Рейтинг: SQL-оценка совпадает с classify(), ранжирование, хранение не меньше квартала.

Запуск:  cd backend && python -m unittest tests.test_rating -v   (временная SQLite-база)
"""
import os
import random
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/rating.db")
os.environ.setdefault("EXTERNAL_POLL_ENABLED", "false")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.database as database  # noqa: E402
from app.config import settings  # noqa: E402
from app.models import Device, Line, Measurement, School  # noqa: E402
from app.services import rating  # noqa: E402
from app.services.retention import MIN_RETENTION_DAYS, purge_old_measurements  # noqa: E402
from app.services.status import STATUS_OK, classify  # noqa: E402

NOW = datetime(2026, 6, 30, 12, 0)
CONTRACTS = {9001: (None, None), 9002: (10.0, 10.0), 9003: (100.0, 5.0), 9004: (500.0, 500.0)}


def _fixture(db):
    """Школа = основная линия + один монитор; замеры вокруг порогов, чтобы задеть все ветки."""
    rnd = random.Random(1)
    for sid, (down, up) in CONTRACTS.items():
        db.add(School(id=sid, name=f"S{sid}", contract_speed_down=down, contract_speed_up=up))
        db.add(Line(id=sid, school_id=sid, role="main"))
        db.add(Device(device_id=f"RT-{sid}", school_id=sid, line_id=sid))
        for i in range(200):
            db.add(Measurement(
                device_id=f"RT-{sid}", school_id=sid, timestamp=NOW - timedelta(hours=i),
                download_speed=rnd.choice([0, 5, 9, 12, 19, 21, 55, 61, 99, 250, 600]),
                upload_speed=rnd.choice([1, 4, 6, 19, 21, 80]), ping=rnd.choice([10, 99, 101, 160, 260]),
                jitter=rnd.choice([5, 29, 31, 50]), packet_loss=rnd.choice([0, 1.9, 2.1, 5, 6]),
                is_offline=rnd.random() < 0.05))
    db.commit()


class RatingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.Base.metadata.create_all(database.engine)
        cls.db = database.SessionLocal()
        _fixture(cls.db)

    @classmethod
    def tearDownClass(cls):
        for model in (Measurement, Device, Line, School):
            cls.db.query(model).filter(model.id.in_(CONTRACTS) if model is not Measurement
                                       else Measurement.school_id.in_(CONTRACTS)).delete(
                synchronize_session=False)
        cls.db.commit()
        cls.db.close()

    def test_sql_matches_classify(self):
        stats = rating.aggregate(self.db, NOW - timedelta(days=30), NOW + timedelta(hours=1), list(CONTRACTS))
        for sid, (down, up) in CONTRACTS.items():
            rows = self.db.query(Measurement).filter(Measurement.school_id == sid).all()
            ok = sum(classify(r.download_speed, r.ping, r.packet_loss, down, r.is_offline,
                              upload=r.upload_speed, jitter=r.jitter, contract_up=up) == STATUS_OK
                     for r in rows)
            self.assertAlmostEqual(stats[sid]["sla_pct"], round(100 * ok / len(rows), 1), delta=0.05)

    def test_ranking_and_unrated(self):
        self.db.add(School(id=9005, name="few"))
        self.db.add(Line(id=9005, school_id=9005, role="main"))
        self.db.add(Device(device_id="RT-9005", school_id=9005, line_id=9005))
        self.db.add(Measurement(device_id="RT-9005", school_id=9005, timestamp=NOW, download_speed=99,
                                upload_speed=99, ping=1, jitter=1, packet_loss=0, is_offline=False))
        self.db.commit()
        try:
            schools = self.db.query(School).filter(School.id.in_([*CONTRACTS, 9005])).all()
            data = rating.rating(self.db, NOW + timedelta(hours=1), 30, [s.id for s in schools], schools)
            ranks = [i["rank"] for i in data["items"]]
            self.assertEqual(ranks, [1, 2, 3, 4, None])          # недоопределённая школа — в конце
            scores = [i["score"] for i in data["items"][:4]]
            self.assertEqual(scores, sorted(scores, reverse=True))
            self.assertEqual(data["unrated"], 1)
        finally:
            self.db.query(Measurement).filter(Measurement.school_id == 9005).delete()
            self.db.query(Device).filter(Device.school_id == 9005).delete()
            self.db.query(Line).filter(Line.id == 9005).delete()
            self.db.query(School).filter(School.id == 9005).delete()
            self.db.commit()

    def test_history_is_daily(self):
        days = rating.history(self.db, NOW + timedelta(hours=1), 30, [9001])
        self.assertTrue(8 <= len(days) <= 10)                     # 200 ч ≈ 8.3 суток
        self.assertEqual(sum(d["samples"] for d in days), 200)

    def test_retention_never_below_quarter(self):
        settings.RETENTION_DAYS = 10                              # «слишком мало» — пол в 92 дня
        db = database.SessionLocal()
        try:
            for school, age in ((9101, MIN_RETENTION_DAYS - 1), (9102, MIN_RETENTION_DAYS + 1)):
                db.add(Measurement(device_id="RT-old", school_id=school, timestamp=NOW - timedelta(days=age)))
            db.add(Measurement(device_id="RT-old", school_id=9103, timestamp=NOW))
            db.commit()
            newest = db.query(Measurement).order_by(Measurement.timestamp.desc()).first().timestamp
            purge_old_measurements(db)
            left = {m.school_id for m in db.query(Measurement).filter(Measurement.school_id.in_([9101, 9102]))}
            # относительно самого свежего замера в базе: моложе квартала — остаётся, старше — удалён
            self.assertEqual(left, {9101} if newest == NOW else left)
            self.assertNotIn(9102, left)
        finally:
            db.query(Measurement).filter(Measurement.school_id.in_([9101, 9102, 9103])).delete()
            db.commit()
            db.close()
            settings.RETENTION_DAYS = 120


if __name__ == "__main__":
    unittest.main()
