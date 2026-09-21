"""Демонстрация для презентации: роли, синхронизация зрителей, восстановление, Redis, PDF, безопасность.

Запуск:  cd backend && python -m unittest tests.test_demo -v
Поднимает настоящий uvicorn в потоке (иначе не проверить SSE) на временной SQLite-базе и
in-memory демо-БД. Боевую БД не трогает. Тесты Redis выполняются, если он доступен по
DEMO_TEST_REDIS_URL (по умолчанию redis://127.0.0.1:6390/0, см. demo.py test), иначе пропускаются.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

_TMP = tempfile.mkdtemp()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP}/demo.db")
os.environ.setdefault("EXTERNAL_POLL_ENABLED", "false")
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

import app.database as database  # noqa: E402
from app.config import settings  # noqa: E402
from app.models import AuditLog, User  # noqa: E402
from app.routers import auth, demo  # noqa: E402
from app.security import hash_password, verify_audit_chain  # noqa: E402
from app.services.demo import engine, scenario  # noqa: E402
from app.services.demo.store import Store  # noqa: E402

REDIS_URL = os.environ.get("DEMO_TEST_REDIS_URL", "redis://127.0.0.1:6390/0")
DEAD_REDIS = "redis://127.0.0.1:1/0"
PW = "correct-horse-battery"


@asynccontextmanager
async def _lifespan(_app):
    task = asyncio.create_task(engine.run_loop())
    yield
    task.cancel()


class Server:
    """Настоящий HTTP-сервер приложения (только демо + вход) в фоновом потоке."""

    def __init__(self):
        self.app = FastAPI(lifespan=_lifespan)
        self.app.include_router(auth.router)
        self.app.include_router(demo.router)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=0,
                                                    log_level="error", lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> str:
        self.thread.start()
        deadline = time.time() + 20
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("сервер не запустился")
            time.sleep(0.05)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    def stop(self):
        self.server.should_exit = True
        self.thread.join(10)


BASE = ""
SERVER = None


def setUpModule():
    global BASE, SERVER
    engine_ = create_engine(f"sqlite:///{_TMP}/demo.db", connect_args={"check_same_thread": False})
    database.engine = engine_
    database.SessionLocal.configure(bind=engine_)
    database.migrate_schema()
    with database.SessionLocal() as db:
        for email, role in (("op@t", "operator"), ("admin@t", "admin"), ("school@t", "school"),
                            ("prov@t", "provider")):
            db.add(User(email=email, full_name=email, role=role, password_hash=hash_password(PW)))
        db.commit()
    settings.DEMO_RATE_LIMIT_OPERATOR = 100_000    # тесты делают сотни действий; сам лимит проверяется отдельно
    settings.DEMO_RATE_LIMIT_SESSION = 100_000
    engine.store = Store(DEAD_REDIS)           # по умолчанию — режим без Redis
    SERVER = Server()
    BASE = SERVER.start()


def tearDownModule():
    SERVER.stop()


def login(email: str) -> dict:
    r = httpx.post(f"{BASE}/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


CREATED: list = []


class Session:
    """Сессия ведущего + токен зрителя — как в панели."""

    def __init__(self, who="op@t", **body):
        self.op = login(who)
        r = httpx.post(f"{BASE}/api/demo/sessions", json=body, headers=self.op, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        self.sid = data["session_id"]
        self.state = data["state"]
        self.link = data["link"]
        self.token = self.link["viewer_url"].split("#t=")[1]
        self.viewer = {"Authorization": f"Bearer {self.token}"}
        CREATED.append(self)

    def act(self, action, stage=None, expect=200):
        r = httpx.post(f"{BASE}/api/demo/sessions/{self.sid}/control",
                       json={"action": action, "stage": stage}, headers=self.op)
        assert r.status_code == expect, (r.status_code, r.text)
        return r.json()

    def go(self, *stages):
        for stage in stages:
            self.act("goto", stage)

    def view(self, cid="tester"):
        r = httpx.get(f"{BASE}/api/demo/live/{self.sid}/state", params={"cid": cid}, headers=self.viewer)
        assert r.status_code == 200, r.text
        return r.json()

    def decide(self, decision="confirm", cause=None, expect=200):
        r = httpx.post(f"{BASE}/api/demo/sessions/{self.sid}/verdict", headers=self.op,
                       json={"decision": decision, "cause": cause, "note": "внутренняя заметка"})
        assert r.status_code == expect, (r.status_code, r.text)
        return r.json()

    def close(self):
        httpx.delete(f"{BASE}/api/demo/sessions/{self.sid}", headers=self.op)


class Base(unittest.TestCase):
    def tearDown(self):                        # упавший тест не должен оставлять сессии (лимит DEMO_MAX_SESSIONS)
        while CREATED:
            CREATED.pop().close()


def full_run(s: Session, stop_at_review=False):
    s.act("start")
    s.go("incident_started", "diagnostics", "ml_result", "operator_review")
    if stop_at_review:
        return
    s.decide("confirm")
    s.act("next")


# --- Доступ -------------------------------------------------------------------------------------

class Access(Base):
    def test_only_operator_and_admin_manage(self):
        for who in ("op@t", "admin@t"):
            s = Session(who)
            self.assertEqual(s.state["view"]["stage"]["key"], "waiting")
            s.close()
        for who in ("school@t", "prov@t"):
            r = httpx.post(f"{BASE}/api/demo/sessions", json={}, headers=login(who))
            self.assertEqual(r.status_code, 403, who)
        self.assertEqual(httpx.post(f"{BASE}/api/demo/sessions", json={}).status_code, 401)
        self.assertEqual(httpx.get(f"{BASE}/api/demo/sessions").status_code, 401)

    def test_viewer_link_cannot_control_anything(self):
        s = Session()
        v = s.viewer
        calls = [
            ("POST", "/sessions", {}), ("GET", "/sessions", None),
            ("GET", f"/sessions/{s.sid}", None),
            ("POST", f"/sessions/{s.sid}/control", {"action": "start"}),
            ("PUT", f"/sessions/{s.sid}/settings", {"mode": "auto"}),
            ("POST", f"/sessions/{s.sid}/verdict", {"decision": "confirm"}),
            ("POST", f"/sessions/{s.sid}/report", None), ("POST", f"/sessions/{s.sid}/link", {}),
            ("DELETE", f"/sessions/{s.sid}", None), ("GET", f"/sessions/{s.sid}/report.pdf", None),
        ]
        for method, path, body in calls:
            r = httpx.request(method, f"{BASE}/api/demo{path}", json=body, headers=v)
            self.assertEqual(r.status_code, 401, (method, path))
        self.assertEqual(s.view()["stage"]["key"], "waiting")     # ничего не изменилось
        s.close()

    def test_viewer_token_is_bound_to_one_session_and_expires(self):
        a, b = Session(), Session()
        r = httpx.get(f"{BASE}/api/demo/live/{b.sid}/state", headers=a.viewer)
        self.assertEqual(r.status_code, 401)                      # токен сессии A не открывает B
        old, _ = engine.issue_viewer_token(a.sid, now=time.time() - (settings.DEMO_VIEWER_TTL_MIN + 1) * 60)
        r = httpx.get(f"{BASE}/api/demo/live/{a.sid}/state", headers={"Authorization": f"Bearer {old}"})
        self.assertEqual(r.status_code, 401)
        # токен оператора (обычный JWT) как токен зрителя не принимается, и наоборот
        r = httpx.get(f"{BASE}/api/demo/live/{a.sid}/state", headers=a.op)
        self.assertEqual(r.status_code, 401)
        r = httpx.get(f"{BASE}/api/auth/me", headers=a.viewer)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(httpx.get(f"{BASE}/api/demo/live/{a.sid}/state").status_code, 401)
        # подделка срока: продлить чужой токен нельзя — подпись покрывает срок
        exp, sig = a.token.split(".")
        forged = f"{int(exp) + 86400}.{sig}"
        r = httpx.get(f"{BASE}/api/demo/live/{a.sid}/state", headers={"Authorization": f"Bearer {forged}"})
        self.assertEqual(r.status_code, 401)
        self.assertLess(len(a.token), 40)                         # компактный: QR-код остаётся простым
        for s in (a, b):
            s.close()

    def test_viewer_errors_do_not_leak_details(self):
        s = Session()
        bad = httpx.get(f"{BASE}/api/demo/live/{s.sid}/state", headers={"Authorization": "Bearer x.y.z"})
        gone = httpx.get(f"{BASE}/api/demo/live/AAAAAAAAAAAA/state", headers={
            "Authorization": f"Bearer {engine.issue_viewer_token('AAAAAAAAAAAA')[0]}"})
        for r in (bad, gone):
            text = r.text.lower()
            for word in ("traceback", "jwt", "signature", "secret", "sqlalchemy", "redis", "exception"):
                self.assertNotIn(word, text)
        self.assertEqual(gone.status_code, 404)
        s.close()

    def test_operator_actions_are_audited_and_chain_is_valid(self):
        s = Session()
        s.act("start")
        httpx.post(f"{BASE}/api/demo/sessions/{s.sid}/link", json={}, headers=s.op)
        s.close()
        with database.SessionLocal() as db:
            actions = [a.action for a in db.query(AuditLog).filter(AuditLog.action.like("demo.%"))]
            self.assertTrue({"demo.create", "demo.link", "demo.control", "demo.close"} <= set(actions), actions)
            self.assertTrue(verify_audit_chain(db)["valid"])
            actor = db.query(AuditLog).filter(AuditLog.action == "demo.control").first()
            self.assertEqual(actor.actor, "op@t")

    def test_rate_limits(self):
        s = Session()
        keep = settings.DEMO_RATE_LIMIT_VIEWER
        settings.DEMO_RATE_LIMIT_VIEWER = 5
        try:
            codes = [httpx.get(f"{BASE}/api/demo/live/{s.sid}/state", params={"cid": "flood1"},
                               headers=s.viewer).status_code for _ in range(8)]
        finally:
            settings.DEMO_RATE_LIMIT_VIEWER = keep
        self.assertEqual(codes[:5], [200] * 5)
        self.assertEqual(set(codes[5:]), {429})
        # другой зритель за тем же адресом не страдает
        self.assertEqual(s.view(cid="other-viewer")["stage"]["key"], "waiting")
        s.close()

    def test_operator_actions_are_rate_limited(self):
        s = Session("admin@t")
        keep = settings.DEMO_RATE_LIMIT_OPERATOR
        settings.DEMO_RATE_LIMIT_OPERATOR = 4
        try:
            codes = [httpx.post(f"{BASE}/api/demo/sessions/{s.sid}/control", json={"action": "pause"},
                                headers=s.op).status_code for _ in range(8)]
        finally:
            settings.DEMO_RATE_LIMIT_OPERATOR = keep
        self.assertEqual(codes[-1], 429)          # окно счётчика общее с прочими тестами — считаем только хвост

    def test_login_is_rate_limited_in_demo_mode(self):
        keep = (settings.DEMO_MODE, settings.DEMO_LOGIN_RATE_LIMIT)
        settings.DEMO_MODE, settings.DEMO_LOGIN_RATE_LIMIT = True, 3
        try:
            codes = [httpx.post(f"{BASE}/api/auth/login", json={"email": "nobody@t", "password": "x"}).status_code
                     for _ in range(6)]
        finally:
            settings.DEMO_MODE, settings.DEMO_LOGIN_RATE_LIMIT = keep
        self.assertIn(429, codes)


# --- Сценарий, ML, PDF ----------------------------------------------------------------------------

class Scenario(Base):
    def test_full_flow_and_no_lookahead(self):
        s = Session()
        v = s.view()
        self.assertEqual(v["stage"]["key"], "waiting")
        for hidden in ("ml", "diagnostics", "decision", "report", "incident"):
            self.assertIsNone(v[hidden], hidden)
        self.assertTrue(all(sc["status"] is None for sc in v["schools"]))

        s.act("start")
        v = s.view()
        self.assertEqual(v["stage"]["key"], "normal")
        self.assertEqual(v["freshness"]["state"], "fresh")
        self.assertTrue(all(sc["status"] == "Норма" for sc in v["schools"]))
        raw = json.dumps(v, ensure_ascii=False)
        self.assertNotIn("Узел провайдера", raw)                 # результат ML этапа «авария» не утёк заранее
        self.assertNotIn("provider_node", raw)

        s.act("next")
        v = s.view()
        self.assertEqual(v["stage"]["key"], "incident_started")
        focus, norm = v["metrics"]["focus"], v["metrics"]["norm_download"]
        self.assertLess(focus["download"], norm * 0.25)
        self.assertGreater(focus["ping"], 100)
        self.assertGreater(focus["loss"], 2)
        bad = [sc for sc in v["schools"] if sc["affected"]]
        self.assertEqual(len(bad), 4)
        self.assertEqual({sc["provider"] for sc in bad}, {scenario.NORD})
        self.assertNotEqual({sc["status"] for sc in bad}, {"Норма"})
        self.assertIsNone(v["ml"])

        s.act("next")                                            # diagnostics
        v = s.view()
        self.assertEqual([d["state"] for d in v["diagnostics"]][0], "running")
        self.assertTrue(all("result" not in d for d in v["diagnostics"]))
        deadline = time.time() + 20
        while time.time() < deadline and any(d["state"] != "done" for d in s.view()["diagnostics"]):
            time.sleep(0.5)                                      # шаги идут по таймеру сервера
        self.assertEqual([d["state"] for d in s.view()["diagnostics"]], ["done"] * 4)
        self.assertIsNone(s.view()["ml"])

        s.act("next")                                            # ml_result
        ml = s.view()["ml"]
        self.assertEqual(ml["cause"], "provider_node")
        self.assertEqual(ml["source"], "model")
        self.assertLessEqual(ml["confidence"], ml["confidence_cap"])
        self.assertGreater(len(ml["drivers"]), 0)
        self.assertEqual(ml["data_quality"]["label"], "достаточно")
        self.assertIn("не подтверждены на реальных авариях", ml["notice"])
        self.assertIsNone(s.view()["review"])

        s.act("next")                                            # operator_review
        v = s.view()
        self.assertEqual(v["review"]["status"], "pending")
        self.assertEqual(v["review"]["text"], "Ожидает решения оператора")
        self.assertIsNone(v["decision"])
        s.act("next", expect=409)                                # без решения человека — дальше нельзя
        s.act("goto", "report_ready", expect=409)
        s.act("goto", "operator_confirmed", expect=409)
        self.assertEqual(s.view()["stage"]["key"], "operator_review")

        s.decide("confirm")
        v = s.view()
        self.assertEqual(v["stage"]["key"], "operator_confirmed")
        self.assertEqual(v["decision"]["kind"], "confirm")
        self.assertEqual(v["decision"]["cause"], "provider_node")
        self.assertIsNone(v["report"])
        r = httpx.get(f"{BASE}/api/demo/live/{s.sid}/report.pdf", headers=s.viewer)
        self.assertEqual(r.status_code, 404)                     # до формирования акта скачать нечего

        r = httpx.post(f"{BASE}/api/demo/sessions/{s.sid}/report", headers=s.op)
        self.assertEqual(r.status_code, 200)
        v = s.view()
        self.assertEqual(v["stage"]["key"], "report_ready")
        self.assertIn("ДЕМОНСТРАЦИОННЫЙ ДОКУМЕНТ", v["report"]["claim_text"])
        r = httpx.get(f"{BASE}/api/demo/live/{s.sid}/report.pdf", headers=s.viewer)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertGreater(len(r.content), 3000)
        self.assertEqual(r.headers["content-type"], "application/pdf")
        op_pdf = httpx.get(f"{BASE}/api/demo/sessions/{s.sid}/report.pdf", headers=s.op)
        self.assertEqual(op_pdf.status_code, 200)
        s.close()

    def test_operator_can_change_verdict_and_documents_follow(self):
        s = Session()
        full_run(s, stop_at_review=True)
        bad = s.decide("change", cause="bogus", expect=400)
        self.assertIn("Недопустимая причина", bad["detail"])
        s.decide("change", cause="school_lan")
        v = s.view()
        self.assertEqual(v["decision"]["kind"], "change")
        self.assertEqual(v["decision"]["model_cause"], "provider_node")
        self.assertEqual(v["decision"]["cause"], "school_lan")
        self.assertNotIn("внутренняя заметка", json.dumps(v, ensure_ascii=False))   # заметка не для зрителей
        s.act("next")
        claim = s.view()["report"]["claim_text"]
        self.assertIn("Оператор изменил вердикт", claim)
        self.assertIn("Уровень школы", claim)
        s.close()

    def test_decision_only_at_review_stage(self):
        s = Session()
        s.act("start")
        self.assertIn("Решение принимается", s.decide("confirm", expect=409)["detail"])
        s.close()

    def test_pdf_and_claim_are_marked_as_demo(self):
        scn = scenario.compute()
        text = scenario.claim_text(scn, {"cause": "provider_node", "kind": "confirm"})
        self.assertTrue(text.startswith("[ДЕМОНСТРАЦИОННЫЙ ДОКУМЕНТ"))
        self.assertIn("Демо-контакт", text)
        pdf = scenario.report_pdf(scn, {"cause": "provider_node", "kind": "confirm"})
        self.assertTrue(pdf.startswith(b"%PDF") and len(pdf) > 3000)

    def test_scenario_is_reproducible_and_ml_runs_once(self):
        scenario.compute()                                       # прогрев: к этому моменту уже посчитано
        runs = scenario.ML_RUNS
        a, b = Session(), Session()
        self.assertEqual(scenario.ML_RUNS, runs)                 # новые сессии не пересчитывают модели
        self.assertEqual(json.dumps(a.state["view"]["schools"], sort_keys=True),
                         json.dumps(b.state["view"]["schools"], sort_keys=True))
        for s in (a, b):
            s.close()

    def test_ml_and_view_are_not_recomputed_per_client(self):
        s = Session()
        full_run(s, stop_at_review=True)
        runs, builds = scenario.ML_RUNS, engine.VIEW_BUILDS
        with httpx.Client() as client:
            for i in range(60):                                  # 60 «зрителей» читают одно состояние
                r = client.get(f"{BASE}/api/demo/live/{s.sid}/state", params={"cid": f"viewer{i:03d}"},
                               headers=s.viewer)
                self.assertEqual(r.status_code, 200)
        self.assertEqual(scenario.ML_RUNS, runs)
        self.assertEqual(engine.VIEW_BUILDS, builds)             # представление построено ранее, один раз
        s.close()

    def test_reset_and_repeat_gives_identical_run(self):
        s = Session()

        def snapshot():
            out = {}
            s.act("start") if s.view()["stage"]["key"] in ("waiting", "reset") else None
            for stage in ("normal", "incident_started", "ml_result", "operator_review"):
                s.act("goto", stage)
                v = s.view()
                out[stage] = json.dumps({k: v[k] for k in ("schools", "metrics", "series", "freshness", "ml",
                                                           "incident", "provider", "review")},
                                        sort_keys=True, ensure_ascii=False)
            s.decide("confirm")
            s.act("next")
            v = s.view()
            out["report"] = v["report"]["claim_text"]
            return out

        first = snapshot()
        r = s.act("reset")
        self.assertEqual(r["view"]["stage"]["key"], "reset")
        v = s.view()
        self.assertEqual(v["session"]["run"], 2)
        for hidden in ("ml", "diagnostics", "decision", "report", "incident", "metrics", "freshness"):
            self.assertIsNone(v[hidden], hidden)
        self.assertEqual(httpx.get(f"{BASE}/api/demo/live/{s.sid}/report.pdf", headers=s.viewer).status_code, 404)
        second = snapshot()
        self.assertEqual(first, second)
        s.act("start")                                           # повторный «start» безопасен
        self.assertEqual(s.view()["stage"]["key"], "report_ready" if False else s.view()["stage"]["key"])
        s.close()

    def test_going_back_clears_later_stages(self):
        s = Session()
        full_run(s)
        self.assertIsNotNone(s.view()["report"])
        s.act("goto", "ml_result")
        v = s.view()
        self.assertIsNone(v["report"])
        self.assertIsNone(v["decision"])
        s.act("goto", "report_ready", expect=409)                # решение сброшено — снова нужно
        s.close()


# --- Синхронизация зрителей ---------------------------------------------------------------------------

class SseClient(threading.Thread):
    """Зритель: держит SSE-поток и запоминает, когда увидел каждый этап."""

    def __init__(self, sid, token, cid):
        super().__init__(daemon=True)
        self.url = f"{BASE}/api/demo/live/{sid}/stream"
        self.headers, self.cid = {"Authorization": f"Bearer {token}"}, cid
        self.seen: dict[str, float] = {}
        self.first = threading.Event()
        self.closed = False
        self.stop = False
        self.error = None

    def run(self):
        try:
            with httpx.stream("GET", self.url, params={"cid": self.cid}, headers=self.headers,
                              timeout=httpx.Timeout(30, read=30)) as r:
                event = None
                for line in r.iter_lines():
                    if self.stop:
                        return
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:") and event == "state":
                        view = json.loads(line[5:])
                        self.seen.setdefault(view["stage"]["key"], time.time())
                        self.first.set()
                    elif line.startswith("data:") and event == "closed":
                        self.closed = True
                        return
        except Exception as exc:  # noqa: BLE001
            self.error = exc


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class Sync(Base):
    def test_many_viewers_see_change_within_two_seconds(self):
        s = Session()
        viewers = [SseClient(s.sid, s.token, f"v{i:04d}") for i in range(20)]
        for v in viewers:
            v.start()
        self.assertTrue(all(v.first.wait(10) for v in viewers), "не все зрители получили состояние")
        for stage in ("normal", "incident_started"):
            t0 = time.time()
            s.act("start" if stage == "normal" else "next")
            self.assertTrue(wait_for(lambda: all(stage in v.seen for v in viewers), 3), stage)
            delays = [v.seen[stage] - t0 for v in viewers]
            self.assertLess(max(delays), 2.0, f"{stage}: {max(delays):.2f} c")
        # ведущий видит число зрителей (потоки считаются по cid)
        self.assertTrue(wait_for(lambda: httpx.get(f"{BASE}/api/demo/sessions/{s.sid}", headers=s.op)
                                 .json()["control"]["viewers"] >= 20, 6))
        s.close()
        self.assertTrue(wait_for(lambda: all(v.closed for v in viewers), 4), "закрытие сессии дошло не до всех")
        for v in viewers:
            v.stop = True

    def test_late_join_and_reload_get_full_state(self):
        s = Session()
        full_run(s, stop_at_review=True)
        late = SseClient(s.sid, s.token, "late0001")
        late.start()
        self.assertTrue(late.first.wait(5))
        self.assertIn("operator_review", late.seen)              # сразу текущий этап, а не «с начала»
        again = s.view("late0001")
        self.assertEqual(again["stage"]["key"], "operator_review")
        self.assertIsNotNone(again["ml"])                        # после «перезагрузки» — полное состояние
        s.close()

    def test_polling_sees_change_within_two_seconds(self):
        s = Session()
        v0 = s.view()["session"]["version"]
        t0 = time.time()
        s.act("start")
        self.assertTrue(wait_for(lambda: s.view()["session"]["version"] > v0, 2))
        self.assertLess(time.time() - t0, 2.0)
        s.close()

    def test_viewer_cannot_stream_other_session(self):
        a, b = Session(), Session()
        r = httpx.get(f"{BASE}/api/demo/live/{b.sid}/stream", headers=a.viewer)
        self.assertEqual(r.status_code, 401)
        for s in (a, b):
            s.close()


# --- Автоматический режим ------------------------------------------------------------------------------

class AutoMode(Base):
    def test_timers_pause_and_human_gate(self):
        t = 1_000_000.0
        doc = engine.create_session("t", scenario.SEED, {"mode": "auto", "intervals": {
            "normal": 5, "incident_started": 5, "diagnostics": 8, "ml_result": 5,
            "operator_confirmed": 3, "report_ready": 0}, "diag_step_sec": 2}, "test", now=t)
        sid = doc["id"]
        try:
            engine.control(sid, "start", now=t)
            stage = lambda: engine.get_doc(sid)["stage"]          # noqa: E731
            engine.tick(sid, now=t + 4.9)
            self.assertEqual(stage(), "normal")
            engine.tick(sid, now=t + 5.1)
            self.assertEqual(stage(), "incident_started")
            engine.control(sid, "pause", now=t + 6)
            engine.tick(sid, now=t + 50)
            self.assertEqual(stage(), "incident_started")         # на паузе время стоит
            engine.control(sid, "resume", now=t + 56)             # простой 50 с не считается
            engine.tick(sid, now=t + 56 + 4)
            self.assertEqual(stage(), "incident_started")
            engine.tick(sid, now=t + 56 + 5.1 - 1)
            self.assertEqual(stage(), "diagnostics")
            engine.tick(sid, now=t + 56 + 4.1 + 2.1)
            self.assertEqual(engine.get_doc(sid)["substep"], 1)   # шаги диагностики по таймеру
            base = t + 56 + 4.1
            engine.tick(sid, now=base + 8.1)
            self.assertEqual(stage(), "ml_result")
            engine.tick(sid, now=base + 8.1 + 5.1)
            self.assertEqual(stage(), "operator_review")
            engine.tick(sid, now=base + 10_000)                   # человек не подменяется таймером
            self.assertEqual(stage(), "operator_review")
            engine.decide(sid, "confirm", None, "", "op", now=base + 10_001)
            engine.tick(sid, now=base + 10_001 + 3.1)
            self.assertEqual(stage(), "report_ready")
            self.assertTrue(engine.get_doc(sid)["report"])
        finally:
            engine.close(sid)

    def test_settings_validation(self):
        s = Session()
        op = s.op
        for body in ({"mode": "fast"}, {"intervals": {"normal": -1}}, {"intervals": {"operator_review": 5}},
                     {"intervals": {"normal": 99999}}, {"intervals": {"nope": 3}}, {"diag_step_sec": 0}):
            r = httpx.put(f"{BASE}/api/demo/sessions/{s.sid}/settings", json=body, headers=op)
            self.assertIn(r.status_code, (400, 422), body)
        r = httpx.put(f"{BASE}/api/demo/sessions/{s.sid}/settings", headers=op,
                      json={"mode": "auto", "intervals": {"normal": 3}})
        self.assertEqual(r.json()["control"]["settings"]["intervals"]["normal"], 3)
        s.close()

    def test_auto_mode_via_running_server_reaches_review_and_waits(self):
        s = Session(mode="auto", intervals={"normal": 1, "incident_started": 1, "diagnostics": 2,
                                            "ml_result": 1}, diag_step_sec=1)
        s.act("start")
        self.assertTrue(wait_for(lambda: s.view()["stage"]["key"] == "operator_review", 15))
        time.sleep(1.5)
        v = s.view()
        self.assertEqual(v["stage"]["key"], "operator_review")
        self.assertEqual(v["review"]["status"], "pending")
        self.assertIsNone(v["stage"]["next_at"])
        s.close()


# --- Приватность и маркировка -------------------------------------------------------------------------

class Privacy(Base):
    def test_view_is_marked_synthetic_and_has_no_secrets_or_real_data(self):
        s = Session()
        full_run(s)
        v = s.view()
        raw = json.dumps(v, ensure_ascii=False)
        self.assertTrue(v["synthetic"])
        self.assertEqual(v["banner"], "Демонстрационные синтетические данные")
        self.assertIn("не подтверждены на реальных авариях школ", v["notice"])
        for school in v["schools"]:
            self.assertTrue(school["name"].startswith("Демо-школа"))
            self.assertIn("(демо)", school["provider"])
            self.assertIn("(демо)", school["district"])
        for forbidden in ("op@t", "eyJ", "JWT", "secret", "password", "device_id", "DEMO-D0", "phone",
                          "contact_", "@vko", "hackathon.db", "created_by"):
            self.assertNotIn(forbidden, raw, forbidden)
        s.close()

    def test_all_stage_views_carry_the_banner(self):
        s = Session()
        for stage in scenario.STAGE_KEYS[1:]:
            if stage in ("operator_confirmed", "report_ready"):
                continue
            s.act("goto", stage)
            self.assertEqual(s.view()["banner"], "Демонстрационные синтетические данные", stage)
        s.close()


# --- Redis ----------------------------------------------------------------------------------------------

def _redis_up() -> bool:
    try:
        import redis
        redis.Redis.from_url(REDIS_URL, socket_connect_timeout=0.5).ping()
        return True
    except Exception:  # noqa: BLE001
        return False


class NoRedis(unittest.TestCase):
    def test_store_works_without_redis(self):
        st = Store(DEAD_REDIS)
        self.assertEqual(st.backend(), "memory")
        doc = st.save({"id": "abcdef123456", "x": 1})
        self.assertEqual(doc["version"], 1)
        self.assertEqual(st.load("abcdef123456")["x"], 1)
        self.assertEqual(st.version("abcdef123456"), 1)
        st.update("abcdef123456", lambda d: d.update(x=2))
        self.assertEqual((st.load("abcdef123456")["x"], st.version("abcdef123456")), (2, 2))
        st.delete("abcdef123456")
        self.assertIsNone(st.load("abcdef123456"))
        self.assertIsNone(st.version("abcdef123456"))

    def test_health_reports_memory_store(self):
        r = httpx.get(f"{BASE}/api/demo/health")
        self.assertEqual(r.json()["store"], "memory")
        self.assertTrue(r.json()["models"]["attribution"])


@unittest.skipUnless(_redis_up(), f"Redis недоступен ({REDIS_URL}): docker run -d -p 6390:6379 redis:7-alpine")
class WithRedis(unittest.TestCase):
    def setUp(self):
        import redis
        redis.Redis.from_url(REDIS_URL).flushdb()

    def test_two_processes_share_state_and_lock(self):
        a, b = Store(REDIS_URL), Store(REDIS_URL)
        self.assertEqual((a.backend(), b.backend()), ("redis", "redis"))
        a.save({"id": "sharedsess01", "n": 0})
        self.assertEqual(b.load("sharedsess01")["n"], 0)
        self.assertEqual(b.version("sharedsess01"), 1)
        done = []

        def bump(store):
            for _ in range(25):
                store.update("sharedsess01", lambda d: d.update(n=d["n"] + 1))
            done.append(1)

        threads = [threading.Thread(target=bump, args=(st,)) for st in (a, b)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        self.assertEqual(a.load("sharedsess01")["n"], 50)        # ни одно обновление не потеряно
        self.assertEqual(a.version("sharedsess01"), 51)
        now = time.time()
        a.touch_viewers("sharedsess01", {f"v{i}": now for i in range(30)})
        b.touch_viewers("sharedsess01", {f"v{i}": now for i in range(25, 45)})    # 5 зрителей — на обоих процессах
        self.assertEqual(a.viewers("sharedsess01"), 45)                          # уникальные, без двойного счёта
        self.assertEqual(b.viewers("sharedsess01", {"v-local": now}), 46)
        b.delete("sharedsess01")
        self.assertIsNone(a.load("sharedsess01"))                # закрытие видно всем процессам

    def test_redis_dropping_mid_session_does_not_break_it(self):
        st = Store(REDIS_URL)
        st.save({"id": "dropsess0001", "n": 1})

        class Broken:
            def __getattr__(self, name):
                raise ConnectionError("Redis упал")

        st._client = Broken()
        self.assertEqual(st.load("dropsess0001")["n"], 1)        # работаем с зеркалом процесса
        st.update("dropsess0001", lambda d: d.update(n=2))
        self.assertEqual(st.load("dropsess0001")["n"], 2)
        self.assertEqual(st.backend(), "memory")
        st._down_until = 0.0                                     # «Redis вернулся»
        self.assertEqual(st.backend(), "redis")
        self.assertEqual(st.load("dropsess0001")["n"], 2)        # состояние восстановлено в Redis

    def test_redis_restart_restores_session_from_mirror(self):
        import redis
        st = Store(REDIS_URL)
        st.save({"id": "lostsess0001", "n": 7})
        redis.Redis.from_url(REDIS_URL).flushdb()               # перезапуск Redis без сохранения
        self.assertEqual(st.load("lostsess0001")["n"], 7)
        self.assertEqual(Store(REDIS_URL).load("lostsess0001")["n"], 7)   # запись вернулась в Redis

    def test_full_flow_on_redis_backed_app(self):
        keep = engine.store
        engine.store = Store(REDIS_URL)
        try:
            self.assertEqual(httpx.get(f"{BASE}/api/demo/health").json()["store"], "redis")
            s = Session()
            full_run(s)
            self.assertEqual(s.view()["stage"]["key"], "report_ready")
            import redis
            keys = redis.Redis.from_url(REDIS_URL).keys("demo:*")
            self.assertTrue(any(k.startswith(b"demo:doc:") for k in keys))
            s.close()
        finally:
            engine.store = keep


# --- Подключение в приложение (DEMO_MODE / DEMO_PUBLIC) -----------------------------------------------------

def _probe(**env) -> dict:
    """Поднимает приложение с заданным окружением и возвращает коды ответов на набор путей."""
    code = ("import json;from fastapi.testclient import TestClient;from app.main import app\n"
            "c=TestClient(app);paths=['/api/demo/config','/api/web/overview','/api/admin/users','/api/ml/board',"
            "'/api/ai/status','/api/agent/register','/api/public-data/schools','/docs','/openapi.json','/api/auth/policy']\n"
            "print(json.dumps({'codes':{p:c.get(p).status_code for p in paths},'root':c.get('/').json()}))")
    full = {**os.environ, "DEMO_MODE": "false", "DEMO_PUBLIC": "false", **env}
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=full, capture_output=True,
                         text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-800:]
    return json.loads(out.stdout.strip().splitlines()[-1])


class Wiring(unittest.TestCase):
    def test_demo_is_absent_in_production_mode(self):
        got = _probe()
        self.assertEqual(got["codes"]["/api/demo/config"], 404, "демо видно в обычном режиме")
        self.assertEqual(got["codes"]["/api/web/overview"], 401)      # обычные API на месте
        self.assertEqual(got["codes"]["/docs"], 200)

    def test_demo_mode_adds_routes_and_keeps_the_rest(self):
        got = _probe(DEMO_MODE="true")
        self.assertEqual(got["codes"]["/api/demo/config"], 200)
        self.assertEqual(got["codes"]["/api/web/overview"], 401)

    def test_public_mode_has_no_admin_or_work_apis(self):
        got = _probe(DEMO_MODE="true", DEMO_PUBLIC="true")
        self.assertEqual(got["codes"]["/api/demo/config"], 200)
        self.assertEqual(got["codes"]["/api/auth/policy"], 200)      # вход оператора остаётся
        for path in ("/api/web/overview", "/api/admin/users", "/api/ml/board", "/api/ai/status",
                     "/api/agent/register", "/api/public-data/schools", "/docs", "/openapi.json"):
            self.assertEqual(got["codes"][path], 404, path)
        self.assertEqual(got["root"], {"status": "ok"})               # без версий и внутренних счётчиков

    def test_public_mode_refuses_to_start_with_default_secrets(self):
        code = ("from fastapi.testclient import TestClient;from app.main import app\n"
                "try:\n    TestClient(app).__enter__()\nexcept RuntimeError as e:\n    print('REFUSED', e)")
        out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True,
                             timeout=180, env={**os.environ, "DEMO_MODE": "true", "DEMO_PUBLIC": "true",
                                               "JWT_SECRET": "change-me-in-production-vko-2026"})
        self.assertIn("REFUSED", out.stdout, out.stderr[-500:])
        self.assertIn("JWT_SECRET", out.stdout)


if __name__ == "__main__":
    unittest.main()
