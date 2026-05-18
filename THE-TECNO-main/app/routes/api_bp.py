"""V53 REFACTOR (phase 5): JSON ``/api/*`` routes extracted from app.py.

This blueprint owns every endpoint under ``/api/`` and the dedicated
``before_request`` origin guard that protects the CSRF-exempt POST
endpoints from being called from a foreign origin.

Routes
------
============================================================  =========================================
URL                                                            Endpoint
============================================================  =========================================
GET  /api/me                                                   ``api.me``
GET  /api/games                                                ``api.games``
GET  /api/games/<slug>                                         ``api.game``
POST /api/login                                                ``api.login``
POST /api/register                                             ``api.register``
POST /api/logout                                               ``api.logout``
GET  /api/orders                                               ``api.orders`` (read)
POST /api/orders                                               ``api.orders`` (write)
GET  /api/payment-methods                                      ``api.payment_methods``
GET  /api/wallet                                               ``api.wallet``
POST /api/validate-player                                      ``api.validate_player``

Endpoint names are namespaced (``api.me`` etc.) following the convention
established by the other blueprints. None of them are referenced via
``url_for`` from the templates — they are called by client-side JS using
the literal URLs above — so the namespace switch is internal-only.

Origin guard
------------
``_api_origin_guard`` is registered as ``@bp.before_request`` so it only
runs for ``/api/*`` traffic. The previous ``@app.before_request``
implementation walked every request and short-circuited on
``request.path``; binding to the blueprint is a tiny optimisation and a
clearer ownership signal.

CSRF
----
The blueprint exempts itself from Flask-WTF's CSRFProtect via
``csrf.exempt(bp)`` inside :func:`init_csrf_exemption`. The legacy
behaviour was to call ``csrf.exempt`` on each individual view function —
calling it on the blueprint is equivalent and keeps the code declarative.
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from urllib.parse import urlparse

from flask import Blueprint, jsonify, request, session

from app.config import BaseConfig
from app.extensions import csrf, limiter
from app.services.mail import (
    email_verification_is_enabled,
    send_verification_email,
)
from app.services.pricing import (
    get_display_currency,
    wallet_money_text,
)
from app.utils.auth import current_user
from app.utils.settings_cache import get_setting

from database import (
    InsufficientBalance,
    authenticate,
    create_order,
    create_user,
    get_game,
    get_product,
    list_payment_methods,
    list_products,
    list_public_games,
    list_user_orders,
)
from providers import validate_player_provider
from request_ip import get_real_ip

log = logging.getLogger("tecnogems.api")

# Re-use the same length caps as the rest of the site so the JSON and
# HTML paths reject the same oversized inputs.
MAX_PASSWORD_LEN = BaseConfig.MAX_PASSWORD_LEN
MAX_EMAIL_LEN = BaseConfig.MAX_EMAIL_LEN
MAX_NAME_LEN = BaseConfig.MAX_NAME_LEN
MAX_PHONE_LEN = BaseConfig.MAX_PHONE_LEN
MAX_PLAYER_ID_LEN = BaseConfig.MAX_PLAYER_ID_LEN


bp = Blueprint("api", __name__, url_prefix="/api")


# ---------------------------------------------------------------------------
# Local rate-limit shim — same pattern used in every other blueprint.
# ---------------------------------------------------------------------------
def _rl(limit: str):
    if limiter is None:
        return lambda f: f
    return limiter.limit(limit)


# ---------------------------------------------------------------------------
# /api/validate-player — TTL cache (per-process)
# ---------------------------------------------------------------------------
# V71: cache process-local لتقليل ضغط الاستعلامات على المورد. نحدّ ب-60 ثانية
# لكل (provider, product_id, player_id). المفتاح يستبعد user_id حتى لو طلب
# عدة مستخدمين نفس اللاعب نقتصر على استعلام واحد للمورد.
_VP_CACHE: dict = {}
_VP_CACHE_LOCK = threading.Lock()
_VP_CACHE_TTL = 60.0


# ---------------------------------------------------------------------------
# Internal serialisers
# ---------------------------------------------------------------------------
def _public_user(user):
    """Return a JSON-safe projection of a user row."""
    if not user:
        return None
    # ``user`` may be a sqlite3.Row OR a dict-with-fallbacks. Both expose
    # mapping-style ``[]`` access; fall back for the dict-only ``.get``
    # cases the same way app.py used to.
    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "phone": user.get("phone")
        if hasattr(user, "get")
        else user["phone"],
        "role": user["role"],
        "balance": float(user["balance"] or 0),
        "email_verified": bool(
            user.get("email_verified", 0)
            if hasattr(user, "get")
            else user["email_verified"]
        )
        if "email_verified" in user.keys()
        else True,
    }


def _game_to_api(g):
    """Same shape used by the React storefront / mobile clients."""
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
        "packagesCount": int(g.get("product_count", 0) or 0)
        if hasattr(g, "get")
        else int(g["product_count"] or 0),
        "startingPrice": float(g.get("min_price", 0) or 0)
        if hasattr(g, "get")
        else float(g["min_price"] or 0),
        "currency": "رصيد",
    }


# ---------------------------------------------------------------------------
# Origin guard — only runs for /api/* routes (binding to the blueprint).
# ---------------------------------------------------------------------------
@bp.before_request
def _api_origin_guard():
    """V50.2 MEDIUM: reject state-changing /api/* requests whose Origin or
    Referer header points at a different host than this server.

    Protects CSRF-exempt endpoints from being called by malicious sites
    that managed to piggy-back on SameSite=Lax exceptions (e.g. top-level
    navigation POST fallbacks). Browsers send Origin / Referer for
    cross-origin POSTs; native clients (curl, mobile) usually send
    neither and are therefore allowed.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    # The before_request only fires for this blueprint, so no need to
    # gate on ``request.path.startswith("/api/")`` — but keep the guard
    # for direct invocation in tests.
    if not request.path.startswith("/api/"):
        return None
    origin = request.headers.get("Origin", "")
    referer = request.headers.get("Referer", "")
    if not origin and not referer:
        return None
    try:
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
                    request.path,
                    origin,
                    referer,
                    host,
                    get_real_ip(),
                )
                return jsonify({"error": "cross-origin request rejected"}), 403
    except Exception as exc:
        log.warning("_api_origin_guard parse error: %s", exc)
    return None


# ---------------------------------------------------------------------------
# /api/me
# ---------------------------------------------------------------------------
@bp.route("/me")
def me():
    user = current_user()
    return jsonify(
        {
            "ok": True,
            "user": _public_user(user),
            "settings": {
                "theme": get_setting("site_theme", "theme-aurora"),
                "support": get_setting("support_contact", "@support"),
                "emailVerification": get_setting(
                    "email_verification_enabled", "0"
                )
                == "1",
            },
        }
    )


# ---------------------------------------------------------------------------
# /api/games
# ---------------------------------------------------------------------------
@bp.route("/games")
def games():
    games_list = [_game_to_api(g) for g in list_public_games(True)]
    return jsonify({"ok": True, "games": games_list})


# ---------------------------------------------------------------------------
# /api/games/<slug>
# ---------------------------------------------------------------------------
@bp.route("/games/<slug>")
def game(slug):
    if "--" not in slug:
        return jsonify({"ok": False, "error": "اللعبة غير موجودة"}), 404
    provider, game_key = slug.split("--", 1)
    g = get_game(provider, game_key)
    if not g or not g["active"]:
        return jsonify({"ok": False, "error": "اللعبة غير موجودة"}), 404
    products = list_products(provider, game_key)
    return jsonify(
        {
            "ok": True,
            "game": _game_to_api(
                {
                    **g,
                    "product_count": len(products),
                    "min_price": min(
                        [p["sell_price"] for p in products], default=0
                    ),
                }
            ),
            "products": [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "priceUsd": float(p["sell_price"] or 0),
                    "basePrice": float(p["base_price"] or 0),
                    "popular": i == 1,
                }
                for i, p in enumerate(products)
            ],
        }
    )


# ---------------------------------------------------------------------------
# /api/login
# ---------------------------------------------------------------------------
@bp.route("/login", methods=["POST"])
@_rl("10 per minute")
def login():
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
        log.warning(
            "Failed api_login for email=%s from ip=%s", email, get_real_ip()
        )
        return jsonify({"ok": False, "error": "بيانات الدخول غير صحيحة"}), 401
    if (
        email_verification_is_enabled()
        and user["role"] != "admin"
        and not user.get("email_verified")
    ):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "يجب تفعيل بريدك الإلكتروني قبل تسجيل الدخول",
                }
            ),
            403,
        )
    # PATCH-H2: clear pre-existing session before authenticating.
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    return jsonify({"ok": True, "user": _public_user(user)})


# ---------------------------------------------------------------------------
# /api/register
# ---------------------------------------------------------------------------
@bp.route("/register", methods=["POST"])
@_rl("8 per minute")
def register():
    from app.utils.security import validate_password_strength  # local — avoids cycle at import time

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
        return (
            jsonify(
                {"ok": False, "error": "أحد الحقول تجاوز الحد المسموح به"}
            ),
            400,
        )
    if not name or not email or not password:
        return jsonify({"ok": False, "error": "أكمل البيانات المطلوبة"}), 400
    if password != password_confirm:
        return (
            jsonify(
                {"ok": False, "error": "كلمة المرور وتأكيدها غير متطابقين"}
            ),
            400,
        )
    pw_ok, pw_err = validate_password_strength(password)
    if not pw_ok:
        return jsonify({"ok": False, "error": pw_err}), 400

    verification_enabled = email_verification_is_enabled()
    token = secrets.token_urlsafe(32) if verification_enabled else None
    ok, err = create_user(
        name,
        email,
        phone,
        password,
        email_verified=0 if verification_enabled else 1,
        email_token=token,
    )
    if not ok:
        return jsonify({"ok": False, "error": err or "فشل إنشاء الحساب"}), 400

    if verification_enabled:
        try:
            send_verification_email(email, token)
            return jsonify(
                {
                    "ok": True,
                    "message": "تم إنشاء الحساب. أرسلنا رابط التفعيل إلى بريدك.",
                }
            )
        except Exception as exc:
            log.warning(
                "api_register verification email failed for %s: %s", email, exc
            )
            # V69: only triggers when RQ enqueue AND sync fallback both fail.
            return jsonify(
                {
                    "ok": True,
                    "message": (
                        "تم إنشاء الحساب، لكن تعذّر إرسال بريد التفعيل الآن. "
                        "تحقّق من إعدادات SMTP في ملف .env، ثم استخدم "
                        "\"إعادة إرسال رابط التفعيل\"."
                    ),
                }
            )
    return jsonify(
        {"ok": True, "message": "تم إنشاء الحساب. يمكنك تسجيل الدخول الآن."}
    )


# ---------------------------------------------------------------------------
# /api/logout
# ---------------------------------------------------------------------------
@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# /api/orders
# ---------------------------------------------------------------------------
@bp.route("/orders", methods=["GET", "POST"])
@_rl("20 per minute")
def orders():
    user = current_user()
    if not user:
        return jsonify({"ok": False, "error": "يجب تسجيل الدخول"}), 401

    if request.method == "GET":
        rows = list_user_orders(user["id"])
        return jsonify({"ok": True, "orders": [dict(o) for o in rows]})

    # POST = create
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
    g = get_game(product["provider"], product["game_key"])
    if not g:
        return jsonify({"ok": False, "error": "اللعبة غير موجودة"}), 404

    # PATCH-B2/B7: rely on atomic balance deduction inside create_order
    # (BEGIN IMMEDIATE + UPDATE WHERE balance>=price) instead of the
    # race-prone Python pre-check, and properly catch InsufficientBalance.
    try:
        order_id, code = create_order(user, product, g, player_id)
    except InsufficientBalance:
        return jsonify({"ok": False, "error": "رصيدك غير كافٍ"}), 400
    except Exception as exc:
        log.exception("api_orders create_order failed: %s", exc)
        return jsonify({"ok": False, "error": "حدث خطأ أثناء إنشاء الطلب"}), 500

    # Order processing dispatch lives in app.services.orders — imported
    # lazily to avoid pulling RQ at module load when REDIS_URL is missing.
    from app.services.orders import enqueue_order_job

    enqueue_order_job(order_id, product, player_id)
    return jsonify({"ok": True, "order_id": order_id, "order_code": code})


# ---------------------------------------------------------------------------
# /api/payment-methods
# ---------------------------------------------------------------------------
@bp.route("/payment-methods")
def payment_methods():
    return jsonify({"ok": True, "methods": list_payment_methods(True)})


# ---------------------------------------------------------------------------
# /api/wallet
# ---------------------------------------------------------------------------
@bp.route("/wallet")
def wallet():
    user = current_user()
    if not user:
        return jsonify({"ok": False, "error": "يجب تسجيل الدخول"}), 401
    bal = float(user["balance"] or 0)
    return jsonify(
        {
            "ok": True,
            "balance": bal,
            # V67.2: also send the *formatted* string so the navbar JS does
            # not have to guess the currency / suffix. Without this the JS
            # was replacing the leading number while keeping the old "ل.س"
            # suffix, which produced "8.20 ل.س" for a USD value when display
            # currency was SYP. Sending the rendered label keeps the navbar
            # consistent with whatever wallet_money_text() decides on the
            # server.
            "balance_text": wallet_money_text(bal),
            "display_currency": get_display_currency(),
            "methods": list_payment_methods(True),
        }
    )


# ---------------------------------------------------------------------------
# /api/validate-player
# ---------------------------------------------------------------------------
@bp.route("/validate-player", methods=["POST"])
@_rl("30 per minute")
def validate_player():
    """V71: validate a player ID with the supplier before order creation.

    Safe and capped:
      * ``@login_required``-equivalent (current_user() check below).
      * Rate-limit 30/min/IP via the ``_rl`` shim above.
      * Toggleable via the ``player_validation_api_enabled`` admin setting.
      * Process-local cache, 60s per (provider, product_id, player_id)
        — limits enumeration and supplier-side DDoS amplification.
    """
    user = current_user()
    if not user:
        return jsonify({"ok": False, "error": "يجب تسجيل الدخول"}), 401

    if get_setting("player_validation_api_enabled", "0") != "1":
        return jsonify(
            {
                "ok": True,
                "enabled": False,
                "success": False,
                "error": "خاصية التحقق من اسم اللاعب غير مفعّلة حاليًا",
            }
        )

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

    cache_key = (
        product["provider"],
        str(product["provider_product_id"]),
        player_id,
    )
    now = time.time()
    with _VP_CACHE_LOCK:
        cached = _VP_CACHE.get(cache_key)
        if cached and (now - cached[0]) < _VP_CACHE_TTL:
            return jsonify({"ok": True, "enabled": True, **cached[1]})

    result = validate_player_provider(
        product["provider"], product["provider_product_id"], player_id
    )
    # Strip raw provider response fields — they sometimes include credentials
    # or internal IDs that we don't want to leak to the front-end (a regression
    # fixed in v50).
    safe = {
        "success": bool(result.get("success")),
        "player_name": str(result.get("player_name") or ""),
        "verified_only": bool(result.get("verified_only")),
        "unsupported": bool(result.get("unsupported")),
    }
    if not safe["success"]:
        safe["error"] = str(result.get("error") or "تعذر التحقق من اللاعب")

    with _VP_CACHE_LOCK:
        if len(_VP_CACHE) >= 2000:
            _VP_CACHE.clear()
        _VP_CACHE[cache_key] = (now, safe)

    return jsonify({"ok": True, "enabled": True, **safe})


# ---------------------------------------------------------------------------
# CSRF exemption — applied at registration time
# ---------------------------------------------------------------------------
def init_csrf_exemption() -> None:
    """Mark this blueprint as exempt from CSRFProtect.

    Called from :func:`app.routes.register_blueprints` immediately after
    the blueprint is registered, so the exempt list is in place before
    the first request arrives.
    """
    if csrf is None:
        return
    try:
        csrf.exempt(bp)
    except Exception:
        # Older Flask-WTF needs per-view exemption; fall back to the
        # legacy approach.
        for fn in (
            login,
            register,
            logout,
            orders,
            validate_player,
            me,
            games,
            game,
            payment_methods,
            wallet,
        ):
            try:
                csrf.exempt(fn)
            except Exception:
                pass
