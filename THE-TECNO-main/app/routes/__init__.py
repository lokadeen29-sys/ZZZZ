"""V53 REFACTOR: Blueprint registration.

Phase 1 extracted ``auth_bp``. Phase 2 added ``oauth_bp`` (Google OAuth client,
``google_oauth_enabled`` template flag, root-scope ``/sw.js`` service worker).
Phase 3 added ``public_bp`` (storefront / SEO / legal pages / proof file
serving / language switcher) and ``wallet_bp`` (the user wallet + deposit
form + transactions list).
Phase 4 added ``admin_2fa_bp`` (TOTP setup / confirm / challenge / disable /
backup-code regeneration) and ``admin_bp`` (every other ``/admin/*`` route).
Phase 5 (this file's current state) adds ``api_bp`` (every ``/api/*`` JSON
endpoint plus the ``_api_origin_guard`` ``before_request`` hook).

Hook-order contract (also documented in design.md "Hook order"):

    1. lang_cookie_reset_v36   — registered in app.create_app()
    2. setup_once              — registered AFTER register_blueprints()
                                  via ``app.bootstrap.register_setup_once``
    3. _api_origin_guard       — blueprint-scoped, fires only for /api/*

Flask runs ``before_request`` hooks in registration order. ``api_bp``'s
own ``before_request`` is bound to the blueprint, not the app, so its
position in the global list is irrelevant — it only fires for
``/api/*`` traffic.

Import is deferred into :func:`register_blueprints` on purpose: every
blueprint module imports from ``app.utils.*`` / ``app.services.*`` /
``app.extensions``, all of which are available immediately after
``create_app`` finishes wiring extensions. We register blueprints last
so the live ``limiter``, ``csrf``, etc. are set when each blueprint's
``_rl(...)`` decorators run.
"""
from __future__ import annotations

from flask import Flask


def register_blueprints(app: Flask, deps=None) -> None:
    """Register every extracted Blueprint on the given Flask app.

    ``deps`` is accepted (but unused today) for backward compatibility
    with the previous no-op signature.
    """
    # ---------------------------------------------------------------
    # OAuth (must be wired BEFORE auth_bp so that the Authlib OAuth
    # client is initialised by the time auth_bp's /auth/google view
    # imports get_oauth().)
    # ---------------------------------------------------------------
    from .oauth_bp import bp as oauth_bp, init_oauth

    init_oauth(app)
    app.register_blueprint(oauth_bp)

    # ---------------------------------------------------------------
    # Auth (login / register / verify / reset / OAuth callback)
    # ---------------------------------------------------------------
    from .auth_bp import bp as auth_bp

    app.register_blueprint(auth_bp)

    # ---------------------------------------------------------------
    # Public storefront + wallet
    # ---------------------------------------------------------------
    from .public_bp import bp as public_bp

    app.register_blueprint(public_bp)

    from .wallet_bp import bp as wallet_bp

    app.register_blueprint(wallet_bp)

    # ---------------------------------------------------------------
    # Admin (2FA first so admin_required's whitelist resolves)
    # ---------------------------------------------------------------
    from .admin_2fa_bp import bp as admin_2fa_bp

    app.register_blueprint(admin_2fa_bp)

    from .admin_bp import bp as admin_bp

    app.register_blueprint(admin_bp)

    # ---------------------------------------------------------------
    # JSON API + origin guard + CSRF exemption
    # ---------------------------------------------------------------
    from .api_bp import bp as api_bp, init_csrf_exemption

    app.register_blueprint(api_bp)
    init_csrf_exemption()
