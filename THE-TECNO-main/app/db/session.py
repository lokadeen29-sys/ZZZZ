"""Context manager that yields a SQLAlchemy session and guarantees cleanup.

Mirrors ``database.db_conn()`` so the migration of each function is just:

  Before:
      with db_conn() as conn:
          row = conn.execute("SELECT ...").fetchone()
          return dict(row) if row else None

  After:
      with get_session() as s:
          obj = s.query(Model).filter_by(...).first()
          return _to_dict(obj) if obj else None

The session is closed even on exception. If an exception bubbles, the
session is rolled back first so we don't leak half-applied state.

Implementation note — why we re-resolve ``SessionLocal`` on every call:

The ``app.db.base`` module exposes ``SessionLocal`` as a *rebindable*
module attribute so test fixtures can call :func:`app.db.base.reset_engine`
after monkeypatching ``DATABASE_URL``. If we did

    from app.db.base import SessionLocal

at import time, that local reference would freeze to the original
engine and tests would silently keep talking to the production-default
``data/site.db``. Looking the attribute up on the module on each call
costs nothing measurable and makes the lifecycle correct.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session

from app.db import base as _base


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a SQLAlchemy session, rolling back + closing on exit."""
    session = _base.SessionLocal()
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
