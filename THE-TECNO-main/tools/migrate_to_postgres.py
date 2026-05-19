"""Migrate data from SQLite (or any SQLAlchemy-compatible source) to Postgres.

V72 / session 4 — first pass at the actual data move.

Pre-conditions
--------------

1. The target Postgres database already exists and the user has CREATE/INSERT
   privileges on every table (created via ``alembic upgrade head`` against
   the target URL).

2. The schemas match. The script copies every column the ORM models declare
   in ``app/db/models.py``. Columns that exist in the source but not in the
   ORM are skipped silently with a warning. Columns that exist in the ORM
   but not in the source are reported as a fatal error before any data is
   touched.

3. The application is paused on the target side. We don't take exclusive
   locks ourselves; the operator is expected to stop ``game-topup.service``
   and ``tecno-worker.service`` before running this script. (It still works
   on a live target — it just risks racing application-side INSERTs.)

What the script does
--------------------

1. Opens both source and target via SQLAlchemy. SQLite source URLs are
   automatically wrapped in a ``mode=ro`` URI so we cannot accidentally
   mutate the original file (V72 safety property).

2. Verifies every ORM-declared table exists on the target. Aborts loudly
   if any are missing.

3. Prints a pre-flight report: source row counts per table.

4. Prompts for ``yes`` confirmation (skip with ``--yes``).

5. Optionally truncates target tables before insert (``--truncate``). On
   Postgres we use ``TRUNCATE ... RESTART IDENTITY CASCADE`` so the
   autoincrement sequences are reset in the same statement. On SQLite we
   ``DELETE FROM`` and ``DELETE FROM sqlite_sequence`` so AUTOINCREMENT
   restarts at 1.

6. Copies table-by-table in dependency order (parents before children).
   Within each table, rows are read from the source in chunks (default 500)
   and bulk-inserted into the target using ``Table.insert()``. Each table
   is committed in its own transaction so a failure mid-run leaves earlier
   tables intact and a clear failure point.

7. After all data is copied, on Postgres only, walks every ``Integer``
   primary key and runs ``setval('<seq>', MAX(id))`` so the next
   application-issued INSERT does not collide with copied IDs.

8. Verifies row counts match between source and target. Prints a sample
   row from ``users`` (id, email, role) from each side as a final visual
   sanity check.

What the script does NOT do
---------------------------

- It does NOT run ``alembic upgrade head`` on the target. The operator is
  expected to do that once, before running this script. Mixing schema
  creation with data copy in one step makes debugging much harder.

- It does NOT touch the ``alembic_version`` table. That row was written
  by ``alembic stamp`` / ``alembic upgrade``, and copying it from SQLite
  would overwrite the correct value.

- It does NOT modify the source database, ever. Read-only mode is enforced
  on SQLite via a URI parameter; on other backends we simply never issue
  any DML to the source connection.

- It does NOT delete or rename the source SQLite file after a successful
  migration. The operator keeps the SQLite file as a recovery artefact
  until session 7 (``MIGRATION_PLAN.md``).

CLI
---

::

    python tools/migrate_to_postgres.py \\
        --source sqlite:///data/site.db \\
        --target postgresql://user:pass@host/db \\
        [--batch-size 500] \\
        [--truncate] \\
        [--yes]

Defaults:

* ``--source`` defaults to the current ``DATABASE_URL`` env var (i.e. what
  the running app uses). On the production host that is the SQLite file.

* ``--target`` defaults to the ``POSTGRES_URL`` env var. If neither is set,
  the script aborts with a clear error.

Exit codes
----------

* ``0`` — success: every row copied, counts match, sequences reset.
* ``1`` — pre-condition failure (missing target table, schema mismatch,
  user declined confirmation, etc.). The target DB has not been touched.
* ``2`` — copy failure mid-run. Partial data is committed up to the last
  successful table; the operator should drop+recreate the target schema
  and retry.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Optional, Sequence

# Make the application package importable when running from the repo root
# or from within tools/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


from sqlalchemy import (  # noqa: E402
    Column,
    Integer,
    MetaData,
    Table,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.engine import Engine  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db import models as _models  # noqa: E402,F401  — registers tables


# ---------------------------------------------------------------------------
# Table copy order
#
# Parents before children. We don't enforce FKs at the DB level (the ORM
# does not declare ForeignKey constraints), but copying parents first means
# that if we ever DO add FK constraints later, the copy keeps working.
# ---------------------------------------------------------------------------
TABLE_ORDER: Sequence[str] = (
    "settings",
    "payment_methods",
    "users",
    "games",
    "product_groups",
    "products",
    "orders",
    "deposits",
    "audit_log",
    "wishlist",
)

# Tables that Alembic owns. We never touch these.
PROTECTED_TABLES: frozenset[str] = frozenset({"alembic_version"})


# ---------------------------------------------------------------------------
# Pretty output
# ---------------------------------------------------------------------------
_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text_: str) -> str:
    return f"\033[{code}m{text_}\033[0m" if _USE_COLOR else text_


def _green(s: str) -> str:
    return _c("92", s)


def _red(s: str) -> str:
    return _c("91", s)


def _yellow(s: str) -> str:
    return _c("93", s)


def _bold(s: str) -> str:
    return _c("1", s)


def _ok(msg: str) -> None:
    print(f"{_green('✓')} {msg}")


def _fail(msg: str) -> None:
    print(f"{_red('✗')} {msg}", file=sys.stderr)


def _warn(msg: str) -> None:
    print(f"{_yellow('!')} {msg}")


def _section(msg: str) -> None:
    print()
    print(_bold(f"=== {msg} ==="))


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------
def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _is_postgres(url: str) -> bool:
    return url.startswith("postgresql") or url.startswith("postgres+")


def _coerce_sqlite_to_readonly(url: str) -> str:
    """Wrap a SQLite URL with the ``mode=ro&uri=true`` flag.

    Examples
    --------
    >>> _coerce_sqlite_to_readonly("sqlite:///data/site.db")
    'sqlite:///file:data/site.db?mode=ro&uri=true'
    >>> _coerce_sqlite_to_readonly("sqlite:////abs/site.db")
    'sqlite:///file:/abs/site.db?mode=ro&uri=true'

    Returns the URL unchanged for:

    * Non-SQLite URLs (e.g. ``postgresql://...``).
    * In-memory databases (``sqlite:///:memory:``).
    * URLs already in URI form (``sqlite:///file:...``).
    * URLs that already specify ``mode=`` (the user has overridden).
    """
    if not _is_sqlite(url):
        return url
    if "mode=" in url:
        return url
    # Strip the leading scheme and the three slashes that mark the path
    # portion of a SQLite URL.
    prefix, sep, path = url.partition("sqlite:///")
    if prefix or not sep or not path:
        # Malformed input — let SQLAlchemy raise a clean error downstream.
        return url
    # ``sqlite:///:memory:`` is an in-memory DB; read-only mode is
    # nonsensical for it (and the URI form ``file::memory:`` doesn't work
    # consistently across sqlite3 versions). Used by the test harness.
    if path == ":memory:":
        return url
    # Already a URI-form path — caller has full control, don't wrap again.
    if path.startswith("file:"):
        return url
    # Standard case: prepend `file:` and append the read-only mode flag.
    return f"sqlite:///file:{path}?mode=ro&uri=true"


# ---------------------------------------------------------------------------
# Engine factories (kept simple; we don't pool — these are short-lived)
# ---------------------------------------------------------------------------
def _make_source_engine(url: str) -> Engine:
    if _is_sqlite(url):
        url = _coerce_sqlite_to_readonly(url)
        return create_engine(
            url,
            future=True,
            connect_args={"uri": True, "check_same_thread": False},
        )
    return create_engine(url, future=True, pool_pre_ping=True)


def _make_target_engine(url: str) -> Engine:
    if _is_sqlite(url):
        return create_engine(
            url,
            future=True,
            connect_args={"check_same_thread": False},
        )
    return create_engine(url, future=True, pool_pre_ping=True)


# ---------------------------------------------------------------------------
# Schema reflection helpers
# ---------------------------------------------------------------------------
def _orm_tables() -> dict[str, Table]:
    """Return the ORM-declared tables keyed by table name."""
    return {t.name: t for t in Base.metadata.sorted_tables if t.name in TABLE_ORDER}


def _reflect_source_columns(source_engine: Engine) -> dict[str, set[str]]:
    """Return ``{table_name: {column_names}}`` reflected from the source DB."""
    insp = inspect(source_engine)
    out: dict[str, set[str]] = {}
    for tname in insp.get_table_names():
        if tname in PROTECTED_TABLES:
            continue
        out[tname] = {c["name"] for c in insp.get_columns(tname)}
    return out


def _verify_target_schema(target_engine: Engine) -> tuple[bool, list[str]]:
    """Return (ok, missing_tables). A missing table aborts the run."""
    insp = inspect(target_engine)
    actual = set(insp.get_table_names())
    missing = [t for t in TABLE_ORDER if t not in actual]
    return (not missing), missing


def _check_column_parity(
    orm_tables: dict[str, Table],
    src_cols: dict[str, set[str]],
) -> list[str]:
    """Return human-readable error strings for column mismatches.

    A column declared in the ORM but missing from the source is fatal.
    A column present in the source but not in the ORM is a warning only
    (the ``copy_table`` routine ignores it).
    """
    errors: list[str] = []
    for name, table in orm_tables.items():
        if name not in src_cols:
            # Missing source table is OK (just no data to copy).
            continue
        orm_set = {c.name for c in table.columns}
        missing_in_source = orm_set - src_cols[name]
        extra_in_source = src_cols[name] - orm_set
        if missing_in_source:
            errors.append(
                f"{name}: source is missing columns {sorted(missing_in_source)}"
            )
        if extra_in_source:
            _warn(
                f"{name}: source has extra columns not in ORM "
                f"{sorted(extra_in_source)} (will be ignored)"
            )
    return errors


# ---------------------------------------------------------------------------
# Counting + truncating
# ---------------------------------------------------------------------------
def _count_rows(engine: Engine, table: str) -> int:
    """Return ``SELECT COUNT(*) FROM <table>`` or 0 if the table is missing."""
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return 0
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0


def _truncate_target(engine: Engine, table: str) -> None:
    """Best-effort wipe of a single table.

    On Postgres we use ``TRUNCATE ... RESTART IDENTITY CASCADE`` so the
    sequence is reset and any FK references in other tables are wiped.
    On SQLite we ``DELETE FROM`` and reset ``sqlite_sequence``.
    """
    url = str(engine.url)
    with engine.begin() as conn:
        if _is_postgres(url):
            # Postgres needs the schema-qualified, quoted name. Default
            # search_path is ``public`` so unquoted is fine for our schema.
            conn.execute(
                text(f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE')
            )
        elif _is_sqlite(url):
            conn.execute(text(f"DELETE FROM {table}"))
            # Reset AUTOINCREMENT counters. The sqlite_sequence table only
            # exists once at least one AUTOINCREMENT row has ever been
            # inserted; missing-table errors are harmless to swallow.
            try:
                conn.execute(
                    text("DELETE FROM sqlite_sequence WHERE name=:n"),
                    {"n": table},
                )
            except Exception:
                pass
        else:
            conn.execute(text(f"DELETE FROM {table}"))


# ---------------------------------------------------------------------------
# Row streaming + bulk insert
# ---------------------------------------------------------------------------
def _stream_rows(
    source_engine: Engine,
    table: Table,
    batch_size: int,
) -> Iterator[list[dict]]:
    """Yield batches of rows from the source table as plain dicts.

    Uses server-side ordering by primary key when available so the dump
    is reproducible; otherwise falls back to insertion order. Each yielded
    batch is at most ``batch_size`` rows.
    """
    pk_cols = [c.name for c in table.primary_key.columns]
    order_clause = ", ".join(pk_cols) if pk_cols else "1"
    # Materialise only the columns the ORM declares; ignore any extras.
    col_list = ", ".join(c.name for c in table.columns)
    sql = text(f"SELECT {col_list} FROM {table.name} ORDER BY {order_clause}")
    column_names = [c.name for c in table.columns]

    with source_engine.connect() as conn:
        # ``yield_per`` keeps memory bounded on huge tables. Postgres needs
        # ``stream_results`` too; SQLite ignores it gracefully.
        result = conn.execution_options(stream_results=True, yield_per=batch_size)
        cursor = result.execute(sql)
        batch: list[dict] = []
        for row in cursor:
            batch.append(dict(zip(column_names, row)))
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch


def _copy_table(
    source_engine: Engine,
    target_engine: Engine,
    table: Table,
    batch_size: int,
) -> int:
    """Copy every row of ``table`` from source to target. Returns row count.

    Each call uses its own transaction on the target. A failure mid-table
    rolls back the whole table so we never leave a half-copied table.
    """
    src_count = _count_rows(source_engine, table.name)
    if src_count == 0:
        _ok(f"{table.name:<18} 0 rows (skipped)")
        return 0

    started = time.monotonic()
    inserted = 0
    with target_engine.begin() as tgt_conn:
        for batch in _stream_rows(source_engine, table, batch_size):
            tgt_conn.execute(table.insert(), batch)
            inserted += len(batch)
    elapsed = time.monotonic() - started
    _ok(
        f"{table.name:<18} {inserted:>6} rows  "
        f"({elapsed:.2f}s, {inserted / max(elapsed, 1e-3):.0f} rows/s)"
    )
    return inserted


# ---------------------------------------------------------------------------
# Postgres sequence reset
# ---------------------------------------------------------------------------
def _reset_postgres_sequences(target_engine: Engine) -> None:
    """For every Integer-PK table, ``setval`` the sequence to MAX(id).

    Required because we copied rows with explicit ``id`` values; without
    this the next application INSERT would re-use ``id=1`` and collide.
    """
    if not _is_postgres(str(target_engine.url)):
        return

    _section("Reset Postgres sequences")
    with target_engine.begin() as conn:
        for tname in TABLE_ORDER:
            table = Base.metadata.tables.get(tname)
            if table is None:
                continue
            int_pk = _integer_primary_key(table)
            if int_pk is None:
                _ok(f"{tname:<18} (no integer PK, skipped)")
                continue
            # ``pg_get_serial_sequence`` returns the sequence name attached
            # to a SERIAL/IDENTITY column, regardless of its actual name
            # (could be ``users_id_seq`` or ``users_id_seq1`` after a
            # schema rebuild). NULL means "no sequence", which means the
            # column was created without ``AUTOINCREMENT`` — in that case
            # we leave it alone.
            seq_q = text(
                "SELECT pg_get_serial_sequence(:tname, :colname)"
            )
            seq_name = conn.execute(
                seq_q, {"tname": tname, "colname": int_pk.name}
            ).scalar()
            if not seq_name:
                _ok(f"{tname:<18} (no sequence attached, skipped)")
                continue

            max_id = conn.execute(
                text(f"SELECT COALESCE(MAX({int_pk.name}), 0) FROM {tname}")
            ).scalar()
            # ``is_called=true`` so the NEXT call to nextval returns
            # ``max_id + 1``. If the table is empty (max_id=0) we set
            # ``is_called=false`` so nextval returns 1 not 2.
            is_called = "true" if max_id and max_id > 0 else "false"
            target = max_id if max_id and max_id > 0 else 1
            conn.execute(
                text("SELECT setval(:seq, :val, :called)"),
                {"seq": seq_name, "val": target, "called": is_called == "true"},
            )
            _ok(f"{tname:<18} {seq_name} -> {target}")


def _integer_primary_key(table: Table) -> Optional[Column]:
    """Return the integer primary-key column of ``table``, if any."""
    for col in table.primary_key.columns:
        if isinstance(col.type, Integer):
            return col
    return None


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def _verify_counts(
    source_engine: Engine, target_engine: Engine
) -> tuple[bool, list[tuple[str, int, int]]]:
    """Return (all_ok, [(table, src_count, tgt_count) for each mismatch])."""
    rows: list[tuple[str, int, int]] = []
    all_ok = True
    for tname in TABLE_ORDER:
        src = _count_rows(source_engine, tname)
        tgt = _count_rows(target_engine, tname)
        rows.append((tname, src, tgt))
        if src != tgt:
            all_ok = False
    return all_ok, rows


def _print_count_summary(rows: list[tuple[str, int, int]]) -> None:
    print(f"  {'table':<18} {'source':>8}  {'target':>8}")
    for tname, src, tgt in rows:
        marker = _green("OK") if src == tgt else _red("DIFF")
        print(f"  {tname:<18} {src:>8}  {tgt:>8}  {marker}")


def _print_sample_users(source_engine: Engine, target_engine: Engine) -> None:
    """Print one row from ``users`` from each side as a final sanity check."""
    sql = text("SELECT id, email, role FROM users ORDER BY id ASC LIMIT 1")

    def _fetch(engine: Engine) -> Optional[tuple]:
        try:
            with engine.connect() as conn:
                row = conn.execute(sql).first()
                return tuple(row) if row else None
        except Exception:
            return None

    src_row = _fetch(source_engine)
    tgt_row = _fetch(target_engine)
    if src_row is None and tgt_row is None:
        _warn("No rows in users on either side — skipping sample compare")
        return
    print(f"  source: {src_row}")
    print(f"  target: {tgt_row}")
    if src_row == tgt_row:
        _ok("Sample row matches.")
    else:
        _fail("Sample row MISMATCH — investigate before going live.")


# ---------------------------------------------------------------------------
# Confirmation prompt
# ---------------------------------------------------------------------------
def _confirm(prompt: str) -> bool:
    """Block on stdin for a literal ``yes`` answer.

    Returns ``False`` if stdin is not a TTY (no human to answer) — the
    caller should require ``--yes`` in that case.
    """
    if not sys.stdin.isatty():
        return False
    try:
        answer = input(f"{prompt} ").strip().lower()
    except EOFError:
        return False
    return answer == "yes"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="migrate_to_postgres",
        description="Copy data from a SQLite database into Postgres.",
    )
    p.add_argument(
        "--source",
        default=os.environ.get("DATABASE_URL"),
        help=(
            "Source SQLAlchemy URL. Defaults to $DATABASE_URL. "
            "SQLite URLs are auto-wrapped in mode=ro to prevent writes."
        ),
    )
    p.add_argument(
        "--target",
        default=os.environ.get("POSTGRES_URL"),
        help=(
            "Target SQLAlchemy URL (typically postgresql://...). "
            "Defaults to $POSTGRES_URL."
        ),
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Rows per INSERT batch (default: 500).",
    )
    p.add_argument(
        "--truncate",
        action="store_true",
        help=(
            "Truncate every target table before copying. "
            "Use this to re-run the migration after a failed first attempt. "
            "Refuses to run unless --yes is also set."
        ),
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt (required in CI / cron).",
    )
    p.add_argument(
        "--no-verify",
        action="store_true",
        help=(
            "Skip the post-copy row-count + sample-row verification. "
            "Faster on huge tables, but you should run "
            "tools/verify_orm_models.py against the target afterwards."
        ),
    )
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_argparser().parse_args(argv)

    if not args.source:
        _fail("No source URL set. Pass --source or set DATABASE_URL.")
        return 1
    if not args.target:
        _fail("No target URL set. Pass --target or set POSTGRES_URL.")
        return 1
    if args.source == args.target:
        _fail("Source and target are identical — refusing to run.")
        return 1
    if args.batch_size < 1:
        _fail("--batch-size must be >= 1.")
        return 1

    if args.truncate and not args.yes:
        _fail(
            "--truncate is destructive and requires --yes to confirm "
            "(use --yes only when you are sure)."
        )
        return 1

    print(_bold("TecnoGems — SQLite → Postgres data migration"))
    print(f"source: {args.source}")
    print(f"target: {args.target}")
    print(f"batch:  {args.batch_size}")
    print(f"flags:  truncate={args.truncate}  no_verify={args.no_verify}")

    source_engine = _make_source_engine(args.source)
    target_engine = _make_target_engine(args.target)

    # ---- 1. Verify target schema -----------------------------------------
    _section("Target schema")
    ok, missing = _verify_target_schema(target_engine)
    if not ok:
        _fail(
            f"Target is missing tables: {missing}. "
            f"Run `alembic upgrade head` against the target first."
        )
        return 1
    for tname in TABLE_ORDER:
        _ok(f"{tname:<18} present")

    # ---- 2. Schema parity check ------------------------------------------
    _section("Schema parity (source vs ORM)")
    src_cols = _reflect_source_columns(source_engine)
    parity_errors = _check_column_parity(_orm_tables(), src_cols)
    if parity_errors:
        for err in parity_errors:
            _fail(err)
        _fail("Schema mismatch — aborting before any data is copied.")
        return 1
    _ok("All ORM-declared columns are present in the source.")

    # ---- 3. Pre-flight report --------------------------------------------
    _section("Pre-flight (source row counts)")
    total = 0
    for tname in TABLE_ORDER:
        c = _count_rows(source_engine, tname)
        total += c
        print(f"  {tname:<18} {c:>8}")
    print(f"  {_bold('total'):<27} {total:>8}")

    # ---- 4. Confirmation -------------------------------------------------
    if not args.yes:
        _section("Confirmation")
        if not _confirm(
            f"Copy {total} rows into {args.target}? Type 'yes' to proceed:"
        ):
            _warn("Aborted by operator (or non-interactive without --yes).")
            return 1

    # ---- 5. Optional truncate -------------------------------------------
    if args.truncate:
        _section("Truncate target tables")
        for tname in reversed(TABLE_ORDER):  # children first
            _truncate_target(target_engine, tname)
            _ok(f"{tname:<18} truncated")

    # ---- 6. Copy ---------------------------------------------------------
    _section("Copy")
    started = time.monotonic()
    copied = 0
    try:
        for tname in TABLE_ORDER:
            table = Base.metadata.tables[tname]
            copied += _copy_table(
                source_engine, target_engine, table, args.batch_size
            )
    except Exception as exc:  # noqa: BLE001
        _fail(f"Copy failed: {exc}")
        _fail(
            "Partial data may be committed. Drop+recreate the target schema "
            "(or re-run with --truncate --yes) before retrying."
        )
        return 2
    elapsed = time.monotonic() - started
    print()
    _ok(f"Copied {copied} rows in {elapsed:.2f}s.")

    # ---- 7. Reset Postgres sequences ------------------------------------
    _reset_postgres_sequences(target_engine)

    # ---- 8. Verify ------------------------------------------------------
    if args.no_verify:
        _warn("Skipped verification (--no-verify).")
    else:
        _section("Verify (row counts)")
        all_ok, rows = _verify_counts(source_engine, target_engine)
        _print_count_summary(rows)
        _section("Verify (sample row from users)")
        _print_sample_users(source_engine, target_engine)
        if not all_ok:
            _fail("Row counts differ between source and target.")
            return 2

    print()
    print(_green(_bold("Migration complete.")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
