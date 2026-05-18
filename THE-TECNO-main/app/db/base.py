"""SQLAlchemy engine + session factory.

The engine is configured from `DATABASE_URL`:

  * Default: SQLite pointing at the existing `data/site.db` file. This is
    so the ORM can be loaded inside the running app without changing
    anything about how the DB is accessed.

  * `postgresql://...`: when set, the same code talks to Postgres instead.
    Switching is a one-line change in `.env` (no code rebuild needed).

  * Bare filesystem path (no scheme): coerced to ``sqlite:///<path>``.
    This is purely a quality-of-life feature for the test harness which
    monkey-patches ``DATABASE_URL`` to a tmp file path; production code
    still ships a fully-qualified URL.

Important pragmas / connect args:

  * SQLite needs `check_same_thread=False` because Flask shares
    connections across threads (gunicorn gthread workers).
  * `pool_pre_ping=True` makes the engine recycle dead connections
    transparently. Critical on Postgres where idle connections can be
    closed by the server / load balancer.

Engine lifecycle:

  The engine and session factory are module-level singletons resolved at
  import time from the *current* environment. Tests that need to point
  the ORM at a different DB after import can call :func:`reset_engine`
  to dispose the old engine and rebuild it from the (now-monkeypatched)
  ``DATABASE_URL``.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


# ---------------------------------------------------------------------------
# DATABASE_URL resolution
# ---------------------------------------------------------------------------
# Precedence:
#   1. `DATABASE_URL` env var (set in .env on production).
#   2. Fallback: SQLite at the existing path used by `database.py`.
#
# We deliberately match `database.DB_PATH` so opening the same file from
# both raw sqlite3 and SQLAlchemy is safe (SQLite WAL allows concurrent
# readers + one writer).

_DEFAULT_SQLITE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "site.db",
)


def _resolve_database_url() -> str:
    """Read ``DATABASE_URL`` from env (or fall back to SQLite default).

    Accepts three forms:
      * ``""`` / unset → SQLite at ``data/site.db`` (default).
      * Anything containing ``://`` → used verbatim (postgresql://, sqlite:///, …).
      * Bare filesystem path → wrapped as ``sqlite:///<path>``. Used by
        the test harness which sets ``DATABASE_URL=/tmp/.../test.db``.
    """
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        return f"sqlite:///{_DEFAULT_SQLITE_PATH}"
    if "://" in raw:
        return raw
    # Bare path: assume SQLite. (Tests set DATABASE_URL to a raw tmp path.)
    return f"sqlite:///{raw}"


# ---------------------------------------------------------------------------
# Engine kwargs per backend
# ---------------------------------------------------------------------------
def _build_engine_kwargs(url: str) -> dict:
    """Return engine kwargs appropriate for the URL backend."""
    kwargs: dict = {"pool_pre_ping": True, "future": True}

    if url.startswith("sqlite"):
        # Flask + gunicorn share connections across threads. SQLite refuses
        # this by default; explicitly opt in.
        kwargs["connect_args"] = {"check_same_thread": False}
        # SQLite + WAL is plenty fast for the session pool we use, no need
        # for a large pool. Keep defaults.
    else:
        # Postgres: small pool sized for our 3-worker gunicorn (worker × 2).
        # Override via env if we scale up later.
        kwargs["pool_size"] = int(os.getenv("DB_POOL_SIZE", "5") or 5)
        kwargs["max_overflow"] = int(os.getenv("DB_MAX_OVERFLOW", "10") or 10)
        kwargs["pool_recycle"] = int(os.getenv("DB_POOL_RECYCLE", "1800") or 1800)

    return kwargs


# ---------------------------------------------------------------------------
# Engine + session factory (module-level, refreshable via reset_engine)
# ---------------------------------------------------------------------------
def _make_engine_and_factory(url: str):
    """Build a fresh engine + session factory for ``url``."""
    eng = create_engine(url, **_build_engine_kwargs(url))
    factory = sessionmaker(
        bind=eng,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )
    return eng, factory


DATABASE_URL = _resolve_database_url()
engine, SessionLocal = _make_engine_and_factory(DATABASE_URL)


def reset_engine() -> str:
    """Re-resolve ``DATABASE_URL`` and rebuild the engine + session factory.

    Disposes the old engine first so its pooled connections (if any) are
    closed cleanly. Returns the new URL for the convenience of tests that
    want to assert on it.

    Use this in test fixtures **after** monkeypatching the env var:

        monkeypatch.setenv("DATABASE_URL", str(tmp_db_file))
        from app.db.base import reset_engine
        reset_engine()

    Production code never calls this — the singletons are immutable for
    the lifetime of the gunicorn worker.
    """
    global DATABASE_URL, engine, SessionLocal
    try:
        engine.dispose()
    except Exception:
        # `dispose` is best-effort; never let a teardown error propagate.
        pass
    DATABASE_URL = _resolve_database_url()
    engine, SessionLocal = _make_engine_and_factory(DATABASE_URL)
    return DATABASE_URL


# ---------------------------------------------------------------------------
# Declarative base — models inherit from this
# ---------------------------------------------------------------------------
Base = declarative_base()
