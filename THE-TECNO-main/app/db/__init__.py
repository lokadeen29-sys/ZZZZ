"""SQLAlchemy + ORM layer for TecnoGems.

V72 — Postgres migration phases 1-3.

Phase 1 (session 1): added the ORM groundwork alongside the legacy raw-SQL
``database.py``. Nothing in the running app used these models yet.

Phase 2 (session 2): wired Alembic to ``Base.metadata`` for migrations.

Phase 3 (session 3, current): ``database.py`` functions are being rewritten,
PR by PR, to use ``get_session()`` + ORM models internally. Their public
signatures and return shapes do not change so callers (routes, services,
templates) stay untouched.

Design constraints (do not violate):

  1. No model field changes the existing schema. If ``users.balance`` is
     REAL in SQLite, it stays Float here. If ``created_at`` is INTEGER
     (unix epoch) it stays Integer here. We explicitly avoid SQLAlchemy
     DateTime mappings so the same models work against the live data
     without conversions.

  2. ``DATABASE_URL`` defaults to the existing ``data/site.db`` path so
     the ORM can be loaded inside the running app without breaking
     anything. Setting ``DATABASE_URL=postgresql://...`` flips it to
     Postgres later.

  3. Tests use :func:`reset_engine` after monkeypatching the env var so
     the module-level engine points at the per-test SQLite file.
"""

from app.db.base import (
    Base,
    DATABASE_URL,
    SessionLocal,
    engine,
    reset_engine,
)
from app.db.session import get_session

__all__ = [
    "Base",
    "DATABASE_URL",
    "engine",
    "SessionLocal",
    "get_session",
    "reset_engine",
]
