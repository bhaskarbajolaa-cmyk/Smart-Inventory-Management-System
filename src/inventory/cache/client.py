"""Small Redis client wrapper with an in-memory local-development fallback."""

from __future__ import annotations

import os
import threading
from typing import Any

from redis.exceptions import WatchError


class CacheClient:
    """Expose the Redis commands used by the inventory cache."""

    def __init__(self, url: str | None = None) -> None:
        redis_url = os.environ.get("REDIS_URL") if url is None else url
        if redis_url:
            from redis import Redis

            self._redis: Any = Redis.from_url(redis_url, decode_responses=True)
        else:
            import fakeredis

            self._redis = fakeredis.FakeRedis(
                server=fakeredis.FakeServer(),
                decode_responses=True,
            )
        self._lock = threading.RLock()

    def get(self, key: str) -> str | None:
        with self._lock:
            value = self._redis.get(key)
        return value if isinstance(value, str) else None

    def set(self, key: str, value: str, ttl_seconds: int | None = 300) -> None:
        with self._lock:
            if ttl_seconds is None:
                self._redis.set(key, value)
            elif ttl_seconds > 0:
                self._redis.set(key, value, ex=ttl_seconds)
            else:
                raise ValueError("ttl_seconds must be positive or None.")

    def set_product_and_dirty(
        self,
        cache_key: str,
        value: str,
        ttl_seconds: int,
        payload_key: str,
        dirty_set: str,
        member: str,
        payload: str,
    ) -> None:
        """Atomically publish a cached product and its durable dirty payload."""
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive.")
        with self._lock:
            with self._redis.pipeline() as pipeline:
                pipeline.set(cache_key, value, ex=ttl_seconds)
                pipeline.set(payload_key, payload)
                pipeline.sadd(dirty_set, member)
                pipeline.execute()

    def remove_product_and_dirty(
        self,
        cache_key: str,
        payload_key: str,
        dirty_set: str,
        member: str,
    ) -> None:
        with self._lock:
            with self._redis.pipeline() as pipeline:
                pipeline.delete(cache_key, payload_key)
                pipeline.srem(dirty_set, member)
                pipeline.execute()

    def delete(self, *keys: str) -> None:
        if keys:
            with self._lock:
                self._redis.delete(*keys)

    def sadd(self, set_name: str, *members: str) -> None:
        if members:
            with self._lock:
                self._redis.sadd(set_name, *members)

    def smembers(self, set_name: str) -> set[str]:
        with self._lock:
            return {str(member) for member in self._redis.smembers(set_name)}

    def srem(self, set_name: str, *members: str) -> None:
        if members:
            with self._lock:
                self._redis.srem(set_name, *members)

    def compare_and_clear_dirty(
        self,
        payload_key: str,
        dirty_set: str,
        member: str,
        expected_value: str,
    ) -> bool:
        """Clear a flushed payload only if nobody replaced it in the meantime."""
        with self._lock:
            with self._redis.pipeline() as pipeline:
                while True:
                    try:
                        pipeline.watch(payload_key)
                        if pipeline.get(payload_key) != expected_value:
                            pipeline.unwatch()
                            return False
                        pipeline.multi()
                        pipeline.delete(payload_key)
                        pipeline.srem(dirty_set, member)
                        pipeline.execute()
                        return True
                    except WatchError:
                        pipeline.reset()
                        continue

    def ping(self) -> bool:
        with self._lock:
            return bool(self._redis.ping())
