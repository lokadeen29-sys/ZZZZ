"""Helpers to convert ORM model instances into the plain ``dict`` shape
that legacy ``database.py`` callers expect.

Rationale
---------

For decades the rest of the codebase has consumed rows produced by

    sqlite3.Row → dict(row)

i.e. a flat ``dict`` with one entry per column. Templates iterate over
``method["address"]``, routes do ``user["balance"]``, etc. We are NOT
changing that contract in session 3 — only the implementation behind
the curtain.

This module provides a single :func:`row_to_dict` that takes any ORM
instance and returns the same shape, including:

* The native column name (``audit_log.metadata`` is exposed as
  ``"metadata"``, not the model attribute name ``"meta"``).
* No SQLAlchemy internals (``_sa_instance_state`` is filtered out).
* No relationships / collections — only mapped columns. We do not yet
  load relationships in session 3, so this is unambiguous.

Why not ``dataclasses.asdict`` or ``model.__dict__``?

* ``__dict__`` contains ``_sa_instance_state`` and unloaded attributes.
* ``asdict`` does not work on declarative SQLAlchemy classes.
* ``inspect(obj).mapper.columns`` gives us exactly the persisted columns
  with their DB names (the ``.key`` of each column object), even when
  the Python attribute uses an alias like ``meta`` for ``metadata``.
"""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import inspect as sa_inspect


def row_to_dict(obj: Any) -> dict:
    """Return a ``{column_name: value}`` dict for an ORM instance.

    The keys are the *DB-side* column names (``Column.key``) so they
    match what a raw ``sqlite3.Row`` produced, even when the model uses
    a Python alias (e.g. ``audit_log.metadata`` ↔ ``AuditLog.meta``).

    Returns ``{}`` if ``obj`` is falsy. Raises if ``obj`` is not a
    mapped instance — callers should guard with ``if obj is None``
    *before* calling this.
    """
    if obj is None:
        return {}
    mapper = sa_inspect(obj).mapper
    out: dict = {}
    for col in mapper.columns:
        # `col.key` is the Python attribute name on the model.
        # When `Column("metadata", Text)` is declared on `AuditLog.meta`,
        # `col.key` is "meta" but `col.name` is "metadata". Legacy callers
        # that came through `sqlite3.Row → dict` saw "metadata" — so we
        # mirror that and key the dict by `col.name`.
        out[col.name] = getattr(obj, col.key)
    return out


def rows_to_dicts(objs: Iterable[Any]) -> list[dict]:
    """Vectorised :func:`row_to_dict` for query result lists."""
    return [row_to_dict(o) for o in objs]
