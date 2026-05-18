"""Context manager that yields a SQLAlchemy session and guarantees cleanup.

Mirrors `database.db_conn()` so the migration of each function is just:

  Before:
      with db_conn() as conn:
          row = conn.execute("SELECT ...").fetchone()
          return dict(row) if row else None

  After:
      with get_session() as s:
          obj = s.query(Model).filter_by(...).first()
          return obj.to_dict() if obj else None

The session is closed even on exception. If an exception bubbles, the
session is rolled back first so we don't leak half-applied state.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session

from app.db.base import SessionLocal


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a SQLAlchemy session, rolling back + closing on exit."""
    session = SessionLocal()
    try:
        yield session
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            session.close()
        except Exception:
            pass
