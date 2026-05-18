"""Verify the ORM models match the live SQLite schema.

What this does:

  1. Opens `data/site.db` via raw `sqlite3` and via SQLAlchemy.
  2. For each table, compares row counts.
  3. Loads one row via each path and prints both for visual diff.
  4. Exits 0 if everything matches, exit 1 otherwise.

Usage:

    # On your laptop or Hetzner server:
    cd /root/project
    .venv/bin/python tools/verify_orm_models.py

What this DOES NOT do:

  - Does NOT modify any data.
  - Does NOT touch Postgres.
  - Does NOT run any migrations.

If counts mismatch, your live DB has columns the models don't know
about (or vice versa). Check `database._init_db_inner` and update
`app/db/models.py` to match.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

# Make `app.*` importable when running from the repo root or `tools/`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import inspect, text  # noqa: E402

from app.db.base import DATABASE_URL, engine  # noqa: E402
from app.db.models import (  # noqa: E402
    AuditLog,
    Deposit,
    Game,
    Order,
    PaymentMethod,
    Product,
    ProductGroup,
    Setting,
    User,
    Wishlist,
)

# Map ORM model -> SQL table name. (We also use this to keep iteration order
# deterministic for nicer output.)
_MODELS = [
    ("users", User),
    ("games", Game),
    ("products", Product),
    ("product_groups", ProductGroup),
    ("orders", Order),
    ("deposits", Deposit),
    ("payment_methods", PaymentMethod),
    ("settings", Setting),
    ("audit_log", AuditLog),
    ("wishlist", Wishlist),
]


# ---------------------------------------------------------------------------
# Pretty output helpers
# ---------------------------------------------------------------------------
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _ok(msg: str) -> None:
    print(f"{GREEN}✓{RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"{RED}✗{RESET} {msg}")


def _warn(msg: str) -> None:
    print(f"{YELLOW}!{RESET} {msg}")


def _section(msg: str) -> None:
    print()
    print(f"{BOLD}=== {msg} ==={RESET}")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def _resolve_sqlite_path() -> str | None:
    """Return the SQLite file path if DATABASE_URL points at one, else None."""
    if not DATABASE_URL.startswith("sqlite"):
        return None
    # Format: sqlite:///absolute/path.db  → strip `sqlite:///`
    return DATABASE_URL.replace("sqlite:///", "", 1)


def check_db_connection() -> bool:
    """Confirm SQLAlchemy can open the database."""
    _section("DB connection")
    try:
        with engine.connect() as conn:
            backend = engine.url.get_backend_name()
            _ok(f"Connected to {backend} via SQLAlchemy")
            print(f"  URL: {DATABASE_URL}")
            return True
    except Exception as exc:
        _fail(f"Cannot open database: {exc}")
        return False


def check_table_existence() -> tuple[bool, list[str]]:
    """Confirm each ORM table exists in the live DB."""
    _section("Tables exist")
    insp = inspect(engine)
    actual = set(insp.get_table_names())
    missing: list[str] = []
    for name, _ in _MODELS:
        if name in actual:
            _ok(f"{name}")
        else:
            _fail(f"{name} (missing in DB)")
            missing.append(name)
    return (not missing), missing


def _row_count_via_sqlite(sqlite_path: str, table: str) -> int | None:
    """Return COUNT(*) using raw sqlite3, or None if the table doesn't exist."""
    try:
        with sqlite3.connect(sqlite_path) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError:
        return None


def _row_count_via_orm(table: str) -> int:
    """Return COUNT(*) using SQLAlchemy."""
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0


def check_row_counts() -> bool:
    """Compare COUNT(*) between raw sqlite3 and SQLAlchemy.

    On Postgres the raw-sqlite3 path is skipped (no SQLite to read).
    The ORM count alone is still printed.
    """
    _section("Row counts (raw sqlite3 vs ORM)")
    sqlite_path = _resolve_sqlite_path()
    all_ok = True
    for table, _model in _MODELS:
        orm_count = _row_count_via_orm(table)
        if sqlite_path is not None and os.path.exists(sqlite_path):
            sqlite_count = _row_count_via_sqlite(sqlite_path, table)
            if sqlite_count is None:
                _warn(f"{table}: ORM={orm_count}  (sqlite3 says table missing)")
                all_ok = False
                continue
            if sqlite_count == orm_count:
                _ok(f"{table:<18} {orm_count:>6}")
            else:
                _fail(f"{table}: sqlite3={sqlite_count} but ORM={orm_count}")
                all_ok = False
        else:
            _ok(f"{table:<18} {orm_count:>6}  (no SQLite file to compare)")
    return all_ok


def check_sample_row() -> bool:
    """Load one row of `users` via both paths and visually compare."""
    _section("Sample row from `users`")
    from app.db.session import get_session

    with get_session() as s:
        user = s.query(User).order_by(User.id.asc()).first()
        if user is None:
            _warn("No rows in users — skipping sample comparison")
            return True
        print(f"  ORM    : id={user.id} email={user.email} role={user.role}")

    sqlite_path = _resolve_sqlite_path()
    if sqlite_path and os.path.exists(sqlite_path):
        with sqlite3.connect(sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, email, role FROM users ORDER BY id ASC LIMIT 1"
            ).fetchone()
            print(
                f"  sqlite3: id={row['id']} email={row['email']} "
                f"role={row['role']}"
            )

    _ok("Sample row loaded via ORM successfully")
    return True


def check_columns_match() -> bool:
    """For each table, compare ORM-declared columns to DB-actual columns.

    Reports columns missing from the DB (will fail at query time) and
    columns present in the DB but not modelled (won't break, but worth
    knowing about).
    """
    _section("Column drift")
    insp = inspect(engine)
    all_ok = True
    for table, model in _MODELS:
        if table not in insp.get_table_names():
            continue  # already reported as missing
        db_cols = {c["name"] for c in insp.get_columns(table)}
        orm_cols = {c.name for c in model.__table__.columns}
        # Note: model `meta` maps to DB column "metadata" — handle alias.
        if model is AuditLog:
            orm_cols = {"metadata" if c == "meta" else c for c in orm_cols}

        missing_in_db = orm_cols - db_cols
        extra_in_db = db_cols - orm_cols

        if not missing_in_db and not extra_in_db:
            _ok(f"{table:<18} columns match ({len(orm_cols)} cols)")
        else:
            if missing_in_db:
                _fail(f"{table}: ORM has columns missing in DB: {missing_in_db}")
                all_ok = False
            if extra_in_db:
                _warn(
                    f"{table}: DB has extra columns not in ORM: {extra_in_db}"
                )
    return all_ok


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    print(f"{BOLD}TecnoGems — ORM model verification{RESET}")
    print(f"DATABASE_URL: {DATABASE_URL}")

    ok = True
    ok &= check_db_connection()
    if not ok:
        _fail("Aborting: cannot connect.")
        return 1

    tables_ok, _ = check_table_existence()
    if not tables_ok:
        _warn("Some tables missing — they may be created on first init_db().")

    ok &= check_row_counts()
    ok &= check_columns_match()
    ok &= check_sample_row()

    print()
    if ok:
        print(f"{GREEN}{BOLD}All checks passed.{RESET}")
        return 0
    print(f"{RED}{BOLD}Some checks failed — review output above.{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
