"""Нагрузочная проверка синхронизации зрителей.

  python demo_load.py --url http://127.0.0.1:8000 --viewers 50 --rounds 3

N зрителей держат SSE-поток одной сессии (часть — резервный опрос), ведущий переключает этапы,
измеряется задержка от отправки действия до получения нового состояния каждым зрителем.
Критерий: максимум ≤ 2 с, никто не пропустил обновление, модели не пересчитывались.
Учётная запись ведущего — из backend/.env (DEMO_OPERATOR_EMAIL / DEMO_OPERATOR_PASSWORD).
"""
import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402

LIMIT_SEC = 2.0


async def stream_viewer(client, base, sid, token, index, seen, closed, ready):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with client.stream("GET", f"{base}/api/demo/live/{sid}/stream",
                                 params={"cid": f"load{index:05d}"}, headers=headers) as response:
            response.raise_for_status()
            event = None
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:") and event == "state":
                    view = json.loads(line[5:])
                    seen[index].setdefault((view["session"]["run"], view["stage"]["key"]), time.perf_counter())
                    ready.add(index)
                elif line.startswith("data:") and event == "closed":
                    closed.add(index)
                    return
    except (httpx.HTTPError, asyncio.CancelledError):
        pass


async def poll_viewer(client, base, sid, token, index, seen, ready):
    headers = {"Authorization": f"Bearer {token}"}
    while True:
        try:
            r = await client.get(f"{base}/api/demo/live/{sid}/state", params={"cid": f"poll{index:05d}"},
                                 headers=headers)
            if r.status_code == 200:
                view = r.json()
                seen[index].setdefault((view["session"]["run"], view["stage"]["key"]), time.perf_counter())
                ready.add(index)
            elif r.status_code == 404:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(1.5)          # как в клиенте (Live.jsx: POLL_MS)


async def main(url: str, viewers: int, rounds: int) -> int:
    base = url.rstrip("/")
    limits = httpx.Limits(max_connections=viewers + 20, max_keepalive_connections=viewers + 20)
    async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10), limits=limits) as client:
        login = await client.post(f"{base}/api/auth/login", json={
            "email": settings.DEMO_OPERATOR_EMAIL, "password": settings.DEMO_OPERATOR_PASSWORD})
        if login.status_code != 200:
            print(f"Вход ведущего не удался ({login.status_code}). Выполните: python demo.py prepare")
            return 2
        op = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created = (await client.post(f"{base}/api/demo/sessions", json={"title": "Нагрузочная проверка"},
                                     headers=op)).json()
        sid = created["session_id"]
        token = created["link"]["viewer_url"].split("#t=")[1]
        ml_before = created["state"]["control"]["ml_runs"]

        polling = max(1, viewers // 10) if viewers >= 10 else 0
        seen = [dict() for _ in range(viewers)]
        ready, closed = set(), set()
        tasks = [asyncio.create_task(stream_viewer(client, base, sid, token, i, seen, closed, ready))
                 for i in range(viewers - polling)]
        tasks += [asyncio.create_task(poll_viewer(client, base, sid, token, i, seen, ready))
                  for i in range(viewers - polling, viewers)]
        started = time.perf_counter()
        while len(ready) < viewers and time.perf_counter() - started < 20:
            await asyncio.sleep(0.05)
        print(f"Подключено зрителей: {len(ready)} из {viewers} "
              f"({viewers - polling} потоков SSE + {polling} с резервным опросом) за {time.perf_counter() - started:.1f} с")
        if len(ready) < viewers:
            print("НЕ ВСЕ ЗРИТЕЛИ ПОДКЛЮЧИЛИСЬ")
            return 1

        await asyncio.sleep(2.5)                                 # даём фоновому циклу посчитать зрителей
        state = (await client.get(f"{base}/api/demo/sessions/{sid}", headers=op)).json()
        print(f"Счётчик зрителей в панели ведущего: {state['control']['viewers']}")

        steps = [{"action": "start"}] + [{"action": "next"}] * (rounds - 1)
        worst, failed = 0.0, False
        for step in steps:
            t0 = time.perf_counter()
            r = await client.post(f"{base}/api/demo/sessions/{sid}/control", json=step, headers=op)
            r.raise_for_status()
            stage = r.json()["view"]["stage"]["key"]
            key = (r.json()["view"]["session"]["run"], stage)
            deadline = t0 + 6
            while time.perf_counter() < deadline and not all(key in s for s in seen):
                await asyncio.sleep(0.02)
            got = [s[key] - t0 for s in seen if key in s]
            missed = viewers - len(got)
            sse = [seen[i][key] - t0 for i in range(viewers - polling) if key in seen[i]]
            pol = [seen[i][key] - t0 for i in range(viewers - polling, viewers) if key in seen[i]]
            worst = max(worst, max(got, default=LIMIT_SEC * 9))
            failed |= missed > 0
            print(f"→ {stage:<18} получили {len(got)}/{viewers}  SSE: p50 {statistics.median(sse) * 1000:5.0f} мс, "
                  f"p95 {sorted(sse)[int(len(sse) * .95) - 1] * 1000:5.0f} мс, max {max(sse) * 1000:5.0f} мс"
                  + (f"  | опрос: max {max(pol) * 1000:5.0f} мс" if pol else ""))

        after = (await client.get(f"{base}/api/demo/sessions/{sid}", headers=op)).json()["control"]["ml_runs"]
        await client.delete(f"{base}/api/demo/sessions/{sid}", headers=op)
        await asyncio.sleep(1.0)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    print(f"Закрытие сессии дошло до SSE-зрителей: {len(closed)} из {viewers - polling}")
    print(f"Расчётов ML за время проверки: {after - ml_before} (должно быть 0)")
    ok = not failed and worst <= LIMIT_SEC and after == ml_before
    print(f"\nИТОГ: максимальная задержка {worst * 1000:.0f} мс при пределе {LIMIT_SEC * 1000:.0f} мс — "
          + ("ПРОЙДЕНО" if ok else "НЕ ПРОЙДЕНО"))
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--viewers", type=int, default=50)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.url, args.viewers, args.rounds)))
