"""Redis-кэш дашборда и rate-limiting.

Если Redis недоступен (демо-стенд, локальный прогон) — прозрачно падаем
на процессный in-memory кэш, чтобы приложение оставалось работоспособным.
"""
import json
import logging
import time
from typing import Any

from .config import settings

log = logging.getLogger("cache")

_redis = None
_memory: dict[str, tuple[float, str]] = {}
_counters: dict[str, tuple[float, int]] = {}


def _client():
    global _redis
    if _redis is not None:
        return _redis
    try:
        import redis as redis_lib

        client = redis_lib.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=0.4,
                                          socket_timeout=0.4, decode_responses=True)
        client.ping()
        _redis = client
        log.info("Redis подключён: %s", settings.REDIS_URL)
    except Exception as exc:  # noqa: BLE001 — любой сбой = переход на fallback
        log.warning("Redis недоступен (%s), используется in-memory кэш", exc)
        _redis = False
    return _redis


def backend_name() -> str:
    return "redis" if _client() else "in-memory"


def cache_get(key: str) -> Any | None:
    client = _client()
    if client:
        try:
            raw = client.get(key)
            return json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001
            return None
    hit = _memory.get(key)
    if not hit:
        return None
    expires_at, raw = hit
    if expires_at < time.time():
        _memory.pop(key, None)
        return None
    return json.loads(raw)


def cache_set(key: str, value: Any, ttl: int | None = None) -> None:
    ttl = ttl or settings.CACHE_TTL
    raw = json.dumps(value, default=str)
    client = _client()
    if client:
        try:
            client.setex(key, ttl, raw)
            return
        except Exception:  # noqa: BLE001
            pass
    _memory[key] = (time.time() + ttl, raw)


def cache_invalidate(prefix: str) -> None:
    client = _client()
    if client:
        try:
            for key in client.scan_iter(f"{prefix}*", count=500):
                client.delete(key)
            return
        except Exception:  # noqa: BLE001
            pass
    for key in [k for k in _memory if k.startswith(prefix)]:
        _memory.pop(key, None)


def rate_limit_ok(identity: str, limit: int, window: int = 60) -> bool:
    """Скользящее окно на счётчиках. True => запрос разрешён."""
    key = f"rl:{identity}:{int(time.time() // window)}"
    client = _client()
    if client:
        try:
            count = client.incr(key)
            if count == 1:
                client.expire(key, window)
            return count <= limit
        except Exception:  # noqa: BLE001
            pass
    expires_at, count = _counters.get(key, (time.time() + window, 0))
    if expires_at < time.time():
        expires_at, count = time.time() + window, 0
    count += 1
    _counters[key] = (expires_at, count)
    return count <= limit
