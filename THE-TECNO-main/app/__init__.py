"""V53 REFACTOR (phase 5): Application Factory.

This package replaces the legacy 1500-line ``app.py`` monolith. The
top-level entry points are:

* :func:`create_app` — build and return a configured Flask app.
* ``app`` (module-level instance, created via ``app = create_app()``) —
  preserved so ``wsgi.py``'s ``from app import app, init_db, ...`` keeps
  working without any change to the deployment glue.

Layout (also documented in ``.kiro/specs/app-refactor/design.md``):

    app/
      __init__.py        # this file — create_app() + module-level app
      config.py          # DevConfig / ProdConfig / TestConfig
      extensions.py      # csrf / limiter / babel / compress (single instances)
      middleware.py      # lang reset, error handlers, CSP, gzip
      bootstrap.py       # @before_request setup_once
      services/          # pricing, mail, images, game_images, orders
      utils/             # auth, security, settings_cache, i18n, filters
      routes/            # auth_bp, public_bp, wallet_bp, admin_bp, …, api_bp

What stays at top level (unchanged): ``wsgi.py``, ``database.py``,
``providers.py``, ``tasks.py``, ``audit.py``, ``request_ip.py``,
``security_2fa.py``, ``sanitize.py``.

The factory is callable multiple times for tests; the module-level
``app`` is *one* of those calls (the production singleton). The Phase 5
PR carefully avoids module-level side-effects beyond that single
instantiation so the test suite keeps working unchanged.
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import Optional

from dotenv import load_dotenv
from flask import Flask, g

from app.config import BaseConfig, get_config
from app.extensions import init_extensions
from app.middleware import (
    lang_cookie_reset_v36,
    register_middleware,
)


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("tecnogems")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def create_app(config_name: Optional[str] = None) -> Flask:
    """Build and return a fully-configured Flask app.

    The boot order is preserved from the legacy ``app.py``:

      1. Construct the Flask app + apply ProxyFix.
      2. Resolve the secret key (with the dev-friendly persistence trick).
      3. Apply the per-environment config class.
      4. Initialise extensions (CSRF, Babel, Limiter, Compress).
      5. Boot-time Redis ping (fail-fast in production).
      6. Register middleware (error handlers, after_request hardening).
      7. Register Jinja filters + the user-context processor.
      8. Register every blueprint via :func:`app.routes.register_blueprints`.
      9. Register the lazy ``setup_once`` hook (DB init / seed_admin / etc.).

    Tests pass ``config_name="testing"`` to short-circuit the production
    fail-fast checks; production ``wsgi.py`` lets ``FLASK_ENV`` drive it.
    """
    app = Flask(
        __name__,
        # The package lives at <repo_root>/app/, but the templates and
        # static folder live at the repo root (legacy layout). Point Flask
        # at them so we don't have to move thousands of files.
        template_folder=os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates",
        ),
        static_folder=os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "static",
        ),
    )

    # --- 1. ProxyFix -------------------------------------------------------
    # V53.1: must run BEFORE any other middleware so request.remote_addr is
    # the real client IP under Cloudflare/Heroku.
    from request_ip import apply_proxy_fix

    apply_proxy_fix(app)

    # --- 2. Secret key -----------------------------------------------------
    _apply_secret_key(app)

    # --- 3. Config ---------------------------------------------------------
    app.config.from_object(get_config(config_name))
    # The legacy app.py also kept BASE_URL as a module global so a few
    # callers do `from app import BASE_URL`. Mirror it via the re-exports
    # at the bottom of this file; the value is read once from BaseConfig
    # which itself reads the env var.
    app.config.setdefault("BASE_URL", BaseConfig.BASE_URL)

    # --- 4. Observability (Sentry + JSON logs) ----------------------------
    # Both are env-gated; calling them with no env is a cheap no-op.
    from audit import init_json_logging, init_sentry

    init_json_logging()
    init_sentry()

    # --- 5. Extensions -----------------------------------------------------
    redis_url = os.getenv("REDIS_URL", "").strip() or None
    _enforce_redis_in_production(redis_url)
    _ping_redis(redis_url)
    init_extensions(app, redis_url=redis_url)

    # If CSRFProtect was unavailable, install the no-op csrf_token() so
    # templates don't crash. (Same fallback the legacy app.py shipped.)
    from app import extensions as _ext

    if _ext.csrf is None:
        @app.context_processor
        def _csrf_noop():
            return {"csrf_token": lambda: ""}

    # --- 6. Uploads folder migration (V50 SECURITY H4) --------------------
    _ensure_upload_folder(app)

    # --- 7. Middleware -----------------------------------------------------
    # Language-cookie reset MUST be the very first before_request hook (see
    # design.md "Hook order" table). We attach it directly on the app
    # *before* register_blueprints so blueprints' before_request hooks
    # (e.g. api_bp's _api_origin_guard) run after it.
    app.before_request(lang_cookie_reset_v36)
    register_middleware(app)

    # --- 8. Jinja filters + context processors ----------------------------
    _register_jinja(app)

    # --- 9. Blueprints + lazy setup_once ----------------------------------
    from app.routes import register_blueprints

    register_blueprints(app)

    # The setup_once hook MUST be registered *after* register_blueprints so
    # the before_request order is:
    #   1. lang_cookie_reset_v36       (registered above, runs first)
    #   2. setup_once                  (registered just below)
    #   3. _api_origin_guard           (registered as part of api_bp)
    # Flask runs hooks in registration order; api_bp's before_request is
    # blueprint-scoped (only fires for /api/* requests) so its position in
    # the list is moot.
    from app.bootstrap import register_setup_once

    register_setup_once(app)

    return app


# ---------------------------------------------------------------------------
# Helpers — kept private to this module
# ---------------------------------------------------------------------------
# NOTE: These MUST be defined before `app = create_app()` below because
# create_app() calls them during module load. Moving them after the
# singleton causes NameError at import time on a fresh Python process.
def _apply_secret_key(app: Flask) -> None:
    """Resolve and set ``app.secret_key``.

    Dev-only convenience: persist a randomly-generated secret to
    ``.secret_key`` so CSRF tokens / sessions survive a process restart
    without the developer manually setting ``SECRET_KEY``. Production
    refuses to boot without an explicit, non-default secret.
    """
    secret = os.getenv("SECRET_KEY", "")
    weak_dev_defaults = ("dev-secret-change-me", "change-this-secret-key")
    if not secret or secret in weak_dev_defaults:
        if os.getenv("FLASK_ENV") == "production":
            raise RuntimeError(
                "SECRET_KEY is missing or default. Set a strong "
                "SECRET_KEY in .env before running in production."
            )
        log.warning(
            "Using development SECRET_KEY. "
            "Set a strong SECRET_KEY in .env for production."
        )
        # Persist a stable secret in the dev workspace so CSRF/sessions
        # survive a restart. Never write to disk in production
        # (containers / Heroku have ephemeral filesystems).
        secret_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".secret_key",
        )
        if not secret:
            try:
                if os.path.exists(secret_file):
                    with open(secret_file, "r", encoding="utf-8") as fh:
                        secret = fh.read().strip()
                if not secret:
                    secret = secrets.token_urlsafe(48)
                    try:
                        with open(secret_file, "w", encoding="utf-8") as fh:
                            fh.write(secret)
                    except OSError:
                        log.warning(
                            ".secret_key file not writable; "
                            "secret is ephemeral this session."
                        )
            except Exception:
                secret = secret or secrets.token_urlsafe(48)
    app.secret_key = secret


def _enforce_redis_in_production(redis_url: str | None) -> None:
    """Refuse to boot without REDIS_URL when FLASK_ENV=production.

    V53 CRITICAL: in-memory Redis fallbacks create three real outages in
    production — rate limits desync across workers, RQ jobs are lost on
    restart, and the settings cache silently drifts for 30 seconds. So
    we fail-fast.
    """
    if os.getenv("FLASK_ENV") == "production" and not redis_url:
        raise RuntimeError(
            "REDIS_URL is required in production. "
            "Set it to a valid redis:// URL (Upstash/Railway/Redis Cloud) "
            "or explicitly set FLASK_ENV=development for local testing."
        )


def _ping_redis(redis_url: str | None) -> None:
    """Verify Redis is reachable at boot — fail-hard in prod, warn in dev."""
    if not redis_url:
        return
    try:
        import redis as _redis_lib

        r = _redis_lib.from_url(redis_url, socket_connect_timeout=3)
        r.ping()
        log.info("Redis reachable at %s", redis_url.split("@")[-1])
    except Exception as exc:
        if os.getenv("FLASK_ENV") == "production":
            raise RuntimeError(f"Cannot reach REDIS_URL: {exc}") from exc
        log.warning("Redis unreachable (dev mode — continuing): %s", exc)


def _ensure_upload_folder(app: Flask) -> None:
    """Create ``data/uploads`` and migrate any legacy ``static/uploads`` files.

    V50 SECURITY (H4): proof images live OUTSIDE ``static/`` so the public
    static handler cannot bypass ``@login_required`` on
    ``/uploads/proof/<file>``. We move any straggling files left over
    from the pre-V50 layout exactly once.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    upload_folder = os.path.join(repo_root, "data", "uploads")
    os.makedirs(upload_folder, exist_ok=True)
    legacy = os.path.join(repo_root, "static", "uploads")
    if os.path.isdir(legacy):
        try:
            for name in os.listdir(legacy):
                src = os.path.join(legacy, name)
                dst = os.path.join(upload_folder, name)
                if os.path.isfile(src) and not os.path.exists(dst):
                    try:
                        os.rename(src, dst)
                    except OSError:
                        pass
        except OSError:
            pass
    app.config["UPLOAD_FOLDER"] = upload_folder


def _register_jinja(app: Flask) -> None:
    """Register every Jinja filter and the global user-context processor."""
    # --- Filters (delegate to the extracted helpers in app.utils.filters) -
    from app.utils.filters import (
        clean_package_name,
        money,
        order_status_class,
        order_status_label,
        public_package_name_filter,
        syria_time,
    )

    app.template_filter("public_package_name")(public_package_name_filter)
    app.template_filter("syria_time")(syria_time)
    app.template_filter("money")(money)
    app.template_filter("clean_package_name")(clean_package_name)
    app.template_filter("order_status_label")(order_status_label)
    app.template_filter("order_status_class")(order_status_class)

    # --- Context processor — replaces the inject_user() in legacy app.py --
    from app.services.game_images import game_image_url, smart_game_image_url
    from app.services.pricing import (
        display_price_text,
        get_display_currency,
        get_pricing_mode,
        manual_price_edit_enabled,
        product_profit_percent,
        product_public_price,
        product_sell_usd,
        wallet_money_text,
    )
    from app.utils.auth import current_user
    from app.utils.i18n import current_lang, lang_url, tr
    from app.utils.settings_cache import get_setting

    @app.context_processor
    def inject_user():
        # PATCH-M2/C4: per-request CSP nonce. The middleware also sets
        # this defensively, but the context processor is the canonical
        # source so templates can reference {{ csp_nonce }}.
        nonce = getattr(g, "_csp_nonce", None)
        if not nonce:
            nonce = secrets.token_urlsafe(16)
            g._csp_nonce = nonce
        return {
            "current_user": current_user(),
            "site_theme": get_setting("site_theme", "theme-aurora"),
            "nav_mode": get_setting("nav_mode", "menu"),
            "show_groups_direct": get_setting("show_groups_direct", "0"),
            "old_games_layout": get_setting("old_games_layout", "0"),
            "display_currency": get_display_currency(),
            "display_price": display_price_text,
            "pricing_mode": get_pricing_mode(),
            "manual_price_edit_enabled": manual_price_edit_enabled(),
            "wallet_money": wallet_money_text,
            "product_sell_usd": product_sell_usd,
            "whatsapp_number": get_setting("whatsapp_number", ""),
            "telegram_username": get_setting("telegram_username", ""),
            "lang": current_lang(),
            "is_en": current_lang() == "en",
            "t": tr,
            "lang_url": lang_url,
            "product_price": product_public_price,
            "csp_nonce": nonce,
            "product_profit_percent": product_profit_percent,
            "smart_game_image": smart_game_image_url,
            "game_image": game_image_url,
        }


# ---------------------------------------------------------------------------
# Module-level singleton — what wsgi.py imports
# ---------------------------------------------------------------------------
app = create_app()


# ---------------------------------------------------------------------------
# wsgi.py compatibility re-exports
# ---------------------------------------------------------------------------
# wsgi.py does:
#     from app import app, init_db, ensure_indexes, seed_admin,
#                     seed_local_provider_catalog, attach_generated_posters
# Re-export them here so the deployment glue keeps working without changes.
from database import (  # noqa: E402,F401  (re-exported for wsgi.py)
    init_db,
    ensure_indexes,
    seed_admin,
    seed_local_provider_catalog,
    attach_generated_posters,
)


# ---------------------------------------------------------------------------
# Public re-exports — see legacy app.py for the historical surface area
# ---------------------------------------------------------------------------
# These keep `from app import X` working for the test suite and for any
# external scripts. They are *thin* — they import from the appropriate
# submodule, so the bridge in old phases (the "transitional re-export"
# block of comments in legacy app.py) is no longer needed.

# Length / dollar caps — same names as the legacy module-level constants.
MAX_PLAYER_ID_LEN = BaseConfig.MAX_PLAYER_ID_LEN
MAX_PASSWORD_LEN = BaseConfig.MAX_PASSWORD_LEN
MAX_EMAIL_LEN = BaseConfig.MAX_EMAIL_LEN
MAX_NAME_LEN = BaseConfig.MAX_NAME_LEN
MAX_PHONE_LEN = BaseConfig.MAX_PHONE_LEN
MAX_PROOF_TEXT_LEN = BaseConfig.MAX_PROOF_TEXT_LEN
MAX_DEPOSIT_USD = BaseConfig.MAX_DEPOSIT_USD
MAX_ADMIN_BALANCE = BaseConfig.MAX_ADMIN_BALANCE
BASE_URL = BaseConfig.BASE_URL

# Auth / security helpers — used by tests and (transitively) admin_bp.py.
from app.utils.auth import (  # noqa: E402,F401
    admin_required,
    current_user,
    login_required,
)
from app.utils.security import (  # noqa: E402,F401
    safe_next_url,
    validate_password_strength,
)


# ---------------------------------------------------------------------------
# PEP 562: lazy attribute lookup for live extension instances.
#
# ``app.extensions.limiter`` (and friends) is bound to ``None`` at module
# load time and reassigned by ``init_extensions(app)`` inside the factory.
# A regular ``from app.extensions import limiter`` at import time would
# therefore pin the name to ``None`` forever. PEP 562 lets us defer the
# lookup to attribute-access time so callers see the live, post-init
# instance.
#
# This keeps tests like ``app_module.limiter.enabled = False`` working
# unchanged.
# ---------------------------------------------------------------------------
def __getattr__(name: str):
    if name in ("limiter", "csrf", "babel", "compress"):
        from app import extensions as _ext
        return getattr(_ext, name)
    raise AttributeError(f"module 'app' has no attribute {name!r}")

__all__ = [
    "create_app",
    "app",
    "log",
    # length / money caps
    "MAX_PLAYER_ID_LEN",
    "MAX_PASSWORD_LEN",
    "MAX_EMAIL_LEN",
    "MAX_NAME_LEN",
    "MAX_PHONE_LEN",
    "MAX_PROOF_TEXT_LEN",
    "MAX_DEPOSIT_USD",
    "MAX_ADMIN_BALANCE",
    "BASE_URL",
    # auth / security
    "current_user",
    "login_required",
    "admin_required",
    "safe_next_url",
    "validate_password_strength",
    # wsgi-side helpers
    "init_db",
    "ensure_indexes",
    "seed_admin",
    "seed_local_provider_catalog",
    "attach_generated_posters",
]
