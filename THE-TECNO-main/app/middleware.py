"""V53 REFACTOR (phase 5): request/response middleware extracted from app.py.

Owns four concerns previously inlined in the monolith:

1. ``lang_cookie_reset_v36``  — V36 fix that resets a stale ``lang=en``
   cookie when the user has not explicitly opted into English.
2. Error handlers              — CSRF errors (302 → home with flash),
   404, and 500 (rendered through the existing templates).
3. Cache-control + security headers (CSP nonce, HSTS, COEP/CORP/COOP,
   form-action, etc.) — applied as an ``after_request`` hook.
4. ``gzip_text_response``      — manual gzip fallback for when
   Flask-Compress is unavailable; preserves legacy size threshold (>1KB).

Each helper is registered explicitly inside :func:`register_middleware`.
The factory calls this *after* extensions are bound, *after* blueprints
are registered, so the after_request runs last and sees the rendered
response.
"""
from __future__ import annotations

import gzip
import secrets
from typing import Any

from flask import (
    Flask,
    Response,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
)


# ---------------------------------------------------------------------------
# Public — language-cookie reset (runs FIRST in the before_request chain)
# ---------------------------------------------------------------------------
def lang_cookie_reset_v36() -> None:
    """V36: reset a stale ``lang=en`` selection that the user did not pick.

    Some old browser cookies kept users locked on English forever. The
    fix is to detect the case where ``session["lang"] == "en"`` but the
    explicit-opt-in marker (``lang_user_selected``) is missing, and
    revert silently to Arabic.
    """
    if session.get("lang") == "en" and session.get("lang_user_selected") != "1":
        session["lang"] = "ar"
        session.pop("lang_user_selected", None)


# ---------------------------------------------------------------------------
# Public — gzip helper for HTML/CSS/JS when Flask-Compress is missing
# ---------------------------------------------------------------------------
def gzip_text_response(response: Response) -> Response:
    """Manual gzip pass for text-y responses larger than 1 KB."""
    try:
        accept = request.headers.get("Accept-Encoding", "")
        ctype = response.headers.get("Content-Type", "")
        if (
            "gzip" in accept
            and response.status_code == 200
            and not response.direct_passthrough
            and "Content-Encoding" not in response.headers
            and any(
                t in ctype
                for t in (
                    "text/html",
                    "text/css",
                    "application/javascript",
                    "text/javascript",
                    "application/json",
                )
            )
        ):
            data = response.get_data()
            if len(data) > 1024:
                gz = gzip.compress(data, compresslevel=5)
                if len(gz) < len(data):
                    response.set_data(gz)
                    response.headers["Content-Encoding"] = "gzip"
                    response.headers["Content-Length"] = str(len(gz))
                    response.headers["Vary"] = "Accept-Encoding"
    except Exception:
        pass
    return response


# ---------------------------------------------------------------------------
# Internal — security + cache headers
# ---------------------------------------------------------------------------
def _add_cache_and_security_headers(response: Response) -> Response:
    """Match the legacy app.py after_request hook line-for-line."""
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable"
        )
    elif (
        request.path.startswith("/admin")
        or request.path.startswith("/login")
        or request.path.startswith("/register")
        or request.path.startswith("/forgot")
        or request.path.startswith("/reset")
        or request.method == "POST"
    ):
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        response.headers["Pragma"] = "no-cache"
    else:
        # V67.1 BUGFIX: logged-in users get no-store so their balance and
        # navbar reflect reality. Anonymous traffic gets a tiny private
        # cache to ease bursty traffic without serving stale logged-in
        # navbars.
        if session.get("user_id"):
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, max-age=0"
            )
            response.headers["Pragma"] = "no-cache"
            response.headers["Vary"] = "Cookie"
        else:
            response.headers.setdefault("Cache-Control", "private, max-age=30")
            response.headers.setdefault("Vary", "Cookie")

    # --- Hardening headers (preserved verbatim) -------------------------
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=()"
    )
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-site"

    # PATCH-C4: nonce-based CSP — the nonce is set per-request inside
    # ``inject_user`` (the user-context processor) and read here.
    nonce = getattr(g, "_csp_nonce", "")
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "connect-src 'self'; "
        "frame-ancestors 'self'; "
        "frame-src 'none'; "
        "object-src 'none'; "
        "form-action 'self'; "
        "base-uri 'self'; "
        "upgrade-insecure-requests;"
    )
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains; preload"
        )
    return gzip_text_response(response)


# ---------------------------------------------------------------------------
# Internal — error handlers
# ---------------------------------------------------------------------------
def _register_error_handlers(app: Flask) -> None:
    # Imported lazily because Flask-WTF is optional; if it's missing we
    # skip registering the CSRFError handler altogether.
    try:
        from flask_wtf.csrf import CSRFError  # type: ignore

        @app.errorhandler(CSRFError)
        def handle_csrf_error(_e: Any):
            from app.utils.security import safe_next_url

            flash("انتهت صلاحية الصفحة، يرجى إعادة المحاولة.", "warning")
            return redirect(safe_next_url("public.home"))
    except Exception:
        pass

    @app.errorhandler(404)
    def not_found(_e: Any):
        return render_template("404.html", title="404 - الصفحة غير موجودة"), 404

    @app.errorhandler(500)
    def server_error(_e: Any):
        return render_template("500.html", title="500 - خطأ في الخادم"), 500


# ---------------------------------------------------------------------------
# Internal — CSP nonce ensures /every/ response has one available
# ---------------------------------------------------------------------------
def _ensure_csp_nonce() -> None:
    """Make sure ``g._csp_nonce`` exists for the after_request hook.

    The user-context processor (``inject_user``) also seeds the nonce so
    templates can reference ``{{ csp_nonce }}``. This second hook is a
    safety net: it covers responses that bypass the context processor
    (e.g. ``/api/*`` JSON endpoints, ``send_from_directory``).
    """
    if not getattr(g, "_csp_nonce", None):
        g._csp_nonce = secrets.token_urlsafe(16)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def register_middleware(app: Flask) -> None:
    """Attach error handlers and the cache/security after_request hook.

    The ``before_request`` chain (lang reset → setup_once → api origin
    guard) is wired by :func:`app.routes.register_blueprints` because
    ordering matters and the API guard is logically owned by the API
    blueprint.
    """
    # Per-request CSP nonce (defensive — the context processor sets it too).
    app.before_request(_ensure_csp_nonce)
    # Error handlers (CSRF, 404, 500).
    _register_error_handlers(app)
    # Cache + security headers + manual gzip.
    app.after_request(_add_cache_and_security_headers)
