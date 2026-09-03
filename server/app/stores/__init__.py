"""Used-nonce store + rate limiter.

Production: Redis (bloom-style used-nonce set + INCR windows). Local dev/tests:
in-memory fallback with the same interface. Redis URL from CRUMBS_REDIS_URL;
empty -> memory (logged at startup).
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time

from ..config import get_settings

log = logging.getLogger("crumbs.stores")


class UsedNonceStore:
    """Reject replay of a receipt's nonce for TTL + grace."""

    def mark_used(self, rid: str, nc: str, ttl_seconds: int) -> bool:
        """Atomically claim the nonce. Returns True if newly claimed, False if replay."""
        raise NotImplementedError

    def is_used(self, rid: str) -> bool:
        raise NotImplementedError


class RateLimiter:
    """Fixed-window rate limit: scope:key -> max events per window_seconds."""

    def hit(self, scope: str, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Increment counter; return (allowed, count_after)."""
        raise NotImplementedError


# ---------------------------------------------------------------------------


class MemoryNonceStore(UsedNonceStore):
    """Thread-safe in-memory used-nonce set with expiry. LOCAL DEV ONLY."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, float] = {}  # rid -> expires_at

    def _purge(self) -> None:
        now = time.monotonic()
        stale = [k for k, exp in self._entries.items() if exp <= now]
        for k in stale:
            del self._entries[k]

    def mark_used(self, rid: str, nc: str, ttl_seconds: int) -> bool:
        with self._lock:
            self._purge()
            if rid in self._entries:
                return False
            self._entries[rid] = time.monotonic() + ttl_seconds
            return True

    def is_used(self, rid: str) -> bool:
        with self._lock:
            self._purge()
            return rid in self._entries


class MemoryRateLimiter(RateLimiter):
    """Thread-safe fixed-window in-memory limiter. LOCAL DEV ONLY."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, str], tuple[int, float]] = {}

    def hit(self, scope: str, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        k = (scope, key)
        now = time.monotonic()
        with self._lock:
            count, window_start = self._counters.get(k, (0, now))
            if now - window_start >= window_seconds:
                count, window_start = 0, now
            count += 1
            self._counters[k] = (count, window_start)
            return (count <= limit), count


# ---------------------------------------------------------------------------


class RedisNonceStore(UsedNonceStore):
    """Redis SET NX EX — the production store (bloom filter at scale, later)."""

    def __init__(self, redis_client) -> None:
        self._r = redis_client

    def mark_used(self, rid: str, nc: str, ttl_seconds: int) -> bool:
        return bool(self._r.set(f"cr:nc:{rid}", nc, nx=True, ex=ttl_seconds))

    def is_used(self, rid: str) -> bool:
        return bool(self._r.exists(f"cr:nc:{rid}"))


class RedisRateLimiter(RateLimiter):
    def __init__(self, redis_client) -> None:
        self._r = redis_client

    def hit(self, scope: str, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        k = f"cr:rl:{scope}:{key}"
        pipe = self._r.pipeline()
        pipe.incr(k)
        pipe.expire(k, window_seconds, nx=True)
        count, _ = pipe.execute()
        return (int(count) <= limit), int(count)


# ---------------------------------------------------------------------------


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def build_stores():
    """Create (nonce_store, rate_limiter) from settings; Redis when configured.

    FAIL-CLOSED: when CRUMBS_REDIS_URL is configured but unreachable,
    the process refuses to start rather than silently degrading to memory
    (which would reset nonce dedup on restart / across workers).
    """
    settings = get_settings()
    if settings.redis_url:
        import redis

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            client.ping()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"CRUMBS_REDIS_URL configured but Redis unreachable — refusing to "
                f"start (fail-closed). Fix Redis or unset CRUMBS_REDIS_URL. ({exc})"
            ) from exc
        log.info("using Redis stores: %s", settings.redis_url.split("@")[-1])
        return RedisNonceStore(client), RedisRateLimiter(client)
    log.warning("CRUMBS_REDIS_URL unset — using in-memory nonce/rate stores (LOCAL DEV ONLY)")
    return MemoryNonceStore(), MemoryRateLimiter()


def fingerprint_ip(ip: str) -> str:
    return _sha256(ip)


def fingerprint_ua(ua: str) -> str:
    return _sha256(ua or "")
