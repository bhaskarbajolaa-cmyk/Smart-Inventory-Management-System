"""Background worker that flushes buffered product metadata to the database."""

from __future__ import annotations

import logging
import threading
import time
import uuid

from inventory.cache.inventory_cache import InventoryCache
from inventory.concurrency.locks import LockManager
from inventory.persistence.repositories.products import ProductRepository

logger = logging.getLogger(__name__)


class FlushWorker:
    """Drain dirty product metadata on a timer, threshold, or explicit request."""

    def __init__(
        self,
        cache: InventoryCache,
        product_repo: ProductRepository,
        lock_manager: LockManager,
        flush_interval_seconds: float = 30,
        max_dirty: int = 100,
    ) -> None:
        if flush_interval_seconds <= 0:
            raise ValueError("flush_interval_seconds must be positive.")
        if max_dirty <= 0:
            raise ValueError("max_dirty must be positive.")
        self.cache = cache
        self.product_repo = product_repo
        self.lock_manager = lock_manager
        self.flush_interval_seconds = flush_interval_seconds
        self.max_dirty = max_dirty
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._flush_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.cache.set_dirty_listener(self._wake_event.set)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if self._stop_event.is_set():
            raise RuntimeError("A stopped FlushWorker cannot be restarted.")
        self._thread = threading.Thread(
            target=self._run,
            name="InventoryFlushWorker",
            daemon=True,
        )
        self._thread.start()

    def stop(self, flush: bool = True) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join()
        if flush:
            self.flush_now()

    def flush_now(self) -> int:
        with self._flush_lock:
            return self._flush_cycle()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            timed_out = not self._wake_event.wait(self.flush_interval_seconds)
            self._wake_event.clear()
            if self._stop_event.is_set():
                return

            if timed_out or len(self.cache.get_dirty_keys()) >= self.max_dirty:
                try:
                    self.flush_now()
                except Exception:
                    logger.exception("Write-behind cache flush failed; dirty records were retained.")

    def _flush_cycle(self) -> int:
        flushed = 0
        started = time.monotonic()
        keys = sorted(self.cache.get_dirty_keys())
        expected_values: dict[str, str] = {}

        for key in keys:
            record = self.cache.get_dirty_record(key)
            if record is None:
                continue
            raw_value, metadata = record

            prefix, separator, raw_id = key.partition(":")
            if prefix != "product" or not separator:
                raise ValueError(f"Unsupported dirty cache key: {key}")
            try:
                product_id = int(raw_id)
            except ValueError as error:
                raise ValueError(f"Invalid dirty product key: {key}") from error

            token = self.lock_manager.acquire(
                str(product_id),
                owner_id=f"flush:{uuid.uuid4()}",
                timeout_ms=5000,
            )
            if token is None:
                continue
            try:
                latest_record = self.cache.get_dirty_record(key)
                if latest_record is None:
                    continue
                raw_value, metadata = latest_record
                try:
                    self.product_repo.save_metadata(
                        product_id,
                        metadata["name"],
                        metadata["unit_price"],
                    )
                except KeyError:
                    self.cache.mark_clean(
                        key,
                        expected_values={key: raw_value},
                    )
                    continue
                expected_values[key] = raw_value
                flushed += 1
            finally:
                self.lock_manager.release(token)

        if expected_values:
            self.cache.mark_clean(*expected_values, expected_values=expected_values)

        logger.info(
            "Flushed %d product metadata record(s) in %.3f seconds.",
            flushed,
            time.monotonic() - started,
        )
        return flushed
