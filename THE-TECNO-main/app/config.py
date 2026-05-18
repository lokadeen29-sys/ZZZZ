"""V53 REFACTOR (phase 5): centralised Flask configuration classes.

Three environments — ``DevConfig``, ``ProdConfig``, ``TestConfig`` —
selected at boot via the ``FLASK_ENV`` env var (or the ``config_name``
argument to :func:`app.create_app`).

Every value here used to live inline in ``app.py``. The behaviour is
preserved 1-for-1: same env var names, same defaults, same fail-fast
checks. Callers that do ``app.config["MAX_CONTENT_LENGTH"]`` etc. keep
working unchanged because the factory copies these onto ``app.config``.

The numeric / boolean knobs that were module-level globals in ``app.py``
(``MAX_PASSWORD_LEN``, ``MAX_DEPOSIT_USD``, …) are also exposed as class
attributes so the factory can copy them onto ``app.config`` and any
caller still doing ``from app import MAX_PASSWORD_LEN`` continues to
resolve via the re-exports in :mod:`app.__init__`.
"""
from __future__ import annotations

import os
from datetime import timedelta


# ---------------------------------------------------------------------------
# Helpers — env parsing
# ---------------------------------------------------------------------------
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Base config — values shared by every environment
# ---------------------------------------------------------------------------
class BaseConfig:
    """Defaults common to dev / prod / test.

    Subclasses override the security-sensitive switches.
    """

    # --- Public site URL & domain ------------------------------------------
    BASE_URL: str = os.getenv("BASE_URL", "https://tecnogems.com").rstrip("/")

    # --- Session cookie ----------------------------------------------------
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"
    # SESSION_COOKIE_SECURE is set per-env (True in prod, gated on https in dev).
    PERMANENT_SESSION_LIFETIME = timedelta(
        days=_env_int("SESSION_LIFETIME_DAYS", 7)
    )

    # --- Upload size cap (V50 SECURITY) -----------------------------------
    MAX_CONTENT_LENGTH: int = 5 * 1024 * 1024  # 5 MB

    # --- CSRF (Flask-WTF) --------------------------------------------------
    # No time limit so the token survives idle login forms — Flask-WTF binds
    # it to the session lifetime instead.
    WTF_CSRF_TIME_LIMIT = None
    WTF_CSRF_SSL_STRICT: bool = False  # overridden per env

    # --- i18n (Flask-Babel) ------------------------------------------------
    BABEL_DEFAULT_LOCALE: str = "ar"
    BABEL_SUPPORTED_LOCALES: list[str] = ["ar", "en"]

    # --- Compression (Flask-Compress) -------------------------------------
    COMPRESS_ALGORITHM: list[str] = ["br", "gzip"]
    COMPRESS_MIN_SIZE: int = 500
    COMPRESS_LEVEL: int = 6
    COMPRESS_BR_LEVEL: int = 5

    # --- App-level input length caps (V50 SECURITY) -----------------------
    MAX_PLAYER_ID_LEN: int = 64
    MAX_PASSWORD_LEN: int = 128
    MAX_EMAIL_LEN: int = 120
    MAX_NAME_LEN: int = 80
    MAX_PHONE_LEN: int = 32
    MAX_PROOF_TEXT_LEN: int = 2000
    MAX_DEPOSIT_USD: float = _env_float("MAX_DEPOSIT_USD", 10000.0)
    MAX_ADMIN_BALANCE: float = _env_float("MAX_ADMIN_BALANCE", 1_000_000.0)


# ---------------------------------------------------------------------------
# Per-environment configs
# ---------------------------------------------------------------------------
class DevConfig(BaseConfig):
    """Local development — permissive defaults, debug-friendly."""

    DEBUG: bool = True
    TESTING: bool = False
    # Cookie is HTTPS-only when BASE_URL is https://, otherwise plain http
    # so browsers actually accept the session cookie on http://127.0.0.1.
    SESSION_COOKIE_SECURE: bool = BaseConfig.BASE_URL.startswith("https://")
    # Dev stays permissive so http:// testing does not trip the Referer check.
    WTF_CSRF_SSL_STRICT: bool = False


class ProdConfig(BaseConfig):
    """Production — fail-fast on missing secrets, SSL-strict CSRF."""

    DEBUG: bool = False
    TESTING: bool = False
    SESSION_COOKIE_SECURE: bool = True
    # V50.2 LOW/MEDIUM: enforce Referer-host match for POSTs over HTTPS.
    WTF_CSRF_SSL_STRICT: bool = True


class TestConfig(BaseConfig):
    """Pytest fixtures — CSRF & limiter disabled in conftest.

    The test suite further patches the live ``app.config`` object inside
    the ``app`` fixture (see ``tests/conftest.py``), so anything that
    must absolutely be off is set there. This class just gives us a
    sensible starting point.
    """

    DEBUG: bool = False
    TESTING: bool = True
    SESSION_COOKIE_SECURE: bool = False
    WTF_CSRF_ENABLED: bool = False  # picked up by Flask-WTF
    WTF_CSRF_SSL_STRICT: bool = False


# ---------------------------------------------------------------------------
# Public selector
# ---------------------------------------------------------------------------
_CONFIG_BY_NAME: dict[str, type[BaseConfig]] = {
    "development": DevConfig,
    "dev": DevConfig,
    "production": ProdConfig,
    "prod": ProdConfig,
    "testing": TestConfig,
    "test": TestConfig,
}


def get_config(name: str | None = None) -> type[BaseConfig]:
    """Return the config class for the given name, or the env-derived default.

    Resolution:
      1. Explicit ``name`` argument (used by tests / CLI).
      2. ``FLASK_ENV`` env var (production / development).
      3. Fallback: :class:`DevConfig`.
    """
    key = (name or os.getenv("FLASK_ENV") or "development").lower().strip()
    return _CONFIG_BY_NAME.get(key, DevConfig)
