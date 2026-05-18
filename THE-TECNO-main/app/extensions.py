"""V53 REFACTOR (phase 5): single-instance Flask extensions.

Extensions are instantiated **once at import time** and bound to the app
in :func:`init_extensions` (called by :func:`app.create_app`). This is
the canonical Application-Factory pattern: blueprints / services
``from app.extensions import csrf, limiter, babel, compress`` and operate
on the shared instance regardless of which Flask app is live.

Failure to import an extension dependency is non-fatal — the variable is
left as ``None`` and a warning is emitted. Callers are expected to guard
``if limiter is not None`` (this matches the legacy ``app.py`` behaviour
that the routes blueprints already follow with their ``_rl()`` shim).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from flask import Flask

log = logging.getLogger("tecnogems.extensions")


# ---------------------------------------------------------------------------
# Extension instances (created lazily / module-level once)
# ---------------------------------------------------------------------------
csrf = None       # type: Optional[object]   # CSRFProtect
limiter = None    # type: Optional[object]   # Flask-Limiter
babel = None      # type: Optional[object]   # Flask-Babel
compress = None   # type: Optional[object]   # Flask-Compress


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------
def _init_csrf(app: Flask) -> None:
    global csrf
    try:
        from flask_wtf import CSRFProtect
        csrf = CSRFProtect(app)
    except Exception:
        csrf = None
        log.warning(
            "Flask-WTF not installed. CSRF protection disabled. "
            "Run: pip install Flask-WTF"
        )


# ---------------------------------------------------------------------------
# Babel (i18n)
# ---------------------------------------------------------------------------
def _select_locale():
    """Locale resolver — session > cookie > Arabic default.

    Identical to the inline lambda used in the legacy ``app.py``.
    """
    from flask import session, request
    return session.get("lang") or request.cookies.get("lang") or "ar"


def _init_babel(app: Flask) -> None:
    global babel
    try:
        from flask_babel import Babel, gettext as _babel_gettext
        app.config.setdefault("BABEL_DEFAULT_LOCALE", "ar")
        app.config.setdefault("BABEL_SUPPORTED_LOCALES", ["ar", "en"])
        babel = Babel(app, locale_selector=_select_locale)
        # Make Babel's gettext callable from every Jinja template under the
        # familiar ``_(...)`` / ``gettext(...)`` aliases.
        app.jinja_env.globals["_"] = _babel_gettext
        app.jinja_env.globals["gettext"] = _babel_gettext
    except Exception as exc:
        babel = None
        log.warning(
            "Flask-Babel not installed; templates fall back to legacy tr(). %s",
            exc,
        )


# ---------------------------------------------------------------------------
# Limiter
# ---------------------------------------------------------------------------
def _init_limiter(app: Flask, redis_url: str | None) -> None:
    """Flask-Limiter, keyed on the *real* client IP (CF-Connecting-IP aware).

    Behaviour matches legacy ``app.py``:
      - Redis backend when ``REDIS_URL`` is set (shared limits across workers).
      - In-memory fallback otherwise (per-worker; emits a warning).
      - Key func is ``request_ip.get_real_ip`` so Cloudflare / Heroku does
        not collapse every visitor onto the proxy's IP.
    """
    global limiter
    try:
        from flask_limiter import Limiter
        from request_ip import get_real_ip

        kwargs: dict = {"app": app, "default_limits": []}
        if redis_url:
            kwargs["storage_uri"] = redis_url
            kwargs["strategy"] = "fixed-window"
        limiter = Limiter(get_real_ip, **kwargs)
        if redis_url:
            log.info("Flask-Limiter using Redis storage backend.")
        else:
            log.warning(
                "Flask-Limiter using in-memory storage — limits are per-worker "
                "and cleared on restart. Set REDIS_URL for shared limits."
            )
    except Exception:
        limiter = None
        log.warning(
            "Flask-Limiter not installed. Rate limiting disabled. "
            "Run: pip install Flask-Limiter"
        )


# ---------------------------------------------------------------------------
# Compress (br + gzip)
# ---------------------------------------------------------------------------
def _init_compress(app: Flask) -> None:
    global compress
    try:
        from flask_compress import Compress
        # Defaults already set on app.config via app.config classes; copy to
        # be explicit in case someone uses BaseConfig.from_object overrides.
        app.config.setdefault("COMPRESS_ALGORITHM", ["br", "gzip"])
        app.config.setdefault("COMPRESS_MIN_SIZE", 500)
        app.config.setdefault("COMPRESS_LEVEL", 6)
        app.config.setdefault("COMPRESS_BR_LEVEL", 5)
        compress = Compress(app)
        log.info("Flask-Compress enabled (br, gzip)")
    except Exception as exc:
        compress = None
        log.warning(
            "Flask-Compress not installed (%s). Run: pip install Flask-Compress",
            exc,
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def init_extensions(app: Flask, redis_url: str | None = None) -> None:
    """Attach every extension to *app* in the order the legacy app.py used.

    Order matters:
      1. ``CSRFProtect``  — must wrap every ``@app.route`` before they exist
         (it does so internally via ``before_request``; binding here is fine
         because routes are registered later in ``create_app``).
      2. ``Babel``        — adds the ``_`` / ``gettext`` Jinja globals before
         any template renders.
      3. ``Limiter``      — needs ``REDIS_URL`` resolved and ``ProxyFix``
         already applied so ``get_real_ip`` returns the correct address.
      4. ``Compress``     — registers an ``after_request`` hook; safe last.
    """
    _init_csrf(app)
    _init_babel(app)
    _init_limiter(app, redis_url=redis_url or os.getenv("REDIS_URL", "").strip() or None)
    _init_compress(app)
