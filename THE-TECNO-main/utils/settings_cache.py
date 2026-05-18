"""V53 REFACTOR (phase 1): 30-second TTL cache around database.get_setting.

Originally lived at app.py:45-66. Extracted verbatim — same TTL, same
behaviour. The cache is process-local (a plain dict guarded by GIL); each
gunicorn worker keeps its own copy. That is intentional: settings change
rarely, and a 30-second eventual-consistency window across workers is
fine for site-theme / payment-method / pricing toggles.

Public symbols (consumed by app.py and the rest of the codebase):

- ``get_setting(key, default=None)`` — read-through cache.
- ``set_setting(key, value)`` — invalidates the entry then writes through.

Both forward to ``database.get_setting`` / ``database.set_setting``.
"""
from __future__ import annotations

import time as _time

from database import get_setting as _db_get_setting
from database import set_setting as _db_set_setting

# --- V35: in-memory settings cache (TTL 30s) to cut SQLite hits per request ---
_SETTINGS_CACHE: dict[str, tuple] = {}
_SETTINGS_TTL: float = 30.0


def get_setting(key, default=None):
    now = _time.time()
    hit = _SETTINGS_CACHE.get(key)
    if hit and hit[1] > now:
        val = hit[0]
        return val if val is not None else default
    val = _db_get_setting(key, default)
    _SETTINGS_CACHE[key] = (val, now + _SETTINGS_TTL)
    return val if val is not None else default


def set_setting(key, value):
    _SETTINGS_CACHE.pop(key, None)
    return _db_set_setting(key, value)


__all__ = ["get_setting", "set_setting"]
