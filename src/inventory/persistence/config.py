"""Database configuration, environment loading, and constants."""

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


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


def load_environment() -> None:
    """Load project-local .env values without overriding process environment."""
    project_root = Path(__file__).resolve().parents[3]
    load_dotenv(project_root / ".env", override=False)


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
        to connect with psycopg, or set ``connection_string`` to a URL.
    """

    # ---- backend selector ----
    backend: str = "sqlite"  # "sqlite" | "postgresql"

    # ---- connection ----
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    db_name: str = DEFAULT_DB_NAME
    connection_string: str = ""
    sslmode: str = "require"

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

    @classmethod
    def from_environment(cls) -> "DatabaseConfig":
        """Build database settings from DATABASE_URL or DB_* environment keys."""
        load_environment()
        connection_string = os.environ.get("DATABASE_URL", "").strip()
        configured_backend = os.environ.get("DATABASE_BACKEND", "").strip().lower()
        backend = configured_backend or (
            "postgresql" if connection_string else "sqlite"
        )
        if backend not in {"sqlite", "postgresql"}:
            raise ValueError("DATABASE_BACKEND must be 'sqlite' or 'postgresql'.")

        if connection_string:
            if not connection_string.startswith(("postgres://", "postgresql://")):
                raise ValueError(
                    "DATABASE_URL must use a postgres:// or postgresql:// URL."
                )
            backend = "postgresql"
        elif backend == "postgresql":
            required = {
                "DB_HOST": os.environ.get("DB_HOST", "").strip(),
                "DB_NAME": os.environ.get("DB_NAME", "").strip(),
                "DB_USER": os.environ.get("DB_USER", "").strip(),
                "DB_PASSWORD": os.environ.get("DB_PASSWORD", ""),
            }
            missing = [key for key, value in required.items() if not value]
            if missing:
                raise ValueError(
                    "PostgreSQL requires DATABASE_URL or these environment "
                    f"variables: {', '.join(missing)}."
                )

        return cls(
            backend=backend,
            host=os.environ.get("DB_HOST", DEFAULT_HOST).strip(),
            port=int(os.environ.get("DB_PORT", str(DEFAULT_PORT))),
            db_name=os.environ.get("DB_NAME", DEFAULT_DB_NAME).strip(),
            user=os.environ.get("DB_USER", "").strip(),
            password=os.environ.get("DB_PASSWORD", ""),
            connection_string=connection_string,
            sslmode=os.environ.get("DATABASE_SSLMODE", "require").strip(),
        )
