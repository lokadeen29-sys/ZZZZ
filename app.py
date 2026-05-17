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

# --- V35: in-memory settings cache (TTL 30s) to cut SQLite hits per request ---
import time as _time
_SETTINGS_CACHE = {}
_SETTINGS_TTL = 30.0

def get_setting(key, default=None):
    now = _time.time()
    hit = _SETTINGS_CACHE.get(key)
    if hit and hit[1] > now:
        val = hit[0]
        return val if val is not None else default
    val = _db_get_setting(key, default)
    _SETTINGS_CACHE[key] = (val, now + _SETTINGS_TTL)
    return val if val is not None else default

def set_setting(key, value):
    _SETTINGS_CACHE.pop(key, None)
    return _db_set_setting(key, value)

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

# V43: WebP image processing on upload (Pillow)
try:
    from PIL import Image, ImageOps
    # PATCH-M3: cap decompression to prevent "image bomb" DoS
    Image.MAX_IMAGE_PIXELS = 25_000_000  # ~25 MP, plenty for any UI image
    _PIL_OK = True
except Exception:
    _PIL_OK = False
    log.warning("Pillow not installed. Image auto-conversion disabled. Run: pip install Pillow")

# Magic-byte signatures (real type check, not just file extension)
_IMG_MAGIC = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    b"RIFF": "webp",  # WEBP starts with RIFF....WEBP
}

def _detect_image_kind(head_bytes):
    if not head_bytes:
        return None
    for sig, kind in _IMG_MAGIC.items():
        if head_bytes.startswith(sig):
            if kind == "webp" and b"WEBP" not in head_bytes[:16]:
                continue
            return kind
    return None

def process_upload_to_webp(file_storage, dest_dir, base_name, max_w=1200, quality=82):
    """
    Read uploaded image, verify magic bytes, strip EXIF, downscale to max_w,
    and save as WebP. Returns saved filename (e.g. "name.webp") or None on failure.
    Falls back to plain save if Pillow is unavailable.
    """
    try:
        head = file_storage.stream.read(32)
        file_storage.stream.seek(0)
        kind = _detect_image_kind(head)
        if not kind:
            return None
        os.makedirs(dest_dir, exist_ok=True)
        out_name = f"{base_name}.webp"
        out_path = os.path.join(dest_dir, out_name)
        if not _PIL_OK:
            # Fallback: just save original under .webp-suffixed name? No — keep original ext.
            ext = "webp" if kind == "webp" else kind
            out_name = f"{base_name}.{ext}"
            out_path = os.path.join(dest_dir, out_name)
            file_storage.save(out_path)
            return out_name
        # PATCH-H3: verify the file is a valid, non-malicious image BEFORE
        # decoding the full payload. Image.verify() consumes the file so we
        # must reopen for actual processing.
        try:
            _verify_img = Image.open(file_storage.stream)
            _verify_img.verify()
        except Exception as exc:
            log.warning("process_upload_to_webp verify failed: %s", exc)
            return None
        try:
            file_storage.stream.seek(0)
        except Exception:
            return None
        img = Image.open(file_storage.stream)
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        if img.width > max_w:
            ratio = max_w / float(img.width)
            new_h = max(1, int(img.height * ratio))
            # PATCH-L5: use new Resampling enum (Pillow ≥ 10) with fallback.
            _resample = getattr(Image, "Resampling", Image).LANCZOS
            img = img.resize((max_w, new_h), _resample)
        save_kwargs = {"quality": quality, "method": 6}
        if img.mode == "RGBA":
            save_kwargs["lossless"] = False
        img.save(out_path, "WEBP", **save_kwargs)
        return out_name
    except Exception as exc:
        log.warning("process_upload_to_webp failed: %s", exc)
        try:
            file_storage.stream.seek(0)
        except Exception:
            pass
        return None


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
# V50.2 LOW: removed "pdf" from deposit-proof allowed extensions. PDFs
# can embed JavaScript and are a common malware vector; images are
# sufficient for a payment-proof screenshot and much safer to serve back.
ALLOWED_UPLOAD_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}

def _ext_ok(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_UPLOAD_EXTS


# PATCH-H4: magic-byte verification for deposit proofs (prevents file-type
# spoofing such as evil.php renamed to evil.png).
# V50.2 LOW: PDF removed — images only.
_PROOF_MAGIC = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
}

def _proof_magic_ok(file_stream):
    """Verify uploaded file's first bytes match an accepted media type.
    Resets stream position to 0 before returning so the caller can save it."""
    try:
        head = file_stream.read(16)
        file_stream.seek(0)
    except Exception:
        return False
    if not head:
        return False
    for sig, _kind in _PROOF_MAGIC.items():
        if head.startswith(sig):
            return True
    # WebP starts with RIFF....WEBP
    if head.startswith(b"RIFF") and b"WEBP" in head[:16]:
        return True
    return False


# PATCH-H1: SVG sanitiser — strips <script>, on* event handlers, and
# javascript:/data: URIs from admin-uploaded SVGs to prevent stored XSS.
import re as _re_svg

_SVG_SCRIPT_RE = _re_svg.compile(r"<\s*script\b[^>]*>.*?<\s*/\s*script\s*>", _re_svg.IGNORECASE | _re_svg.DOTALL)
_SVG_EVENT_RE = _re_svg.compile(r"\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", _re_svg.IGNORECASE)
_SVG_JS_URI_RE = _re_svg.compile(r"(href|xlink:href|src)\s*=\s*(\"|')\s*(javascript|data):[^\"']*(\"|')", _re_svg.IGNORECASE)
_SVG_FOREIGN_RE = _re_svg.compile(r"<\s*(foreignObject|iframe|object|embed)\b[^>]*>.*?<\s*/\s*\1\s*>", _re_svg.IGNORECASE | _re_svg.DOTALL)

def _sanitise_svg(svg_text):
    """Best-effort SVG XSS sanitiser. Removes scripts, event handlers,
    foreignObject / iframe nodes, and javascript:/data: URLs."""
    if not svg_text:
        return ""
    s = _SVG_SCRIPT_RE.sub("", svg_text)
    s = _SVG_FOREIGN_RE.sub("", s)
    s = _SVG_EVENT_RE.sub("", s)
    s = _SVG_JS_URI_RE.sub(r"\1=\2#\4", s)
    return s


# Make csrf_token() always available in templates even if Flask-WTF missing
if csrf is None:
    @app.context_processor
    def _csrf_noop():
        return {"csrf_token": lambda: ""}


MAIL_SERVER = os.getenv("MAIL_SERVER", "")
MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "1") == "1"
MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "").replace(" ", "").strip()
MAIL_FROM = os.getenv("MAIL_FROM", MAIL_USERNAME or "no-reply@tecnogems.com")
# V67 DELIVERABILITY: friendly From-name, Reply-To, and explicit envelope sender.
# When using Gmail/Workspace SMTP, the envelope sender MUST equal the
# authenticated mailbox or the message fails SPF alignment and lands in Spam.
MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "TecnoGems").strip() or "TecnoGems"
MAIL_REPLY_TO = os.getenv("MAIL_REPLY_TO", "").strip()
# Domain used in Message-ID and List-Unsubscribe URLs.
try:
    _BASE_DOMAIN = BASE_URL.split("//", 1)[-1].split("/", 1)[0]
except Exception:
    _BASE_DOMAIN = "tecnogems.com"


def _aligned_envelope_sender():
    """Return the SMTP envelope sender that aligns with the SMTP login.

    Gmail / Google Workspace REWRITE the From: header to the authenticated
    mailbox if it doesn't match. To preserve a clean From: while still
    passing SPF/DKIM alignment, we set the SMTP-level MAIL FROM (envelope)
    to the authenticated user. Most consumer mailbox providers honour this.
    """
    if MAIL_USERNAME and "@" in MAIL_USERNAME:
        return MAIL_USERNAME
    return MAIL_FROM


# --- Public language system (Arabic default / English optional) ---
PUBLIC_TRANSLATIONS = {
    "home": {"ar": "الرئيسية", "en": "Home"},
    "my_orders": {"ar": "طلباتي", "en": "My Orders"},
    "wallet_records": {"ar": "سجل طلبات الرصيد", "en": "Wallet Requests"},
    "topup_wallet": {"ar": "شحن المحفظة", "en": "Top Up Wallet"},
    "balance": {"ar": "الرصيد", "en": "Balance"},
    "login": {"ar": "دخول", "en": "Login"},
    "register": {"ar": "إنشاء حساب", "en": "Create Account"},
    "logout": {"ar": "خروج", "en": "Logout"},
    "menu": {"ar": "القائمة", "en": "Menu"},
    "hero_pill": {"ar": "✨ منصة شحن الألعاب الأسرع في الشرق الأوسط", "en": "✨ Fast game top-up for global players"},
    "hero_title_1": {"ar": "اشحن لعبتك المفضلة", "en": "Top up your favorite game"},
    "hero_title_2": {"ar": "بضغطة واحدة", "en": "in one simple step"},
    "hero_desc": {"ar": "جواهر، شدات، نقاط CP وأكثر —", "en": "Diamonds, UC, CP and more — fast and secure."},
    "browse_games": {"ar": "تصفح الألعاب", "en": "Browse Games"},
    "available_now": {"ar": "🔥 المتاح الآن", "en": "🔥 Available Now"},
    "games_sections": {"ar": "الألعاب والأقسام المتاحة", "en": "Available Games & Sections"},
    "choose_game": {"ar": "اختر اللعبة أو القسم المناسب مباشرة.", "en": "Choose a game or section directly."},
    "search_game": {"ar": "🔍 ابحث عن لعبة أو قسم...", "en": "🔍 Search for a game or section..."},
    "packages": {"ar": "باقة", "en": "packages"},
    "packages_plural": {"ar": "باقات", "en": "packages"},
    "from": {"ar": "من", "en": "From"},
    "back_games": {"ar": "← العودة للألعاب", "en": "← Back to games"},
    "choose_package": {"ar": "اختر الباقة المناسبة.", "en": "Choose your package."},
    "search_package": {"ar": "🔍 ابحث عن باقة...", "en": "🔍 Search packages..."},
    "buy": {"ar": "شراء", "en": "Buy"},
    "login_to_buy": {"ar": "سجل للشراء", "en": "Login to buy"},
    "no_packages": {"ar": "لا توجد باقات متاحة لهذه اللعبة حاليًا.", "en": "No packages are available for this game right now."},
    "checkout": {"ar": "تأكيد الشراء", "en": "Confirm Purchase"},
    "game": {"ar": "اللعبة", "en": "Game"},
    "package": {"ar": "الباقة", "en": "Package"},
    "price": {"ar": "السعر", "en": "Price"},
    "player_id": {"ar": "معرف اللاعب Player ID", "en": "Player ID"},
    "confirm_order": {"ar": "تأكيد الطلب", "en": "Confirm Order"},
    "example_id": {"ar": "مثال: 123456789", "en": "Example: 123456789"},
    "order": {"ar": "الطلب", "en": "Order"},
    "status": {"ar": "الحالة", "en": "Status"},
    "date": {"ar": "التاريخ / سوريا", "en": "Date / Syria"},
    "waiting": {"ar": "بانتظار التنفيذ", "en": "Waiting"},
    "manual_pending": {"ar": "بانتظار تنفيذ يدوي", "en": "Manual processing"},
    "processing": {"ar": "جاري التنفيذ", "en": "Processing"},
    "completed": {"ar": "مكتمل", "en": "Completed"},
    "rejected": {"ar": "مرفوض", "en": "Rejected"},
    "wallet": {"ar": "المحفظة", "en": "Wallet"},
    "available_balance": {"ar": "الرصيد المتاح", "en": "Available Balance"},
    "support": {"ar": "الدعم", "en": "Support"},
    "amount": {"ar": "المبلغ", "en": "Amount"},
    "payment_method": {"ar": "طريقة الدفع", "en": "Payment Method"},
    "deposit_note": {"ar": "أدخل المبلغ حسب عملة طريقة الدفع المختارة.", "en": "Enter the amount using the selected payment method currency."},
    "method_currency": {"ar": "عملة الطريقة", "en": "Method currency"},
    "address": {"ar": "العنوان / الرقم", "en": "Address / Number"},
    "proof": {"ar": "إثبات الدفع", "en": "Payment proof"},
    "submit_deposit": {"ar": "إرسال طلب الشحن", "en": "Submit Top-up Request"},
    "email": {"ar": "البريد الإلكتروني", "en": "Email"},
    "password": {"ar": "كلمة المرور", "en": "Password"},
    "forgot_password": {"ar": "نسيت كلمة المرور؟", "en": "Forgot password?"},
    "resend_verification": {"ar": "إعادة إرسال رابط التفعيل", "en": "Resend verification link"},
    "name": {"ar": "الاسم", "en": "Name"},
    "phone": {"ar": "رقم الهاتف", "en": "Phone"},
    "confirm_password": {"ar": "تأكيد كلمة المرور", "en": "Confirm Password"},
    "create_account": {"ar": "إنشاء الحساب", "en": "Create Account"},
}

def current_lang():
    # Arabic is the default. English only if explicitly selected in this session.
    return "en" if session.get("lang") == "en" and session.get("lang_user_selected") == "1" else "ar"


def tr(key):
    return PUBLIC_TRANSLATIONS.get(key, {}).get(current_lang(), PUBLIC_TRANSLATIONS.get(key, {}).get("ar", key))

def lang_url(target):
    nxt = request.path
    if nxt.startswith("/lang/"):
        nxt = url_for("home")
    if request.query_string and not nxt.startswith("/lang/"):
        nxt = request.full_path
    return url_for("set_language", lang=target, next=nxt)


def public_price_text(usd_amount):
    try:
        amount = float(usd_amount or 0)
    except Exception:
        amount = 0.0
    if current_lang() == "en":
        return f"${amount:.2f}"
    return wallet_money_text(amount)


def product_public_price(product, game=None):
    return product_display_price(product, game)


def package_public_name(name):
    name = str(name or "")
    # Remove supplier/category labels in both languages.
    remove_terms = ["MENA Direct Topup", "Mena Direct Topup", "Direct Topup", "direct topup", "شحن مباشر"]
    for old in remove_terms:
        name = name.replace(old, "")

    if current_lang() == "en":
        replacements = [
            ("جواهر", "Diamonds"), ("جوهرة", "Diamond"),
            ("شدات ببجي", "PUBG UC"), ("شدات", "UC"),
            ("بطاقات", "Cards"), ("بطاقة", "Card"),
            ("عملات", "Coins"), ("عملة", "Coin"),
            ("قسائم", "Vouchers"), ("قسيمة", "Voucher"),
            ("نقاط", "Points"), ("نقطة", "Point")
        ]
        for old, new in replacements:
            name = name.replace(old, new)
        return re.sub(r"\s+", " ", name).strip(" -–—|")

    # Arabic display: translate provider/product English words even if stored in DB in English.
    replacements = [
        (r"\bdiamonds\b", "جواهر"),
        (r"\bdiamond\b", "جوهرة"),
        (r"\bpubg\s*uc\b", "شدات ببجي"),
        (r"\buc\b", "شدات"),
        (r"\bcards\b", "بطاقات"),
        (r"\bcard\b", "بطاقة"),
        (r"\bcoins\b", "عملات"),
        (r"\bcoin\b", "عملة"),
        (r"\bvouchers\b", "قسائم"),
        (r"\bvoucher\b", "قسيمة"),
        (r"\bpoints\b", "نقاط"),
        (r"\bpoint\b", "نقطة"),
        (r"\bweekly\b", "أسبوعي"),
        (r"\bmonthly\b", "شهري"),
    ]
    for old, new in replacements:
        name = re.sub(old, new, name, flags=re.I)
    return translate_product_name(re.sub(r"\s+", " ", name).strip(" -–—|"))



@app.route("/reset-lang")
def reset_lang():
    session.pop("lang", None)
    session.pop("lang_user_selected", None)
    return redirect(url_for("home"))

@app.route("/lang/<lang>")
def set_language(lang):
    # V35: only switch language if request comes from same-origin click (Referer check).
    # This prevents browser preloads / prefetch / cached requests from accidentally toggling language.
    referer = request.headers.get("Referer", "")
    same_origin = referer and (referer.startswith(BASE_URL) or referer.startswith(request.host_url))
    if same_origin:
        if lang == "en":
            session["lang"] = "en"
            session["lang_user_selected"] = "1"
        else:
            session["lang"] = "ar"
            session.pop("lang_user_selected", None)
    nxt = request.args.get("next") or (referer if same_origin else None) or url_for("home")
    if not isinstance(nxt, str) or not nxt.startswith("/") or nxt.startswith("/lang/"):
        nxt = url_for("home")
    resp = redirect(nxt)
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp



def get_pricing_mode():
    """base pricing mode: usd | auto_syp"""
    mode = get_setting("pricing_mode", "usd")
    return mode if mode in ("usd", "auto_syp") else "usd"


def get_display_currency():
    return "USD" if get_pricing_mode() == "usd" else "SYP"


def manual_price_edit_enabled():
    return get_setting("manual_price_edit_enabled", "0") == "1"


def get_usd_syp_rate():
    try:
        return float(get_setting("usd_syp_rate", "15000") or 15000)
    except Exception:
        return 15000.0


def display_price_value(usd_amount, currency=None):
    try:
        amount = float(usd_amount or 0)
    except Exception:
        amount = 0.0
    cur = currency or get_display_currency()
    if cur == "SYP":
        return round(amount * get_usd_syp_rate(), 0)
    return round(amount, 2)


def display_price_text(usd_amount, currency=None):
    cur = currency or get_display_currency()
    val = display_price_value(usd_amount, cur)
    if cur == "SYP":
        return f"{val:,.0f} ل.س"
    return f"{val:.2f}$"


def product_manual_syp(product):
    try:
        return float((product or {}).get("manual_price_syp") or 0)
    except Exception:
        return 0.0


def manual_syp_override_active(product):
    return manual_price_edit_enabled() and product_manual_syp(product) > 0


def product_sell_usd(product, game=None):
    product = product or {}
    rate = get_usd_syp_rate()
    try:
        sell_usd = float(product.get("sell_price") or 0)
    except Exception:
        sell_usd = 0.0

    # Manual SYP is an override only when enabled and a value exists.
    if manual_syp_override_active(product) and rate > 0:
        return product_manual_syp(product) / rate

    return sell_usd


def product_display_price(product, game=None):
    product = product or {}

    if current_lang() == "en":
        return f"${product_sell_usd(product, game):.2f}"

    # Manual SYP overrides the selected base pricing mode only if enabled.
    if manual_syp_override_active(product):
        return f"{product_manual_syp(product):,.0f} ل.س"

    if get_pricing_mode() == "auto_syp":
        return display_price_text(product.get("sell_price", 0), "SYP")

    return display_price_text(product.get("sell_price", 0), "USD")


def product_profit_percent(product, game=None):
    try:
        base = float((product or {}).get("base_price") or 0)
        sell = float(product_sell_usd(product, game))
        if base <= 0:
            return None
        return round(((sell / base) - 1) * 100, 2)
    except Exception:
        return None


def wallet_money_text(amount):
    try:
        amount = float(amount or 0)
    except Exception:
        amount = 0.0
    if current_lang() == "en":
        return f"${amount:.2f}"
    if get_display_currency() == "SYP":
        return f"{amount * get_usd_syp_rate():,.0f} ل.س"
    return f"{amount:.2f}$"







def email_verification_is_enabled():
    return get_setting("email_verification_enabled", "0") == "1"


def email_is_configured():
    return bool(MAIL_SERVER and MAIL_USERNAME and MAIL_PASSWORD and MAIL_FROM)


# V42 batch2: async email queue ----------------------------------------
email_queue = Queue()


def _send_email_sync(to_email, subject, body, html_body=None):
    if not email_is_configured():
        app.logger.warning("Email skipped (SMTP not configured): to=%s subject=%s", to_email, subject)
        return
    # Build multipart message (plain + HTML) to improve deliverability
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    # V67 DELIVERABILITY: From, Sender, Reply-To
    # Gmail rewrites From: to the auth user if MAIL_FROM differs, but if we
    # explicitly set Sender: to the auth user we keep the friendly From: AND
    # pass SPF/DKIM alignment. Reply-To routes user replies to the inbox we
    # actually monitor.
    msg["From"] = formataddr((MAIL_FROM_NAME, MAIL_FROM))
    if MAIL_USERNAME and MAIL_USERNAME.lower() != MAIL_FROM.lower():
        msg["Sender"] = MAIL_USERNAME
    msg["To"] = to_email
    msg["Reply-To"] = MAIL_REPLY_TO or MAIL_FROM
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=MAIL_FROM.split("@")[-1] if "@" in MAIL_FROM else _BASE_DOMAIN)
    # V67 DELIVERABILITY headers for transactional mail.
    # IMPORTANT: do NOT add "Precedence: bulk" or a mailto-only
    # List-Unsubscribe — those are signals for newsletters and push
    # account-verification mail straight into Spam at Gmail.
    msg["X-Mailer"] = "TecnoGems Transactional Mailer"
    msg["X-Auto-Response-Suppress"] = "All"
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Entity-Ref-ID"] = make_msgid(domain=_BASE_DOMAIN).strip("<>")
    # MIME-Version is required by some spam filters even though Python adds
    # it automatically — set it explicitly so it always lands at the top.
    msg["MIME-Version"] = "1.0"

    # Attach plain text first (fallback)
    part_text = MIMEText(body, "plain", "utf-8")
    msg.attach(part_text)
    # Attach HTML if provided
    if html_body:
        part_html = MIMEText(html_body, "html", "utf-8")
        msg.attach(part_html)
    try:
        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=30) as server:
            server.ehlo()
            if MAIL_USE_TLS:
                server.starttls()
                server.ehlo()
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
            # V67 DELIVERABILITY: pass an explicit envelope sender that is
            # always the authenticated mailbox. This is the SPF-aligned
            # address — without it Gmail rewrites the bounce path and the
            # message can fail SPF.
            server.send_message(
                msg,
                from_addr=_aligned_envelope_sender(),
                to_addrs=[to_email],
            )
        app.logger.info("Email sent successfully to=%s subject=%s", to_email, subject)
    except smtplib.SMTPAuthenticationError as exc:
        app.logger.error(
            "Email AUTH FAILED to=%s subject=%s: %s. "
            "تأكد من استخدام Gmail App Password (16 حرف بدون مسافات) في MAIL_PASSWORD وليس كلمة مرور الحساب العادية.",
            to_email, subject, exc,
        )
        raise
    except smtplib.SMTPException as exc:
        app.logger.error("Email SMTP error to=%s subject=%s: %s", to_email, subject, exc)
        raise
    except Exception as exc:
        app.logger.error("Email send failed to=%s subject=%s: %s", to_email, subject, exc)
        raise


def _email_worker():
    while True:
        item = email_queue.get()
        try:
            if item is None:
                continue
            _send_email_sync(*item)
        except Exception as exc:
            app.logger.error("email_worker error: %s", exc)
        finally:
            email_queue.task_done()


# spin up 2 worker threads (lightweight) for parallel SMTP sends
for _i in range(2):
    threading.Thread(target=_email_worker, daemon=True, name=f"email-worker-{_i}").start()


def send_email(to_email, subject, body, html_body=None):
    """Non-blocking: enqueue email and return immediately.

    V45: prefer durable RQ queue when REDIS_URL is configured; fall back to
    the in-process thread queue otherwise (backwards compatible).
    """
    if not email_is_configured():
        raise RuntimeError("SMTP email settings are missing. Check .env")
    try:
        from tasks import enqueue_email, USE_RQ
        if USE_RQ:
            enqueue_email(
                to_email, subject, body,
                MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD,
                MAIL_USE_TLS, MAIL_FROM,
                html_body=html_body,
                mail_from_name=MAIL_FROM_NAME,
                reply_to=MAIL_REPLY_TO or MAIL_FROM,
            )
            return
    except Exception as exc:
        app.logger.warning("RQ enqueue failed, falling back to thread queue: %s", exc)
    email_queue.put((to_email, subject, body, html_body))


def _build_email_html(title, greeting, message, button_text, button_url, footer_note):
    """Build a professional HTML email that passes spam filters.

    Key anti-spam techniques:
    - Proper HTML structure with DOCTYPE
    - Inline CSS only (no external stylesheets)
    - Good text-to-image ratio (no images)
    - Clear unsubscribe/ignore note
    - Mobile-responsive design
    - No spam trigger words in subject handled by callers
    """
    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background-color:#0f172a;font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color:#0f172a;">
<tr><td align="center" style="padding:40px 20px;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:520px;background-color:#1e293b;border-radius:16px;border:1px solid #334155;">
<!-- Header -->
<tr><td style="padding:32px 32px 0;text-align:center;">
  <h1 style="margin:0;font-size:28px;font-weight:800;color:#a78bfa;letter-spacing:-0.5px;">TecnoGems</h1>
  <p style="margin:8px 0 0;font-size:13px;color:#64748b;">منصة شحن الألعاب</p>
</td></tr>
<!-- Body -->
<tr><td style="padding:32px;">
  <h2 style="margin:0 0 16px;font-size:20px;color:#f1f5f9;font-weight:700;">{greeting}</h2>
  <p style="margin:0 0 24px;font-size:15px;line-height:1.7;color:#cbd5e1;">{message}</p>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
  <tr><td align="center">
    <a href="{button_url}" target="_blank"
       style="display:inline-block;padding:14px 36px;background-color:#7c3aed;color:#ffffff;font-size:16px;font-weight:700;text-decoration:none;border-radius:10px;letter-spacing:0.3px;">
      {button_text}
    </a>
  </td></tr>
  </table>
  <p style="margin:24px 0 0;font-size:13px;color:#64748b;line-height:1.6;">
    إذا لم يعمل الزر، انسخ الرابط التالي والصقه في المتصفح:<br>
    <a href="{button_url}" style="color:#a78bfa;word-break:break-all;font-size:12px;">{button_url}</a>
  </p>
</td></tr>
<!-- Footer -->
<tr><td style="padding:0 32px 32px;border-top:1px solid #334155;">
  <p style="margin:20px 0 0;font-size:12px;color:#475569;line-height:1.6;text-align:center;">
    {footer_note}<br>
    <a href="{BASE_URL}/email-info" style="color:#64748b;text-decoration:underline;">لماذا وصلتك هذه الرسالة؟</a><br>
    <span style="color:#64748b;">&copy; TecnoGems - جميع الحقوق محفوظة</span>
  </p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def send_verification_email(to_email, token):
    link = f"{BASE_URL}/verify-email/{token}"
    # V67 DELIVERABILITY: richer plain-text body. A near-empty plain-text
    # part with a heavy HTML part is one of the strongest spam signals at
    # Gmail. Match the HTML content closely in plain text.
    body = f"""مرحبًا بك في TecnoGems

شكرًا لإنشاء حسابك. لتفعيل بريدك الإلكتروني والبدء في استخدام المنصة،
افتح الرابط التالي خلال 24 ساعة:

{link}

إذا لم تطلب إنشاء حساب على TecnoGems يمكنك تجاهل هذه الرسالة بأمان،
ولن يتم إنشاء أي حساب باستخدام بريدك.

— فريق TecnoGems
{BASE_URL}

ملاحظة: هذه رسالة تلقائية لتأكيد البريد الإلكتروني، يرجى عدم الرد عليها.
للدعم تواصل معنا عبر صفحة الدعم على الموقع.
"""
    html_body = _build_email_html(
        title="تفعيل حسابك - TecnoGems",
        greeting="مرحبًا بك في TecnoGems!",
        message="شكرًا لإنشاء حسابك. لتفعيل بريدك الإلكتروني والبدء في استخدام المنصة، اضغط على الزر أدناه. صلاحية الرابط 24 ساعة.",
        button_text="تفعيل الحساب",
        button_url=link,
        footer_note="إذا لم تقم بإنشاء حساب في TecnoGems، يمكنك تجاهل هذه الرسالة بأمان. هذه رسالة تلقائية، لا تردّ عليها.",
    )
    # V62.1 FIX: send synchronously so SMTP errors surface to the caller
    # (registration / resend-verification) instead of being silently swallowed
    # by the background queue. The async send_email() path was the reason
    # users complained "test email arrives but verification email never does":
    # send_email() returned immediately and any SMTP failure happened later
    # in a thread / RQ worker and never reached the user.
    if not email_is_configured():
        raise RuntimeError("SMTP email settings are missing. Check .env")
    _send_email_sync(to_email, "TecnoGems - تفعيل حسابك", body, html_body=html_body)


def send_password_reset_email(to_email, token):
    link = f"{BASE_URL}/reset-password/{token}"
    # V67 DELIVERABILITY: plain text mirrors HTML content for a healthier
    # text-to-html ratio.
    body = f"""مرحبًا

تلقينا طلبًا لإعادة تعيين كلمة المرور الخاصة بحسابك على TecnoGems.

لإنشاء كلمة مرور جديدة، افتح الرابط التالي:
{link}

صلاحية الرابط ساعة واحدة فقط من تاريخ إرسال هذه الرسالة.
إذا لم تطلب استعادة كلمة المرور يمكنك تجاهل هذه الرسالة، حسابك آمن
ولن يتم إجراء أي تغيير.

— فريق TecnoGems
{BASE_URL}

ملاحظة: هذه رسالة تلقائية، يرجى عدم الرد عليها.
للدعم تواصل معنا عبر صفحة الدعم على الموقع.
"""
    html_body = _build_email_html(
        title="استعادة كلمة المرور - TecnoGems",
        greeting="استعادة كلمة المرور",
        message="تلقينا طلبًا لإعادة تعيين كلمة المرور الخاصة بحسابك. اضغط على الزر أدناه لإنشاء كلمة مرور جديدة. صلاحية الرابط ساعة واحدة فقط.",
        button_text="إعادة تعيين كلمة المرور",
        button_url=link,
        footer_note="إذا لم تطلب استعادة كلمة المرور، يمكنك تجاهل هذه الرسالة. حسابك آمن. هذه رسالة تلقائية، لا تردّ عليها.",
    )
    # V62.1 FIX: same reason as send_verification_email — send synchronously
    # so the user gets a real error message when SMTP misbehaves.
    if not email_is_configured():
        raise RuntimeError("SMTP email settings are missing. Check .env")
    _send_email_sync(to_email, "TecnoGems - استعادة كلمة المرور", body, html_body=html_body)


def send_email_change_confirmation(to_email, token):
    link = f"{BASE_URL}/confirm-email-change/{token}"
    body = f"""مرحبًا

تلقينا طلبًا لتغيير البريد الإلكتروني المرتبط بحسابك على TecnoGems.

لتأكيد التغيير، افتح الرابط التالي:
{link}

إذا لم تطلب تغيير البريد يمكنك تجاهل هذه الرسالة، ولن يتم إجراء أي
تعديل على حسابك.

— فريق TecnoGems
{BASE_URL}

ملاحظة: هذه رسالة تلقائية، يرجى عدم الرد عليها.
"""
    html_body = _build_email_html(
        title="تأكيد تغيير البريد - TecnoGems",
        greeting="تأكيد تغيير البريد الإلكتروني",
        message="تلقينا طلبًا لتغيير البريد الإلكتروني المرتبط بحسابك. اضغط على الزر أدناه لتأكيد التغيير.",
        button_text="تأكيد تغيير البريد",
        button_url=link,
        footer_note="إذا لم تطلب تغيير البريد الإلكتروني، يمكنك تجاهل هذه الرسالة.",
    )
    # V62.1 FIX (extended): send synchronously so SMTP errors surface to the
    # caller in profile() instead of being silently swallowed by the background
    # queue. The async send_email() path returned immediately and any SMTP
    # failure happened later in a thread / RQ worker and never reached the
    # user, so they always saw "تم الإرسال" even when the email never left.
    if not email_is_configured():
        raise RuntimeError("SMTP email settings are missing. Check .env")
    _send_email_sync(to_email, "TecnoGems - تأكيد تغيير البريد", body, html_body=html_body)


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



@app.template_filter("public_package_name")
def public_package_name_filter(value):
    return package_public_name(value)

@app.template_filter("syria_time")
def syria_time(value):
    """تحويل timestamp إلى توقيت سوريا UTC+3."""
    try:
        ts = int(value)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(timezone(timedelta(hours=3)))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return value


@app.template_filter("money")
def money(amount):
    return display_price_text(amount)

@app.template_filter("clean_package_name")
def clean_package_name(value):
    """تنظيف أسماء الباقات المعروضة للمستخدم من عبارات المزود الفنية."""
    text = str(value or "")
    patterns = [
        r"\bMENA\s+Direct\s+Topup\b\s*-?\s*",
        r"\bMena\s+Direct\s+Topup\b\s*-?\s*",
        r"\bmena\s+direct\s+topup\b\s*-?\s*",
    ]
    for pat in patterns:
        text = re.sub(pat, "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s{2,}", " ", text).strip(" -–—\t\n")
    return text or str(value or "")



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


def safe_next_url(default_endpoint="home", **url_for_kwargs):
    """PATCH-B4: now accepts kwargs forwarded to url_for() so callers like
    safe_next_url("products", provider=p, game_key=k) no longer crash with
    TypeError. The ?next= parameter still wins when present and safe.

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
        endpoint = (request.endpoint or "")
        whitelist = {
            "admin_2fa_setup", "admin_2fa_confirm",
            "admin_2fa_challenge", "admin_2fa_disable",
            "admin_2fa_regenerate_backup_codes",
        }
        if endpoint not in whitelist:
            if int(user.get("totp_enabled") or 0) == 1:
                if not session.get("admin_2fa_verified"):
                    flash("يرجى إدخال رمز المصادقة الثنائية للمتابعة.", "warning")
                    return redirect(url_for("admin_2fa_challenge",
                                            next=request.full_path))
            else:
                if get_setting("admin_2fa_required", "0") == "1":
                    flash("يجب تفعيل المصادقة الثنائية لحسابات الإدارة.", "warning")
                    return redirect(url_for("admin_2fa_setup"))
        return fn(*args, **kwargs)
    return wrapper



@app.template_filter("order_status_label")
def order_status_label(status):
    labels = {
        "waiting": "بانتظار التنفيذ",
        "processing": "جاري التنفيذ",
        "supplier_pending": "جاري التنفيذ",
        "manual_pending": "بانتظار تنفيذ يدوي",
        "completed": "مكتمل",
        "rejected": "مرفوض",
        "pending": "معلق",
    }
    return labels.get(status, status)


@app.template_filter("order_status_class")
def order_status_class(status):
    classes = {
        "waiting": "waiting",
        "processing": "processing",
        "supplier_pending": "processing",
        "manual_pending": "pending",
        "completed": "completed",
        "rejected": "rejected",
        "pending": "pending",
    }
    return classes.get(status, "pending")




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
    return redirect(safe_next_url("home"))

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


@app.route("/robots.txt")
def robots_txt():
    return app.send_static_file("robots.txt")

@app.route("/manifest.json")
def manifest_json():
    return app.send_static_file("manifest.json")


# V67 DELIVERABILITY: public, no-auth informational page about our mail.
# Linked from email footers — boosts Gmail's trust signal that the sender
# operates a real, navigable HTTPS site for the From: domain. Also serves
# as a no-cost replacement for List-Unsubscribe (removed from headers
# because it incorrectly classified transactional mail as bulk).
@app.route("/email-info")
def email_info():
    html = """<!doctype html>
<html lang="ar" dir="rtl"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>عن رسائل البريد الإلكتروني — TecnoGems</title>
<meta name="robots" content="index,follow">
<style>
body{{font-family:Arial,Helvetica,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:32px;line-height:1.8}}
main{{max-width:720px;margin:0 auto;background:#1e293b;padding:32px;border-radius:12px;border:1px solid #334155}}
h1{{color:#a78bfa;margin-top:0}}
h2{{color:#c4b5fd;margin-top:28px;font-size:18px}}
a{{color:#a78bfa}}
code{{background:#0f172a;padding:2px 6px;border-radius:4px;font-size:13px}}
.note{{background:#0f172a;padding:14px 18px;border-right:3px solid #7c3aed;border-radius:6px;margin:16px 0}}
</style>
</head><body><main>
<h1>عن رسائل البريد الإلكتروني من TecnoGems</h1>

<p>تُرسل TecnoGems رسائل بريد إلكتروني <strong>خدمية فقط</strong> (transactional)
للأشخاص الذين أنشأوا حسابًا على المنصة، وذلك في الحالات التالية:</p>
<ul>
  <li>تفعيل البريد الإلكتروني عند إنشاء حساب جديد.</li>
  <li>استعادة كلمة المرور بناءً على طلبك.</li>
  <li>تأكيد تغيير البريد الإلكتروني المرتبط بحسابك.</li>
</ul>

<h2>لماذا وصلتك هذه الرسالة؟</h2>
<p>نحن لا نُرسل رسائل ترويجية أو نشرات بريدية. إذا وصلتك رسالة منا فهذا يعني
أن أحدًا (غالبًا أنت) أدخل بريدك في صفحة إنشاء الحساب أو استعادة كلمة المرور
على <a href="{base_url}">{domain}</a>.</p>

<div class="note">
لم تنشئ حسابًا؟ يمكنك تجاهل الرسالة بأمان، فلن يُفعَّل أي حساب على بريدك إلا
بالنقر على رابط التفعيل.
</div>

<h2>إيقاف الرسائل</h2>
<p>بما أن جميع الرسائل خدمية ومرتبطة بحسابك، فإن أبسط طريقة لإيقافها هي
حذف الحساب من <a href="{base_url}/profile">صفحة الملف الشخصي</a>،
أو التواصل مع فريق الدعم.</p>

<h2>المُرسل</h2>
<p>تصل الرسائل من العنوان الموضح في حقل <code>From</code>،
وهو عنوان معتمد ومُهيأ بسجلات SPF و DKIM و DMARC على نطاق
<code>{domain}</code>.</p>

<h2>تواصل</h2>
<p>للإبلاغ عن رسالة مشبوهة أو طلب الدعم، تواصل معنا عبر
<a href="{base_url}/">الموقع الرسمي</a>.</p>

<p style="text-align:center;margin-top:32px;color:#64748b;font-size:13px;">
&copy; TecnoGems — جميع الحقوق محفوظة
</p>
</main></body></html>""".format(base_url=BASE_URL, domain=_BASE_DOMAIN)
    resp = Response(html, mimetype="text/html; charset=utf-8")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.route("/sitemap.xml")
def sitemap_xml():
    # V43: full sitemap with hreflang ar/en alternates + per-game URLs
    static_paths = ["/", "/login", "/register", "/games"]
    game_paths = []
    try:
        for g in list_public_games(True):
            p = g.get("provider"); k = g.get("game_key")
            if p and k:
                game_paths.append(f"/products/{p}/{k}")
    except Exception as exc:
        log.warning("sitemap games enumeration failed: %s", exc)

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
             'xmlns:xhtml="http://www.w3.org/1999/xhtml">']

    def _entry(path, prio="0.8", freq="weekly"):
        loc_ar = f"{BASE_URL}{path}"
        loc_en = f"{BASE_URL}/lang/en?next={path}"
        lines.append(f"  <url><loc>{loc_ar}</loc>"
                     f"<changefreq>{freq}</changefreq><priority>{prio}</priority>"
                     f'<xhtml:link rel="alternate" hreflang="ar" href="{loc_ar}"/>'
                     f'<xhtml:link rel="alternate" hreflang="en" href="{loc_en}"/>'
                     f'<xhtml:link rel="alternate" hreflang="x-default" href="{loc_ar}"/>'
                     f"</url>")

    for p in static_paths:
        _entry(p, "1.0" if p == "/" else "0.7", "daily" if p == "/" else "weekly")
    for p in game_paths:
        _entry(p, "0.8", "weekly")
    lines.append("</urlset>")
    return Response("\n".join(lines), mimetype="application/xml")


# Back-compat: redirect any remaining /legacy/... links to clean URLs (single rule)
@app.route("/legacy/<path:rest>")
def legacy_redirect(rest):
    return redirect("/" + rest, code=301)


# Secure proof file delivery (login required, owner-or-admin via DB check)
@app.route("/uploads/proof/<path:filename>")
@login_required
def serve_proof(filename):
    user = current_user()
    if not user:
        abort(403)
    safe = secure_filename(filename)
    if safe != filename:
        abort(400)

    is_admin = user.get("role") == "admin"

    if not can_download_proof(user["id"], is_admin, safe):
        log_audit(
            "PROOF_DOWNLOAD_DENIED",
            actor_id=user["id"],
            metadata={"filename": safe},
        )
        abort(403)

    full = os.path.join(app.config["UPLOAD_FOLDER"], safe)
    if not os.path.exists(full):
        # Return 403 instead of 404 to avoid file-existence enumeration
        abort(403)

    log_audit(
        "PROOF_DOWNLOAD",
        actor_id=user["id"],
        metadata={"filename": safe, "admin_viewing": is_admin},
    )
    resp = send_from_directory(app.config["UPLOAD_FOLDER"], safe, as_attachment=False)
    resp.headers["Cache-Control"] = "no-store"
    return resp


# V50 SECURITY (H4): explicitly block the legacy public path.
# Flask's built-in static handler would otherwise happily serve anything
# left in static/uploads/ to the world without auth. This route takes
# precedence and forces a 403.
@app.route("/static/uploads/<path:_ignored>")
def _block_static_uploads(_ignored):
    abort(403)


# --- Legal / info pages ---
@app.route("/privacy")
def privacy():
    return render_template("privacy.html", title="سياسة الخصوصية",
                           seo_title="سياسة الخصوصية - TecnoGems",
                           seo_description="سياسة الخصوصية لمنصة TecnoGems لشحن الألعاب.")

@app.route("/terms")
def terms():
    return render_template("terms.html", title="شروط الاستخدام",
                           seo_title="شروط الاستخدام - TecnoGems",
                           seo_description="شروط استخدام منصة TecnoGems لشحن الألعاب.")

@app.route("/refund")
def refund():
    return render_template("refund.html", title="سياسة الاسترجاع",
                           seo_title="سياسة الاسترجاع - TecnoGems",
                           seo_description="سياسة الاسترجاع والاستبدال في منصة TecnoGems.")

@app.route("/contact")
def contact():
    return render_template("contact.html", title="اتصل بنا",
                           seo_title="اتصل بنا - TecnoGems",
                           seo_description="تواصل مع فريق دعم TecnoGems عبر واتساب أو تيليجرام أو البريد الإلكتروني.")


@app.route("/")
@app.route("/legacy")
def home():
    games = list_public_games(True)
    # V68: استبعاد ألعاب السيرفر المُعطّل من الواجهة الرئيسية بالكامل.
    _s1 = (get_setting("show_server1", "1") == "1")
    _s2 = (get_setting("show_server2", "1") == "1")
    games = [g for g in games
             if (g.get("provider") == "server1" and _s1)
             or (g.get("provider") == "server2" and _s2)]
    groups = list_public_product_groups_for_home() if get_setting("show_groups_direct", "0") == "1" else []
    if groups:
        grouped_keys = {(g.get("provider"), g.get("game_key")) for g in groups}
        games = [g for g in games if (g.get("provider"), g.get("game_key")) not in grouped_keys]
    recent_orders = []
    user = current_user()
    if user:
        recent_orders = list_user_orders(user["id"])[:3]
    all_stats = stats()
    # V55: Homepage shows ONLY games flagged by admin (show_on_home=1). If the
    # admin hasn't picked any, fall back to the first 8 active games with
    # packages so the page is never blank.
    home_selected = list_home_games()
    # V68: استبعاد ألعاب السيرفر المُعطّل من القائمة المختارة للرئيسية أيضاً.
    home_selected = [g for g in home_selected
                     if (g.get("provider") == "server1" and _s1)
                     or (g.get("provider") == "server2" and _s2)]
    if home_selected:
        # احترم إعدادات الأدمن حتى لو اللعبة لا تملك باقات بعد.
        featured = [g for g in home_selected if g.get("product_count", 0) > 0] or home_selected
    else:
        featured = [g for g in games if g.get("product_count", 0) > 0][:8]
    has_more_games = len([g for g in games if g.get("product_count", 0) > 0]) > len(featured)
    # V66: homepage section toggles + editable testimonial copy.
    # Empty admin values fall back to the bilingual defaults below.
    _t_defaults = [
        {
            "name_ar": "أحمد ع.", "name_en": "Ahmad A.",
            "game": "PUBG",
            "text_ar": "أسرع موقع شحن جربته. وصلتني الـUC قبل ما أكمل أكتب الإيميل!",
            "text_en": "Fastest top-up I've ever used. UC arrived before I finished typing my email!",
        },
        {
            "name_ar": "ليث م.", "name_en": "Layth M.",
            "game": "Free Fire",
            "text_ar": "الأسعار ممتازة والدعم رد عليّ خلال دقيقتين. صار موقعي الثابت.",
            "text_en": "Great prices and the team replied in two minutes. My go-to site now.",
        },
        {
            "name_ar": "سارة ك.", "name_en": "Sara K.",
            "game": "Genshin",
            "text_ar": "الواجهة جميلة جداً والدفع كان سهل. شحنت من الجوال بثوانٍ.",
            "text_en": "Beautiful UI and easy checkout. Topped up from my phone in seconds.",
        },
    ]
    is_en = current_lang() == "en"
    testimonials = []
    for i, d in enumerate(_t_defaults, start=1):
        nm = (get_setting(f"testimonial_{i}_name", "") or "").strip()
        gm = (get_setting(f"testimonial_{i}_game", "") or "").strip()
        tx = (get_setting(f"testimonial_{i}_text", "") or "").strip()
        testimonials.append({
            "name": nm or (d["name_en"] if is_en else d["name_ar"]),
            "game": gm or d["game"],
            "text": tx or (d["text_en"] if is_en else d["text_ar"]),
        })
    return render_template("home.html", games=games, home_groups=groups,
        featured_games=featured,
        has_more_games=has_more_games,
        recent_orders=recent_orders,
        games_count=len(games) + len(groups),
        completed_orders_count=all_stats.get("completed", 0) if all_stats else 0,
        show_popular_bar=(get_setting("show_popular_bar", "1") == "1"),
        show_testimonials=(get_setting("show_testimonials", "1") == "1"),
        testimonials=testimonials)


# ---------------------------------------------------------------------------
# V53 REFACTOR (phase 1): auth routes have moved to routes/auth_bp.py.
#
# Moved here:
#   /register, /verify-email/<token>, /resend-verification,
#   /forgot-password, /reset-password/<token>, /login, /logout
#   (/auth/google and /auth/google/callback also moved — see below.)
#
# The Blueprint is registered at the very bottom of this file so that every
# helper function (limiter, safe_next_url, validate_password_strength, …)
# it imports from `app` has already been defined at import time.
# ---------------------------------------------------------------------------


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    orders = list_user_orders(user["id"])[:5]
    return render_template("dashboard.html", orders=orders)


@app.route("/servers")
@app.route("/legacy/servers")
def servers():
    return redirect(url_for("home"))


@app.route("/games/<provider>")
@app.route("/legacy/games/<provider>")
def games(provider):
    if provider not in ("server1", "server2"):
        abort(404)
    games = list_games(provider)
    return render_template("games.html", provider=provider, games=games)


# V55: Landing page for "عرض جميع الألعاب" — shows every active game in one grid.
@app.route("/all-games")
@app.route("/legacy/all-games")
def all_games():
    games = [g for g in list_public_games(True) if g.get("product_count", 0) > 0]
    # V68: استبعاد ألعاب السيرفر المُعطّل من صفحة "كل الألعاب" أيضاً.
    _s1 = (get_setting("show_server1", "1") == "1")
    _s2 = (get_setting("show_server2", "1") == "1")
    games = [g for g in games
             if (g.get("provider") == "server1" and _s1)
             or (g.get("provider") == "server2" and _s2)]
    groups = list_public_product_groups_for_home() if get_setting("show_groups_direct", "0") == "1" else []
    if groups:
        grouped_keys = {(g.get("provider"), g.get("game_key")) for g in groups}
        games = [g for g in games if (g.get("provider"), g.get("game_key")) not in grouped_keys]
    return render_template("all_games.html", games=games, home_groups=groups)


@app.route("/products/<provider>/<game_key>")
@app.route("/legacy/products/<provider>/<game_key>")
def products(provider, game_key):
    game = get_game(provider, game_key)
    if not game:
        abort(404)
    groups = list_product_groups(provider, game_key, True)
    if groups:
        return render_template("product_groups.html", provider=provider, game=game, groups=groups)
    products = list_products(provider, game_key)
    return render_template("products.html", provider=provider, game=game, products=products, group=None)


@app.route("/products/<provider>/<game_key>/group/<int:group_id>")
def products_group(provider, game_key, group_id):
    game = get_game(provider, game_key)
    group = get_product_group(group_id)
    if not game or not group or group["provider"] != provider or group["game_key"] != game_key or not group.get("active", 1):
        abort(404)
    products = list_products(provider, game_key, group_id=group_id)
    return render_template("products.html", provider=provider, game=game, products=products, group=group)


@app.route("/checkout/<int:product_id>", methods=["GET", "POST"])
@app.route("/legacy/checkout/<int:product_id>", methods=["GET", "POST"])
@(limiter.limit("20 per minute") if limiter else (lambda f: f))
@login_required
def checkout(product_id):
    user = current_user()
    if not user:
        session.clear()
        flash("انتهت الجلسة أو لم يعد الحساب موجودًا. يرجى تسجيل الدخول مرة أخرى.", "warning")
        return redirect("/login")

    product = get_product(product_id)
    if not product:
        abort(404)
    game = get_game(product["provider"], product["game_key"])
    if not game:
        abort(404)
    if request.method == "POST":
        player_id = request.form.get("player_id", "").strip()
        # V50 SECURITY (C1): bound player_id length to prevent storage-bomb
        # attacks where MBs of garbage are shoved into orders.player_id.
        if len(player_id) < 3 or len(player_id) > MAX_PLAYER_ID_LEN:
            flash("معرف اللاعب غير صحيح", "danger")
            return redirect(request.url)

        try:
            order_id, code = create_order(user, product, game, player_id)
        except InsufficientBalance:
            flash("رصيدك غير كافٍ", "danger")
            return redirect(safe_next_url("home"))
        except Exception as _e:
            log.exception("create_order failed: %s", _e)
            flash("حدث خطأ أثناء إنشاء الطلب، يرجى المحاولة مجدداً", "danger")
            return redirect(request.url)

        enqueue_order_job(order_id, product, player_id)
        # V67: clearer confirmation with a clickable link to the orders page
        # so the user can immediately track execution status. Also tells the
        # user the order has been received (matches the wording the user
        # asked for: "تم استلام طلب الشراء وبانتظار التنفيذ").
        track_url = url_for("orders")
        flash(Markup(
            f'تم استلام الطلب <strong>{code}</strong> وبانتظار بدء التنفيذ. '
            f'لمتابعة حالة طلبك <a href="{track_url}" class="alert-link"><strong>اضغط هنا</strong></a>.'
        ), "success")
        return redirect(url_for("orders"))
    return render_template(
        "checkout.html",
        product=product,
        game=game,
        show_server1=get_setting("show_server1", "1"),
        show_server2=get_setting("show_server2", "1"),
        # V71: نمرّر حالة الإعداد للقالب حتى يقرّر إظهار/إخفاء زر "تحقق من الاسم"
        player_validation_enabled=(get_setting("player_validation_api_enabled", "0") == "1"),
    )



@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        new_email = request.form.get("new_email", "").strip().lower()

        if not name:
            flash("الاسم مطلوب", "danger")
            return redirect(url_for("profile"))

        update_user_profile(user["id"], name, phone)

        if new_email and new_email != user["email"]:
            if get_user_by_email(new_email):
                flash("هذا البريد مستخدم في حساب آخر", "danger")
            else:
                token = secrets.token_urlsafe(32)
                set_pending_email_change(user["id"], new_email, token)
                try:
                    send_email_change_confirmation(new_email, token)
                    flash("تم إرسال رابط تأكيد تغيير البريد إلى البريد الجديد", "success")
                except Exception:
                    flash("تم حفظ طلب تغيير البريد، لكن تعذر إرسال رسالة التأكيد الآن", "warning")
        else:
            flash("تم تحديث الملف الشخصي", "success")
        return redirect(url_for("profile"))

    return render_template("profile.html", user=user)


@app.route("/confirm-email-change/<token>")
def confirm_email_change(token):
    ok, error = confirm_pending_email_change(token)
    if ok:
        flash("تم تغيير البريد الإلكتروني بنجاح", "success")
    else:
        flash(error or "تعذر تغيير البريد", "danger")
    return redirect(url_for("profile") if current_user() else url_for("auth.login"))


@app.route("/orders")
@app.route("/legacy/orders")
@login_required
def orders():
    user = current_user()
    return render_template("orders.html", orders=list_user_orders(user["id"]))


@app.route("/wallet", methods=["GET", "POST"])
@app.route("/wallet/deposit", methods=["GET", "POST"])
@app.route("/legacy/wallet", methods=["GET", "POST"])
@login_required
def wallet():
    user = current_user()
    if request.method == "POST":
        try:
            amount = float(request.form.get("amount", "0"))
        except Exception:
            amount = 0
        method_id = request.form.get("method_id", "")
        method = get_payment_method(method_id)
        proof_text = request.form.get("proof", "").strip()
        # V50 SECURITY (HE): cap proof-text length (2000 chars ~= 2KB).
        # Prevents a user from shoving MBs of garbage into deposits.proof.
        if len(proof_text) > MAX_PROOF_TEXT_LEN:
            flash("وصف الإثبات طويل جداً (الحد الأقصى 2000 حرف)", "danger")
            return redirect(url_for("wallet"))
        proof_file = request.files.get("proof_image")
        proof_parts = []
        proof_filename_saved = None

        if proof_text:
            proof_parts.append(proof_text)

        if proof_file and proof_file.filename:
            if not _ext_ok(proof_file.filename):
                flash("نوع الملف غير مدعوم. الأنواع المسموحة: jpg, png, webp, gif", "danger")
                return redirect(url_for("wallet"))
            # PATCH-H4: verify file content (magic bytes) — not just extension.
            if not _proof_magic_ok(proof_file.stream):
                flash("الملف لا يطابق نوعه المُعلَن. أرسل صورة (JPG/PNG/WebP/GIF) حقيقياً.", "danger")
                return redirect(url_for("wallet"))
            filename = secure_filename(proof_file.filename)
            ext = os.path.splitext(filename)[1].lower()
            filename = f"{user['id']}_{secrets.token_urlsafe(16)}{ext}"
            save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            proof_file.save(save_path)
            proof_filename_saved = filename
            # Use authenticated route instead of public /static/uploads/
            proof_parts.append(f"صورة: /uploads/proof/{filename}")


        proof = "\n".join(proof_parts)

        if amount <= 0:
            flash("أدخل مبلغًا صحيحًا", "danger")
            return redirect(url_for("wallet"))
        if not method:
            flash("طريقة الدفع غير صحيحة", "danger")
            return redirect(url_for("wallet"))
        # V67.3: الإيصال أصبح اختياريًا. الإدارة تراجع الطلبات يدويًا داخل
        # المركز ولا تحتاج إلى إيصال إجباري لكل طلب. السلوك السابق كان
        # يطلب إيصالًا في بعض الطلبات ويتجاوزه في طلبات أخرى (لأن الـ
        # التحقق كان مبنيًا على طول النص فقط)، وهو ما تسبب في إرباك
        # المستخدم. لم نعد نرفض الطلب إذا كان الإيصال فارغًا — نخزّن
        # نصًا افتراضيًا حتى لا تُكسر القيود في قاعدة البيانات.
        if not proof:
            proof = "(بدون إيصال — سيتم التحقق يدويًا من الإدارة)"

        rate = float(get_setting("usd_syp_rate", "15000") or 15000)
        if method.get("currency") == "SYP":
            amount_usd = round(amount / rate, 2)
        else:
            amount_usd = amount

        # V50 SECURITY (C3): cap the USD-equivalent deposit amount so abusive
        # users cannot submit fake deposits for billions that clutter the
        # admin queue or cause float overflow in financial aggregation.
        if amount_usd > MAX_DEPOSIT_USD:
            flash(f"المبلغ يتجاوز الحد الأقصى المسموح به ({MAX_DEPOSIT_USD:.0f}$)", "danger")
            return redirect(url_for("wallet"))

        dep = create_deposit(user["id"], amount, method_id, proof, amount_usd=amount_usd,
                             proof_filename=proof_filename_saved)
        if dep:
            # V69: لم نعد نستخدم flash() لرسالة "تم استلام طلب الشحن" لأن
            # كثيرًا من المستخدمين كانوا يرونها تظهر مرة أخرى عند تحديث
            # الصفحة (F5) بسبب مزيج من bfcache + StaleWhileRevalidate في
            # service worker + إعادة استهلاك الـ session flash بعد رحلة
            # ذهاب وإياب. الحل: نرفع الرسالة عبر query param في الـ URL
            # (?deposit_ok=<code>) ثم نُزيله من شريط العنوان بـ
            # history.replaceState فور عرض الـ toast، فلا يبقى أثر بعد
            # أول refresh.
            return redirect(url_for("wallet", deposit_ok=dep[1]))
        else:
            flash("فشل إرسال طلب الشحن", "danger")
        return redirect(url_for("wallet"))

    deposit_ok = (request.args.get("deposit_ok") or "").strip()[:64] or None
    return render_template(
        "wallet.html",
        methods=list_payment_methods(only_active=True),
        support=get_setting("support_contact", "@support"),
        deposits=list_deposits_for_user(user["id"]),
        usd_syp_rate=get_usd_syp_rate(),
        deposit_ok=deposit_ok,
    )


@app.route("/wallet/transactions")
@login_required
def wallet_transactions():
    user = current_user()
    return render_template("wallet_transactions.html", deposits=list_deposits_for_user(user["id"]))




@app.route("/admin/game/<provider>/<game_key>/manual-prices", methods=["POST"])
@login_required
@admin_required
def admin_update_manual_syp_prices(provider, game_key):
    if not manual_price_edit_enabled():
        flash("تعديل الأسعار من الواجهة غير مفعّل من الإعدادات", "danger")
        return redirect(url_for("products", provider=provider, game_key=game_key))

    updates = []
    for key, value in request.form.items():
        if key.startswith("manual_syp_"):
            product_id = key.replace("manual_syp_", "")
            updates.append((product_id, value))
    update_manual_syp_prices(updates)
    flash("تم حفظ أسعار الليرة اليدوية", "success")
    return redirect(safe_next_url("products", provider=provider, game_key=game_key))

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
# --- V51 task B: admin 2FA (TOTP) endpoints ------------------------------
# All of these require a logged-in admin, but MUST be excluded from the
# 2FA gate in admin_required to avoid redirect loops. admin_required
# already whitelists them by endpoint name.

def _is_admin_user():
    u = current_user()
    return u if (u and u.get("role") == "admin") else None


@app.route("/admin/2fa/setup", methods=["GET"])
@login_required
@admin_required
@(limiter.limit("10 per minute") if limiter else (lambda f: f))
def admin_2fa_setup():
    """Show the QR + secret. If the admin already has 2FA enabled, we
    redirect to the dashboard — regeneration goes through /disable first
    to force the password + current-TOTP confirmation path."""
    user = _is_admin_user()
    if not user:
        abort(403)
    if int(user.get("totp_enabled") or 0) == 1:
        flash("المصادقة الثنائية مفعّلة بالفعل. عطّلها أولاً لإعادة الإعداد.", "info")
        return redirect(url_for("admin_dashboard"))
    # Keep the secret stable while the admin is on the setup page so a
    # page refresh does not invalidate the QR they already scanned.
    secret = user.get("totp_secret")
    if not secret:
        secret = generate_totp_secret()
        set_user_totp_secret(user["id"], secret)
    uri = provisioning_uri(secret, user["email"])
    svg = qr_svg(uri)
    return render_template(
        "admin/2fa_setup.html",
        secret=secret,
        qr_svg=svg,
        seo_title="إعداد المصادقة الثنائية - TecnoGems",
        seo_description="إعداد المصادقة الثنائية لحسابات الإدارة."
    )


@app.route("/admin/2fa/confirm", methods=["POST"])
@login_required
@admin_required
@(limiter.limit("10 per minute") if limiter else (lambda f: f))
def admin_2fa_confirm():
    """Confirm setup: verify the user can read codes from the authenticator,
    THEN flip totp_enabled and show the backup codes ONCE."""
    user = _is_admin_user()
    if not user:
        abort(403)
    if int(user.get("totp_enabled") or 0) == 1:
        return redirect(url_for("admin_dashboard"))
    secret = user.get("totp_secret")
    code = (request.form.get("code") or "").strip()
    if not secret or not verify_totp(secret, code):
        log_audit(
            "ADMIN_2FA_SETUP_FAIL",
            actor_id=user["id"],
            actor_email=user["email"],
            target_type="user",
            target_id=user["id"],
            ip=get_real_ip(),
            user_agent=request.headers.get("User-Agent"),
        )
        flash("الرمز غير صحيح أو انتهت صلاحيته. حاول مرة أخرى.", "danger")
        return redirect(url_for("admin_2fa_setup"))
    plain, hashed = generate_backup_codes()
    enable_user_totp(user["id"], serialize_backup_codes(hashed))
    session["admin_2fa_verified"] = True
    log_audit(
        "ADMIN_2FA_ENABLED",
        actor_id=user["id"],
        actor_email=user["email"],
        target_type="user",
        target_id=user["id"],
        ip=get_real_ip(),
        user_agent=request.headers.get("User-Agent"),
    )
    # Show backup codes ONCE. No way to retrieve them later — only regenerate.
    return render_template(
        "admin/2fa_backup_codes.html",
        backup_codes=plain,
        just_enabled=True,
        seo_title="رموز الاسترداد - TecnoGems",
        seo_description="احفظ رموز الاسترداد في مكان آمن."
    )


@app.route("/admin/2fa/challenge", methods=["GET", "POST"])
@login_required
@(limiter.limit("15 per minute") if limiter else (lambda f: f))
def admin_2fa_challenge():
    """Post-login gate for admins who have 2FA enabled. Accepts either a
    current TOTP code or a one-time backup code."""
    user = current_user()
    if not user or user.get("role") != "admin":
        abort(403)
    if int(user.get("totp_enabled") or 0) != 1:
        # Nothing to challenge — skip to admin.
        return redirect(url_for("admin_dashboard"))
    if session.get("admin_2fa_verified"):
        return redirect(safe_next_url("admin_dashboard"))

    if request.method == "POST":
        submitted = (request.form.get("code") or "").strip()
        # 1) TOTP path
        if verify_totp(user.get("totp_secret") or "", submitted):
            session["admin_2fa_verified"] = True
            log_audit(
                "ADMIN_2FA_PASS",
                actor_id=user["id"],
                actor_email=user["email"],
                target_type="user",
                target_id=user["id"],
                ip=get_real_ip(),
                user_agent=request.headers.get("User-Agent"),
                metadata={"method": "totp"},
                level="info",
            )
            return redirect(safe_next_url("admin_dashboard"))
        # 2) Backup code path (one-time)
        codes = deserialize_backup_codes(user.get("totp_backup_codes"))
        remaining = consume_backup_code(codes, submitted)
        if remaining is not None:
            update_user_backup_codes(user["id"], serialize_backup_codes(remaining))
            session["admin_2fa_verified"] = True
            log_audit(
                "ADMIN_2FA_PASS",
                actor_id=user["id"],
                actor_email=user["email"],
                target_type="user",
                target_id=user["id"],
                ip=get_real_ip(),
                user_agent=request.headers.get("User-Agent"),
                metadata={"method": "backup_code", "remaining": len(remaining)},
            )
            if len(remaining) <= 2:
                flash(f"تحذير: تبقى {len(remaining)} رموز استرداد فقط. أعد توليدها قريبًا.", "warning")
            return redirect(safe_next_url("admin_dashboard"))
        log_audit(
            "ADMIN_2FA_FAIL",
            actor_id=user["id"],
            actor_email=user["email"],
            target_type="user",
            target_id=user["id"],
            ip=get_real_ip(),
            user_agent=request.headers.get("User-Agent"),
        )
        flash("الرمز غير صحيح. حاول مرة أخرى.", "danger")

    return render_template(
        "admin/2fa_challenge.html",
        seo_title="المصادقة الثنائية - TecnoGems",
        seo_description="أدخل رمز المصادقة الثنائية لمتابعة الدخول إلى لوحة الإدارة."
    )


@app.route("/admin/2fa/disable", methods=["POST"])
@login_required
@admin_required
@(limiter.limit("5 per minute") if limiter else (lambda f: f))
def admin_2fa_disable():
    """Disable 2FA. Requires both the current password AND a valid TOTP
    code (or backup code) — same strength as the challenge itself."""
    user = _is_admin_user()
    if not user or int(user.get("totp_enabled") or 0) != 1:
        flash("المصادقة الثنائية غير مفعّلة.", "info")
        return redirect(url_for("admin_dashboard"))
    password = request.form.get("password", "")
    code = (request.form.get("code") or "").strip()
    if len(password) > MAX_PASSWORD_LEN or not password:
        flash("كلمة المرور مطلوبة.", "danger")
        return redirect(url_for("admin_dashboard"))
    # Re-authenticate with the password
    if not authenticate(user["email"], password):
        log_audit(
            "ADMIN_2FA_DISABLE_BAD_PW",
            actor_id=user["id"],
            actor_email=user["email"],
            target_type="user",
            target_id=user["id"],
            ip=get_real_ip(),
            user_agent=request.headers.get("User-Agent"),
        )
        flash("كلمة المرور غير صحيحة.", "danger")
        return redirect(url_for("admin_dashboard"))
    # Require a valid second factor too (TOTP or backup code)
    ok = verify_totp(user.get("totp_secret") or "", code)
    if not ok:
        codes = deserialize_backup_codes(user.get("totp_backup_codes"))
        ok = consume_backup_code(codes, code) is not None
    if not ok:
        log_audit(
            "ADMIN_2FA_DISABLE_BAD_CODE",
            actor_id=user["id"],
            actor_email=user["email"],
            target_type="user",
            target_id=user["id"],
            ip=get_real_ip(),
            user_agent=request.headers.get("User-Agent"),
        )
        flash("رمز المصادقة غير صحيح.", "danger")
        return redirect(url_for("admin_dashboard"))
    disable_user_totp(user["id"])
    session.pop("admin_2fa_verified", None)
    log_audit(
        "ADMIN_2FA_DISABLED",
        actor_id=user["id"],
        actor_email=user["email"],
        target_type="user",
        target_id=user["id"],
        ip=get_real_ip(),
        user_agent=request.headers.get("User-Agent"),
    )
    flash("تم إلغاء المصادقة الثنائية. يُنصح بإعادة تفعيلها.", "warning")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/2fa/backup-codes/regenerate", methods=["POST"])
@login_required
@admin_required
@(limiter.limit("3 per hour") if limiter else (lambda f: f))
def admin_2fa_regenerate_backup_codes():
    """Regenerate the 10 backup codes. Requires the current TOTP to be
    valid so a stolen session alone cannot rotate them."""
    user = _is_admin_user()
    if not user or int(user.get("totp_enabled") or 0) != 1:
        abort(404)
    code = (request.form.get("code") or "").strip()
    if not verify_totp(user.get("totp_secret") or "", code):
        log_audit(
            "ADMIN_2FA_REGEN_BAD_CODE",
            actor_id=user["id"],
            actor_email=user["email"],
            target_type="user",
            target_id=user["id"],
            ip=get_real_ip(),
            user_agent=request.headers.get("User-Agent"),
        )
        flash("رمز المصادقة غير صحيح.", "danger")
        return redirect(url_for("admin_dashboard"))
    plain, hashed = generate_backup_codes()
    update_user_backup_codes(user["id"], serialize_backup_codes(hashed))
    log_audit(
        "ADMIN_2FA_BACKUP_CODES_REGEN",
        actor_id=user["id"],
        actor_email=user["email"],
        target_type="user",
        target_id=user["id"],
        ip=get_real_ip(),
        user_agent=request.headers.get("User-Agent"),
    )
    return render_template(
        "admin/2fa_backup_codes.html",
        backup_codes=plain,
        just_enabled=False,
        seo_title="رموز الاسترداد - TecnoGems",
        seo_description="احفظ رموز الاسترداد في مكان آمن."
    )


@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    return render_template("admin/dashboard.html", stats=stats())


@app.route("/admin/orders")
@login_required
@admin_required
def admin_orders():
    status = request.args.get("status")
    # V67.1: filter by provider so the admin can focus on a single supplier
    # at a time. Defaults to the configured primary_provider so the page
    # opens straight to the operator's main supplier.
    provider = (request.args.get("provider") or "").strip()
    if provider not in ("server1", "server2", "all"):
        provider = get_setting("primary_provider", "server2")
    orders = list_orders(status)
    if provider in ("server1", "server2"):
        orders = [o for o in orders if (o.get("provider") or "") == provider]
    return render_template(
        "admin/orders.html",
        orders=orders,
        status=status,
        active_provider=provider,
        primary_provider=get_setting("primary_provider", "server2"),
    )


@app.route("/admin/order/<int:order_id>/<action>", methods=["POST"])
@login_required
@admin_required
@(limiter.limit("60 per minute") if limiter else (lambda f: f))
def admin_order_action(order_id, action):
    order = get_order(order_id)
    if not order:
        abort(404)
    # V50.2 MEDIUM: audit trail for every admin order action so a compromised
    # admin account leaves a paper trail (who, from where, which order, what
    # change, from which status).
    admin = current_user()
    # V69.1: حماية ضد ضغطات مزدوجة بعد التنفيذ — الطلبات النهائية لا
    # تقبل تعديلاً جديداً. الواجهة تخفي الأزرار، لكن نحرس السيرفر أيضاً.
    if order.get("status") in ("completed", "rejected") and action in ("complete", "reject"):
        flash(
            f"الطلب {order.get('order_code')} في حالة نهائية ({order.get('status')}) — لا يمكن تعديله.",
            "warning",
        )
        return redirect(url_for("admin_orders"))
    if action == "complete":
        # V67.1: be explicit that this is a MANUAL completion (admin
        # fulfilled the order outside the platform). Stamp the note so
        # the user/admin can later tell apart auto-fulfilled vs. manual.
        ok = update_order(order_id, "completed", order.get("provider_order_id"),
                     "تم الإكمال يدوياً من قبل الإدارة")
        # V52 (task D): structured audit row — replaces legacy log.warning.
        log_audit(
            "ADMIN_ORDER_COMPLETE",
            actor_id=(admin or {}).get("id"),
            actor_email=(admin or {}).get("email"),
            target_type="order",
            target_id=order_id,
            ip=get_real_ip(),
            user_agent=request.headers.get("User-Agent"),
            old={"status": order.get("status")},
            new={"status": "completed"},
            metadata={"user_id": order.get("user_id")},
        )
        if ok:
            flash(
                f"✅ تم إكمال الطلب {order.get('order_code')} يدوياً بنجاح.",
                "success",
            )
        else:
            flash(
                f"تعذّر تعديل الطلب {order.get('order_code')} (تمت معالجته مسبقاً).",
                "warning",
            )
    elif action == "reject":
        ok = update_order(order_id, "rejected", None, "Manual reject")
        log_audit(
            "ADMIN_ORDER_REJECT",
            actor_id=(admin or {}).get("id"),
            actor_email=(admin or {}).get("email"),
            target_type="order",
            target_id=order_id,
            ip=get_real_ip(),
            user_agent=request.headers.get("User-Agent"),
            old={"status": order.get("status")},
            new={"status": "rejected"},
            metadata={"user_id": order.get("user_id"), "amount": order.get("price")},
        )
        if ok:
            flash(
                f"❌ تم رفض الطلب {order.get('order_code')} وإرجاع الرصيد للمستخدم.",
                "warning",
            )
        else:
            flash(
                f"تعذّر تعديل الطلب {order.get('order_code')} (تمت معالجته مسبقاً).",
                "warning",
            )
    elif action == "retry":
        # V67.1: re-push to supplier. Useful when the first push failed
        # (network / supplier outage / wrong product mapping that's now
        # fixed). Resets status to 'waiting' and re-enqueues so the worker
        # processes it on the next tick. Refunds are NOT issued here —
        # the rejection path (with auto_refund) is the only path that
        # ever returns balance.
        if order.get("status") in ("completed",):
            flash("لا يمكن إعادة محاولة طلب مكتمل بالفعل", "warning")
            return redirect(url_for("admin_orders"))
        update_order(order_id, "waiting", None,
                     "إعادة إرسال إلى المورد بأمر من الإدارة")
        try:
            enqueue_order_job(order_id)
        except Exception as exc:
            log.exception("admin_order_action retry enqueue failed: %s", exc)
            flash(f"تعذر إعادة الإرسال: {exc}", "danger")
            return redirect(url_for("admin_orders"))
        log_audit(
            "ADMIN_ORDER_RETRY",
            actor_id=(admin or {}).get("id"),
            actor_email=(admin or {}).get("email"),
            target_type="order",
            target_id=order_id,
            ip=get_real_ip(),
            user_agent=request.headers.get("User-Agent"),
            old={"status": order.get("status")},
            new={"status": "waiting"},
            metadata={"user_id": order.get("user_id")},
        )
        flash("تم إعادة إرسال الطلب إلى المورد. سيتم تحديث الحالة بعد قليل.",
              "success")
    else:
        abort(404)
    return redirect(url_for("admin_orders"))


@app.route("/admin/users")
@login_required
@admin_required
def admin_users():
    q = request.args.get("q", "").strip()
    return render_template("admin/users.html", users=search_users(q), q=q)


@app.route("/admin/user/<int:user_id>")
@login_required
@admin_required
def admin_user_detail(user_id):
    user = get_user_by_id(user_id)
    if not user:
        abort(404)
    return render_template(
        "admin/user_detail.html",
        u=user,
        summary=user_financial_summary(user_id),
        orders=list_user_orders(user_id),
        deposits=list_user_deposits_admin(user_id)
    )


@app.route("/admin/user/<int:user_id>/balance", methods=["POST"])
@login_required
@admin_required
@(limiter.limit("30 per minute") if limiter else (lambda f: f))
def admin_user_balance(user_id):
    try:
        raw_amount = float(request.form.get("amount", "0") or 0)
    except Exception:
        flash("أدخل رقمًا صحيحًا للرصيد", "danger")
        return redirect(safe_next_url("admin_users"))

    # V49.1: let the admin pick USD or SYP when setting a balance. Previously
    # the raw number was always interpreted as USD, which was dangerous for
    # SYP-heavy operators (e.g. typing "50000" meant $50,000 by mistake).
    # The user's balance is still stored internally as USD; SYP input is
    # converted once, at write time, using the current exchange rate.
    currency = (request.form.get("currency") or "USD").upper()
    if currency == "SYP":
        rate = get_usd_syp_rate()
        if not rate or rate <= 0:
            flash("سعر الصرف غير صحيح. عدِّل الإعدادات أولاً.", "danger")
            return redirect(safe_next_url("admin_users"))
        new_balance = round(raw_amount / rate, 4)
    else:
        new_balance = raw_amount

    # V50 SECURITY (HG): bound admin balance edits to [0, MAX_ADMIN_BALANCE].
    # Prevents a compromised admin account from setting negative or
    # astronomical balances. Bound is checked on the USD value we will store.
    if not (0 <= new_balance <= MAX_ADMIN_BALANCE):
        flash(f"القيمة يجب أن تكون بين 0 و {MAX_ADMIN_BALANCE:.0f}$ (بعد التحويل)", "danger")
        return redirect(safe_next_url("admin_users"))

    admin = current_user()
    old = get_user_by_id(user_id)
    old_balance = float(old["balance"]) if old else None
    set_user_balance(user_id, new_balance)
    # V52 (task D): structured audit row — persists to audit_log table.
    log_audit(
        "ADMIN_BALANCE_CHANGE",
        actor_id=(admin or {}).get("id"),
        actor_email=(admin or {}).get("email"),
        target_type="user",
        target_id=user_id,
        ip=get_real_ip(),
        user_agent=request.headers.get("User-Agent"),
        old={"balance": old_balance},
        new={"balance": new_balance},
    )
    flash("تم تعيين الرصيد الجديد للمستخدم", "success")
    return redirect(safe_next_url("admin_users"))


@app.route("/admin/balances")
@login_required
@admin_required
def admin_balances():
    balances = {
        "server1": get_provider_balance("server1"),
        "server2": get_provider_balance("server2"),
    }
    return render_template("admin/balances.html", balances=balances)



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


@app.route("/games")
def games_index():
    return redirect(url_for("home"))



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



@app.route("/admin/games", methods=["GET", "POST"])
@login_required
@admin_required
def admin_games():
    if request.method == "POST":
        # V68/V72: حفظ تفعيل/إيقاف السيرفرات. صفحة /admin/games هي المالك
        # الوحيد لإعدادي show_server1 و show_server2 (تمت إزالتهما من
        # /admin/settings لتوحيد المصدر وتجنّب التعارض بين نموذجين).
        set_setting("show_server1", "1" if request.form.get("show_server1") else "0")
        set_setting("show_server2", "1" if request.form.get("show_server2") else "0")

        # V55: حفظ حقلين — الألعاب المفعّلة والألعاب التي تظهر في الرئيسية.
        active_keys = set(request.form.getlist("active_game"))
        home_keys = set(request.form.getlist("home_game"))
        for game in list_all_game_groups():
            key = f"{game['provider']}::{game['game_key']}"
            is_active = key in active_keys
            set_game_active(game["provider"], game["game_key"], is_active)
            # إظهار في الرئيسية مقيَّد بالألعاب المفعّلة فقط.
            set_game_show_on_home(
                game["provider"], game["game_key"], is_active and (key in home_keys)
            )
            # V68: ترتيب اللعبة في الواجهة الرئيسية (يدوي). 0 = ترتيب افتراضي.
            sort_val = request.form.get(f"sort_order::{key}", "0")
            try:
                sort_int = int(sort_val or 0)
            except Exception:
                sort_int = 0
            set_game_home_sort_order(game["provider"], game["game_key"], sort_int)
        flash("تم حفظ الألعاب المعروضة في الواجهة", "success")
        return redirect(url_for("admin_games"))

    # V68: تقسيم الألعاب حسب السيرفر لعرضها في عمودين منفصلين.
    all_groups = list_all_game_groups()
    server1_games = [g for g in all_groups if g.get("provider") == "server1"]
    server2_games = [g for g in all_groups if g.get("provider") == "server2"]
    return render_template(
        "admin/games.html",
        games=all_groups,
        server1_games=server1_games,
        server2_games=server2_games,
        server1_enabled=(get_setting("show_server1", "1") == "1"),
        server2_enabled=(get_setting("show_server2", "1") == "1"),
        discovered=list_product_games_from_products()
    )


@app.route("/admin/games/add", methods=["POST"])
@login_required
@admin_required
@(limiter.limit("20 per minute") if limiter else (lambda f: f))
def admin_add_game():
    provider = request.form.get("provider", "").strip()
    game_key = request.form.get("game_key", "").strip().lower().replace(" ", "_")
    name = clean_plain_text(request.form.get("name", ""), max_len=100)
    emoji = request.form.get("emoji", "").strip() or "🎮"
    image_url = clean_plain_text(request.form.get("image_url", ""), max_len=500)

    if provider not in ("server1", "server2") or not game_key or not name:
        flash("تأكد من إدخال المزود ومعرّف اللعبة واسمها", "danger")
    else:
        add_custom_game(provider, game_key, name, emoji, image_url, 1)
        set_game_active(provider, game_key, True)
        # V52 (task D): structured audit row for admin catalogue changes.
        admin = current_user()
        log_audit(
            "ADMIN_GAME_ADD",
            actor_id=(admin or {}).get("id"),
            actor_email=(admin or {}).get("email"),
            target_type="game",
            target_id=f"{provider}:{game_key}",
            ip=get_real_ip(),
            user_agent=request.headers.get("User-Agent"),
            new={"provider": provider, "game_key": game_key, "name": name},
        )
        flash(f"تمت إضافة/تفعيل اللعبة: {name}", "success")
    return redirect(url_for("admin_games"))


@app.route("/admin/game/<provider>/<game_key>/image", methods=["POST"])
@login_required
@admin_required
def admin_game_image(provider, game_key):
    game = get_game(provider, game_key)
    if not game:
        abort(404)

    file = request.files.get("image")
    image_url = request.form.get("image_url", "").strip()

    if file and file.filename:
        uploads_dir = Path("static/uploads/games")
        uploads_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(file.filename).suffix.lower()
        if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"]:
            flash("نوع الصورة غير مدعوم", "danger")
            return redirect(url_for("admin_games"))
        # V50.2 MEDIUM (M8): random suffix on uploaded game filenames so they
        # are not trivially enumerable. The previous "provider_gamekey.ext"
        # pattern let anyone guess URLs for every game in the catalogue
        # (and cache-bust attacks / asset scraping).
        _rand = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10]
        if ext == ".svg":
            # PATCH-H1: sanitise SVG to prevent stored-XSS via <script> or
            # javascript: URIs that would execute when the image is shown.
            safe_name = secure_filename(f"{provider}_{game_key}_{_rand}.svg")
            target = uploads_dir / safe_name
            try:
                raw = file.stream.read().decode("utf-8", errors="replace")
            except Exception:
                flash("تعذّر قراءة ملف SVG", "danger")
                return redirect(url_for("admin_games"))
            cleaned = _sanitise_svg(raw)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(cleaned)
            image_url = "/" + str(target).replace("\\", "/")
        else:
            base = secure_filename(f"{provider}_{game_key}_{_rand}")
            saved = process_upload_to_webp(file, str(uploads_dir), base, max_w=800, quality=82)
            if not saved:
                flash("تعذّر معالجة الصورة", "danger")
                return redirect(url_for("admin_games"))
            image_url = "/" + str(uploads_dir / saved).replace("\\", "/")


    if image_url:
        update_game_image(provider, game_key, image_url)
        # V52 (task D): structured audit row for admin image changes.
        admin = current_user()
        log_audit(
            "ADMIN_GAME_IMAGE",
            actor_id=(admin or {}).get("id"),
            actor_email=(admin or {}).get("email"),
            target_type="game",
            target_id=f"{provider}:{game_key}",
            ip=get_real_ip(),
            user_agent=request.headers.get("User-Agent"),
            new={"image_url": image_url},
        )
        flash("تم تحديث صورة اللعبة", "success")
    else:
        flash("اختر صورة أو ضع رابط صورة", "warning")

    return redirect(url_for("admin_games"))

@app.route("/admin/game/<provider>/<game_key>/products", methods=["GET", "POST"])
@login_required
@admin_required
def admin_game_products(provider, game_key):
    game = get_game(provider, game_key)
    if not game:
        abort(404)

    if request.method == "POST":
        action = request.form.get("action", "save_products")

        if action == "create_group":
            name = clean_plain_text(request.form.get("group_name", ""), max_len=100)
            image_url = clean_plain_text(request.form.get("group_image_url", ""), max_len=500) or game.get("image_url", "")
            sort_order = request.form.get("group_sort_order", "1")
            if name:
                create_product_group(provider, game_key, name, image_url, sort_order, 1 if request.form.get("group_active") else 0)
                flash("تم إنشاء واجهة العرض", "success")
            else:
                flash("اكتب اسم واجهة العرض", "danger")
            return redirect(url_for("admin_game_products", provider=provider, game_key=game_key))

        if action == "update_group":
            group_id = request.form.get("group_id")
            if group_id:
                update_product_group(
                    group_id,
                    clean_plain_text(request.form.get("group_name", ""), max_len=100),
                    clean_plain_text(request.form.get("group_image_url", ""), max_len=500) or game.get("image_url", ""),
                    request.form.get("group_sort_order", "1"),
                    1 if request.form.get("group_active") else 0
                )
                flash("تم تحديث واجهة العرض", "success")
            return redirect(url_for("admin_game_products", provider=provider, game_key=game_key))

        if action == "delete_group":
            group_id = request.form.get("group_id")
            if group_id:
                delete_product_group(group_id)
                flash("تم حذف واجهة العرض وإرجاع باقاتها إلى عام", "warning")
            return redirect(url_for("admin_game_products", provider=provider, game_key=game_key))

        update_game_pricing(provider, game_key, request.form.get("pricing_currency", "GLOBAL"))
        updates = []
        for key, value in request.form.items():
            if key.startswith("sort_"):
                product_id = key.replace("sort_", "")
                updates.append({
                    "product_id": int(product_id),
                    "sort_order": int(value or 0),
                    "group_id": request.form.get(f"group_{product_id}") or None,
                    "fixed_syp_price": request.form.get(f"fixed_syp_{product_id}") or 0,
                    "pricing_mode": request.form.get(f"pricing_mode_{product_id}") or "usd"
                })
        update_products_admin(updates, get_usd_syp_rate())
        flash("تم حفظ ترتيب الباقات وتقسيمها والتسعير", "success")
        return redirect(url_for("admin_game_products", provider=provider, game_key=game_key))

    products = list_all_products_for_admin(provider, game_key)
    groups = list_product_groups(provider, game_key, False)
    return render_template("admin/game_products.html", game=game, products=products, groups=groups, usd_syp_rate=get_usd_syp_rate())




@app.route("/admin/accounting", methods=["GET", "POST"])
@login_required
@admin_required
def admin_accounting():
    if request.method == "POST":
        val = request.form.get("sales_override", "").strip()
        if val == "":
            set_setting("sales_override", "")
            flash("تم إلغاء تصحيح رقم المبيعات والعودة لحساب السجل", "success")
        else:
            try:
                float(val)
                set_setting("sales_override", val)
                flash("تم حفظ رقم المبيعات المعروض", "success")
            except Exception:
                flash("رقم المبيعات غير صحيح", "danger")
        return redirect(url_for("admin_accounting"))
    return render_template("admin/accounting.html", data=accounting_summary())


@app.route("/admin/deposits")
@login_required
@admin_required
def admin_deposits():
    status = request.args.get("status")
    q = request.args.get("q", "").strip()
    deposits = list_deposits(status)
    if q:
        ql = q.lower()
        deposits = [d for d in deposits if ql in str(d.get("deposit_code","")).lower()
                    or ql in str(d.get("user_name","")).lower()
                    or ql in str(d.get("user_email","")).lower()
                    or ql in str(d.get("proof","")).lower()]
    return render_template("admin/deposits.html", deposits=deposits, status=status, q=q)


@app.route("/admin/deposit/<int:deposit_id>/<action>", methods=["POST"])
@login_required
@admin_required
@(limiter.limit("60 per minute") if limiter else (lambda f: f))
def admin_deposit_action(deposit_id, action):
    # V52 (task D): structured audit trail for deposit approve/reject.
    # Deposits credit the user balance directly, so every admin decision
    # is recorded in audit_log with old/new state and metadata (amount,
    # currency, method, user_id) for forensic queryability — not just a
    # log.warning grep contract. Keeps log.warning alongside so existing
    # log aggregator alerts on "ADMIN_DEPOSIT_*" keep firing.
    admin = current_user()
    deposit = get_deposit(deposit_id)
    if not deposit:
        abort(404)
    if action == "approve":
        ok = update_deposit(deposit_id, "approved")
        log_audit(
            "ADMIN_DEPOSIT_APPROVE",
            actor_id=(admin or {}).get("id"),
            actor_email=(admin or {}).get("email"),
            target_type="deposit",
            target_id=deposit_id,
            ip=get_real_ip(),
            user_agent=request.headers.get("User-Agent"),
            old={"status": deposit.get("status")},
            new={"status": "approved"},
            metadata={
                "user_id": deposit.get("user_id"),
                "amount": deposit.get("amount"),
                "amount_usd": deposit.get("amount_usd"),
                "currency": deposit.get("currency"),
                "method": deposit.get("method"),
                "deposit_code": deposit.get("deposit_code"),
                "ok": bool(ok),
            },
        )
        log.warning(
            "ADMIN_DEPOSIT_APPROVE admin_id=%s admin_email=%s deposit_id=%s ok=%s ip=%s",
            (admin or {}).get("id"), (admin or {}).get("email"),
            deposit_id, ok, get_real_ip(),
        )
        flash("تمت الموافقة وإضافة الرصيد" if ok else "لا يمكن تعديل هذا الطلب", "success" if ok else "warning")
    elif action == "reject":
        ok = update_deposit(deposit_id, "rejected")
        log_audit(
            "ADMIN_DEPOSIT_REJECT",
            actor_id=(admin or {}).get("id"),
            actor_email=(admin or {}).get("email"),
            target_type="deposit",
            target_id=deposit_id,
            ip=get_real_ip(),
            user_agent=request.headers.get("User-Agent"),
            old={"status": deposit.get("status")},
            new={"status": "rejected"},
            metadata={
                "user_id": deposit.get("user_id"),
                "amount": deposit.get("amount"),
                "amount_usd": deposit.get("amount_usd"),
                "currency": deposit.get("currency"),
                "method": deposit.get("method"),
                "deposit_code": deposit.get("deposit_code"),
                "ok": bool(ok),
            },
        )
        log.warning(
            "ADMIN_DEPOSIT_REJECT admin_id=%s admin_email=%s deposit_id=%s ok=%s ip=%s",
            (admin or {}).get("id"), (admin or {}).get("email"),
            deposit_id, ok, get_real_ip(),
        )
        flash("تم رفض طلب الشحن" if ok else "لا يمكن تعديل هذا الطلب", "warning")
    else:
        abort(404)
    return redirect(url_for("admin_deposits"))


# V67: manual trigger for the supplier-status sweep. Useful when:
#   1. The Redis worker isn't running (single-dyno Heroku free tier).
#   2. An admin wants to immediately unstick orders without waiting for
#      the next 90-second poll tick.
@app.route("/admin/refresh-pending-orders", methods=["POST"])
@login_required
@admin_required
@(limiter.limit("6 per minute") if limiter else (lambda f: f))
def admin_refresh_pending_orders():
    try:
        from tasks import refresh_pending_orders
        counters = refresh_pending_orders(limit=100)
        flash(
            f"تم الفحص: {counters.get('checked', 0)} طلب — "
            f"اكتمل {counters.get('completed', 0)}، "
            f"رُفض {counters.get('rejected', 0)}، "
            f"لا يزال قيد الانتظار {counters.get('still_pending', 0)}، "
            f"أخطاء {counters.get('errors', 0)}",
            "success"
        )
    except Exception as exc:
        log.exception("admin_refresh_pending_orders failed: %s", exc)
        flash("فشل تحديث حالات الطلبات", "danger")
    return redirect(request.referrer or url_for("admin_dashboard"))


@app.route("/admin/payment-methods")
@login_required
@admin_required
def admin_payment_methods():
    return render_template("admin/payment_methods.html", methods=list_payment_methods())


@app.route("/admin/payment-method/<method_id>", methods=["GET", "POST"])
@login_required
@admin_required
def admin_payment_method_edit(method_id):
    method = get_payment_method(method_id)
    if not method:
        abort(404)
    if request.method == "POST":
        update_payment_method(
            method_id,
            name=clean_plain_text(request.form.get("name", ""), max_len=100),
            emoji=request.form.get("emoji", "").strip() or "💳",
            address=clean_plain_text(request.form.get("address", ""), max_len=200),
            instructions=clean_rich_text(request.form.get("instructions", ""), max_len=1500),
            active=bool(request.form.get("active")),
            currency=clean_plain_text(request.form.get("currency", "USD"), max_len=10)
        )
        flash("تم تحديث طريقة الدفع", "success")
        return redirect(url_for("admin_payment_methods"))
    return render_template("admin/payment_method_edit.html", method=method)


@app.route("/admin/test-email", methods=["POST"])
@login_required
@admin_required
@(limiter.limit("5 per minute") if limiter else (lambda f: f))
def admin_test_email():
    """Send a synchronous test email to the admin to verify SMTP works.

    Sends directly (not via queue) so any error surfaces immediately in
    the flash message — much easier to diagnose than queued failures.
    """
    admin = current_user()
    target = (request.form.get("to") or admin["email"]).strip().lower()

    if not email_is_configured():
        flash(
            "إعدادات SMTP غير مكتملة في .env. تأكد من: MAIL_SERVER, MAIL_PORT, "
            "MAIL_USERNAME, MAIL_PASSWORD, MAIL_FROM",
            "danger",
        )
        return redirect(url_for("admin_settings"))

    try:
        link = f"{BASE_URL}/admin"
        body = (
            "هذا اختبار إرسال البريد من TecnoGems.\n\n"
            f"الخادم: {MAIL_SERVER}:{MAIL_PORT}\n"
            f"المستخدم: {MAIL_USERNAME}\n"
            f"المرسل من: {MAIL_FROM}\n\n"
            "إذا وصلتك هذه الرسالة، فإعدادات الإيميل تعمل بنجاح."
        )
        html_body = _build_email_html(
            title="اختبار الإيميل - TecnoGems",
            greeting="رسالة اختبار",
            message=(
                "تم إرسال هذه الرسالة من لوحة الإدارة لاختبار إعدادات SMTP. "
                f"الخادم: <code>{MAIL_SERVER}:{MAIL_PORT}</code>"
            ),
            button_text="فتح لوحة الإدارة",
            button_url=link,
            footer_note="إذا وصلتك هذه الرسالة، فإعدادات الإيميل تعمل بنجاح.",
        )
        # Bypass the queue: send synchronously so we can show the real error.
        _send_email_sync(target, "TecnoGems - اختبار الإيميل", body, html_body=html_body)
        flash(
            f"تم إرسال إيميل الاختبار إلى {target}. تحقق من البريد الوارد و"
            "مجلد الإيميلات غير المرغوبة (Spam).",
            "success",
        )
    except smtplib.SMTPAuthenticationError as exc:
        flash(
            f"فشل التحقق من بيانات الدخول لخادم البريد. الخطأ: {exc}. "
            "السبب الأكثر شيوعًا في Gmail: استخدام كلمة مرور الحساب العادية. "
            "يجب استخدام App Password (16 حرفًا) من إعدادات Google. "
            "اذهب إلى: https://myaccount.google.com/apppasswords",
            "danger",
        )
    except smtplib.SMTPConnectError as exc:
        flash(
            f"تعذر الاتصال بخادم SMTP ({MAIL_SERVER}:{MAIL_PORT}). الخطأ: {exc}. "
            "تأكد من صحة عنوان الخادم والبورت، وأن الـ firewall لا يحجبه.",
            "danger",
        )
    except smtplib.SMTPException as exc:
        flash(f"خطأ SMTP: {exc}", "danger")
    except Exception as exc:
        flash(f"خطأ غير متوقع: {type(exc).__name__}: {exc}", "danger")

    return redirect(url_for("admin_settings"))


@app.route("/admin/settings", methods=["GET", "POST"])
@login_required
@admin_required
def admin_settings():
    if request.method == "POST":
        set_setting("support_contact", clean_plain_text(request.form.get("support_contact", ""), max_len=100))
        set_setting("whatsapp_number", clean_plain_text(request.form.get("whatsapp_number", "").replace("+", ""), max_len=32))
        set_setting("telegram_username", clean_plain_text(request.form.get("telegram_username", "").lstrip("@"), max_len=80))
        set_setting("usd_syp_rate", request.form.get("usd_syp_rate", "15000").strip())
        set_setting("pricing_mode", request.form.get("pricing_mode", "usd"))
        set_setting("manual_orders", "1" if request.form.get("manual_orders") else "0")
        # V72: show_server1 / show_server2 يُداران من /admin/games فقط لتجنّب
        # التعارض بين نموذجين (كان نفس المفتاح يُحفظ من مكانين بأسماء مختلفة).
        # V67.1: primary provider — used by admin filters & badges so the
        # operator can clearly tell which orders/games are on which supplier.
        _pp = (request.form.get("primary_provider") or "server2").strip()
        if _pp not in ("server1", "server2"):
            _pp = "server2"
        set_setting("primary_provider", _pp)
        set_setting("email_verification_enabled", "1" if request.form.get("email_verification_enabled") else "0")
        set_setting("hide_phone_on_register", "1" if request.form.get("hide_phone_on_register") else "0")
        set_setting("site_theme", request.form.get("site_theme", "theme-aurora"))
        set_setting("nav_mode", request.form.get("nav_mode", "menu"))
        set_setting("show_groups_direct", "1" if request.form.get("show_groups_direct") else "0")
        # old_games_layout setting removed in V44.2 (single neon layout only)
        set_setting("manual_price_edit_enabled", "1" if request.form.get("manual_price_edit_enabled") else "0")
        set_setting("auto_refund_on_failure", "1" if request.form.get("auto_refund_on_failure") else "0")
        # V71: تفعيل/إيقاف نقطة API للتحقق من اسم اللاعب لدى المورد.
        set_setting("player_validation_api_enabled", "1" if request.form.get("player_validation_api_enabled") else "0")
        # V66: homepage section toggles + editable testimonial copy.
        set_setting("show_popular_bar", "1" if request.form.get("show_popular_bar") else "0")
        set_setting("show_testimonials", "1" if request.form.get("show_testimonials") else "0")
        for i in (1, 2, 3):
            set_setting(f"testimonial_{i}_name",
                        clean_plain_text(request.form.get(f"testimonial_{i}_name", ""), max_len=80))
            set_setting(f"testimonial_{i}_game",
                        clean_plain_text(request.form.get(f"testimonial_{i}_game", ""), max_len=60))
            set_setting(f"testimonial_{i}_text",
                        clean_plain_text(request.form.get(f"testimonial_{i}_text", ""), max_len=400))
        try:
            new_margin = float(request.form.get("profit_margin", "1.20"))
            try:
                old_margin = float(get_setting("profit_margin", "1.20") or "1.20")
            except Exception:
                old_margin = None
            if old_margin is None or abs(new_margin - old_margin) > 1e-6:
                update_profit_margin(new_margin)
        except Exception:
            flash("نسبة الربح غير صحيحة", "danger")
        flash("تم حفظ الإعدادات", "success")
        return redirect(url_for("admin_settings"))
    return render_template(
        "admin/settings.html",
        support=get_setting("support_contact", "@support"),
        usd_syp_rate=get_setting("usd_syp_rate", "15000"),
        selected_display_currency=get_display_currency(),
        selected_pricing_mode=get_pricing_mode(),
        manual_price_edit_enabled_setting=get_setting("manual_price_edit_enabled", "0"),
        whatsapp_number_setting=get_setting("whatsapp_number", ""),
        telegram_username_setting=get_setting("telegram_username", ""),
        manual_orders=get_setting("manual_orders", "0"),
        # V67.1 — primary provider for admin UI separation (badges, filters).
        primary_provider_setting=get_setting("primary_provider", "server2"),
        email_verification_enabled=get_setting("email_verification_enabled", "0"),
        hide_phone_on_register=get_setting("hide_phone_on_register", "0"),
        email_is_configured=email_is_configured(),
        profit_margin=get_setting("profit_margin", "1.20"),
        selected_theme=get_setting("site_theme", "theme-aurora"),
        selected_nav_mode=get_setting("nav_mode", "menu"),
        show_groups_direct_setting=get_setting("show_groups_direct", "0"),
        old_games_layout_setting=get_setting("old_games_layout", "0"),
        auto_refund_on_failure_setting=get_setting("auto_refund_on_failure", "0"),
        # V71 — توگل خاصية التحقق من اسم اللاعب عبر API.
        player_validation_api_enabled_setting=get_setting("player_validation_api_enabled", "0"),
        # V66: homepage section toggles + editable testimonial copy.
        show_popular_bar_setting=get_setting("show_popular_bar", "1"),
        show_testimonials_setting=get_setting("show_testimonials", "1"),
        testimonial_1_name=get_setting("testimonial_1_name", ""),
        testimonial_1_game=get_setting("testimonial_1_game", ""),
        testimonial_1_text=get_setting("testimonial_1_text", ""),
        testimonial_2_name=get_setting("testimonial_2_name", ""),
        testimonial_2_game=get_setting("testimonial_2_game", ""),
        testimonial_2_text=get_setting("testimonial_2_text", ""),
        testimonial_3_name=get_setting("testimonial_3_name", ""),
        testimonial_3_game=get_setting("testimonial_3_game", ""),
        testimonial_3_text=get_setting("testimonial_3_text", ""),
        # SMTP diagnostics for the admin settings UI
        smtp_server=MAIL_SERVER,
        smtp_port=MAIL_PORT,
        smtp_username=MAIL_USERNAME,
        smtp_from=MAIL_FROM,
        smtp_use_tls=MAIL_USE_TLS,
        smtp_pw_len=len(MAIL_PASSWORD or ""),
    )


# ============================================================================
# V43: Wishlist + Search Autocomplete REMOVED per user request.
# Google OAuth + Service Worker remain (below).
# ============================================================================
from database import (
    get_user_by_google_sub as _db_get_user_by_google_sub,
    create_user_oauth as _db_create_user_oauth,
    link_user_google_sub as _db_link_user_google_sub,
)


# ---------------- Service Worker (root scope) ----------------
@app.route("/sw.js")
def service_worker():
    resp = send_from_directory("static", "sw.js")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


# ---------------- Google OAuth ----------------
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "").strip() or f"{BASE_URL}/auth/google/callback"

_oauth = None
try:
    if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
        from authlib.integrations.flask_client import OAuth as _AuthlibOAuth
        _oauth = _AuthlibOAuth(app)
        _oauth.register(
            name="google",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
except Exception as exc:
    app.logger.warning("Google OAuth disabled: %s", exc)
    _oauth = None


@app.context_processor
def _inject_oauth_flags():
    return {"google_oauth_enabled": bool(_oauth)}


# V53 REFACTOR (phase 1): /auth/google + /auth/google/callback routes have
# moved to routes/auth_bp.py. The oauth object (_oauth) + GOOGLE_REDIRECT_URI
# remain defined here because they must be configured at app-startup time
# (not inside a request); the Blueprint reaches them via `import app`.


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
