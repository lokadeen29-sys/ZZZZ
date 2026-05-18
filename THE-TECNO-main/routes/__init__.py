"""V53 REFACTOR: Blueprint registration.

Phase 1 extracted ``auth_bp``. Phase 2 adds ``oauth_bp`` (Google OAuth client,
``google_oauth_enabled`` template flag, root-scope ``/sw.js`` service worker).
Future phases will register admin_bp, wallet_bp, public_bp, api_bp here —
without further edits to app.py.

Import is deferred into ``register_blueprints()`` on purpose: ``auth_bp.py``
does ``from app import …helpers…`` at module top, so it can only be imported
*after* all helpers in app.py have been defined. app.py therefore calls
``register_blueprints(app)`` at the very end of its module body.
"""
from __future__ import annotations


def register_blueprints(app, deps=None) -> None:
    """Register every extracted Blueprint on the given Flask app.

    ``deps`` is accepted (but unused today) for backward compatibility with
    the previous no-op signature.
    """
    # Phase 2: oauth_bp must be wired BEFORE auth_bp so that the Authlib
    # OAuth client is initialised by the time auth_bp's /auth/google view
    # imports get_oauth(). The actual import order between the two blueprint
    # modules doesn't matter (auth_bp uses a lazy in-function import), but
    # init_oauth(app) does need a live app, so we run it here.
    from .oauth_bp import bp as oauth_bp, init_oauth
    init_oauth(app)
    app.register_blueprint(oauth_bp)

    from .auth_bp import bp as auth_bp
    app.register_blueprint(auth_bp)
