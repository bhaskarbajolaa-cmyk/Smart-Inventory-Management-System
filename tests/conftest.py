"""Pytest configuration for isolated offline unit and integration testing.

Ensures tests run by default against an in-memory SQLite database and with
fake/in-memory Redis cache client, avoiding DNS lookup failures or accidental
remote network calls to external cloud instances during test runs.
"""

import os

# Set testing environment variables before any application imports occur
os.environ["DATABASE_BACKEND"] = "sqlite"
os.environ["DATABASE_URL"] = ""
os.environ["REDIS_URL"] = ""
os.environ["INVENTORY_TESTING"] = "1"
