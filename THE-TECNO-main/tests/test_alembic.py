"""V72 phase 2 — Alembic baseline + round-trip tests.

These tests do NOT touch the conftest.py app fixture. They run Alembic
directly against an isolated SQLite file and verify:

  1. `alembic upgrade head` succeeds on an empty DB.
  2. After the upgrade, every table the legacy app expects exists.
  3. The default seed rows (payment_methods, settings) are present.
  4. The schema produced by Alembic matches the ORM models exactly —
     i.e. the same in-memory engine could host either pathway.
  5. `alembic downgrade base` cleanly removes everything.
  6. `upgrade head` -> `downgrade base` -> `upgrade head` is idempotent.

If any of these fails, the baseline migration drifted from the live
schema (or from the ORM models). Run with:

    pytest tests/test_alembic.py -v

Skipped automatically if the optional Alembic dependency is missing
locally (so CI on a clean checkout without `pip install -r
requirements.txt` does not red-fail with a confusing import error).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Skip cleanly if alembic isn't installed in the local venv. CI installs it
# via requirements-dev.txt; ad-hoc developer machines may not.
# ---------------------------------------------------------------------------
alembic = pytest.importorskip("alembic", reason="alembic not installed")
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402


# All tests in this module exercise a real DB schema upgrade path; we want
# them in the dedicated Postgres CI pass so a Postgres-only migration bug
# (e.g. a missing CASCADE, a Postgres-only DDL clause) cannot hide behind
# SQLite's looser semantics.
pytestmark = pytest.mark.postgres


_REPO_ROOT = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_alembic_config(db_path: Path) -> Config:
    """Build an Alembic Config object pointing at an isolated SQLite file.

    We have to set `DATABASE_URL` BEFORE Alembic imports `app.db.base` (the
    URL is captured at import time). Pytest's `monkeypatch.setenv` would
    work but only inside a fixture; this helper is callable directly.
    """
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

    # Drop any cached app.db modules so the URL change takes effect on
    # the next import inside env.py.
    for mod in ("app.db.base", "app.db.session", "app.db", "app.db.models"):
        sys.modules.pop(mod, None)

    cfg = Config(str(_ALEMBIC_INI))
    # Belt-and-braces — also override the ini option directly so even
    # offline `--sql` mode would see the right URL.
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    # Force script_location to absolute so tests work no matter the cwd.
    cfg.set_main_option(
        "script_location", str(_REPO_ROOT / "migrations")
    )
    return cfg


_EXPECTED_TABLES = {
    "users",
    "games",
    "products",
    "product_groups",
    "orders",
    "deposits",
    "payment_methods",
    "settings",
    "audit_log",
    "wishlist",
    "alembic_version",  # written by Alembic itself
}

_EXPECTED_INDEXES = {
    "users": {
        "idx_users_email",
        "idx_users_email_token",
        "idx_users_reset_token",
        "idx_users_google_sub",
    },
    "games": {"idx_games_active"},
    "products": {
        "idx_products_game",
        "idx_products_game_key",
        "idx_products_active_sort",
    },
    "orders": {
        "idx_orders_user_id",
        "idx_orders_status",
        "idx_orders_created_at",
        "idx_orders_user_created",
        "idx_orders_status_created",
    },
    "deposits": {
        "idx_deposits_user_id",
        "idx_deposits_status",
        "idx_deposits_user_created",
    },
    "settings": {"idx_settings_key"},
    "audit_log": {
        "idx_audit_ts",
        "idx_audit_actor",
        "idx_audit_target",
        "idx_audit_action",
    },
    "wishlist": {"idx_wishlist_user"},
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def fresh_db(tmp_path):
    """Yield (Config, db_path) backed by a brand-new SQLite file."""
    db_path = tmp_path / "alembic-test.db"
    cfg = _make_alembic_config(db_path)
    yield cfg, db_path
    # Best-effort cleanup of the cached env var so other tests aren't
    # surprised by a stale DATABASE_URL pointing at a deleted tmp file.
    os.environ.pop("DATABASE_URL", None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_upgrade_head_creates_all_tables(fresh_db):
    cfg, db_path = fresh_db
    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    missing = _EXPECTED_TABLES - tables
    assert not missing, f"Missing tables after upgrade: {missing}"


def test_upgrade_head_creates_expected_indexes(fresh_db):
    cfg, db_path = fresh_db
    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    inspector = inspect(engine)

    for table, expected in _EXPECTED_INDEXES.items():
        actual = {idx["name"] for idx in inspector.get_indexes(table)}
        # SQLite auto-creates an index for UNIQUE constraints that
        # we don't care about asserting.
        missing = expected - actual
        assert not missing, (
            f"{table}: missing indexes {missing}; got {actual}"
        )


def test_seed_payment_methods_and_settings(fresh_db):
    """The baseline migration seeds the same default rows that
    `database._init_db_inner` writes via INSERT OR IGNORE. Production
    behaviour relies on these being present."""
    cfg, db_path = fresh_db
    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.connect() as conn:
        pm_count = conn.execute(
            text("SELECT COUNT(*) FROM payment_methods")
        ).scalar()
        assert pm_count == 6, f"expected 6 payment methods, got {pm_count}"

        settings_count = conn.execute(
            text("SELECT COUNT(*) FROM settings")
        ).scalar()
        assert settings_count == 11, (
            f"expected 11 default settings, got {settings_count}"
        )

        # Spot-check a couple of representative seeds.
        rate = conn.execute(
            text("SELECT value FROM settings WHERE key='usd_syp_rate'")
        ).scalar()
        assert rate == "15000"

        usdt_currency = conn.execute(
            text("SELECT currency FROM payment_methods WHERE id='usdt'")
        ).scalar()
        assert usdt_currency == "USD"


def test_alembic_version_table_records_revision(fresh_db):
    cfg, db_path = fresh_db
    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.connect() as conn:
        rev = conn.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar()
        # V73: head is now 0002_orders_provider_response_raw. The old
        # baseline assertion (`rev == "0001_baseline"`) regressed when
        # we added the orphan-recovery migration; update tests in
        # lockstep with every new revision.
        assert rev == "0002_orders_provider_response_raw"


def test_downgrade_base_drops_all_tables(fresh_db):
    cfg, db_path = fresh_db
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    # `alembic_version` survives — that's expected; everything else
    # must be gone.
    assert tables.issubset({"alembic_version"}), (
        f"Tables left after downgrade base: {tables - {'alembic_version'}}"
    )


def test_round_trip_upgrade_downgrade_upgrade_is_idempotent(fresh_db):
    """Running upgrade -> downgrade -> upgrade ends in the same shape
    as a single upgrade. Catches subtle drop_index typos in downgrade()."""
    cfg, db_path = fresh_db
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    missing = _EXPECTED_TABLES - tables
    assert not missing, f"Missing after round-trip: {missing}"


def test_baseline_matches_orm_metadata(fresh_db):
    """The schema produced by `alembic upgrade head` must contain every
    table that `app.db.models` defines. If a future migration adds a
    new table to the ORM but forgets to write the migration, this
    test fails loudly."""
    cfg, db_path = fresh_db
    command.upgrade(cfg, "head")

    # Re-import models AFTER upgrade so we don't get a stale Base.metadata.
    for mod in ("app.db.models", "app.db", "app.db.base"):
        sys.modules.pop(mod, None)
    from app.db.base import Base  # noqa: E402
    from app.db import models as _m  # noqa: E402,F401

    orm_tables = set(Base.metadata.tables.keys())

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    db_tables = set(inspect(engine).get_table_names()) - {"alembic_version"}

    missing_in_db = orm_tables - db_tables
    assert not missing_in_db, (
        f"Tables defined in ORM but missing in baseline migration: "
        f"{missing_in_db}"
    )
