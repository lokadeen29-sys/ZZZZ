import os
import gzip
from io import BytesIO
from pathlib import Path
from queue import Queue  # PATCH-A1: must be imported BEFORE email_queue = Queue()

import re
from datetime import datetime, timezone, timedelta
import threading
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, formatdate, make_msgid
from werkzeug.utils import secure_filename

from functools import wraps
from dotenv import load_dotenv
from flask import Response, Flask, render_template, request, redirect, url_for, session, flash, abort, jsonify, send_from_directory
from markupsafe import Markup
from flask_wtf.csrf import CSRFProtect, CSRFError

from database import (
    InsufficientBalance,
    init_db, ensure_indexes, seed_admin, create_user, authenticate, get_user, get_user_by_email, verify_user_email, set_user_email_token, set_password_reset_token, get_user_by_reset_token, reset_user_password, list_games, list_products, list_public_games, list_all_game_groups, list_product_games_from_products, add_custom_game, set_game_active, set_game_show_on_home, set_game_home_sort_order, list_home_games, accounting_summary, list_product_groups, get_product_group, create_product_group, update_product_group, delete_product_group, update_products_admin, update_game_pricing, update_manual_syp_prices, translate_product_name, list_public_product_groups_for_home,
    get_product, get_game, create_order, update_order, list_user_orders, list_orders,
    get_order, stats, list_users, search_users, get_user_by_id, user_financial_summary, list_user_deposits_admin, update_user_profile, set_pending_email_change, confirm_pending_email_change, set_user_balance, change_balance,
    list_payment_methods, get_payment_method, update_payment_method,
    create_deposit, list_deposits, list_deposits_for_user, get_deposit, update_deposit, get_setting as _db_get_setting, set_setting as _db_set_setting,
    list_orders_for_auto_refresh, get_order_public,
    list_all_games_for_admin, update_game_image, list_all_products_for_admin, update_product_sort_orders, update_profit_margin, seed_local_provider_catalog, attach_generated_posters,
    # V51 task B: admin 2FA persistence helpers
    set_user_totp_secret, enable_user_totp, disable_user_totp, update_user_backup_codes,
    # V53: IDOR fix on proof downloads
    can_download_proof,
)

# V51 task B: TOTP 2FA helpers for admin accounts (opt-in per admin)
from security_2fa import (
    generate_totp_secret, provisioning_uri, qr_svg,
    verify_totp, generate_backup_codes,
    serialize_backup_codes, deserialize_backup_codes, consume_backup_code,
)

# V53 REFACTOR (phase 1): settings cache moved to utils/settings_cache.py.
# Re-exported here so existing `from app import get_setting` callers keep
# working until phase 5. The underlying `_db_get_setting` / `_db_set_setting`
# imports from database.py above remain so any direct user of those names
# in this module is unaffected.
from utils.settings_cache import get_setting, set_setting  # noqa: E402,F401
# Internal cache state — kept under the legacy names so the (very rare)
# direct touchers keep working transparently.
from utils.settings_cache import _SETTINGS_CACHE, _SETTINGS_TTL  # noqa: E402,F401
import time as _time  # legacy alias retained for any module importing app._time

from providers import create_provider_order, get_provider_balance, validate_player_provider
# V71: للـ in-process cache الخاص بـ /api/validate-player.
import threading as _vp_threading
import time as _vp_time
from sanitize import clean_plain_text, clean_rich_text

load_dotenv()

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("tecnogems")

# V52 (task D): observability — Sentry + JSON logs + audit trail.
# All three are opt-in via environment. Must be imported BEFORE any
# @app.route so Sentry can wrap the Flask integration cleanly.
from audit import init_sentry, init_json_logging, log_audit
init_json_logging()  # respects LOG_JSON env
init_sentry()        # respects SENTRY_DSN env

app = Flask(__name__)

# V53.1: قراءة IP العميل الحقيقي خلف Cloudflare/Heroku.
# يجب أن يُطبَّق ProxyFix قبل أي middleware آخر (Limiter, Compress, ...) كي
# يرى remote_addr المُصحَّح. get_real_ip() يفضّل CF-Connecting-IP ثم
# request.remote_addr بعد ProxyFix. تفاصيل الـenv vars في .env.example.
from request_ip import apply_proxy_fix, get_real_ip
apply_proxy_fix(app)

_secret = os.getenv("SECRET_KEY", "")
if not _secret or _secret == "dev-secret-change-me" or _secret == "change-this-secret-key":
    if os.getenv("FLASK_ENV") == "production":
        raise RuntimeError("SECRET_KEY is missing or default. Set a strong SECRET_KEY in .env before running in production.")
    log.warning("Using development SECRET_KEY. Set a strong SECRET_KEY in .env for production.")
    # In dev: persist a stable secret to avoid CSRF/session invalidation on restart.
    # Never write to disk in production (containers/Heroku have ephemeral filesystems).
    _secret_file = os.path.join(os.path.dirname(__file__), ".secret_key")
    if not _secret:
        try:
            if os.path.exists(_secret_file):
                with open(_secret_file, "r", encoding="utf-8") as fh:
                    _secret = fh.read().strip()
            if not _secret:
                _secret = secrets.token_urlsafe(48)
                try:
                    with open(_secret_file, "w", encoding="utf-8") as fh:
                        fh.write(_secret)
                except OSError:
                    # Read-only filesystem (containers) — use in-memory secret for this run
                    log.warning(".secret_key file not writable; secret is ephemeral this session.")
        except Exception:
            _secret = _secret or secrets.token_urlsafe(48)
app.secret_key = _secret

BASE_URL = os.getenv("BASE_URL", "https://tecnogems.com").rstrip("/")
_is_https = BASE_URL.startswith("https://")

# V50.2 LOW/MEDIUM: production should enforce CSRF SSL strict check (verifies
# Referer header matches host over HTTPS). Dev stays permissive so you can
# test from http://127.0.0.1 without tripping the check.
_IS_PROD = os.getenv("FLASK_ENV") == "production"
# V50.2 MEDIUM: shorten session lifetime from 14 days to 7 days. Long-lived
# sessions survive long after a device is stolen/lost. 7 days balances UX
# (weekly-or-more active users stay logged in) with risk.
_SESSION_DAYS = int(os.getenv("SESSION_LIFETIME_DAYS", "7") or 7)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=_is_https,
    PERMANENT_SESSION_LIFETIME=timedelta(days=_SESSION_DAYS),
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,  # 5 MB upload cap
    # CSRF: no time limit (token tied to session lifetime). Avoids the
    # "page expired, please retry" error after the user idles on the login form.
    WTF_CSRF_TIME_LIMIT=None,
    # V50.2 MEDIUM: SSL-strict enforces Referer header check for POSTs over
    # HTTPS. Kept off in dev so local http:// testing still works.
    WTF_CSRF_SSL_STRICT=_IS_PROD,
)

# V50 SECURITY: input length caps to prevent storage-bomb / CPU-DoS attacks.
# Any field longer than these limits is rejected before touching the DB or
# password hashing. Values balance real-world use vs abuse.
MAX_PLAYER_ID_LEN = 64
MAX_PASSWORD_LEN = 128
MAX_EMAIL_LEN = 120
MAX_NAME_LEN = 80
MAX_PHONE_LEN = 32
MAX_PROOF_TEXT_LEN = 2000
# Deposit ceiling (in the method's native currency for SYP, USD otherwise).
# Defaults to 10,000 USD. Override via MAX_DEPOSIT_USD env var.
try:
    MAX_DEPOSIT_USD = float(os.getenv("MAX_DEPOSIT_USD", "10000"))
except Exception:
    MAX_DEPOSIT_USD = 10000.0
# Admin balance set ceiling (prevents a compromised admin wiping the company).
try:
    MAX_ADMIN_BALANCE = float(os.getenv("MAX_ADMIN_BALANCE", "1000000"))
except Exception:
    MAX_ADMIN_BALANCE = 1_000_000.0

# CSRF protection
try:
    from flask_wtf import CSRFProtect
    csrf = CSRFProtect(app)
except Exception:  # graceful if dependency missing in dev
    csrf = None
    log.warning("Flask-WTF not installed. CSRF protection disabled. Run: pip install Flask-WTF")

# V45: Flask-Babel (real i18n). Falls back gracefully if not installed.
try:
    from flask_babel import Babel, gettext as _babel_gettext
    app.config.setdefault("BABEL_DEFAULT_LOCALE", "ar")
    app.config.setdefault("BABEL_SUPPORTED_LOCALES", ["ar", "en"])

    def _select_locale():
        from flask import session, request
        return session.get("lang") or request.cookies.get("lang") or "ar"

    babel = Babel(app, locale_selector=_select_locale)
    app.jinja_env.globals["_"] = _babel_gettext
    app.jinja_env.globals["gettext"] = _babel_gettext
except Exception as _exc:
    log.warning("Flask-Babel not installed; templates fall back to legacy tr(). %s", _exc)

# V53 REFACTOR (phase 1): Blueprint registration is deferred to the end of
# this module. auth_bp.py imports helpers (safe_next_url, limiter, …) from
# `app`, which can only work once those helpers have been defined below.
# The actual call to `register_blueprints(app)` happens at the bottom of
# this file — NOT here.

# V53 CRITICAL: Redis إلزامي في الإنتاج — رفض الإقلاع بدونه.
# In-memory fallback يخلق ثلاث مشاكل في الإنتاج:
#   1. Rate-limiter: كل worker حصة منفصلة → bypass عبر توزيع الحمل.
#   2. RQ queue: فقدان الطلبات عند restart (خسارة مالية فعلية).
#   3. Settings cache: عدم توافق بين workers لـ30 ثانية.
_redis_url = os.getenv("REDIS_URL", "").strip()
if os.getenv("FLASK_ENV") == "production" and not _redis_url:
    raise RuntimeError(
        "REDIS_URL is required in production. "
        "Set it to a valid redis:// URL (Upstash/Railway/Redis Cloud) "
        "or explicitly set FLASK_ENV=development for local testing."
    )

# V53: ping Redis at boot — fail hard in production, warn in dev.
if _redis_url:
    try:
        import redis as _redis_lib
        _r = _redis_lib.from_url(_redis_url, socket_connect_timeout=3)
        _r.ping()
        log.info("Redis reachable at %s", _redis_url.split("@")[-1])
    except Exception as exc:
        if os.getenv("FLASK_ENV") == "production":
            raise RuntimeError(f"Cannot reach REDIS_URL: {exc}") from exc
        log.warning("Redis unreachable (dev mode — continuing): %s", exc)

# Rate limiting
try:
    from flask_limiter import Limiter
    # V53.1: استخدمنا get_real_ip بدل get_remote_address لقراءة IP العميل
    # الحقيقي خلف Cloudflare/Heroku. بدون هذا، كل العملاء يشاركون نفس
    # rate-limit bucket عبر IP الـproxy.
    # V50.2 MEDIUM: when REDIS_URL is set, use the Redis storage backend so
    # rate limits are shared across gunicorn workers and survive restarts.
    # Falls back to in-memory when Redis is unavailable (dev or single-process).
    _limiter_kwargs = {"app": app, "default_limits": []}
    if _redis_url:
        _limiter_kwargs["storage_uri"] = _redis_url
        _limiter_kwargs["strategy"] = "fixed-window"
    limiter = Limiter(get_real_ip, **_limiter_kwargs)
    if _redis_url:
        log.info("Flask-Limiter using Redis storage backend.")
    else:
        log.warning("Flask-Limiter using in-memory storage — limits are per-worker and cleared on restart. Set REDIS_URL for shared limits.")
except Exception:
    limiter = None
    log.warning("Flask-Limiter not installed. Rate limiting disabled. Run: pip install Flask-Limiter")

# V43: Brotli + Gzip compression for HTML/CSS/JS responses
try:
    from flask_compress import Compress
    app.config["COMPRESS_ALGORITHM"] = ["br", "gzip"]
    app.config["COMPRESS_MIN_SIZE"] = 500
    app.config["COMPRESS_LEVEL"] = 6
    app.config["COMPRESS_BR_LEVEL"] = 5
    Compress(app)
    log.info("Flask-Compress enabled (br, gzip)")
except Exception as _e:
    log.warning("Flask-Compress not installed (%s). Run: pip install Flask-Compress", _e)

# V53 REFACTOR (phase 1): image upload helpers moved to services/images.py.
# Re-exported here so existing `from app import process_upload_to_webp` etc.
# keep working until phase 5. Pillow init (PATCH-M3 25 MP cap) now happens
# inside the services.images module — same effect since both modules import
# the same PIL.Image at process startup.
from services.images import (  # noqa: E402,F401
    ALLOWED_UPLOAD_EXTS,
    _IMG_MAGIC,
    _PIL_OK,
    _PROOF_MAGIC,
    _detect_image_kind,
    _ext_ok,
    _proof_magic_ok,
    _sanitise_svg,
    process_upload_to_webp,
)
# Pillow itself is also imported here so any code in this module that
# references the bare `Image` / `ImageOps` symbols (none today, but search
# `_PIL_OK` to be safe) keeps compiling. Failure is non-fatal — services.images
# already logs the warning and falls back gracefully.
try:
    from PIL import Image, ImageOps  # noqa: F401  # used transitively
except Exception:
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]


# Uploads (V50 SECURITY H4): moved OUT of static/ into data/uploads/ so
# the public static handler cannot bypass login_required on /uploads/proof/.
# The old /static/uploads/ path is also explicitly blocked below.
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "data", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
# Migrate any legacy files that may already exist under static/uploads/ once.
_LEGACY_UPLOADS = os.path.join(os.path.dirname(__file__), "static", "uploads")
if os.path.isdir(_LEGACY_UPLOADS):
    try:
        for _name in os.listdir(_LEGACY_UPLOADS):
            _src = os.path.join(_LEGACY_UPLOADS, _name)
            _dst = os.path.join(UPLOAD_FOLDER, _name)
            if os.path.isfile(_src) and not os.path.exists(_dst):
                try:
                    os.rename(_src, _dst)
                except OSError:
                    pass
    except OSError:
        pass
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
# V53 REFACTOR (phase 1): ALLOWED_UPLOAD_EXTS / _ext_ok / _PROOF_MAGIC /
# _proof_magic_ok / _sanitise_svg now live in services/images.py and are
# re-exported above. The original definitions used to live here.


# Make csrf_token() always available in templates even if Flask-WTF missing
if csrf is None:
    @app.context_processor
    def _csrf_noop():
        return {"csrf_token": lambda: ""}


# V53 REFACTOR (phase 1): MAIL_* constants and _aligned_envelope_sender
# moved to services/mail.py. Re-exported here so existing
# `from app import MAIL_FROM, _aligned_envelope_sender` callers keep
# working until phase 5. _BASE_DOMAIN is also re-exported.
from services.mail import (  # noqa: E402,F401
    BASE_URL as _MAIL_BASE_URL,  # alias to avoid shadowing app.BASE_URL
    MAIL_FROM,
    MAIL_FROM_NAME,
    MAIL_PASSWORD,
    MAIL_PORT,
    MAIL_REPLY_TO,
    MAIL_SERVER,
    MAIL_USERNAME,
    MAIL_USE_TLS,
    _BASE_DOMAIN,
    _aligned_envelope_sender,
)
del _MAIL_BASE_URL  # the app.py BASE_URL above is the canonical one


# V53 REFACTOR (phase 1): public translations + current_lang/tr/lang_url +
# package_public_name moved to utils/i18n.py. Re-exported here so the
# context processor and templates keep working until phase 5.
from utils.i18n import (  # noqa: E402,F401
    PUBLIC_TRANSLATIONS,
    current_lang,
    lang_url,
    package_public_name,
    tr,
)


# V53 REFACTOR (phase 1): public-facing price wrappers and package_public_name
# moved to services/pricing.py and utils/i18n.py respectively. Re-exported above.
from services.pricing import (  # noqa: E402,F401
    product_public_price,
    public_price_text,
)



# V53 REFACTOR (phase 3): /reset-lang and /lang/<lang> moved to
# routes/public_bp.py (public.reset_lang, public.set_language).



# V53 REFACTOR (phase 1): pricing helpers moved to services/pricing.py.
# Re-exported here so existing `from app import display_price_text` etc.
# keep working until phase 5.
from services.pricing import (  # noqa: E402,F401
    display_price_text,
    display_price_value,
    get_display_currency,
    get_pricing_mode,
    get_usd_syp_rate,
    manual_price_edit_enabled,
    manual_syp_override_active,
    product_display_price,
    product_manual_syp,
    product_profit_percent,
    product_sell_usd,
    wallet_money_text,
)







# V53 REFACTOR (phase 1): mail subsystem moved to services/mail.py and the
# transactional email HTML to templates/email/*.html. Re-exported here so
# `from app import send_email`, `_send_email_sync`, `email_queue`, and
# `_build_email_html` keep working until phase 5.
#
# IMPORTANT: services/mail.py spawns its own worker threads at import. The
# threads previously spawned here (`for _i in range(2): threading.Thread(...)`)
# have been removed to avoid double-spawning.
from services.mail import (  # noqa: E402,F401
    _build_email_html,
    _send_email_sync,
    email_is_configured,
    email_queue,
    email_verification_is_enabled,
    send_email,
    send_email_change_confirmation,
    send_password_reset_email,
    send_verification_email,
)
# _email_worker remains as a re-export for any external monitoring tool.
from services.mail import _email_worker  # noqa: E402,F401


# V53: RQ is the only order queue backend. In-memory fallback removed —
# Redis is enforced at boot (see boot check above).
from rq import Queue as _RQQueue
from redis import Redis

redis_conn = Redis.from_url(_redis_url) if _redis_url else None
order_queue = _RQQueue("tecnogems_orders", connection=redis_conn) if redis_conn else None
if redis_conn:
    log.info("Using Redis Queue for order processing (worker_rq.py).")
else:
    log.warning(
        "REDIS_URL not set — running orders synchronously (development mode). "
        "Production MUST set REDIS_URL."
    )


def enqueue_order_job(order_id, product=None, player_id=None):
    """Enqueue an order for async processing via RQ.

    Production: uses Redis Queue (validated at boot in wsgi.py).
    Dev/tests without REDIS_URL: runs process_order synchronously so
    the user-flow still works end-to-end.

    The product and player_id parameters are ignored (kept for API compat).
    tasks.process_order re-fetches the order from DB by id.
    """
    from tasks import process_order
    if order_queue is None:
        # Synchronous fallback for dev / tests. Production refuses to
        # boot without Redis (see boot check), so this branch is never
        # taken in production.
        if os.getenv("FLASK_ENV") == "production":
            raise RuntimeError(
                "order_queue is None in production — Redis boot check failed silently"
            )
        try:
            process_order(order_id)
        except Exception as exc:
            log.exception("Synchronous process_order failed for order=%s: %s", order_id, exc)
        return
    order_queue.enqueue(process_order, order_id)



def smart_game_image_url(game):
    """Lightweight generated SVG thumbnails for games without uploaded images."""
    try:
        name = str((game or {}).get("name") or "")
        key = str((game or {}).get("game_key") or "")
    except Exception:
        name, key = str(game or ""), ""
    s = (name + " " + key).lower().replace("_", " ")
    mapping = {
        # --- Original mappings ---
        "8 ball": "8-ball-pool.svg", "afk": "afk-journey.svg", "acecraft": "acecraft.svg",
        "arena breakout": "arena-breakout.svg", "arena of valor": "arena-of-valor.svg",
        "asphalt": "asphalt-9-legends.svg", "black clover": "black-clover-m.svg",
        "blood strike": "blood-strike.svg", "call of duty": "call-of-duty-mobile.svg", "cod": "call-of-duty-mobile.svg",
        "crossfire": "crossfire-mobile.svg", "delta": "delta-force-mobile.svg", "dragon nest": "dragon-nest-m.svg",
        "fc": "ea-fc-mobile.svg", "fifa": "ea-fc-mobile.svg", "eafc": "ea-fc-mobile.svg",
        "eve": "eve-echoes.svg",
        "eggy": "eggy-party.svg", "farlight": "farlight-84.svg", "genshin": "genshin-impact.svg",
        "honkai": "honkai-star-rail.svg", "honor": "honor-of-kings.svg", "mobile legends": "mobile-legends.svg",
        "pubg": "pubg-mobile.svg", "free fire": "free-fire.svg", "freefire": "free-fire.svg",
        "roblox": "roblox.svg", "minecraft": "minecraft.svg", "valorant": "valorant.svg",
        "clash royale": "clash-royale.svg", "clash": "clash-of-clans.svg", "stumble": "stumble-guys.svg",
        "wild rift": "wild-rift.svg", "zenless": "zenless-zone-zero.svg", "ragnarok": "ragnarok-x.svg",
        "solo leveling": "solo-leveling.svg", "magic chess": "magic-chess.svg", "crystal": "crystal-of-atlan.svg",
        "etheria": "etheria-restart.svg", "watcher": "watcher-of-realms.svg", "harry potter": "harry-potter-magic-awakened.svg",
        "blockman": "blockman-go.svg", "bleach": "bleach-soul-resonance.svg", "devil may cry": "devil-may-cry.svg",
        "echocalypse": "echocalypse.svg", "frag": "frag-pro-shooter.svg", "heartopia": "heartopia.svg",
        "mecha break": "mecha-break.svg", "marvel duel": "marvel-duel.svg",
        # --- New mappings (V62) ---
        "age of empire": "age-of-empires-mobile.svg", "age of magic": "age-of-magic.svg",
        "arknights": "arknights-endfield.svg", "arknight": "arknights-endfield.svg",
        "azur lane": "azur-lane.svg",
        "bigo": "bigo-live.svg", "bullet echo": "bullet-echo.svg",
        "cats": "cats-arena.svg", "crash arena": "cats-arena.svg",
        "civilization": "civilization-mobile.svg", "crossout": "crossout-mobile.svg",
        "deadly dudes": "deadly-dudes.svg", "destiny": "destiny-rising.svg",
        "dragon raja": "dragon-raja.svg", "dragonheir": "dragonheir.svg",
        "duet night": "duet-night-abyss.svg", "dunk city": "dunk-city-dynasty.svg",
        "enhypen": "enhypen-world.svg", "nikke": "goddess-of-victory-nikke.svg", "gov": "goddess-of-victory-nikke.svg",
        "undawn": "garena-undawn.svg", "ghost story": "ghost-story.svg",
        "growtopia": "growtopia.svg", "haikyu": "haikyu-fly-high.svg",
        "hatsune": "hatsune-miku.svg", "miku": "hatsune-miku.svg",
        "heaven burns": "heaven-burns-red.svg", "identity v": "identity-v.svg",
        "kings choice": "kings-choice.svg", "king's choice": "kings-choice.svg",
        "kingshot": "kingshot.svg", "knives out": "knives-out.svg",
        "league of legends": "league-of-legends.svg", "lol": "league-of-legends.svg",
        "legend of the phoenix": "legend-of-phoenix.svg", "legend of phoenix": "legend-of-phoenix.svg",
        "legends of runeterra": "legends-of-runeterra.svg", "runeterra": "legends-of-runeterra.svg",
        "life makeover": "life-makeover.svg", "lifeafter": "lifeafter.svg",
        "likee": "likee.svg", "lineage": "lineage2m.svg",
        "lord of the rings": "lord-of-rings-war.svg", "lotr": "lord-of-rings-war.svg",
        "love nikki": "love-nikki.svg", "love and deepspace": "love-and-deepspace.svg",
        "maplestory": "maplestory-m.svg", "maple story": "maplestory-m.svg",
        "marvel rivals": "marvel-rivals.svg", "marvel mystic": "marvel-rivals.svg",
        "metal slug": "metal-slug-awakening.svg", "modern strike": "modern-strike-online.svg",
        "moonlight blade": "moonlight-blade.svg", "my singing": "my-singing-monsters.svg",
        "once human": "once-human.svg", "onmyoji": "onmyoji-arena.svg",
        "overmortal": "overmortal.svg", "oxide": "oxide-survival.svg",
        "path to nowhere": "path-to-nowhere.svg", "pixel gun": "pixel-gun-3d.svg",
        "poppo": "poppo-live.svg", "project entropy": "project-entropy.svg",
        "punishing": "punishing-gray-raven.svg", "gray raven": "punishing-gray-raven.svg",
        "puzzles": "puzzles-survival.svg", "racing master": "racing-master.svg",
        "rainbow six": "rainbow-six-mobile.svg", "r6": "rainbow-six-mobile.svg",
        "rememento": "rememento.svg", "sausage man": "sausage-man.svg",
        "sea of conquest": "sea-of-conquest.svg", "shining nikki": "shining-nikki.svg",
        "silver and blood": "silver-and-blood.svg",
        "sky children": "sky-children-light.svg", "sky: children": "sky-children-light.svg",
        "snowbreak": "snowbreak.svg", "soul land": "soul-land.svg",
        "spring valley": "spring-valley.svg", "star resonance": "star-resonance.svg",
        "starmaker": "starmaker.svg", "state of survival": "state-of-survival.svg",
        "stormshot": "stormshot.svg", "super sus": "super-sus.svg",
        "sword of justice": "sword-of-justice.svg", "t3 arena": "t3-arena.svg",
        "tarisland": "tarisland.svg", "teamfight": "teamfight-tactics.svg", "tft": "teamfight-tactics.svg",
        "teen patti": "teen-patti-gold.svg", "telegram": "telegram.svg",
        "the division": "the-division.svg", "division resurgence": "the-division.svg",
        "tiles survive": "tiles-survive.svg",
        "where winds": "where-winds-meet.svg", "whiteout": "whiteout-survival.svg",
        "wuthering": "wuthering-waves.svg", "yalla": "yalla-ludo.svg",
        "zepeto": "zepeto.svg",
    }
    for needle, filename in mapping.items():
        if needle in s:
            return url_for("static", filename=f"img/smart-games/{filename}")
    return url_for("static", filename="img/smart-games/game-default-smart.svg")



def _get_poster_available():
    """Cache poster basenames -> file extension from static/img/games/.

    V65: switched from a flat set of webp basenames to a {basename: ext} map
    so we can serve the new high-res `.jpg` artwork without breaking the
    handful of games still on the old `.webp` thumbnails. JPG takes priority
    when both are present.
    """
    if not hasattr(_get_poster_available, "_cache"):
        import os as _os
        poster_dir = _os.path.join(_os.path.dirname(__file__), "static", "img", "games")
        ext_map = {}
        if _os.path.isdir(poster_dir):
            for f in _os.listdir(poster_dir):
                if f.endswith(".jpg"):
                    ext_map[f[:-4]] = "jpg"
                elif f.endswith(".webp") and f[:-5] not in ext_map:
                    ext_map[f[:-5]] = "webp"
        _get_poster_available._cache = ext_map
    return _get_poster_available._cache


def _resolve_poster_for_display(game_key):
    """Use the same resolution logic as database._resolve_poster_key to find
    the correct WebP poster for a game_key at display time.

    Resolution order:
      1. exact match
      2. explicit alias table (_POSTER_ALIASES from database.py)
      3. progressively drop trailing _segment(s)
    """
    from database import _POSTER_ALIASES

    available = _get_poster_available()
    if not available or not game_key:
        return None

    gk = game_key.lower()

    # 1. Exact match
    if gk in available:
        return gk

    # 2. Alias table
    alias = _POSTER_ALIASES.get(gk)
    if alias and alias in available:
        return alias

    # 3. Progressive suffix stripping
    parts = gk.split("_")
    while len(parts) > 1:
        parts.pop()
        cand = "_".join(parts)
        if cand in available:
            return cand
        cand_alias = _POSTER_ALIASES.get(cand)
        if cand_alias and cand_alias in available:
            return cand_alias

    return None


def game_image_url(game):
    """Priority: admin uploaded/custom image -> matched WebP poster from
    static/img/games/ -> smart SVG fallback.

    V64: Replaced old substring-matching (which caused wrong images) with
    precise game_key-based poster resolution using the same alias table and
    suffix-stripping logic as attach_generated_posters().

    V66: Self-heal at display time. If the stored image_url points to a
    file that was removed on disk (e.g. a `/static/img/games/<key>.webp`
    that V65 replaced with `.jpg`), skip it and fall through to the live
    resolver. Admin-uploaded URLs (anything not under /static/img/games/
    top-level) and remote URLs are still trusted as-is.
    """
    try:
        name = str((game or {}).get("name") or (game or {}).get("game_name") or "")
        key = str((game or {}).get("game_key") or "")
        custom = str((game or {}).get("image_url") or (game or {}).get("game_image_url") or "")
    except Exception:
        name, key, custom = str(game or ""), "", ""

    # 1. Admin-uploaded or DB-assigned image (highest priority) — but only
    #    if the file actually exists for auto-generated /static/img/games/<x>
    #    paths; otherwise fall through to the live resolver below.
    if custom:
        if custom.startswith("/static/img/games/"):
            rel = custom[len("/static/img/games/"):]
            if "/" not in rel:  # top-level auto poster
                import os as _os
                on_disk = _os.path.join(_os.path.dirname(__file__),
                                        "static", "img", "games", rel)
                if _os.path.isfile(on_disk):
                    return custom
                # else: fall through to resolver
            else:
                return custom
        else:
            return custom

    # 2. Match poster by game_key (precise, no substring false-positives)
    poster = _resolve_poster_for_display(key)
    if poster:
        ext = _get_poster_available().get(poster, "webp")
        return url_for("static", filename=f"img/games/{poster}.{ext}")

    # 3. Smart SVG fallback (generated thumbnails)
    return smart_game_image_url(game)


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    user = get_user(uid)
    if not user:
        session.clear()
        return None
    # V50 SECURITY (HH): deactivated users must not retain access on
    # long-lived sessions. authenticate() checks active=1 at login but the
    # per-request guard did not. Clear the session if the user was deactivated.
    try:
        if int(user.get("active", 1)) != 1:
            session.clear()
            return None
    except Exception:
        pass
    # V53 SECURITY: invalidate session when password was changed (session_version
    # mismatch means the password was reset after this session was created).
    db_version = int(user.get("session_version") or 1)
    sess_version = session.get("sess_v")
    if sess_version is not None and int(sess_version) != db_version:
        session.clear()
        return None
    return user


@app.context_processor
def inject_user():
    # PATCH-M2/C4: per-request CSP nonce for all inline <script> tags.
    # Combined with the strict CSP header (no 'unsafe-inline'), this is
    # the canonical way to whitelist trusted inline JS.
    from flask import g as _g
    nonce = getattr(_g, "_csp_nonce", None)
    if not nonce:
        nonce = secrets.token_urlsafe(16)
        _g._csp_nonce = nonce
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
        "game_image": game_image_url
    }



# V53 REFACTOR (phase 1): Jinja template filters moved to utils/filters.py.
# We keep the @app.template_filter decorators here so the filters are
# registered on this app instance (the legacy auth_bp / templates rely on
# the names being live), but each one delegates to the extracted helper.
# The bare names (`syria_time`, `money`, ...) are also re-exported for
# any direct `from app import money` imports that may exist.
from utils.filters import (  # noqa: E402,F401
    clean_package_name as _filter_clean_package_name,
    money as _filter_money,
    order_status_class as _filter_order_status_class,
    order_status_label as _filter_order_status_label,
    public_package_name_filter as _filter_public_package_name,
    syria_time as _filter_syria_time,
)


@app.template_filter("public_package_name")
def public_package_name_filter(value):
    return _filter_public_package_name(value)


@app.template_filter("syria_time")
def syria_time(value):
    return _filter_syria_time(value)


@app.template_filter("money")
def money(amount):
    return _filter_money(amount)


@app.template_filter("clean_package_name")
def clean_package_name(value):
    return _filter_clean_package_name(value)



def validate_password_strength(password):
    """PATCH-M4: enforce minimum complexity for production-grade safety.
    Requires 8+ chars and at least 2 of: lowercase, uppercase, digit, symbol.
    """
    password = password or ""
    if len(password) < 8:
        return False, "كلمة المرور يجب أن تكون 8 أحرف على الأقل"
    classes = sum([
        bool(re.search(r"[a-z]", password)),
        bool(re.search(r"[A-Z]", password)),
        bool(re.search(r"\d", password)),
        bool(re.search(r"[^A-Za-z0-9]", password)),
    ])
    if classes < 2:
        return False, "كلمة المرور ضعيفة. استخدم مزيجاً من الأحرف والأرقام (أو رموزاً)"
    return True, None


def safe_next_url(default_endpoint="public.home", **url_for_kwargs):
    """PATCH-B4: now accepts kwargs forwarded to url_for() so callers like
    safe_next_url("public.products", provider=p, game_key=k) no longer crash
    with TypeError. The ?next= parameter still wins when present and safe.

    V50 SECURITY (HF): hardened against open-redirect variants:
    - reject backslashes (\\evil.com), null bytes, control chars
    - reject any ':' (blocks javascript:, http://, etc)
    - reject /%2f%2f... (encoded protocol-relative)
    - cap length to avoid log/memory pollution
    """
    nxt = request.args.get("next") or request.form.get("next") or ""
    if not nxt or len(nxt) > 512:
        return url_for(default_endpoint, **url_for_kwargs)
    # Reject anything that is not a plain same-origin path.
    bad_chars = ("\\", "\x00", "\r", "\n", "\t", " ")
    if any(c in nxt for c in bad_chars) or ":" in nxt:
        return url_for(default_endpoint, **url_for_kwargs)
    # Lowercase for encoded-scheme check
    low = nxt.lower()
    if low.startswith("//") or low.startswith("/%2f") or low.startswith("/\\"):
        return url_for(default_endpoint, **url_for_kwargs)
    if nxt.startswith("/") and not nxt.startswith("/legacy"):
        return nxt
    return url_for(default_endpoint, **url_for_kwargs)

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            flash("يرجى تسجيل الدخول أولًا", "warning")
            return redirect("/login")
        # V53 SECURITY: validate session is still valid (e.g. password changed).
        if not current_user():
            flash("يرجى تسجيل الدخول أولًا", "warning")
            return redirect("/login")
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or user["role"] != "admin":
            abort(403)
        # V51 task B: enforce 2FA on admin routes.
        #
        # Policy (with a safe rollout):
        #   - If the admin has 2FA enabled → they MUST pass the challenge
        #     once per session (session["admin_2fa_verified"] = 1) before
        #     any /admin/* route renders. This is unconditional.
        #   - If the admin has NOT enabled 2FA and the global setting
        #     `admin_2fa_required` is "1" → redirect to setup. This lets
        #     ops flip the switch once every admin has enrolled.
        #   - The 2FA endpoints themselves are whitelisted via
        #     request.endpoint so setup/challenge/disable don't loop.
        # V53 REFACTOR (phase 4): the 2FA endpoints now live in
        # routes/admin_2fa_bp.py under the ``admin_2fa.`` namespace
        # (admin_2fa.setup, admin_2fa.confirm, admin_2fa.challenge,
        # admin_2fa.disable, admin_2fa.regenerate_backup_codes).
        endpoint = (request.endpoint or "")
        whitelist = {
            "admin_2fa.setup", "admin_2fa.confirm",
            "admin_2fa.challenge", "admin_2fa.disable",
            "admin_2fa.regenerate_backup_codes",
        }
        if endpoint not in whitelist:
            if int(user.get("totp_enabled") or 0) == 1:
                if not session.get("admin_2fa_verified"):
                    flash("يرجى إدخال رمز المصادقة الثنائية للمتابعة.", "warning")
                    return redirect(url_for("admin_2fa.challenge",
                                            next=request.full_path))
            else:
                if get_setting("admin_2fa_required", "0") == "1":
                    flash("يجب تفعيل المصادقة الثنائية لحسابات الإدارة.", "warning")
                    return redirect(url_for("admin_2fa.setup"))
        return fn(*args, **kwargs)
    return wrapper



@app.template_filter("order_status_label")
def order_status_label(status):
    return _filter_order_status_label(status)


@app.template_filter("order_status_class")
def order_status_class(status):
    return _filter_order_status_class(status)




def gzip_text_response(response):
    try:
        accept = request.headers.get("Accept-Encoding", "")
        ctype = response.headers.get("Content-Type", "")
        if (
            "gzip" in accept
            and response.status_code == 200
            and not response.direct_passthrough
            and "Content-Encoding" not in response.headers
            and any(t in ctype for t in ["text/html", "text/css", "application/javascript", "text/javascript", "application/json"])
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





@app.before_request
def lang_cookie_reset_v36():
    if session.get("lang") == "en" and session.get("lang_user_selected") != "1":
        session["lang"] = "ar"
        session.pop("lang_user_selected", None)


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    flash("انتهت صلاحية الصفحة، يرجى إعادة المحاولة.", "warning")
    return redirect(safe_next_url("public.home"))

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html", title="404 - الصفحة غير موجودة"), 404

@app.errorhandler(500)
def server_error(e):
    return render_template("500.html", title="500 - خطأ في الخادم"), 500

@app.after_request
def add_cache_headers(response):
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif (
        request.path.startswith("/admin")
        or request.path.startswith("/login")
        or request.path.startswith("/register")
        or request.path.startswith("/forgot")
        or request.path.startswith("/reset")
        or request.method == "POST"
    ):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    else:
        # V67.1 BUGFIX: previously every public GET was cached for 120s with
        # `private, max-age=120`. That cached the *logged-out* HTML (which
        # shows "إنشاء حساب" in the navbar) for 2 minutes, so users saw the
        # logged-out navbar for up to two minutes after signing in until they
        # manually refreshed. It also held stale flashed messages and stale
        # balance values.
        # Fix: any request that has a logged-in user MUST NOT be cached. We
        # also weaken the public cache to 30s (was 120) so sign-up impressions
        # are quicker too. Static assets are unchanged (still 1 year).
        if session.get("user_id"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Vary"] = "Cookie"
        else:
            response.headers.setdefault("Cache-Control", "private, max-age=30")
            response.headers.setdefault("Vary", "Cookie")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # V50.2 LOW: block legacy cross-domain Flash/Silverlight policy files.
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    # V50.2 LOW: isolate browsing-context group (mitigates Spectre / cross-origin
    # window references). Safe here because we do not embed third-party windows.
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    # V50.2 LOW: block other origins from embedding our resources as images
    # or scripts. "same-site" keeps our own subdomains working.
    response.headers["Cross-Origin-Resource-Policy"] = "same-site"
    # PATCH-C4: nonce-based CSP — no more 'unsafe-inline' for scripts.
    # Inline <script> blocks must declare nonce="{{ csp_nonce }}" to execute.
    from flask import g as _g
    _nonce = getattr(_g, "_csp_nonce", "")
    # V50.2 MEDIUM: tighter CSP. Added object-src 'none' (blocks <object>/<embed>
    # and Flash/plugins), form-action 'self' (forms can only POST to our origin
    # — blocks form-hijack XSS payloads), frame-src 'none' (we don't use frames),
    # and upgrade-insecure-requests so any accidental http:// asset is auto-upgraded.
    # Note: style-src still includes 'unsafe-inline' because many templates use
    # inline style="..." attributes. Removing it is tracked as a follow-up refactor.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        f"script-src 'self' 'nonce-{_nonce}'; "
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
        # V50.2 LOW: add 'preload' so the browser can submit the domain to
        # the HSTS preload list (requires 2-year max-age + includeSubDomains,
        # which we have). Site admins must still enrol at hstspreload.org.
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    return gzip_text_response(response)


# ---------------------------------------------------------------------------
# V53 REFACTOR (phase 3): public-facing routes moved to routes/public_bp.py
# and the wallet pages moved to routes/wallet_bp.py.
#
# Moved to public_bp:
#   /robots.txt, /manifest.json, /email-info, /sitemap.xml,
#   /legacy/<path:rest>, /uploads/proof/<filename>,
#   /static/uploads/<path:_ignored>, /privacy, /terms, /refund, /contact,
#   /  (and /legacy alias), /dashboard, /servers (+ /legacy/servers),
#   /games/<provider> (+ legacy), /all-games (+ legacy),
#   /products/<provider>/<game_key> (+ legacy),
#   /products/<provider>/<game_key>/group/<int:group_id>,
#   /checkout/<int:product_id> (+ legacy), /profile, /orders (+ legacy)
# Moved to wallet_bp:
#   /wallet, /wallet/deposit, /legacy/wallet, /wallet/transactions
#
# Endpoint names are now namespaced — public.home, public.products,
# public.checkout, wallet.wallet, … — see routes/public_bp.py and
# routes/wallet_bp.py for the canonical definitions. Templates and the
# remaining handlers in this file have been updated accordingly.
#
# The Blueprints are registered at the very bottom of this file via
# `register_blueprints(app)` so every helper they import (limiter,
# safe_next_url, current_user, login_required, _ext_ok, …) has been
# defined by the time the import runs.
# ---------------------------------------------------------------------------




@app.route("/api/validate-player", methods=["POST"])
@login_required
@(limiter.limit("30 per minute") if limiter else (lambda f: f))
def api_validate_player():
    """
    V71: التحقق من اسم اللاعب لدى المورد قبل إنشاء الطلب.

    سلوك آمن ومحدود:
    - يحتاج تسجيل دخول (`@login_required`).
    - Rate-limit 30/دقيقة لكل IP (مطبّق فعلاً عبر الـ decorator).
    - مفعّل/معطّل عبر إعداد admin: `player_validation_api_enabled`.
    - Cache داخلي (process-local) لكل (provider, product_id, player_id):
        نتيجة المورد تُحفظ ٦٠ ثانية فقط. يمنع enumeration والـ DDoS
        الذي يكلّفنا استدعاءات للمورد.
    - يقبل JSON: { product_id: int, player_id: str }.
    - يرجع JSON موحّد بنفس شكل validate_player_provider.

    ملاحظة: هذا المسار يُكشف نتيجة "ID صحيح أم لا" — وهو مقبول هنا لأنه
    يحاكي بالضبط ما تفعله المواقع المنافسة، لكن نضيف rate-limit + cache
    للحدّ من الإساءة.
    """
    user = current_user()
    if not user:
        return jsonify({"ok": False, "error": "يجب تسجيل الدخول"}), 401

    if get_setting("player_validation_api_enabled", "0") != "1":
        return jsonify({
            "ok": True,
            "enabled": False,
            "success": False,
            "error": "خاصية التحقق من اسم اللاعب غير مفعّلة حاليًا",
        })

    data = request.get_json(silent=True) or {}
    try:
        product_id = int(data.get("product_id"))
    except Exception:
        return jsonify({"ok": False, "error": "الباقة غير صحيحة"}), 400

    player_id = (data.get("player_id") or "").strip()
    if len(player_id) < 3 or len(player_id) > MAX_PLAYER_ID_LEN:
        return jsonify({"ok": False, "error": "معرف اللاعب غير صحيح"}), 400

    product = get_product(product_id)
    if not product:
        return jsonify({"ok": False, "error": "الباقة غير موجودة"}), 404

    cache_key = (product["provider"], str(product["provider_product_id"]), player_id)
    now = _vp_time.time()
    with _VP_CACHE_LOCK:
        cached = _VP_CACHE.get(cache_key)
        if cached and (now - cached[0]) < _VP_CACHE_TTL:
            return jsonify({"ok": True, "enabled": True, **cached[1]})

    result = validate_player_provider(
        product["provider"],
        product["provider_product_id"],
        player_id,
    )
    # ننظّف من حقل raw قبل الإرجاع للعميل: قد يحوي تفاصيل المورد التي
    # لا حاجة للعميل بها (وسبق وتسبّبت في تسريبات في v50).
    safe = {
        "success": bool(result.get("success")),
        "player_name": str(result.get("player_name") or ""),
        "verified_only": bool(result.get("verified_only")),
        "unsupported": bool(result.get("unsupported")),
    }
    if not safe["success"]:
        safe["error"] = str(result.get("error") or "تعذر التحقق من اللاعب")

    with _VP_CACHE_LOCK:
        # حدّ بسيط لحجم الـ cache كي لا ينمو بلا نهاية.
        if len(_VP_CACHE) >= 2000:
            _VP_CACHE.clear()
        _VP_CACHE[cache_key] = (now, safe)

    return jsonify({"ok": True, "enabled": True, **safe})


# V71: cache process-local لتقليل ضغط الاستعلامات على المورد. نحدّ ب-60 ثانية
# لكل (provider, product_id, player_id). المفتاح يستبعد user_id حتى لو طلب
# عدة مستخدمين نفس اللاعب نقتصر على استعلام واحد للمورد.
_VP_CACHE: dict = {}
_VP_CACHE_LOCK = _vp_threading.Lock()
_VP_CACHE_TTL = 60.0




# Admin
# ---------------------------------------------------------------------------
# V53 REFACTOR (phase 4): admin 2FA routes moved to routes/admin_2fa_bp.py.
#
# Previously this section defined:
#   /admin/2fa/setup                       -> admin_2fa.setup
#   /admin/2fa/confirm                     -> admin_2fa.confirm
#   /admin/2fa/challenge                   -> admin_2fa.challenge
#   /admin/2fa/disable                     -> admin_2fa.disable
#   /admin/2fa/backup-codes/regenerate     -> admin_2fa.regenerate_backup_codes
#
# plus the small _is_admin_user() helper, which only the 2FA flow used and
# is now defined alongside it inside routes/admin_2fa_bp.py.
#
# The endpoint-name whitelist inside admin_required (above) was updated to
# the namespaced "admin_2fa.*" names in lock-step.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# V53 REFACTOR (phase 4): admin core routes moved to routes/admin_bp.py.
#
# Previously this section defined:
#   /admin                                       -> admin.dashboard
#   /admin/orders                                -> admin.orders
#   /admin/order/<int:order_id>/<action>         -> admin.order_action
#   /admin/users                                 -> admin.users
#   /admin/user/<int:user_id>                    -> admin.user_detail
#   /admin/user/<int:user_id>/balance            -> admin.user_balance
#   /admin/balances                              -> admin.balances
#   /admin/games                                 -> admin.games
#   /admin/games/add                             -> admin.add_game
#   /admin/game/<provider>/<game_key>/image      -> admin.game_image
#   /admin/game/<provider>/<game_key>/products   -> admin.game_products
#   /admin/game/<provider>/<game_key>/manual-prices -> admin.update_manual_syp_prices
#   /admin/accounting                            -> admin.accounting
#   /admin/deposits                              -> admin.deposits
#   /admin/deposit/<int:deposit_id>/<action>     -> admin.deposit_action
#   /admin/refresh-pending-orders                -> admin.refresh_pending_orders
#   /admin/payment-methods                       -> admin.payment_methods
#   /admin/payment-method/<method_id>            -> admin.payment_method_edit
#   /admin/test-email                            -> admin.test_email
#   /admin/settings                              -> admin.settings
#
# All decorators (@login_required, @admin_required, rate limits) and
# CSRF behaviour are preserved verbatim. Templates that reference these
# endpoints have been updated wholesale in this same PR.
# ---------------------------------------------------------------------------


def _public_user(user):
    if not user:
        return None
    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "phone": user.get("phone") if hasattr(user, "get") else user["phone"],
        "role": user["role"],
        "balance": float(user["balance"] or 0),
        "email_verified": bool(user.get("email_verified", 0) if hasattr(user, "get") else user["email_verified"]) if "email_verified" in user.keys() else True,
    }


def _game_to_api(g):
    slug = f"{g['provider']}--{g['game_key']}"
    img = g.get("image_url", "") if hasattr(g, "get") else g["image_url"]
    if not img:
        img = "/static/img/game-default.svg"
    return {
        "id": slug,
        "slug": slug,
        "provider": g["provider"],
        "game_key": g["game_key"],
        "name": g["name"],
        "emoji": g.get("emoji", "🎮") if hasattr(g, "get") else g["emoji"],
        "cover": img,
        "image_url": img,
        "category": "ألعاب",
        "packagesCount": int(g.get("product_count", 0) or 0) if hasattr(g, "get") else int(g["product_count"] or 0),
        "startingPrice": float(g.get("min_price", 0) or 0) if hasattr(g, "get") else float(g["min_price"] or 0),
        "currency": "رصيد",
    }


@app.route("/api/me")
def api_me():
    user = current_user()
    return jsonify({
        "ok": True,
        "user": _public_user(user),
        "settings": {
            "theme": get_setting("site_theme", "theme-aurora"),
            "support": get_setting("support_contact", "@support"),
            "emailVerification": get_setting("email_verification_enabled", "0") == "1",
        }
    })


@app.route("/api/games")
def api_games():
    games = [_game_to_api(g) for g in list_public_games(True)]
    return jsonify({"ok": True, "games": games})


@app.route("/api/games/<slug>")
def api_game(slug):
    if "--" not in slug:
        return jsonify({"ok": False, "error": "اللعبة غير موجودة"}), 404
    provider, game_key = slug.split("--", 1)
    game = get_game(provider, game_key)
    if not game or not game["active"]:
        return jsonify({"ok": False, "error": "اللعبة غير موجودة"}), 404
    products = list_products(provider, game_key)
    return jsonify({
        "ok": True,
        "game": _game_to_api({
            **game,
            "product_count": len(products),
            "min_price": min([p["sell_price"] for p in products], default=0)
        }),
        "products": [{
            "id": p["id"],
            "name": p["name"],
            "priceUsd": float(p["sell_price"] or 0),
            "basePrice": float(p["base_price"] or 0),
            "popular": i == 1,
        } for i, p in enumerate(products)]
    })


@app.route("/api/login", methods=["POST"])
@(limiter.limit("10 per minute") if limiter else (lambda f: f))
def api_login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    # V50 SECURITY (HD): oversized inputs rejected pre-hash.
    if len(password) > MAX_PASSWORD_LEN or len(email) > MAX_EMAIL_LEN:
        log.warning("Rejected oversized api_login inputs from %s", get_real_ip())
        return jsonify({"ok": False, "error": "بيانات الدخول غير صحيحة"}), 401
    user = authenticate(email, password)
    if not user:
        # V50 SECURITY (M10): log failed API auth attempts.
        log.warning("Failed api_login for email=%s from ip=%s",
                    email, get_real_ip())
        return jsonify({"ok": False, "error": "بيانات الدخول غير صحيحة"}), 401
    if email_verification_is_enabled() and user["role"] != "admin" and not user.get("email_verified"):
        return jsonify({"ok": False, "error": "يجب تفعيل بريدك الإلكتروني قبل تسجيل الدخول"}), 403
    # PATCH-H2: clear pre-existing session before authenticating
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    return jsonify({"ok": True, "user": _public_user(user)})


@app.route("/api/register", methods=["POST"])
@(limiter.limit("8 per minute") if limiter else (lambda f: f))
def api_register():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    phone = (data.get("phone") or "").strip()
    password = data.get("password") or ""
    password_confirm = data.get("password_confirm") or ""
    # V50 SECURITY (HD): cap input lengths before password hashing.
    if (
        len(password) > MAX_PASSWORD_LEN
        or len(email) > MAX_EMAIL_LEN
        or len(name) > MAX_NAME_LEN
        or len(phone) > MAX_PHONE_LEN
    ):
        return jsonify({"ok": False, "error": "أحد الحقول تجاوز الحد المسموح به"}), 400
    if not name or not email or not password:
        return jsonify({"ok": False, "error": "أكمل البيانات المطلوبة"}), 400
    if password != password_confirm:
        return jsonify({"ok": False, "error": "كلمة المرور وتأكيدها غير متطابقين"}), 400
    pw_ok, pw_err = validate_password_strength(password)
    if not pw_ok:
        return jsonify({"ok": False, "error": pw_err}), 400

    verification_enabled = email_verification_is_enabled()
    token = secrets.token_urlsafe(32) if verification_enabled else None
    ok, err = create_user(name, email, phone, password, email_verified=0 if verification_enabled else 1, email_token=token)
    if not ok:
        return jsonify({"ok": False, "error": err or "فشل إنشاء الحساب"}), 400

    if verification_enabled:
        try:
            send_verification_email(email, token)
            return jsonify({"ok": True, "message": "تم إنشاء الحساب. أرسلنا رابط التفعيل إلى بريدك."})
        except Exception as exc:
            log.warning("api_register verification email failed for %s: %s", email, exc)
            return jsonify({"ok": True, "message": "تم إنشاء الحساب، لكن لم يتم إرسال بريد التفعيل. افحص إعدادات Gmail App Password في ملف .env، ثم استخدم إعادة إرسال رابط التفعيل."})
    return jsonify({"ok": True, "message": "تم إنشاء الحساب. يمكنك تسجيل الدخول الآن."})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/orders", methods=["GET", "POST"])
@(limiter.limit("20 per minute") if limiter else (lambda f: f))
def api_orders():
    user = current_user()
    if not user:
        return jsonify({"ok": False, "error": "يجب تسجيل الدخول"}), 401

    if request.method == "GET":
        orders = list_user_orders(user["id"])
        return jsonify({"ok": True, "orders": [dict(o) for o in orders]})

    data = request.get_json(silent=True) or {}
    try:
        product_id = int(data.get("product_id"))
    except Exception:
        return jsonify({"ok": False, "error": "الباقة غير صحيحة"}), 400
    player_id = (data.get("player_id") or "").strip()
    # V50 SECURITY (C1): bound player_id length (see checkout).
    if len(player_id) < 3 or len(player_id) > MAX_PLAYER_ID_LEN:
        return jsonify({"ok": False, "error": "معرف اللاعب غير صحيح"}), 400

    product = get_product(product_id)
    if not product:
        return jsonify({"ok": False, "error": "الباقة غير موجودة"}), 404
    game = get_game(product["provider"], product["game_key"])
    if not game:
        return jsonify({"ok": False, "error": "اللعبة غير موجودة"}), 404

    # PATCH-B2/B7: rely on atomic balance deduction inside create_order
    # (BEGIN IMMEDIATE + UPDATE WHERE balance>=price) instead of the
    # race-prone Python pre-check, and properly catch InsufficientBalance
    # so the client gets a clean 400 instead of a 500.
    try:
        order_id, code = create_order(user, product, game, player_id)
    except InsufficientBalance:
        return jsonify({"ok": False, "error": "رصيدك غير كافٍ"}), 400
    except Exception as _e:
        log.exception("api_orders create_order failed: %s", _e)
        return jsonify({"ok": False, "error": "حدث خطأ أثناء إنشاء الطلب"}), 500

    enqueue_order_job(order_id, product, player_id)
    return jsonify({"ok": True, "order_id": order_id, "order_code": code})


@app.route("/api/payment-methods")
def api_payment_methods():
    return jsonify({"ok": True, "methods": list_payment_methods(True)})


@app.route("/api/wallet")
def api_wallet():
    user = current_user()
    if not user:
        return jsonify({"ok": False, "error": "يجب تسجيل الدخول"}), 401
    bal = float(user["balance"] or 0)
    return jsonify({
        "ok": True,
        "balance": bal,
        # V67.2: also send the *formatted* string so the navbar JS does not
        # have to guess the currency / suffix. Without this the JS was
        # replacing the leading number while keeping the old "ل.س" suffix,
        # which produced "8.20 ل.س" for a USD value when display currency
        # was SYP. Sending the rendered label keeps the navbar consistent
        # with whatever wallet_money_text() decides on the server.
        "balance_text": wallet_money_text(bal),
        "display_currency": get_display_currency(),
        "methods": list_payment_methods(True),
    })


# V53 REFACTOR (phase 3): /games (games_index, redirect to home) moved to
# routes/public_bp.py as `public.games_index`.



_setup_lock = threading.Lock()

@app.before_request
def setup_once():
    if not getattr(app, "_setup_done", False):
        with _setup_lock:
            if not getattr(app, "_setup_done", False):
                # V47: warn / block weak admin password in production
                _admin_pw = os.getenv("ADMIN_PASSWORD", "admin123456")
                _weak_passwords = {"admin123456", "admin", "password", "123456", "change-this", "<CHANGE-THIS-STRONG-PASSWORD>", ""}
                if _admin_pw in _weak_passwords or len(_admin_pw) < 10:
                    if os.getenv("FLASK_ENV") == "production":
                        raise RuntimeError(
                            "ADMIN_PASSWORD is too weak or still the default value. "
                            "Set a strong ADMIN_PASSWORD in .env before running in production."
                        )
                    log.warning("ADMIN_PASSWORD is weak or default. Change it before going to production.")
                init_db()
                # PATCH-B8: ensure performance indexes also exist in production
                # (previously only ran via __main__).
                try:
                    ensure_indexes()
                except Exception as _exc:
                    log.warning("ensure_indexes failed: %s", _exc)
                seed_admin(os.getenv("ADMIN_EMAIL", "admin@example.com"), _admin_pw)
                seed_local_provider_catalog()
                try:
                    attach_generated_posters()
                except Exception:
                    pass
                app._setup_done = True



# ============================================================================
# V43: Wishlist + Search Autocomplete REMOVED per user request.
# V53 REFACTOR (phase 2): Google OAuth client + /sw.js service worker
# moved to routes/oauth_bp.py. The DB helpers for the OAuth callback now
# live in routes/auth_bp.py (and database.py, where they have always been).
#
# We re-export the public symbols below so any caller still doing
# `from app import _oauth` / `GOOGLE_REDIRECT_URI` keeps working until the
# transitional bridge is removed in phase 5. `_oauth` is a *property-like*
# accessor (a function call returns the live instance) so that callers see
# the post-init_oauth() value, not the None it had at import time.
# ============================================================================
from routes.oauth_bp import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
    get_oauth as _get_oauth,
)


def __getattr__(name):
    """PEP 562: lazy attribute lookup so `from app import _oauth` returns the
    live OAuth client populated by init_oauth() rather than the import-time
    placeholder. Called only when normal lookup fails.
    """
    if name == "_oauth":
        return _get_oauth()
    raise AttributeError(name)


# V53 REFACTOR (phase 2): /auth/google, /auth/google/callback, /sw.js and
# the `google_oauth_enabled` template flag now live entirely in
# routes/oauth_bp.py + routes/auth_bp.py. The Authlib OAuth client is wired
# at blueprint-registration time (see routes/__init__.py -> init_oauth(app)).
#
# The `__getattr__` shim above keeps `from app import _oauth` working until
# the transitional bridge is removed in phase 5.


# PATCH-H1: exempt all /api/* JSON endpoints from CSRF.
# JSON requests cannot be forged via a hidden form, and the session cookie
# uses SameSite=Lax which already blocks cross-site form-style POSTs. Apps
# that need to call these from another origin must add their own auth.
#
# V50.2 MEDIUM: however, attackers CAN reach these endpoints from any
# origin if the browser decides to send cookies. We now add a defence-in-depth
# Origin/Referer check via before_request: for non-GET /api/* requests the
# Origin (or Referer) header must match our host or be absent (curl / native
# app clients). Mismatched origins are rejected with 403.
if csrf is not None:
    for _fn in (
        api_login, api_register, api_logout, api_orders,
        api_validate_player, api_me, api_games, api_game,
        api_payment_methods, api_wallet,
    ):
        try:
            csrf.exempt(_fn)
        except Exception:
            pass


@app.before_request
def _api_origin_guard():
    """V50.2 MEDIUM: reject state-changing /api/* requests whose Origin or
    Referer header points at a different host than this server. Protects
    CSRF-exempt endpoints from being called by malicious sites that
    managed to piggy-back on SameSite=Lax exceptions (e.g. top-level
    navigation POST fallbacks).
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    if not request.path.startswith("/api/"):
        return None
    origin = request.headers.get("Origin", "")
    referer = request.headers.get("Referer", "")
    # Absent Origin+Referer is allowed: real mobile / native / curl clients
    # often omit them. Browsers will send at least one.
    if not origin and not referer:
        return None
    try:
        from urllib.parse import urlparse
        host = request.host  # includes port if non-standard
        for candidate in (origin, referer):
            if not candidate:
                continue
            p = urlparse(candidate)
            if not p.netloc:
                continue
            if p.netloc != host:
                log.warning(
                    "API_ORIGIN_BLOCK path=%s origin=%s referer=%s host=%s ip=%s",
                    request.path, origin, referer, host, get_real_ip(),
                )
                return jsonify({"error": "cross-origin request rejected"}), 403
    except Exception as exc:
        log.warning("_api_origin_guard parse error: %s", exc)
    return None


# ---------------------------------------------------------------------------
# V53 REFACTOR (phase 1): register Blueprints AFTER every helper has been
# defined. auth_bp.py does `from app import safe_next_url, limiter, ...` at
# module top, so it must be imported only at this point — not earlier.
# ---------------------------------------------------------------------------
try:
    from routes import register_blueprints
    register_blueprints(app)
except Exception as _exc:
    log.warning("Blueprint registration failed: %s", _exc)
    raise


if __name__ == "__main__":
    init_db()
    ensure_indexes()
    _admin_pw_dev = os.getenv("ADMIN_PASSWORD", "").strip()
    if not _admin_pw_dev or len(_admin_pw_dev) < 10 or _admin_pw_dev in {"admin", "admin123456", "password", "<CHANGE-THIS-STRONG-PASSWORD>"}:
        log.warning("ADMIN_PASSWORD missing/weak — using temporary dev password 'changeme123!'. Override via .env.")
        _admin_pw_dev = "changeme123!"
    seed_admin(os.getenv("ADMIN_EMAIL", "admin@example.com"), _admin_pw_dev)
    # V50 SECURITY (CB): never turn on the Werkzeug debugger outside explicit
    # development. The debugger console is a remote code execution surface
    # if any attacker can reach it.
    _debug = os.getenv("FLASK_ENV", "development").lower() == "development"
    if os.getenv("FLASK_ENV") == "production":
        _debug = False
    # B104 suppressed: dev-only entry point guarded by __main__; production
    # uses gunicorn via Procfile which binds only to $PORT.
    app.run(host="0.0.0.0", port=5000, debug=_debug)  # nosec B104
