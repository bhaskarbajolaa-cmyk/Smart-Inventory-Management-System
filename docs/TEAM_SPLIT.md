# Team Ownership and Handoff

The six work areas map to the six roles in `readme.txt`. The folders below define ownership, not six independent Python projects: all modules import from the shared `inventory` package and should stay in the same repository.

| Teammate | Owns | Coordinate with |
| --- | --- | --- |
| 1. Foundation | `src/inventory/persistence/database.py`, `src/inventory/persistence/repository.py`, `src/inventory/catalog/`, `src/inventory/persistence/repositories/products.py`, `src/inventory/persistence/repositories/suppliers.py` | Publish the Repository interface first; unblock all repository authors. |
| 2. Transactions | `src/inventory/transactions/`, `src/inventory/persistence/repositories/transactions.py` | Person 1 for repository and transaction APIs. |
| 3. Access Control | `src/inventory/access_control/`, `src/inventory/persistence/repositories/roles.py` | Person 1 for the repository base; Person 4 for role assignment. |
| 4. Auth | `src/inventory/auth/`, `src/inventory/persistence/repositories/users.py` | Person 3 for roles and Person 1 for the repository base. |
| 5. Concurrency / OS | `src/inventory/concurrency/`, `scripts/concurrency_demo.py`, concurrency tests | Coordinate Job-to-Sale calls with Person 2 and transaction boundaries with Person 1. |
| 6. Integration + Demo | `src/inventory/app/`, `scripts/seed_data.py`, integration tests, and `docs/` demo notes | Integrate continuously; keep the CLI runnable as modules arrive. |

## Working agreement

- Use `src/inventory/` as the import root; do not duplicate shared classes into personal folders.
- Only repository classes may call `DatabaseManager` or issue database operations.
- Entities hold data and business rules; repositories own persistence.
- Agree on signatures in `persistence/repository.py` and the related model modules before parallel implementations depend on them.
- Keep `custom_tables/` out of Phase 2 unless every Must-Build behavior and its tests are complete.

## Sending work to teammates

Preferred: create a private/shared GitHub repository from this project folder, invite teammates, and assign each person the files above. Ask them to branch from the same base, commit only their owned module and tests, then open a pull request. Person 1 should merge the base Repository contract first; the others can start against that agreed contract while implementation continues.

If Git is unavailable, send each teammate their owned files plus the shared `src/inventory/` package and `pyproject.toml`. They should return changed files while preserving the directory paths. Sending only one module directory may omit imports it needs, so use that only for review, not as a separately runnable project.
