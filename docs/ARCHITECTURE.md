# Architecture Map

```text
app / scripts
  -> auth, access_control, catalog, transactions, concurrency
  -> persistence repositories
  -> DatabaseManager
```

`catalog`, `transactions`, `access_control`, and `auth` contain domain models and behavior. `persistence/repositories/` contains entity-specific storage adapters. `persistence/database.py` owns the storage connection or in-memory store. `concurrency/` owns per-resource locking, retries, job ordering, and workers. `app/` is the integration surface; `scripts/` contains seed and demonstration entry points.

## Product cache and write-behind

The product cache uses Redis when `REDIS_URL` is set and `fakeredis` otherwise.
`CACHE_POLICY` selects the local LRU (default) or LFU eviction index; the local
capacity defaults to 64 products. Product reads use cache-aside behavior.
Warm entries are served from the app process's memory for up to two seconds,
then revalidated against Redis. This avoids an Upstash network round trip on
every repeated read while bounding cross-process staleness; app-side mutations
also invalidate the local entry immediately.
The local `fakeredis` fallback is volatile; abrupt process termination can lose
metadata that has not yet been flushed. Configure `REDIS_URL` when that risk is
unacceptable.

Only product `name` and `unit_price` edits are buffered for background persistence.
Stock quantity changes and `stock_transactions` inserts remain synchronous in
the same database transaction. Successful stock transactions invalidate the
cached product, while pending metadata remains in the separate dirty buffer.
The shared database manager holds an exclusive transaction lock across each
transaction so the flush worker or another request cannot accidentally write
through an in-progress transaction on the shared connection.
The flush worker batches dirty metadata using `CACHE_FLUSH_INTERVAL` (default
30 seconds) and `CACHE_MAX_DIRTY` (default 100). The worker is also drained on
process shutdown.

Run the isolated consistency stress test from the project root:

```text
python scripts/stress_test.py --transactions 1000 --workers 10 --mode sale
```

The stress test creates a temporary SQLite database and uses an in-memory cache;
it does not alter the configured application database or require Upstash.

The public `/cache-demo` page compares cumulative database-only and cache-path
read times and estimates break-even for repeated reads. It also tracks successful
benchmark read counts for the current browser page session; those counts are
diagnostic and are not global production traffic metrics. The route is not
linked from the main application navigation, but it is public and unprotected;
URL secrecy is not authentication.

The custom-table models and repository are isolated in `custom_tables/` and are stretch-only. The eventual request/transaction flow should acquire the product lock, execute the stock change through the transaction/repository path, commit or roll back, and release the lock in a `finally` block.

## API naming

Method names from the UML and `readme.txt` are written in Python `snake_case` in source files (for example, `findById` becomes `find_by_id`). Model attributes likewise use `snake_case`. Business methods remain TODO contracts until their owning teammate implements and tests them.
