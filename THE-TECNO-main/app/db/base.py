"""SQLAlchemy engine + session factory.

The engine is configured from `DATABASE_URL`:

  * Default: SQLite pointing at the existing `data/site.db` file. This is
    so the ORM can be loaded inside the running app without changing
    anything about how the DB is accessed.

  * `postgresql://...`: when set, the same code talks to Postgres instead.
    Switching is a one-line change in `.env` (no code rebuild needed).

Important pragmas / connect args:

  * SQLite needs `check_same_thread=False` because Flask shares
    connections across threads (gunicorn gthread workers).
  * `pool_pre_ping=True` makes the engine recycle dead connections
    transparently. Critical on Postgres where idle connections can be
    closed by the server / load balancer.
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
DATABASE_URL = os.getenv("DATABASE_URL", "").strip() or f"sqlite:///{_DEFAULT_SQLITE_PATH}"


# ---------------------------------------------------------------------------
# Engine
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


engine = create_engine(DATABASE_URL, **_build_engine_kwargs(DATABASE_URL))


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------
# autocommit=False  → explicit `commit()` is required, matches old code.
# autoflush=False   → keep behaviour predictable; we flush manually if needed.
# expire_on_commit=False  → after commit, attributes are still readable.
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    future=True,
)


# ---------------------------------------------------------------------------
# Declarative base — models inherit from this
# ---------------------------------------------------------------------------
Base = declarative_base()
