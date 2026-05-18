"""SQLAlchemy + ORM layer for TecnoGems.

V72 — Postgres migration phase 1.

This package adds an ORM layer alongside the existing raw-SQL `database.py`.
The legacy module continues to work unchanged; nothing in the running app
uses these models yet. The first consumer will be `tools/verify_orm_models.py`
which only READS from the existing SQLite DB to validate the models match.

Design constraints (do not violate):

  1. No model field changes the existing schema. If `users.balance` is REAL
     in SQLite, it stays Float here. If `created_at` is INTEGER (unix epoch)
     it stays Integer here. We explicitly avoid SQLAlchemy DateTime mappings
     so the same models work against the live data without conversions.

  2. `DATABASE_URL` defaults to the existing `data/site.db` path so the ORM
     can be loaded inside the running app without breaking anything.
     Setting `DATABASE_URL=postgresql://...` flips it to Postgres later.

  3. No relationships are defined yet. They will come in later phases when
     we replace functions in `database.py` one at a time.
"""

from app.db.base import Base, DATABASE_URL, engine, SessionLocal
from app.db.session import get_session

__all__ = [
    "Base",
    "DATABASE_URL",
    "engine",
    "SessionLocal",
    "get_session",
]
