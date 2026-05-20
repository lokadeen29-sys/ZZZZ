"""
V51 task C — shared pytest fixtures.

Design goals:
  - Each test module gets an isolated SQLite database inside a tmp dir
    (monkey-patch `database.DB_PATH` BEFORE importing `app`).
  - CSRF and Flask-Limiter are disabled: they are covered by their own
    dedicated tests but would make every other test flaky/slow.
  - `app._setup_done` is flipped to True so the lazy `@before_request`
    initialiser does not run seed_local_provider_catalog / network code.
  - Environment is clamped to `FLASK_ENV=development` to avoid tripping
    the strict production guards in wsgi / app startup.
"""
from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Repo root on sys.path — tests live in tests/ next to app.py
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Session-wide environment — set BEFORE importing app
# ---------------------------------------------------------------------------
def _prime_env() -> None:
    os.environ["FLASK_ENV"] = "development"
    os.environ["SECRET_KEY"] = "test-" + "x" * 48
    os.environ["ADMIN_PASSWORD"] = "TestAdminPass123!"
    os.environ["ADMIN_EMAIL"] = "admin@test.local"
    # Absolutely no outbound network in tests.
    os.environ.pop("REDIS_URL", None)
    os.environ.pop("GOOGLE_CLIENT_ID", None)
    os.environ.pop("GOOGLE_CLIENT_SECRET", None)
    os.environ["BASE_URL"] = "http://localhost"


_prime_env()


# ---------------------------------------------------------------------------
# Per-test fresh SQLite file + re-imported app module
# ---------------------------------------------------------------------------
@pytest.fixture()
def app(tmp_path, monkeypatch):
    """Return a fully-configured Flask `app` pointing at an isolated DB.

    We use `function` scope so each test starts with a clean database and
    a clean Flask-Limiter state. The cost is ~100ms per test, which is
    fine at this scale.
    """
    # Point the DB at a fresh file in the tmp dir.
    db_file = tmp_path / "test-site.db"
    monkeypatch.setenv("DATABASE_URL", str(db_file))  # future-proof

    # Ensure modules can be re-imported cleanly when another test already
    # imported them with a different DB_PATH.
    for mod in (
        "app",
        "app.config",
        "app.extensions",
        "app.middleware",
        "app.bootstrap",
        "app.routes",
        "app.routes.auth_bp",
        "app.routes.oauth_bp",
        "app.routes.public_bp",
        "app.routes.wallet_bp",
        "app.routes.admin_bp",
        "app.routes.admin_2fa_bp",
        "app.routes.api_bp",
        "app.services",
        "app.services.images",
        "app.services.mail",
        "app.services.pricing",
        "app.services.orders",
        "app.services.game_images",
        "app.utils",
        "app.utils.auth",
        "app.utils.filters",
        "app.utils.i18n",
        "app.utils.security",
        "app.utils.settings_cache",
        "database",
        "tasks",
        "providers",
        "security_2fa",
        "sync_products",
        "featured_games",
        "request_ip",
    ):
        sys.modules.pop(mod, None)

    import database  # noqa: E402 — imported after sys.modules reset
    database.DB_PATH = str(db_file)
    # Reset the "pragmas applied" flag so WAL etc. are re-issued for the
    # fresh DB file (otherwise the module-global says "already done").
    database._PRAGMAS_APPLIED = False

    # V72 / session 3: rebuild the SQLAlchemy engine + session factory
    # against the per-test DB. `app.db.base` reads `DATABASE_URL` only at
    # import time (and it may have already been imported by an earlier
    # test pointing at a different tmp dir), so an explicit reset is
    # required for ORM-backed code to see the right DB.
    from app.db import base as _db_base
    _db_base.reset_engine()

    import app as app_module  # noqa: E402

    flask_app = app_module.app
    flask_app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SESSION_COOKIE_SECURE=False,
        SERVER_NAME=None,
    )
    # Disable rate limiting by default; the dedicated test re-enables it.
    if getattr(app_module, "limiter", None) is not None:
        app_module.limiter.enabled = False

    # Initialise the schema + seed the default admin exactly once and mark
    # the lazy before_request initialiser as "done".
    database.init_db()
    try:
        database.ensure_indexes()
    except Exception:
        pass
    database.seed_admin(os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"])
    flask_app._setup_done = True

    # Expose helpers on the app object for test convenience.
    flask_app._test_db_path = str(db_file)
    flask_app._test_module = app_module
    flask_app._test_database = database

    yield flask_app


@pytest.fixture()
def client(app):
    """Flask test client bound to the isolated app."""
    return app.test_client()


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------
@pytest.fixture()
def make_user(app):
    """Create a regular user via the DB layer and return its dict row."""
    database = app._test_database

    def _factory(
        email: str = "user@test.local",
        password: str = "UserPass123!",
        balance: float = 0.0,
        role: str = "user",
        email_verified: int = 1,
    ):
        ok, err = database.create_user(
            name="Test User",
            email=email,
            phone="",
            password=password,
            email_verified=email_verified,
        )
        if not ok and err != "البريد مستخدم مسبقًا":
            raise AssertionError(f"create_user failed: {err}")
        user = database.get_user_by_email(email)
        if balance:
            database.set_user_balance(user["id"], balance)
        if role != "user":
            conn = database.connect()
            conn.execute("UPDATE users SET role=? WHERE id=?", (role, user["id"]))
            conn.commit()
            conn.close()
        return database.get_user_by_email(email)

    return _factory


@pytest.fixture()
def login_as(client):
    """Log in a user by planting `user_id` directly in the session — skips
    the rate-limited /login route for test speed."""

    def _do(user_id: int, admin_2fa_verified: bool | None = None):
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            if admin_2fa_verified is not None:
                sess["admin_2fa_verified"] = admin_2fa_verified
        return client

    return _do



# ---------------------------------------------------------------------------
# V73 / Postgres CI — opt-in fixture for tests that need a real Postgres.
#
# Most of the suite runs on SQLite (the `app` fixture above). A handful of
# tests exercise behaviour that SQLite silently papers over:
#   - case-sensitive LIKE / search_users (PR #20)
#   - strict GROUP BY rules (PR #24)
#   - round(double, int) signature (update_profit_margin)
#   - Alembic upgrade against the production-shaped backend
#
# Those tests opt in with @pytest.mark.postgres and use this fixture. CI
# provides TEST_POSTGRES_URL via a Postgres 16 service container; locally
# the tests skip cleanly when the env var is unset, so day-to-day developer
# runs stay SQLite-only and fast.
#
# The fixture:
#   1. Points DATABASE_URL at the CI Postgres for the duration of the test
#      (monkeypatch reverts it on teardown).
#   2. Resets app.db.base's module-level engine so SQLAlchemy reconnects to
#      Postgres instead of whatever an earlier test cached.
#   3. Runs `alembic upgrade head` to materialise the production schema.
#   4. Yields, then truncates the data tables on teardown so consecutive
#      Postgres-marked tests start clean without paying the upgrade cost
#      again. Tables are truncated in dependency-friendly order with
#      RESTART IDENTITY CASCADE.
# ---------------------------------------------------------------------------
@pytest.fixture()
def postgres_session(monkeypatch):
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL not set")

    monkeypatch.setenv("DATABASE_URL", url)

    from app.db.base import reset_engine
    reset_engine()

    from alembic.config import Config
    from alembic import command

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")

    yield

    from sqlalchemy import text
    from app.db.base import engine
    with engine.begin() as c:
        for tbl in (
            "wishlist",
            "audit_log",
            "orders",
            "deposits",
            "products",
            "product_groups",
            "games",
            "users",
        ):
            c.execute(text(f"TRUNCATE TABLE {tbl} RESTART IDENTITY CASCADE"))
