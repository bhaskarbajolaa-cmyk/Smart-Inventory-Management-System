"""Database configuration and constants."""

from dataclasses import dataclass, field  # noqa: F401


# ============ CONSTANTS ============
# Connection timeouts (in milliseconds)
DEFAULT_CONNECTION_TIMEOUT_MS = 30000
DEFAULT_BUSY_TIMEOUT_MS = 5000

# Lock settings (in milliseconds)
DEFAULT_LOCK_TIMEOUT_MS = 1000
DEFAULT_LOCK_LEASE_MS = 5000

# Transaction settings
DEFAULT_ISOLATION_LEVEL = None  # autocommit mode

# PRAGMA settings
PRAGMA_FOREIGN_KEYS = "PRAGMA foreign_keys = ON"
PRAGMA_JOURNAL_MODE = "PRAGMA journal_mode = WAL"
PRAGMA_BUSY_TIMEOUT = "PRAGMA busy_timeout = 5000"

# Default configuration values
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 5432
DEFAULT_DB_NAME = "inventory.db"


# ============ CONFIGURATION CLASS ============
@dataclass
class DatabaseConfig:
    """Database connection configuration.

    Set ``backend`` to ``"sqlite"`` (default) or ``"postgresql"`` to choose
    which adapter ``DatabaseManager`` instantiates at connect time.

    SQLite:
        Only ``db_name`` is used (treated as a file path, or ``":memory:"``).

    PostgreSQL:
        ``host``, ``port``, ``db_name``, ``user``, and ``password`` are used
        to build the psycopg connection string.  Install the optional dep with::

            pip install "psycopg[binary]>=3.1"
    """

    # ---- backend selector ----
    backend: str = "sqlite"  # "sqlite" | "postgresql"

    # ---- connection ----
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    db_name: str = DEFAULT_DB_NAME

    # ---- PostgreSQL-only credentials ----
    user: str = ""
    password: str = ""

    # ---- SQLite / connection tuning ----
    check_same_thread: bool = False
    timeout: float = 30.0
    busy_timeout: int = DEFAULT_BUSY_TIMEOUT_MS
    journal_mode: str = "WAL"
    foreign_keys: bool = True
    row_factory: bool = True
