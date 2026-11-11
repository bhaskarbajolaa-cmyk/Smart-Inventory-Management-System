"""Manual LRU and LFU policies used to bound the local cache index."""

from __future__ import annotations

import heapq
import itertools
from collections import OrderedDict


class LRUPolicy:
    """Least Recently Used eviction with O(1) lookups and updates."""

    def __init__(self, capacity: int = 64) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive.")
        self.capacity = capacity
        self._cache: OrderedDict[str, str] = OrderedDict()

    def get(self, key: str) -> str | None:
        value = self._cache.get(key)
        if value is not None:
            self._cache.move_to_end(key)
        return value

    def put(self, key: str, value: str) -> str | None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        if len(self._cache) > self.capacity:
            evicted, _ = self._cache.popitem(last=False)
            return evicted
        return None

    def remove(self, key: str) -> None:
        self._cache.pop(key, None)

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, key: object) -> bool:
        return key in self._cache


class LFUPolicy:
    """Least Frequently Used eviction with LRU tie-breaking."""

    def __init__(self, capacity: int = 64) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive.")
        self.capacity = capacity
        self._freq: dict[str, int] = {}
        self._last_access: dict[str, int] = {}
        self._versions: dict[str, int] = {}
        self._values: dict[str, str] = {}
        self._heap: list[tuple[int, int, str, int]] = []
        self._clock = itertools.count()

    def _record_access(self, key: str) -> None:
        timestamp = next(self._clock)
        version = self._versions.get(key, 0) + 1
        self._versions[key] = version
        self._last_access[key] = timestamp
        heapq.heappush(
            self._heap,
            (self._freq[key], timestamp, key, version),
        )
        self._compact_heap()

    def _compact_heap(self) -> None:
        if len(self._heap) > max(32, len(self._values) * 4):
            self._heap = [
                (self._freq[key], self._last_access[key], key, self._versions[key])
                for key in self._values
            ]
            heapq.heapify(self._heap)

    def _eviction_candidate(self) -> str:
        while self._heap:
            frequency, timestamp, key, version = heapq.heappop(self._heap)
            if (
                key in self._values
                and version == self._versions[key]
                and frequency == self._freq[key]
                and timestamp == self._last_access[key]
            ):
                return key
        raise RuntimeError("LFU policy heap is inconsistent with its value map.")

    def get(self, key: str) -> str | None:
        value = self._values.get(key)
        if value is not None:
            self._freq[key] += 1
            self._record_access(key)
        return value

    def put(self, key: str, value: str) -> str | None:
        evicted: str | None = None
        if key not in self._values and len(self._values) >= self.capacity:
            evicted = self._eviction_candidate()
            self.remove(evicted)
        self._values[key] = value
        if key not in self._freq:
            self._freq[key] = 1
        self._record_access(key)
        return evicted

    def remove(self, key: str) -> None:
        self._values.pop(key, None)
        self._freq.pop(key, None)
        self._last_access.pop(key, None)
        self._versions.pop(key, None)

    def __len__(self) -> int:
        return len(self._values)

    def __contains__(self, key: object) -> bool:
        return key in self._values
