"""Общее состояние демо-сессий: Redis (несколько процессов) с прозрачным fallback в память.

Правила:
  • источник истины при доступном Redis — Redis; несколько процессов uvicorn видят одно состояние;
  • каждая запись дублируется в память процесса (зеркало): если Redis недоступен или потерял
    данные (перезапуск), процесс продолжает работать со своим зеркалом. Это безопасно для
    одного процесса; при нескольких процессах и упавшем Redis они расходятся до его возврата;
  • сбой Redis не роняет запрос: клиент отключается на RETRY_SEC и затем пробуется снова.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager

from ...config import settings

log = logging.getLogger("demo.store")
RETRY_SEC = 5.0
VIEWER_TTL_SEC = 10.0


class Store:
    def __init__(self, url: str | None = None):
        self._url = url
        self._client = None
        self._down_until = 0.0
        self.pid = f"{os.getpid()}-{uuid.uuid4().hex[:6]}"   # идентификатор процесса для счётчика зрителей
        self._mu = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}
        self._docs: dict[str, str] = {}          # зеркало: sid -> JSON
        self._ver: dict[str, int] = {}
        self._blobs: dict[str, str] = {}
        self._gone: set[str] = set()
        self._viewers: dict[str, dict[str, tuple[int, float]]] = {}
        self._expires: dict[str, float] = {}

    # --- Redis: подключение и защищённый вызов ------------------------------------------

    @property
    def url(self) -> str:
        return self._url if self._url is not None else settings.REDIS_URL

    def _redis(self):
        if time.time() < self._down_until:
            return None
        if self._client is None:
            try:
                import redis
                client = redis.Redis.from_url(self.url, socket_connect_timeout=0.4, socket_timeout=1.0,
                                              decode_responses=True)
                client.ping()
                self._client = client
                log.info("Демо: Redis подключён (%s)", self.url)
            except Exception as exc:  # noqa: BLE001 — любой сбой = работаем в памяти
                self._fail(exc)
                return None
        return self._client

    def _fail(self, exc: Exception) -> None:
        if self._client is not None or self._down_until == 0.0:
            log.warning("Демо: Redis недоступен (%s) — работаем в памяти процесса", exc)
        self._client = None
        self._down_until = time.time() + RETRY_SEC

    def _try(self, fn):
        """Вызов Redis. (результат, True) при успехе, (None, False) — Redis недоступен."""
        client = self._redis()
        if client is None:
            return None, False
        try:
            return fn(client), True
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            return None, False

    def backend(self) -> str:
        return "redis" if self._redis() is not None else "memory"

    def _key(self, kind: str, sid: str) -> str:
        return f"demo:{kind}:{sid}"

    def _ttl(self) -> int:
        return settings.DEMO_SESSION_TTL_MIN * 60

    # --- Сессии ----------------------------------------------------------------------------

    def ids(self) -> list[str]:
        found, ok = self._try(lambda r: r.smembers("demo:sessions"))
        now = time.time()
        with self._mu:
            local = [s for s, t in self._expires.items() if t > now and s not in self._gone]
        return sorted(set(found or []) | set(local)) if ok else sorted(local)

    def load(self, sid: str) -> dict | None:
        raw, ok = self._try(lambda r: r.get(self._key("doc", sid)))
        from_redis = raw is not None
        if ok and raw is None:
            gone, _ = self._try(lambda r: r.exists(self._key("gone", sid)))
            if gone:
                self._forget(sid)
                return None
        with self._mu:
            if raw is None:
                # Redis недоступен или потерял данные: берём зеркало (если сессию не закрыли).
                if sid in self._gone or self._expires.get(sid, 0) < time.time():
                    return None
                raw = self._docs.get(sid)
                if raw is not None and ok:
                    self._restore_locked(sid, raw)
        doc = json.loads(raw) if raw else None
        if doc and from_redis:
            stale = False
            with self._mu:
                if self._ver.get(sid, 0) > doc["version"] and sid in self._docs:
                    # Redis отстал (был недоступен, пока мы писали в зеркало): он догоняет нас.
                    raw, stale = self._docs[sid], True
                    doc = json.loads(raw)
                else:       # держим зеркало свежим: оно понадобится, если Redis упадёт
                    self._docs[sid], self._ver[sid] = raw, doc["version"]
                self._expires[sid] = time.time() + self._ttl()
                self._gone.discard(sid)
            if stale:
                self._restore_locked(sid, raw)
        return doc

    def _restore_locked(self, sid: str, raw: str) -> None:
        self._try(lambda r: (r.set(self._key("doc", sid), raw, ex=self._ttl()),
                             r.set(self._key("v", sid), self._ver.get(sid, 0), ex=self._ttl()),
                             r.sadd("demo:sessions", sid)))

    def version(self, sid: str) -> int | None:
        """Номер версии без разбора документа: по нему процессы понимают, что состояние изменилось."""
        raw, ok = self._try(lambda r: r.get(self._key("v", sid)))
        with self._mu:
            if sid in self._gone or self._expires.get(sid, 0) < time.time():
                return int(raw) if ok and raw is not None else None
            mine = self._ver.get(sid)
        # зеркало может быть новее Redis, если Redis был недоступен: берём большую версию
        return max(int(raw), mine or 0) if ok and raw is not None else mine

    def save(self, doc: dict) -> dict:
        """Сохраняет документ, увеличивая версию. Вызывать под update()/create()."""
        sid = doc["id"]
        doc["version"] = doc.get("version", 0) + 1
        raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
        with self._mu:
            self._docs[sid], self._ver[sid] = raw, doc["version"]
            self._expires[sid] = time.time() + self._ttl()
            self._gone.discard(sid)
        self._try(lambda r: (r.set(self._key("doc", sid), raw, ex=self._ttl()),
                             r.set(self._key("v", sid), doc["version"], ex=self._ttl()),
                             r.sadd("demo:sessions", sid), r.delete(self._key("gone", sid))))
        return doc

    @contextmanager
    def lock(self, sid: str):
        """Взаимное исключение на сессию: в процессе — RLock, между процессами — блокировка Redis."""
        with self._mu:
            local = self._locks.setdefault(sid, threading.RLock())
        with local:
            client = self._redis()
            remote = None
            if client is not None:
                try:
                    remote = client.lock(self._key("lock", sid), timeout=10, blocking_timeout=5)
                    if not remote.acquire():
                        raise TimeoutError("Сессия занята другим процессом")
                except TimeoutError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    self._fail(exc)
                    remote = None
            try:
                yield
            finally:
                if remote is not None:
                    try:
                        remote.release()
                    except Exception:  # noqa: BLE001 — блокировка истекла или Redis упал
                        pass

    def update(self, sid: str, fn) -> dict | None:
        """Чтение → fn(doc) → запись под блокировкой. fn вернула False — запись пропускается.
        Нет сессии — None; исключение из fn откатывает изменение."""
        with self.lock(sid):
            doc = self.load(sid)
            if doc is None:
                return None
            if fn(doc) is False:
                return doc
            return self.save(doc)

    def delete(self, sid: str) -> None:
        with self._mu:
            for table in (self._docs, self._ver, self._blobs, self._viewers, self._expires):
                table.pop(sid, None)
            self._gone.add(sid)
        self._try(lambda r: (r.delete(self._key("doc", sid), self._key("v", sid),
                                      self._key("pdf", sid), self._key("viewers", sid)),
                             r.srem("demo:sessions", sid),
                             r.set(self._key("gone", sid), 1, ex=self._ttl())))

    def _forget(self, sid: str) -> None:
        with self._mu:
            for table in (self._docs, self._ver, self._blobs, self._viewers, self._expires):
                table.pop(sid, None)
            self._gone.add(sid)

    # --- Вложения (PDF) --------------------------------------------------------------------

    def put_blob(self, sid: str, data: bytes) -> None:
        text = base64.b64encode(data).decode()
        with self._mu:
            self._blobs[sid] = text
        self._try(lambda r: r.set(self._key("pdf", sid), text, ex=self._ttl()))

    def get_blob(self, sid: str) -> bytes | None:
        text, ok = self._try(lambda r: r.get(self._key("pdf", sid)))
        with self._mu:
            text = text or self._blobs.get(sid)
        return base64.b64decode(text) if text else None

    def del_blob(self, sid: str) -> None:
        with self._mu:
            self._blobs.pop(sid, None)
        self._try(lambda r: r.delete(self._key("pdf", sid)))

    # --- Зрители ---------------------------------------------------------------------------

    def set_viewers(self, sid: str, count: int) -> None:
        """Каждый процесс публикует число СВОИХ зрителей; сумма — общее число."""
        now = time.time()
        with self._mu:
            self._viewers.setdefault(sid, {})[self.pid] = (count, now)
        key = self._key("viewers", sid)

        def write(r):
            r.hset(key, self.pid, f"{count}:{now}")
            r.expire(key, 60)
        self._try(write)

    def viewers(self, sid: str, own: int | None = None) -> int:
        """Всего зрителей по всем процессам. own — число зрителей этого процесса «прямо сейчас»."""
        now = time.time()
        table, ok = self._try(lambda r: r.hgetall(self._key("viewers", sid)))
        if not ok:
            with self._mu:
                table = {p: f"{c}:{t}" for p, (c, t) in self._viewers.get(sid, {}).items()}
        total = 0
        for pid, value in (table or {}).items():
            count, stamp = value.split(":")
            if pid == self.pid and own is not None:
                continue
            if now - float(stamp) <= VIEWER_TTL_SEC:
                total += int(count)
        return total + (own or 0)
