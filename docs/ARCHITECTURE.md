# Architecture Map

```text
app / scripts
  -> auth, access_control, catalog, transactions, concurrency
  -> persistence repositories
  -> DatabaseManager
```

`catalog`, `transactions`, `access_control`, and `auth` contain domain models and behavior. `persistence/repositories/` contains entity-specific storage adapters. `persistence/database.py` owns the storage connection or in-memory store. `concurrency/` owns per-resource locking, retries, job ordering, and workers. `app/` is the integration surface; `scripts/` contains seed and demonstration entry points.

The custom-table models and repository are isolated in `custom_tables/` and are stretch-only. The eventual request/transaction flow should acquire the product lock, execute the stock change through the transaction/repository path, commit or roll back, and release the lock in a `finally` block.

## API naming

Method names from the UML and `readme.txt` are written in Python `snake_case` in source files (for example, `findById` becomes `find_by_id`). Model attributes likewise use `snake_case`. Business methods remain TODO contracts until their owning teammate implements and tests them.
