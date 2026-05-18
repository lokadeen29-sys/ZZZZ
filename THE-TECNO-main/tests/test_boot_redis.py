"""V53 — verify that production boot requires REDIS_URL.

These tests import the app module in a subprocess-like fashion (clearing
sys.modules) and assert that the expected RuntimeError fires when REDIS_URL
is missing or unreachable in production.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _clean_app_modules():
    """Remove cached app-related modules so a fresh import re-evaluates guards."""
    for mod in list(sys.modules):
        if mod in (
            "app", "database", "tasks", "providers", "security_2fa",
            "sync_products", "featured_games", "routes", "routes.lang_bp",
            "audit", "wsgi",
        ) or mod.startswith("routes."):
            sys.modules.pop(mod, None)


def test_production_requires_redis_url(tmp_path, monkeypatch):
    """FLASK_ENV=production without REDIS_URL → RuntimeError."""
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "x" * 50)
    monkeypatch.setenv("ADMIN_PASSWORD", "StrongTestPass123!")
    monkeypatch.setenv("ADMIN_EMAIL", "admin@test.local")
    monkeypatch.setenv("BASE_URL", "http://localhost")
    monkeypatch.delenv("REDIS_URL", raising=False)

    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", str(db_file))

    _clean_app_modules()
    # Patch database.DB_PATH before importing app
    import database
    database.DB_PATH = str(db_file)
    database._PRAGMAS_APPLIED = False

    with pytest.raises(RuntimeError, match="REDIS_URL is required in production"):
        import app  # noqa: F401


def test_development_works_without_redis_url(tmp_path, monkeypatch):
    """FLASK_ENV=development without REDIS_URL → no crash (graceful)."""
    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "test-" + "x" * 48)
    monkeypatch.setenv("ADMIN_PASSWORD", "TestAdminPass123!")
    monkeypatch.setenv("ADMIN_EMAIL", "admin@test.local")
    monkeypatch.setenv("BASE_URL", "http://localhost")
    monkeypatch.delenv("REDIS_URL", raising=False)

    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", str(db_file))

    _clean_app_modules()
    import database
    database.DB_PATH = str(db_file)
    database._PRAGMAS_APPLIED = False

    # Should import without raising
    import app as app_module  # noqa: F401
    assert app_module.app is not None
