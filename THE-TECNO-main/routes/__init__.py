"""V53 REFACTOR: Blueprint registration.

Phase 1 extracted ``auth_bp``. Phase 2 added ``oauth_bp`` (Google OAuth client,
``google_oauth_enabled`` template flag, root-scope ``/sw.js`` service worker).
Phase 3 added ``public_bp`` (storefront / SEO / legal pages / proof file
serving / language switcher) and ``wallet_bp`` (the user wallet + deposit
form + transactions list).
Phase 4 adds ``admin_2fa_bp`` (TOTP setup / confirm / challenge / disable /
backup-code regeneration) and ``admin_bp`` (every other ``/admin/*`` route:
dashboard, orders, users, balances, games, products, accounting, deposits,
payment methods, settings, the SMTP test button, and the manual SYP price
override form).

Future phases will register api_bp here — without further edits to app.py.

Import is deferred into ``register_blueprints()`` on purpose: every
extracted blueprint module does ``from app import …helpers…`` at module
top, so it can only be imported *after* all helpers in app.py have been
defined. app.py therefore calls ``register_blueprints(app)`` at the very
end of its module body.
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

    # Phase 3: public-facing routes (home/dashboard/products/checkout/profile/
    # orders/legal/sitemap/robots/manifest/language/legacy redirects/proof file
    # serving) and the wallet (deposit form + transactions list).
    #
    # public_bp is registered before wallet_bp so the storefront surface area
    # comes online first; the order is otherwise free since the URL spaces
    # don't overlap. Both blueprints use namespaced endpoint names
    # (``public.home``, ``wallet.wallet``, …) following the Phase 2
    # auth_bp convention.
    from .public_bp import bp as public_bp
    app.register_blueprint(public_bp)

    from .wallet_bp import bp as wallet_bp
    app.register_blueprint(wallet_bp)

    # Phase 4: split admin into two blueprints. ``admin_2fa_bp`` is registered
    # *before* ``admin_bp`` so the 2FA endpoint names ("admin_2fa.setup",
    # "admin_2fa.challenge", …) resolve when ``admin_required`` (defined in
    # app.py) builds its whitelist of endpoints that must NOT be gated by
    # the 2FA challenge. URL spaces don't overlap so the order is otherwise
    # free, but registering 2FA first matches the dependency direction.
    from .admin_2fa_bp import bp as admin_2fa_bp
    app.register_blueprint(admin_2fa_bp)

    from .admin_bp import bp as admin_bp
    app.register_blueprint(admin_bp)
