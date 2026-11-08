from __future__ import annotations
import logging
import threading
from typing import Any, Callable, Dict, Tuple
from django.core.cache import cache
from django.db import connection

logger = logging.getLogger("lazarus.api")

CACHE_VERSION_KEY = "lazarus:state_version"
EXPIRING_KEYS_TTL = 5     # 1 Stellar ledger (~5 seconds)
HEATMAP_CACHE_TTL = 15    # 3 Stellar ledgers (~15 seconds)
LIST_CACHE_TTL = 15       # Versioned list cache (invalidated immediately on state mutation)
HEALTH_CACHE_TTL = 5

_LOCKS_GUARD = threading.Lock()
_KEY_LOCKS: Dict[str, threading.Lock] = {}


def _get_key_lock(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _KEY_LOCKS.get(key)
        if lock is None:
            if len(_KEY_LOCKS) > 512:
                _KEY_LOCKS.clear()
            lock = threading.Lock()
            _KEY_LOCKS[key] = lock
        return lock


class LazarusCacheManager:
    """
    Redis-backed cache coordinator for Lazarus Protocol with Singleflight
    (Thundering Herd / Cache Stampede) protection.
    Uses an atomic namespace version key (`lazarus:state_version`) so any mutation to
    `MonitoredContract`, `StorageKeyIndex`, `KeeperRegistry`, or `VoucherLog` invalidates
    stale ledger caches in O(1) time without requiring expensive Redis wildcard `KEYS` scans.
    """

    @classmethod
    def get_state_version(cls) -> int:
        try:
            version = cache.get(CACHE_VERSION_KEY)
            if version is None:
                cache.set(CACHE_VERSION_KEY, 1, timeout=None)
                return 1
            return int(version)
        except Exception as exc:
            logger.warning("Redis cache get_state_version fallback: %s", exc)
            return 1

    @classmethod
    def invalidate_state_caches(cls, reason: str = "state_mutation") -> int:
        try:
            try:
                new_ver = cache.incr(CACHE_VERSION_KEY)
            except ValueError:
                cache.set(CACHE_VERSION_KEY, 2, timeout=None)
                new_ver = 2
            logger.debug(
                "Invalidated Lazarus Redis state caches (new_version=%d reason=%s)",
                new_ver,
                reason,
            )
            return int(new_ver)
        except Exception as exc:
            logger.warning("Redis cache invalidation error: %s", exc)
            return 1

    @classmethod
    def _singleflight_get_or_set(
        cls,
        key: str,
        builder_fn: Callable[[], Any],
        ttl: int,
    ) -> Tuple[Any, str]:
        try:
            cached = cache.get(key)
            if cached is not None:
                return cached, "HIT"
            key_lock = _get_key_lock(key)
            with key_lock:
                cached = cache.get(key)
                if cached is not None:
                    return cached, "HIT"
                payload = builder_fn()
                cache.set(key, payload, timeout=ttl)
                return payload, "MISS"
        except Exception as exc:
            logger.warning("Redis cache error on %s (%s); querying DB directly", key, exc)
            return builder_fn(), "BYPASS"

    @classmethod
    def get_or_set_expiring_keys(
        cls,
        current_ledger: int,
        builder_fn: Callable[[], Dict[str, Any]],
        ttl: int = EXPIRING_KEYS_TTL,
    ) -> Tuple[Dict[str, Any], str]:
        version = cls.get_state_version()
        key = f"lazarus:expiring:v{version}:ledger:{current_ledger}"
        return cls._singleflight_get_or_set(key, builder_fn, ttl)

    @classmethod
    def get_or_set_heatmap(
        cls,
        current_ledger: int,
        contract_id: str | None,
        builder_fn: Callable[[], Dict[str, Any]],
        ttl: int = HEATMAP_CACHE_TTL,
    ) -> Tuple[Dict[str, Any], str]:
        version = cls.get_state_version()
        cid_tag = contract_id or "ALL"
        key = f"lazarus:heatmap:v{version}:cid:{cid_tag}:ledger:{current_ledger}"
        return cls._singleflight_get_or_set(key, builder_fn, ttl)

    @classmethod
    def get_or_set_contracts_list(
        cls,
        builder_fn: Callable[[], Any],
        ttl: int = LIST_CACHE_TTL,
    ) -> Tuple[Any, str]:
        version = cls.get_state_version()
        key = f"lazarus:contracts_list:v{version}"
        return cls._singleflight_get_or_set(key, builder_fn, ttl)

    @classmethod
    def get_or_set_keepers_list(
        cls,
        builder_fn: Callable[[], Any],
        ttl: int = LIST_CACHE_TTL,
    ) -> Tuple[Any, str]:
        version = cls.get_state_version()
        key = f"lazarus:keepers_list:v{version}"
        return cls._singleflight_get_or_set(key, builder_fn, ttl)

    @classmethod
    def get_or_set_health(
        cls,
        builder_fn: Callable[[], Dict[str, Any]],
        ttl: int = HEALTH_CACHE_TTL,
    ) -> Tuple[Dict[str, Any], str]:
        version = cls.get_state_version()
        key = f"lazarus:health_summary:v{version}"
        return cls._singleflight_get_or_set(key, builder_fn, ttl)

    @classmethod
    def check_infrastructure_health(cls) -> Dict[str, str]:
        db_status = "ok"
        db_engine = connection.vendor
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
                cursor.fetchone()
        except Exception as exc:
            logger.error("Database health check failed: %s", exc)
            db_status = "error"

        cache_status = "ok"
        try:
            probe_key = "lazarus:health:probe"
            cache.set(probe_key, "1", timeout=5)
            if cache.get(probe_key) != "1":
                cache_status = "degraded"
        except Exception as exc:
            logger.error("Redis cache health check failed: %s", exc)
            cache_status = "error"

        return {
            "database": db_status,
            "database_vendor": db_engine,
            "redis_cache": cache_status,
        }
