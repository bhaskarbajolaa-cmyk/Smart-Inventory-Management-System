# Start Here

This guide gets a teammate set up and running the tests. Start in the project root, the folder containing `pyproject.toml`.

## 1. Create the environment

In PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

If PowerShell blocks activation, see [Troubleshooting](TROUBLESHOOTING.md#powershell-wont-activate-the-virtual-environment). You can also use `.venv\Scripts\python.exe` directly without activating.

## 2. Run the tests

```powershell
python -m pytest -q
```

## Configure PostgreSQL or Redis

The application uses SQLite by default. To configure Aiven PostgreSQL locally,
copy `.env.example` to `.env` and set `DATABASE_URL` to the connection URL
from the Aiven console. Keep the `.env` file private; it is ignored by Git.
When `DATABASE_URL` is set, PostgreSQL is selected automatically. Aiven
connections should use TLS (`sslmode=require`); the application defaults to
that mode unless the URL already specifies an `sslmode`.

Alternatively, set `DATABASE_BACKEND=postgresql` and fill in the `DB_HOST`,
`DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD` values. To use SQLite again,
set `DATABASE_BACKEND=sqlite` and clear `DATABASE_URL`.

Set `REDIS_URL` in the same `.env` file to connect to Upstash. If it is blank,
the cache uses fakeredis in memory for local development. Set `CACHE_POLICY`,
`CACHE_CAPACITY`, `CACHE_FLUSH_INTERVAL`, and `CACHE_MAX_DIRTY` to adjust the
cache behavior.

### Render deployment

Render does not read the local `.env` file. The repository's `render.yaml`
declares the runtime settings; if deploying with the Blueprint, enter
`DATABASE_URL` and `REDIS_URL` when prompted, or add/update them under your
Render service's **Environment** settings:

| Key | Value |
| --- | --- |
| `DATABASE_URL` | Aiven's PostgreSQL connection URL, including `sslmode=require` |
| `DATABASE_SSLMODE` | `require` |
| `REDIS_URL` | Upstash Redis TLS URL (`rediss://...`) |
| `CACHE_POLICY` | `lru` |
| `CACHE_CAPACITY` | `64` |
| `CACHE_FLUSH_INTERVAL` | `30` |
| `CACHE_MAX_DIRTY` | `100` |

Do not set `DATABASE_BACKEND=sqlite` on Render when using Aiven. With
`DATABASE_URL` set, the app selects PostgreSQL automatically. Save the changes
and trigger a deploy/restart. Render's build only installs dependencies; schema
initialization and idempotent demo seeding happen when the app starts, so the
build does not need to connect to Aiven.

The cache demo remains separate from the home-page navigation and login flow.
Visit `/cache-demo` for the performance comparison and `/cache-traffic` for a
live request monitor that refreshes every two seconds. Both routes now require an administrator session; anonymous visitors are
redirected to the login page.
Traffic metrics count dynamic requests within the current server process and
are not aggregated across multiple workers or instances. Never commit or paste
connection URLs containing passwords into source control.

The starter test checks that the package imports. Create a focused test file for the class or behavior you implement, then run just that file while working. For example, after creating `tests/test_catalog.py`:

```powershell
python -m pytest tests/test_catalog.py -q
```

For example, a product stock test should create a `Product`, call `update_stock(-2)`, and assert the quantity decreased by two. The method currently raises `NotImplementedError`, so that behavior test becomes green only after the method is implemented.

## 3. Work in your assigned files

Use the role map in the [README](../README.md#find-your-work) and the detailed [team split](TEAM_SPLIT.md). Before writing a repository subclass, coordinate with Person 1 on `src/inventory/persistence/repository.py`.

## Run the application later

The class methods are intentionally TODOs, so application startup currently stops when it reaches `DatabaseManager`. After the foundation is implemented, launch the CLI:

```powershell
python -m inventory.app.main --mode cli
```

For the backend API, install Flask and start server mode:

```powershell
python -m pip install -e ".[web]"
python -m inventory.app.main --mode server --host 127.0.0.1 --port 5000
```

The standalone equivalent is `python -m inventory.app.server`. For common setup and development problems, see [Troubleshooting](TROUBLESHOOTING.md).