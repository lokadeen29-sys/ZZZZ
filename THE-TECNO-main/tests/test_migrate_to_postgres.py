"""End-to-end tests for ``tools/migrate_to_postgres.py``.

We can't bring up a real Postgres instance in CI, so the tests use SQLite
on both sides. The migration script is engine-agnostic above the
``_reset_postgres_sequences`` helper (which is a no-op on SQLite), so
SQLite → SQLite exercises the copy logic, schema parity check,
truncate path, batch streaming, and verification end-to-end.

A separate, manually-run test against a local Postgres in Docker is
documented in ``MIGRATION_GUIDE.md``.
"""

from __future__ import annotations

import io
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

# Repo root on sys.path so we can import ``tools.migrate_to_postgres`` and
# ``app.db.*`` directly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.db.base import Base  # noqa: E402
from app.db import models as _models  # noqa: E402,F401  — registers tables

from tools import migrate_to_postgres as mig  # noqa: E402


# ---------------------------------------------------------------------------
# Sample data — one row per table so every code path is exercised.
# Column lists mirror app/db/models.py exactly (DB-side names).
# ---------------------------------------------------------------------------
def _seed_source(engine) -> dict[str, int]:
    """Populate ``engine`` with sample rows; return ``{table: row_count}``."""
    now = int(time.time())
    rows: dict[str, list[dict]] = {
        "settings": [
            {"key": "site_title", "value": "TecnoGems"},
            {"key": "usd_syp_rate", "value": "12000"},
            {"key": "pricing_mode", "value": "usd"},
        ],
        "payment_methods": [
            {
                "id": "usdt",
                "name": "USDT TRC20",
                "emoji": "💎",
                "address": "TXyz...",
                "instructions": "send USDT only",
                "active": 1,
                "currency": "USD",
            },
            {
                "id": "syriatel",
                "name": "Syriatel Cash",
                "emoji": "📱",
                "address": "0993000000",
                "instructions": "",
                "active": 1,
                "currency": "SYP",
            },
        ],
        "users": [
            {
                "id": 1,
                "name": "Admin",
                "email": "admin@test.local",
                "phone": None,
                "password_hash": "x" * 60,
                "role": "admin",
                "balance": 10.0,
                "active": 1,
                "email_verified": 1,
                "email_token": None,
                "email_token_created_at": None,
                "reset_token": None,
                "reset_token_created_at": None,
                "pending_email": None,
                "pending_email_token": None,
                "pending_email_created_at": None,
                "totp_secret": None,
                "totp_enabled": 0,
                "totp_backup_codes": None,
                "totp_enabled_at": None,
                "google_sub": None,
                "session_version": 1,
                "created_at": now,
            },
            {
                "id": 2,
                "name": "User Two",
                "email": "user2@test.local",
                "phone": "0999",
                "password_hash": "y" * 60,
                "role": "user",
                "balance": 0.0,
                "active": 1,
                "email_verified": 1,
                "email_token": None,
                "email_token_created_at": None,
                "reset_token": None,
                "reset_token_created_at": None,
                "pending_email": None,
                "pending_email_token": None,
                "pending_email_created_at": None,
                "totp_secret": None,
                "totp_enabled": 0,
                "totp_backup_codes": None,
                "totp_enabled_at": None,
                "google_sub": None,
                "session_version": 1,
                "created_at": now,
            },
        ],
        "games": [
            {
                "id": 1,
                "provider": "server1",
                "game_key": "pubg",
                "name": "PUBG Mobile",
                "emoji": "🎮",
                "image_url": "",
                "active": 1,
                "pricing_currency": "GLOBAL",
                "show_on_home": 1,
                "home_sort_order": 0,
            }
        ],
        "product_groups": [
            {
                "id": 1,
                "provider": "server1",
                "game_key": "pubg",
                "name": "UC packs",
                "image_url": "",
                "sort_order": 1,
                "active": 1,
                "created_at": now,
            }
        ],
        "products": [
            {
                "id": 1,
                "provider": "server1",
                "game_key": "pubg",
                "provider_product_id": "uc-60",
                "name": "60 UC",
                "base_price": 1.0,
                "sell_price": 1.5,
                "sort_order": 1,
                "active": 1,
                "group_id": 1,
                "fixed_syp_price": 0.0,
                "pricing_mode": "usd",
                "manual_price_syp": 0.0,
            }
        ],
        "orders": [
            {
                "id": 1,
                "order_code": "ORDtest1",
                "user_id": 2,
                "provider": "server1",
                "game_key": "pubg",
                "game_name": "PUBG Mobile",
                "product_id": 1,
                "product_name": "60 UC",
                "player_id": "12345",
                "price": 1.5,
                "status": "completed",
                "provider_order_id": "p_abc",
                "note": None,
                "created_at": now,
                "updated_at": now,
            }
        ],
        "deposits": [
            {
                "id": 1,
                "deposit_code": "DEPtest1",
                "user_id": 2,
                "amount": 5000.0,
                "method": "syriatel",
                "proof": "no proof",
                "status": "approved",
                "created_at": now,
                "currency": "SYP",
                "amount_usd": 0.42,
                "proof_filename": None,
            }
        ],
        # Two audit rows: one with a value in the aliased ``metadata``
        # column, one without — so we cover the alias both populated and
        # null.
        "audit_log": [
            {
                "id": 1,
                "ts": now,
                "action": "ADMIN_LOGIN",
                "actor_id": 1,
                "actor_email": "admin@test.local",
                "target_type": None,
                "target_id": None,
                "ip": "127.0.0.1",
                "user_agent": "pytest",
                "old_value": None,
                "new_value": None,
                # NOTE: DB-side name is ``metadata`` (Python attr ``meta``).
                "metadata": '{"reason": "smoke"}',
            },
            {
                "id": 2,
                "ts": now + 1,
                "action": "ADMIN_LOGOUT",
                "actor_id": 1,
                "actor_email": "admin@test.local",
                "target_type": None,
                "target_id": None,
                "ip": "127.0.0.1",
                "user_agent": "pytest",
                "old_value": None,
                "new_value": None,
                "metadata": None,
            },
        ],
        "wishlist": [
            {
                "id": 1,
                "user_id": 2,
                "provider": "server1",
                "game_key": "pubg",
                "created_at": now,
            }
        ],
    }

    counts: dict[str, int] = {}
    with engine.begin() as conn:
        for tname in mig.TABLE_ORDER:
            tbl = Base.metadata.tables[tname]
            data = rows.get(tname, [])
            if data:
                conn.execute(tbl.insert(), data)
            counts[tname] = len(data)
    return counts


def _create_schema(engine) -> None:
    """Create the full ORM schema on ``engine``.

    We use ``Base.metadata.create_all`` rather than alembic upgrade so the
    test does not depend on alembic.ini being on the path. The hand-written
    baseline migration mirrors ``Base.metadata`` exactly (verified by
    ``test_alembic.py::test_baseline_matches_orm_metadata``), so the two
    paths produce the same schema.
    """
    Base.metadata.create_all(engine)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def src_url(tmp_path: Path) -> str:
    """Source SQLite URL with schema + seeded sample data."""
    db = tmp_path / "src.db"
    url = f"sqlite:///{db}"
    eng = create_engine(url, future=True)
    _create_schema(eng)
    _seed_source(eng)
    eng.dispose()
    return url


@pytest.fixture()
def empty_src_url(tmp_path: Path) -> str:
    """Source SQLite URL with schema but NO data."""
    db = tmp_path / "src_empty.db"
    url = f"sqlite:///{db}"
    eng = create_engine(url, future=True)
    _create_schema(eng)
    eng.dispose()
    return url


@pytest.fixture()
def tgt_url(tmp_path: Path) -> str:
    """Target SQLite URL with schema, no data."""
    db = tmp_path / "tgt.db"
    url = f"sqlite:///{db}"
    eng = create_engine(url, future=True)
    _create_schema(eng)
    eng.dispose()
    return url


@pytest.fixture()
def tgt_url_no_schema(tmp_path: Path) -> str:
    """Target SQLite URL pointing at an empty file (no tables created)."""
    db = tmp_path / "tgt_no_schema.db"
    db.touch()
    return f"sqlite:///{db}"


def _run(argv: list[str]) -> tuple[int, str]:
    """Invoke ``mig.main(argv)`` and capture stdout."""
    buf = io.StringIO()
    # The script uses ``sys.stderr`` for fail messages; merge it into stdout
    # for assertion convenience by temporarily redirecting both.
    err_buf = io.StringIO()
    real_stderr = sys.stderr
    sys.stderr = err_buf
    try:
        with redirect_stdout(buf):
            rc = mig.main(argv)
    finally:
        sys.stderr = real_stderr
    return rc, buf.getvalue() + err_buf.getvalue()


# ---------------------------------------------------------------------------
# Happy path: full copy
# ---------------------------------------------------------------------------
class TestHappyPath:
    def test_copy_succeeds_and_counts_match(self, src_url, tgt_url):
        rc, out = _run(["--source", src_url, "--target", tgt_url, "--yes"])
        assert rc == 0, out
        assert "Migration complete" in out

        eng = create_engine(tgt_url, future=True)
        with eng.connect() as conn:
            for tname in mig.TABLE_ORDER:
                src_count = create_engine(src_url, future=True).connect().execute(
                    text(f"SELECT COUNT(*) FROM {tname}")
                ).scalar()
                tgt_count = conn.execute(
                    text(f"SELECT COUNT(*) FROM {tname}")
                ).scalar()
                assert src_count == tgt_count, (
                    f"{tname} count mismatch: src={src_count} tgt={tgt_count}"
                )

    def test_audit_log_metadata_alias_preserved(self, src_url, tgt_url):
        """The ``audit_log.metadata`` column must round-trip with its DB name."""
        rc, _ = _run(["--source", src_url, "--target", tgt_url, "--yes"])
        assert rc == 0
        eng = create_engine(tgt_url, future=True)
        with eng.connect() as conn:
            row = conn.execute(
                text("SELECT id, metadata FROM audit_log ORDER BY id ASC")
            ).all()
        assert len(row) == 2
        assert row[0][1] == '{"reason": "smoke"}'
        assert row[1][1] is None

    def test_dict_shape_per_row(self, src_url, tgt_url):
        """Every row in the target must have the same column values as the source."""
        _run(["--source", src_url, "--target", tgt_url, "--yes"])

        src_eng = create_engine(src_url, future=True)
        tgt_eng = create_engine(tgt_url, future=True)
        for tname in mig.TABLE_ORDER:
            tbl = Base.metadata.tables[tname]
            cols = ", ".join(c.name for c in tbl.columns)
            pk = ", ".join(c.name for c in tbl.primary_key.columns)
            sql = text(f"SELECT {cols} FROM {tname} ORDER BY {pk}")
            with src_eng.connect() as c:
                src_rows = [tuple(r) for r in c.execute(sql).all()]
            with tgt_eng.connect() as c:
                tgt_rows = [tuple(r) for r in c.execute(sql).all()]
            assert src_rows == tgt_rows, f"{tname} row content mismatch"

    def test_pre_flight_report_lists_every_table(self, src_url, tgt_url):
        rc, out = _run(["--source", src_url, "--target", tgt_url, "--yes"])
        assert rc == 0
        for tname in mig.TABLE_ORDER:
            assert tname in out, f"{tname} missing from report"

    def test_sample_users_section_appears(self, src_url, tgt_url):
        rc, out = _run(["--source", src_url, "--target", tgt_url, "--yes"])
        assert rc == 0
        assert "Sample row from users" in out
        assert "admin@test.local" in out

    def test_small_batch_size_still_copies_everything(self, src_url, tgt_url):
        """Force batch-size=1 so multi-batch streaming is exercised."""
        rc, _ = _run(
            [
                "--source",
                src_url,
                "--target",
                tgt_url,
                "--yes",
                "--batch-size",
                "1",
            ]
        )
        assert rc == 0
        eng = create_engine(tgt_url, future=True)
        with eng.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM users")).scalar() == 2
            assert conn.execute(text("SELECT COUNT(*) FROM audit_log")).scalar() == 2


# ---------------------------------------------------------------------------
# Empty source
# ---------------------------------------------------------------------------
class TestEmptySource:
    def test_empty_source_succeeds_with_zero_rows(self, empty_src_url, tgt_url):
        rc, out = _run(
            ["--source", empty_src_url, "--target", tgt_url, "--yes"]
        )
        assert rc == 0
        assert "Migration complete" in out
        assert "Copied 0 rows" in out


# ---------------------------------------------------------------------------
# Refusals + pre-condition errors
# ---------------------------------------------------------------------------
class TestPreconditionFailures:
    def test_target_missing_schema_aborts(self, src_url, tgt_url_no_schema):
        rc, out = _run(
            ["--source", src_url, "--target", tgt_url_no_schema, "--yes"]
        )
        assert rc == 1
        assert "missing tables" in out
        assert "alembic upgrade head" in out

    def test_source_equals_target_aborts(self, tgt_url):
        rc, out = _run(["--source", tgt_url, "--target", tgt_url, "--yes"])
        assert rc == 1
        assert "identical" in out

    def test_missing_source_argument_aborts(self, tgt_url, monkeypatch):
        # Wipe DATABASE_URL fallback so argparse default resolves to None.
        monkeypatch.delenv("DATABASE_URL", raising=False)
        rc, out = _run(["--target", tgt_url, "--yes"])
        assert rc == 1
        assert "No source URL set" in out

    def test_missing_target_argument_aborts(self, src_url, monkeypatch):
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        rc, out = _run(["--source", src_url, "--yes"])
        assert rc == 1
        assert "No target URL set" in out

    def test_truncate_without_yes_aborts(self, src_url, tgt_url):
        rc, out = _run(
            ["--source", src_url, "--target", tgt_url, "--truncate"]
        )
        assert rc == 1
        assert "--truncate" in out and "--yes" in out

    def test_negative_batch_size_aborts(self, src_url, tgt_url):
        rc, out = _run(
            [
                "--source",
                src_url,
                "--target",
                tgt_url,
                "--yes",
                "--batch-size",
                "0",
            ]
        )
        assert rc == 1
        assert "--batch-size" in out


class TestSchemaParity:
    def test_source_missing_orm_column_aborts(self, tmp_path):
        """If the source has all tables but a column the ORM expects is
        missing, the script must abort BEFORE writing any data."""
        src = tmp_path / "src_drift.db"
        tgt = tmp_path / "tgt_drift.db"
        src_eng = create_engine(f"sqlite:///{src}", future=True)
        tgt_eng = create_engine(f"sqlite:///{tgt}", future=True)

        # Create a degenerate ``users`` table with one missing column.
        with src_eng.begin() as c:
            c.execute(
                text(
                    "CREATE TABLE users ("
                    "id INTEGER PRIMARY KEY, name TEXT, email TEXT, "
                    "password_hash TEXT, role TEXT, balance REAL, active INTEGER, "
                    "email_verified INTEGER, created_at INTEGER"
                    # `phone`, `email_token`, etc. all MISSING.
                    ")"
                )
            )
            # Create the rest empty so the parity check fires on users only.
            Base.metadata.create_all(c, checkfirst=True)

        _create_schema(tgt_eng)

        rc, out = _run(
            [
                "--source",
                f"sqlite:///{src}",
                "--target",
                f"sqlite:///{tgt}",
                "--yes",
            ]
        )
        assert rc == 1
        assert "missing columns" in out
        # And the target was untouched.
        with tgt_eng.connect() as c:
            assert c.execute(text("SELECT COUNT(*) FROM users")).scalar() == 0


# ---------------------------------------------------------------------------
# --truncate path
# ---------------------------------------------------------------------------
class TestTruncate:
    def test_truncate_wipes_target_then_copies(self, src_url, tgt_url):
        # Pre-populate target with garbage that --truncate must remove.
        eng = create_engine(tgt_url, future=True)
        with eng.begin() as c:
            c.execute(
                text(
                    "INSERT INTO settings (key, value) VALUES "
                    "('garbage', 'should-be-deleted')"
                )
            )
            c.execute(
                text(
                    "INSERT INTO users (id, name, email, password_hash, "
                    "role, balance, active, email_verified, "
                    "session_version, created_at) VALUES "
                    "(99, 'ghost', 'ghost@x', 'h', 'user', 0, 1, 1, 1, 0)"
                )
            )

        rc, _ = _run(
            [
                "--source",
                src_url,
                "--target",
                tgt_url,
                "--yes",
                "--truncate",
            ]
        )
        assert rc == 0

        with eng.connect() as c:
            # garbage row gone
            assert (
                c.execute(
                    text("SELECT COUNT(*) FROM settings WHERE key='garbage'")
                ).scalar()
                == 0
            )
            # ghost user gone
            assert (
                c.execute(text("SELECT COUNT(*) FROM users WHERE id=99")).scalar()
                == 0
            )
            # source data present
            assert (
                c.execute(
                    text("SELECT COUNT(*) FROM users WHERE email='admin@test.local'")
                ).scalar()
                == 1
            )

    def test_running_twice_with_truncate_is_idempotent(self, src_url, tgt_url):
        """Two consecutive --truncate runs must yield identical state."""
        rc1, _ = _run(
            [
                "--source",
                src_url,
                "--target",
                tgt_url,
                "--yes",
                "--truncate",
            ]
        )
        assert rc1 == 0

        rc2, _ = _run(
            [
                "--source",
                src_url,
                "--target",
                tgt_url,
                "--yes",
                "--truncate",
            ]
        )
        assert rc2 == 0

        eng = create_engine(tgt_url, future=True)
        with eng.connect() as c:
            assert c.execute(text("SELECT COUNT(*) FROM users")).scalar() == 2
            assert c.execute(text("SELECT COUNT(*) FROM orders")).scalar() == 1


# ---------------------------------------------------------------------------
# Source read-only protection
# ---------------------------------------------------------------------------
class TestReadOnlySource:
    def test_sqlite_source_url_is_coerced_to_readonly(self):
        url = "sqlite:///data/site.db"
        coerced = mig._coerce_sqlite_to_readonly(url)
        assert "mode=ro" in coerced
        assert "uri=true" in coerced

    def test_postgres_source_url_is_unchanged(self):
        url = "postgresql://user:pass@host/db"
        assert mig._coerce_sqlite_to_readonly(url) == url

    def test_in_memory_sqlite_is_not_wrapped(self):
        url = "sqlite:///:memory:"
        assert mig._coerce_sqlite_to_readonly(url) == url

    def test_already_uri_form_is_not_wrapped_twice(self):
        url = "sqlite:///file:foo.db?mode=rwc&uri=true"
        # Already has mode= → unchanged.
        assert mig._coerce_sqlite_to_readonly(url) == url

    def test_absolute_path_is_wrapped(self):
        url = "sqlite:////abs/path.db"
        coerced = mig._coerce_sqlite_to_readonly(url)
        assert coerced == "sqlite:///file:/abs/path.db?mode=ro&uri=true"

    def test_attempted_write_to_source_fails(self, src_url, tgt_url):
        """Sanity check: after the script wraps the source as read-only,
        the very engine it built refuses writes.

        We re-create the engine the same way the script does so the test
        stays in sync if the wrapping changes.
        """
        eng = mig._make_source_engine(src_url)
        with pytest.raises(Exception):  # noqa: PT011
            with eng.begin() as conn:
                conn.execute(text("DELETE FROM users"))


# ---------------------------------------------------------------------------
# --no-verify
# ---------------------------------------------------------------------------
class TestNoVerify:
    def test_no_verify_skips_verification_section(self, src_url, tgt_url):
        rc, out = _run(
            [
                "--source",
                src_url,
                "--target",
                tgt_url,
                "--yes",
                "--no-verify",
            ]
        )
        assert rc == 0
        assert "Skipped verification" in out
        # Sample row block is part of verify; should not appear.
        assert "Sample row from users" not in out


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------
class TestHelpers:
    def test_table_order_covers_every_orm_table(self):
        """Every ORM-declared table must be in TABLE_ORDER."""
        orm_tables = {t.name for t in Base.metadata.sorted_tables}
        # alembic_version is created by Alembic, not by our ORM.
        orm_tables.discard("alembic_version")
        assert orm_tables == set(mig.TABLE_ORDER)

    def test_protected_tables_includes_alembic_version(self):
        assert "alembic_version" in mig.PROTECTED_TABLES

    def test_integer_primary_key_helper(self):
        users = Base.metadata.tables["users"]
        pm = Base.metadata.tables["payment_methods"]
        settings = Base.metadata.tables["settings"]

        assert mig._integer_primary_key(users) is not None
        assert mig._integer_primary_key(users).name == "id"
        # payment_methods.id is TEXT, settings.key is TEXT — no integer PK.
        assert mig._integer_primary_key(pm) is None
        assert mig._integer_primary_key(settings) is None

    def test_count_rows_returns_zero_for_missing_table(self, tmp_path):
        eng = create_engine(f"sqlite:///{tmp_path / 'empty.db'}", future=True)
        # Don't create schema; the fn must return 0 instead of raising.
        assert mig._count_rows(eng, "users") == 0
