"""
Test that LIKE wildcard characters in search queries are properly escaped,
preventing wildcard injection (searching for '%' should not return all rows).

This test imports `database` directly (no full app import) to avoid
Python 3.9 incompatibility issues with providers.py union-type syntax.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Provide an isolated database module with a fresh SQLite file."""
    monkeypatch.setenv("FLASK_ENV", "development")

    # Remove cached modules so DB_PATH takes effect
    for mod in list(sys.modules):
        if mod == "database" or mod.startswith("database."):
            del sys.modules[mod]

    import database
    db_file = tmp_path / "test.db"
    database.DB_PATH = str(db_file)
    database._PRAGMAS_APPLIED = False
    database.init_db()
    return database


def _make_user(db, name="Test User", email="user@test.local"):
    """Insert a user directly for test purposes."""
    from werkzeug.security import generate_password_hash

    with db.db_conn() as conn:
        conn.execute(
            "INSERT INTO users (name, email, phone, password_hash, role, balance, "
            "active, email_verified, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (name, email, "", generate_password_hash("Pass1234!"), "user", 0, 1, 1, int(time.time())),
        )
        conn.commit()


def _make_game(db, provider="test", game_key="g1", name="Normal Game"):
    """Insert a game directly for test purposes."""
    with db.db_conn() as conn:
        conn.execute(
            "INSERT INTO games (provider, game_key, name, emoji, active) "
            "VALUES (?, ?, ?, ?, ?)",
            (provider, game_key, name, "\U0001f3ae", 1),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# search_users tests
# ---------------------------------------------------------------------------

def test_search_users_percent_returns_no_match(db):
    """Searching for literal '%' must NOT return all users."""
    _make_user(db, name="Alice", email="alice@test.local")

    results = db.search_users("%")
    assert results == [], f"Expected 0 results for '%' search, got {len(results)}"


def test_search_users_underscore_returns_no_match(db):
    """Searching for literal '_' must NOT return all users."""
    _make_user(db, name="Bob", email="bob@test.local")

    results = db.search_users("_")
    assert results == [], f"Expected 0 results for '_' search, got {len(results)}"


def test_search_users_finds_literal_percent_in_name(db):
    """A user whose name contains '%' IS found when searching for '%'."""
    _make_user(db, name="100% Gamer", email="percent@test.local")

    results = db.search_users("%")
    assert len(results) == 1
    assert results[0]["name"] == "100% Gamer"


# ---------------------------------------------------------------------------
# search_suggest tests
# ---------------------------------------------------------------------------

def test_search_suggest_percent_returns_no_match(db):
    """search_suggest('%') must NOT return all games/products."""
    _make_game(db, game_key="g1", name="Normal Game")

    results = db.search_suggest("%")
    assert results == [], f"Expected 0 results for '%' search, got {len(results)}"


def test_search_suggest_underscore_returns_no_match(db):
    """search_suggest('_') must NOT return all games/products."""
    _make_game(db, game_key="g2", name="Another Game")

    results = db.search_suggest("_")
    assert results == [], f"Expected 0 results for '_' search, got {len(results)}"


def test_search_suggest_finds_literal_percent_in_name(db):
    """A game whose name contains '%' IS found when searching for '%'."""
    _make_game(db, game_key="g3", name="50% Off Sale")

    results = db.search_suggest("%")
    assert len(results) == 1
    assert results[0]["label"] == "50% Off Sale"
