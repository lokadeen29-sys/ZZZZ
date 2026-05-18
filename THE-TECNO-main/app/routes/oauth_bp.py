"""V53 REFACTOR (phase 2): Google OAuth + service worker, extracted from app.py.

This Blueprint owns three previously-app-level concerns that all relate to
"client-side helpers wired onto the root URL space":

1. **Google OAuth client lifecycle** — the Authlib ``OAuth`` instance that
   ``auth_bp.auth_google_login`` / ``auth_bp.auth_google_callback`` reach
   into. The actual ``/auth/google`` and ``/auth/google/callback`` routes
   stay in ``auth_bp`` because they belong to the auth flow narrative.
2. **The ``google_oauth_enabled`` template flag** — exposed via an
   ``app_context_processor`` so ``templates/login.html`` and
   ``templates/register.html`` can conditionally render the "Sign in with
   Google" button.
3. **The root-scope service worker** — ``GET /sw.js`` must be served from
   the application root so it can claim the entire origin's scope. It is a
   single trivial route, so co-locating it here (rather than spawning a
   ``misc_bp.py`` for one route) follows the spec's note that the service
   worker can ride along with ``oauth_bp``.

Design notes
------------
- ``_oauth`` requires the *live* Flask app (Authlib's ``OAuth(app)`` ties
  itself to ``app.extensions``). We therefore initialise it lazily inside
  :func:`init_oauth` which is called by ``routes.register_blueprints(app)``
  *after* the app object exists. Until ``init_oauth`` runs, ``get_oauth()``
  returns ``None`` and the OAuth views in ``auth_bp`` fall back gracefully.
- ``GOOGLE_REDIRECT_URI`` defaults to ``f"{BASE_URL}/auth/google/callback"``
  exactly like the previous app.py snippet — no behaviour change.
- The ``_inject_oauth_flags`` context processor is registered as
  ``bp.app_context_processor`` so it applies app-wide (not just to this
  blueprint's templates), preserving the original ``@app.context_processor``
  semantics 1:1.
- All names ``app.py`` currently re-exports are still importable from this
  module: ``GOOGLE_CLIENT_ID``, ``GOOGLE_CLIENT_SECRET``,
  ``GOOGLE_REDIRECT_URI``, ``_oauth``. The transitional bridge in app.py
  re-exports them so any third-party caller doing ``from app import _oauth``
  keeps working until phase 5.
"""
from __future__ import annotations

import os

from flask import Blueprint, send_from_directory


# ---------------------------------------------------------------------------
# Module-level config (read once at import time, same as the original app.py)
# ---------------------------------------------------------------------------
GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()

# Computed lazily on first read so that BASE_URL changes (tests monkeypatch
# os.environ) are picked up. Falls back to BASE_URL + /auth/google/callback.
def _default_redirect_uri() -> str:
    base = os.getenv("BASE_URL", "https://tecnogems.com").rstrip("/")
    return f"{base}/auth/google/callback"


GOOGLE_REDIRECT_URI: str = (
    os.getenv("GOOGLE_REDIRECT_URI", "").strip() or _default_redirect_uri()
)

# Populated by init_oauth(); ``None`` means OAuth is disabled (env vars
# missing or Authlib import failed).
_oauth = None


bp = Blueprint("oauth", __name__)


# ---------------------------------------------------------------------------
# Public helpers (used by routes/auth_bp.py)
# ---------------------------------------------------------------------------
def get_oauth():
    """Return the live Authlib ``OAuth`` instance, or ``None`` if disabled."""
    return _oauth


def get_redirect_uri() -> str:
    """Return the OAuth callback URL configured for this deployment."""
    return GOOGLE_REDIRECT_URI


# ---------------------------------------------------------------------------
# OAuth client wiring
# ---------------------------------------------------------------------------
def init_oauth(app) -> None:
    """Bind the Authlib OAuth client to ``app`` if Google credentials are set.

    Safe to call multiple times — re-initialisation simply rebinds. Any
    failure (Authlib missing, registration error) is logged on the app
    logger and ``_oauth`` stays ``None`` so the rest of the site keeps
    working without Google sign-in.
    """
    global _oauth
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        _oauth = None
        return
    try:
        from authlib.integrations.flask_client import OAuth as _AuthlibOAuth
        oauth = _AuthlibOAuth(app)
        oauth.register(
            name="google",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            server_metadata_url=(
                "https://accounts.google.com/.well-known/openid-configuration"
            ),
            client_kwargs={"scope": "openid email profile"},
        )
        _oauth = oauth
    except Exception as exc:
        app.logger.warning("Google OAuth disabled: %s", exc)
        _oauth = None


# ---------------------------------------------------------------------------
# Template flag — replaces app.py's @app.context_processor _inject_oauth_flags
# ---------------------------------------------------------------------------
@bp.app_context_processor
def _inject_oauth_flags():
    return {"google_oauth_enabled": bool(_oauth)}


# ---------------------------------------------------------------------------
# Service Worker (root scope)
# ---------------------------------------------------------------------------
@bp.route("/sw.js")
def service_worker():
    resp = send_from_directory("static", "sw.js")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


__all__ = [
    "bp",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REDIRECT_URI",
    "init_oauth",
    "get_oauth",
    "get_redirect_uri",
]
