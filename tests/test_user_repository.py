"""Integration tests for UserRepository and its connection to Authentication."""

import unittest
from datetime import datetime
from unittest.mock import patch

from inventory.auth.models import User, UserCredentials
from inventory.auth.service import Authentication
from inventory.persistence.database import DatabaseManager
from inventory.persistence.repositories.users import UserRepository


def _make_db() -> DatabaseManager:
    """Create and connect a fresh in-memory database."""
    db = DatabaseManager(db_name=":memory:")
    db.connect()
    return db


def _make_user(user_id: int = 0, username: str = "alice", role_id: int = 2) -> User:
    return User(
        user_id=user_id,
        username=username,
        email=f"{username}@example.com",
        role_id=role_id,
        created_at=datetime.now(),
    )


class TestUserRepositoryCRUD(unittest.TestCase):
    """Verify save / find_by_id / find_all / delete work against a real SQLite DB."""

    def setUp(self) -> None:
        DatabaseManager.reset_instance()
        self.db = _make_db()
        self.repo = UserRepository(self.db)
        # Insert a role row so FK constraint on users.role_id is satisfied
        self.db.execute_query(
            "INSERT INTO roles (role_id, role_name, created_by, is_system_default, default_job_priority) "
            "VALUES (?, ?, ?, ?, ?)",
            (2, "Staff", None, 0, 3),
        )

    def tearDown(self) -> None:
        self.db.close()
        DatabaseManager.reset_instance()

    # ------------------------------------------------------------------
    # save — auto-assign ID
    # ------------------------------------------------------------------
    def test_save_auto_assigns_id(self) -> None:
        user = _make_user(user_id=0)
        saved = self.repo.save(user)
        self.assertGreater(saved.user_id, 0, "DB should assign a positive user_id")

    # ------------------------------------------------------------------
    # find_by_id
    # ------------------------------------------------------------------
    def test_find_by_id_returns_correct_user(self) -> None:
        saved = self.repo.save(_make_user())
        fetched = self.repo.find_by_id(saved.user_id)
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched.username, "alice")
        self.assertEqual(fetched.email, "alice@example.com")
        self.assertEqual(fetched.role_id, 2)

    def test_find_by_id_missing_returns_none(self) -> None:
        self.assertIsNone(self.repo.find_by_id(9999))

    # ------------------------------------------------------------------
    # save — update existing row
    # ------------------------------------------------------------------
    def test_save_updates_existing_user(self) -> None:
        saved = self.repo.save(_make_user())
        saved.email = "newalice@example.com"
        self.repo.save(saved)

        updated = self.repo.find_by_id(saved.user_id)
        assert updated is not None
        self.assertEqual(updated.email, "newalice@example.com")

    # ------------------------------------------------------------------
    # find_all
    # ------------------------------------------------------------------
    def test_find_all_returns_all_users(self) -> None:
        self.repo.save(_make_user(username="alice"))
        self.repo.save(_make_user(username="bob"))
        users = self.repo.find_all()
        self.assertEqual(len(users), 2)
        names = {u.username for u in users}
        self.assertIn("alice", names)
        self.assertIn("bob", names)

    def test_find_all_empty_db(self) -> None:
        self.assertEqual(self.repo.find_all(), [])

    # ------------------------------------------------------------------
    # find_by_username
    # ------------------------------------------------------------------
    def test_find_by_username_case_insensitive(self) -> None:
        self.repo.save(_make_user(username="Alice"))
        self.assertIsNotNone(self.repo.find_by_username("alice"))
        self.assertIsNotNone(self.repo.find_by_username("ALICE"))
        self.assertIsNotNone(self.repo.find_by_username("Alice"))

    def test_find_by_username_missing_returns_none(self) -> None:
        self.assertIsNone(self.repo.find_by_username("ghost"))

    # ------------------------------------------------------------------
    # delete
    # ------------------------------------------------------------------
    def test_delete_removes_user(self) -> None:
        saved = self.repo.save(_make_user())
        self.repo.delete(saved.user_id)
        self.assertIsNone(self.repo.find_by_id(saved.user_id))

    def test_delete_nonexistent_raises_key_error(self) -> None:
        with self.assertRaises(KeyError):
            self.repo.delete(9999)

    # ------------------------------------------------------------------
    # save_password_hash + find_credentials_by_username
    # ------------------------------------------------------------------
    def test_save_and_find_credentials(self) -> None:
        saved = self.repo.save(_make_user())
        self.repo.save_password_hash(saved.user_id, "hashed_secret")

        creds = self.repo.find_credentials_by_username("alice")
        self.assertIsNotNone(creds)
        assert creds is not None
        self.assertEqual(creds.user_id, saved.user_id)
        self.assertEqual(creds.password_hash, "hashed_secret")

    def test_find_credentials_no_hash_returns_none(self) -> None:
        # User exists but no password_hash set
        self.repo.save(_make_user())
        self.assertIsNone(self.repo.find_credentials_by_username("alice"))

    def test_find_credentials_unknown_user_returns_none(self) -> None:
        self.assertIsNone(self.repo.find_credentials_by_username("ghost"))

    def test_save_password_hash_unknown_user_raises(self) -> None:
        with self.assertRaises(KeyError):
            self.repo.save_password_hash(9999, "hash")


class TestAuthenticationWithRealRepository(unittest.TestCase):
    """End-to-end: Authentication + UserRepository against a real in-memory DB."""

    def setUp(self) -> None:
        DatabaseManager.reset_instance()
        self.db = _make_db()
        self.repo = UserRepository(self.db)
        self.auth = Authentication(self.repo)
        # Insert the role row the FK needs
        self.db.execute_query(
            "INSERT INTO roles (role_id, role_name, created_by, is_system_default, default_job_priority) "
            "VALUES (?, ?, ?, ?, ?)",
            (2, "Staff", None, 0, 3),
        )
        # Register a user and store their hashed password
        self.user = self.repo.save(_make_user())
        hashed = Authentication._hash_password("correct_password")
        self.repo.save_password_hash(self.user.user_id, hashed)

    def tearDown(self) -> None:
        self.db.close()
        DatabaseManager.reset_instance()

    def test_verify_credentials_correct_password(self) -> None:
        self.assertTrue(self.auth.verify_credentials("alice", "correct_password"))

    def test_verify_credentials_wrong_password(self) -> None:
        self.assertFalse(self.auth.verify_credentials("alice", "wrong_password"))

    def test_verify_credentials_unknown_user(self) -> None:
        self.assertFalse(self.auth.verify_credentials("ghost", "correct_password"))

    def test_login_returns_valid_session(self) -> None:
        session = self.auth.login("alice", "correct_password")
        self.assertEqual(session.user_id, self.user.user_id)
        self.assertFalse(session.is_expired())
        self.assertIsNotNone(session.session_token)

    def test_login_wrong_password_raises(self) -> None:
        with self.assertRaises(PermissionError):
            self.auth.login("alice", "wrong_password")

    def test_get_session_returns_live_session(self) -> None:
        session = self.auth.login("alice", "correct_password")
        retrieved = self.auth.get_session(session.session_token)
        self.assertIsNotNone(retrieved)
        assert retrieved is not None
        self.assertEqual(retrieved.user_id, self.user.user_id)

    def test_get_session_unknown_token_returns_none(self) -> None:
        self.assertIsNone(self.auth.get_session("no_such_token"))

    def test_logout_invalidates_session(self) -> None:
        session = self.auth.login("alice", "correct_password")
        self.auth.logout(session)
        self.assertIsNone(self.auth.get_session(session.session_token))

    def test_change_password_updates_db(self) -> None:
        self.auth.change_password(self.user, "new_password")
        # Old password should no longer work
        self.assertFalse(self.auth.verify_credentials("alice", "correct_password"))
        # New password should work
        self.assertTrue(self.auth.verify_credentials("alice", "new_password"))

    def test_duplicate_username_rejected_by_db(self) -> None:
        """The DB UNIQUE constraint on username must prevent duplicate inserts."""
        duplicate = _make_user(username="alice")
        with self.assertRaises(Exception):
            self.repo.save(duplicate)


class TestUserRepositoryFindByEmail(unittest.TestCase):
    """Verify find_by_email lookup semantics against a real SQLite DB."""

    def setUp(self) -> None:
        DatabaseManager.reset_instance()
        self.db = _make_db()
        self.repo = UserRepository(self.db)
        # Insert a role row so FK constraint on users.role_id is satisfied
        self.db.execute_query(
            "INSERT INTO roles (role_id, role_name, created_by, is_system_default, default_job_priority) "
            "VALUES (?, ?, ?, ?, ?)",
            (2, "Staff", None, 0, 3),
        )

    def tearDown(self) -> None:
        self.db.close()
        DatabaseManager.reset_instance()

    # ------------------------------------------------------------------
    # find_by_email
    # ------------------------------------------------------------------
    def test_find_by_email_exact_match(self) -> None:
        saved = self.repo.save(_make_user(username="alice"))
        found = self.repo.find_by_email("alice@example.com")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.user_id, saved.user_id)
        self.assertEqual(found.username, "alice")

    def test_find_by_email_case_insensitive(self) -> None:
        saved = self.repo.save(_make_user(username="Alice"))
        found = self.repo.find_by_email("ALICE@EXAMPLE.COM")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.user_id, saved.user_id)

    def test_find_by_email_unknown_returns_none(self) -> None:
        self.repo.save(_make_user(username="alice"))
        self.assertIsNone(self.repo.find_by_email("ghost@example.com"))

    def test_find_by_email_blank_returns_none_without_query(self) -> None:
        self.repo.save(_make_user(username="alice"))
        with patch.object(
            self.db, "execute_query", wraps=self.db.execute_query
        ) as spy:
            self.assertIsNone(self.repo.find_by_email(""))
            self.assertIsNone(self.repo.find_by_email("   "))
        self.assertEqual(spy.call_count, 0, "blank email must not hit the database")

    def test_find_by_email_duplicates_return_lowest_user_id(self) -> None:
        # Schema does not enforce email uniqueness, so duplicates are inserted
        # directly through the repository with distinct usernames. They are
        # inserted higher user_id first to prove the lowest one is chosen.
        high = _make_user(user_id=50, username="dup_high")
        high.email = "shared@example.com"
        saved_high = self.repo.save(high)

        low = _make_user(user_id=10, username="dup_low")
        low.email = "shared@example.com"
        saved_low = self.repo.save(low)

        self.assertEqual(
            len(self.repo.find_all()), 2, "both duplicate email rows must exist"
        )
        self.assertGreater(saved_high.user_id, saved_low.user_id)

        found = self.repo.find_by_email("shared@example.com")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.user_id, 10)
        self.assertEqual(found.username, "dup_low")

    def test_find_by_email_query_is_parameterised_and_ordered(self) -> None:
        # SQLite scans rowid tables in key order, so the row-level assertions
        # above cannot detect a missing ORDER BY. Check the statement itself.
        self.repo.save(_make_user(username="alice"))
        with patch.object(
            self.db, "execute_query", wraps=self.db.execute_query
        ) as spy:
            self.repo.find_by_email("alice@example.com")
        sql = spy.call_args.args[0]
        self.assertIn("LOWER(email) = LOWER(?)", sql)
        self.assertIn("ORDER BY user_id ASC", sql)
        self.assertNotIn("alice@example.com", sql, "email must be bound, not inlined")


if __name__ == "__main__":
    unittest.main()
