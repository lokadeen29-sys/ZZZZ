"""V53 REFACTOR (phase 3): public-facing routes extracted from app.py.

This Blueprint owns every route a logged-out OR logged-in user can reach
that is *not* part of auth, OAuth, the wallet, the admin area, or the JSON
API. Endpoint names live under the ``public.`` namespace
(e.g. ``url_for("public.home")``, ``url_for("public.products", ...)``).

Routes
------
Public information / SEO:
  GET  /reset-lang                    -> public.reset_lang
  GET  /lang/<lang>                   -> public.set_language
  GET  /robots.txt                    -> public.robots_txt
  GET  /manifest.json                 -> public.manifest_json
  GET  /email-info                    -> public.email_info
  GET  /sitemap.xml                   -> public.sitemap_xml
  GET  /legacy/<path:rest>            -> public.legacy_redirect (301 to /<rest>)
  GET  /uploads/proof/<filename>      -> public.serve_proof (login + ACL)
  GET  /static/uploads/<path:_ig>     -> public._block_static_uploads (403)
  GET  /privacy                       -> public.privacy
  GET  /terms                         -> public.terms
  GET  /refund                        -> public.refund
  GET  /contact                       -> public.contact

Storefront:
  GET  /                              -> public.home  (also /legacy)
  GET  /dashboard                     -> public.dashboard (login)
  GET  /servers                       -> public.servers (also /legacy/servers)
  GET  /games/<provider>              -> public.games (also /legacy/...)
  GET  /games                         -> public.games_index (redirect to home)
  GET  /all-games                     -> public.all_games (also /legacy/...)
  GET  /products/<provider>/<game>    -> public.products (also /legacy/...)
  GET  /products/.../group/<id>       -> public.products_group
  G/POST /checkout/<int:product_id>   -> public.checkout (login + 20/min)
  G/POST /profile                     -> public.profile (login)
  GET  /orders                        -> public.orders (login, also /legacy/...)

Design notes
------------
- This module imports a handful of helpers from app.py (limiter,
  current_user, login_required, safe_next_url, BASE_URL, etc.). To keep
  those imports working without a circular-import crash, ``app.py``
  registers this blueprint at the *end* of its module body — after every
  helper has been defined. See ``register_blueprints(app)`` at the bottom
  of app.py.
- Only the HTTP layer (parse → call helper → render) lives here; heavy
  business logic stays in ``database.py`` / ``services/`` so the diff is
  pure relocation.
- Endpoint names are namespaced (``public.home``, ``public.wallet``,
  etc.) to match the convention established by ``routes/auth_bp.py`` in
  phase 2. Templates and other modules referring to these endpoints have
  been updated wholesale in this same PR.
- Every decorator and rate-limit annotation from the original app.py
  block is preserved verbatim — only the leading ``@app.route`` becomes
  ``@bp.route`` and is augmented with explicit ``methods=`` where the
  original decorator implied them.
"""
from __future__ import annotations

import os
import secrets

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from markupsafe import Markup
from werkzeug.utils import secure_filename

from app.config import BaseConfig
from app.extensions import limiter
from app.services.mail import (
    _BASE_DOMAIN,
    send_email_change_confirmation,
)
from app.services.orders import enqueue_order_job
from app.utils.auth import current_user, login_required
from app.utils.i18n import current_lang
from app.utils.security import safe_next_url
from app.utils.settings_cache import get_setting
from audit import log_audit
from database import (
    InsufficientBalance,
    can_download_proof,
    create_order,
    get_game,
    get_product,
    get_product_group,
    get_user_by_email,
    list_games,
    list_home_games,
    list_product_groups,
    list_products,
    list_public_games,
    list_public_product_groups_for_home,
    list_user_orders,
    set_pending_email_change,
    stats,
    update_user_profile,
)
import logging

log = logging.getLogger("tecnogems.public")

# Length caps + URL constants (mirror the legacy app.py module globals).
BASE_URL = BaseConfig.BASE_URL
MAX_PLAYER_ID_LEN = BaseConfig.MAX_PLAYER_ID_LEN


bp = Blueprint("public", __name__)


def _rl(limit: str):
    """Mirror of the rate-limit shim used in routes/auth_bp.py.

    Returns the live ``limiter.limit(limit)`` decorator when Flask-Limiter
    is installed; otherwise returns a no-op decorator. Keeps every route's
    rate-limit annotation behaviour-identical to the pre-refactor
    ``@(limiter.limit(...) if limiter else (lambda f: f))`` pattern.
    """
    if limiter is None:
        return lambda f: f
    return limiter.limit(limit)


# ---------------------------------------------------------------------------
# /reset-lang  +  /lang/<lang>
# ---------------------------------------------------------------------------
@bp.route("/reset-lang")
def reset_lang():
    session.pop("lang", None)
    session.pop("lang_user_selected", None)
    return redirect(url_for("public.home"))


@bp.route("/lang/<lang>")
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
    nxt = request.args.get("next") or (referer if same_origin else None) or url_for("public.home")
    if not isinstance(nxt, str) or not nxt.startswith("/") or nxt.startswith("/lang/"):
        nxt = url_for("public.home")
    resp = redirect(nxt)
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


# ---------------------------------------------------------------------------
# /robots.txt  +  /manifest.json
# ---------------------------------------------------------------------------
@bp.route("/robots.txt")
def robots_txt():
    return current_app.send_static_file("robots.txt")


@bp.route("/manifest.json")
def manifest_json():
    return current_app.send_static_file("manifest.json")


# ---------------------------------------------------------------------------
# /email-info  (V67 deliverability-trust info page)
# ---------------------------------------------------------------------------
# V67 DELIVERABILITY: public, no-auth informational page about our mail.
# Linked from email footers — boosts Gmail's trust signal that the sender
# operates a real, navigable HTTPS site for the From: domain. Also serves
# as a no-cost replacement for List-Unsubscribe (removed from headers
# because it incorrectly classified transactional mail as bulk).
@bp.route("/email-info")
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


# ---------------------------------------------------------------------------
# /sitemap.xml
# ---------------------------------------------------------------------------
@bp.route("/sitemap.xml")
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


# ---------------------------------------------------------------------------
# /legacy/<path:rest>  (back-compat redirect)
# ---------------------------------------------------------------------------
# Back-compat: redirect any remaining /legacy/... links to clean URLs (single rule)
@bp.route("/legacy/<path:rest>")
def legacy_redirect(rest):
    return redirect("/" + rest, code=301)


# ---------------------------------------------------------------------------
# /uploads/proof/<filename>  (login required + ACL)
# ---------------------------------------------------------------------------
# Secure proof file delivery (login required, owner-or-admin via DB check)
@bp.route("/uploads/proof/<path:filename>")
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

    full = os.path.join(current_app.config["UPLOAD_FOLDER"], safe)
    if not os.path.exists(full):
        # Return 403 instead of 404 to avoid file-existence enumeration
        abort(403)

    log_audit(
        "PROOF_DOWNLOAD",
        actor_id=user["id"],
        metadata={"filename": safe, "admin_viewing": is_admin},
    )
    resp = send_from_directory(current_app.config["UPLOAD_FOLDER"], safe, as_attachment=False)
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------------------
# /static/uploads/<path:_ignored>  (legacy path block)
# ---------------------------------------------------------------------------
# V50 SECURITY (H4): explicitly block the legacy public path.
# Flask's built-in static handler would otherwise happily serve anything
# left in static/uploads/ to the world without auth. This route takes
# precedence and forces a 403.
@bp.route("/static/uploads/<path:_ignored>")
def _block_static_uploads(_ignored):
    abort(403)


# ---------------------------------------------------------------------------
# Legal / info pages
# ---------------------------------------------------------------------------
# --- Legal / info pages ---
@bp.route("/privacy")
def privacy():
    return render_template("privacy.html", title="سياسة الخصوصية",
                           seo_title="سياسة الخصوصية - TecnoGems",
                           seo_description="سياسة الخصوصية لمنصة TecnoGems لشحن الألعاب.")


@bp.route("/terms")
def terms():
    return render_template("terms.html", title="شروط الاستخدام",
                           seo_title="شروط الاستخدام - TecnoGems",
                           seo_description="شروط استخدام منصة TecnoGems لشحن الألعاب.")


@bp.route("/refund")
def refund():
    return render_template("refund.html", title="سياسة الاسترجاع",
                           seo_title="سياسة الاسترجاع - TecnoGems",
                           seo_description="سياسة الاسترجاع والاستبدال في منصة TecnoGems.")


@bp.route("/contact")
def contact():
    return render_template("contact.html", title="اتصل بنا",
                           seo_title="اتصل بنا - TecnoGems",
                           seo_description="تواصل مع فريق دعم TecnoGems عبر واتساب أو تيليجرام أو البريد الإلكتروني.")


# ---------------------------------------------------------------------------
# /  +  /legacy   (home)
# ---------------------------------------------------------------------------
@bp.route("/")
@bp.route("/legacy")
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
# /dashboard
# ---------------------------------------------------------------------------
@bp.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    orders = list_user_orders(user["id"])[:5]
    return render_template("dashboard.html", orders=orders)


# ---------------------------------------------------------------------------
# /servers   (also /legacy/servers)
# ---------------------------------------------------------------------------
@bp.route("/servers")
@bp.route("/legacy/servers")
def servers():
    return redirect(url_for("public.home"))


# ---------------------------------------------------------------------------
# /games/<provider>   (also /legacy/games/<provider>)
# ---------------------------------------------------------------------------
@bp.route("/games/<provider>")
@bp.route("/legacy/games/<provider>")
def games(provider):
    if provider not in ("server1", "server2"):
        abort(404)
    games = list_games(provider)
    return render_template("games.html", provider=provider, games=games)


# ---------------------------------------------------------------------------
# /all-games   (also /legacy/all-games)
# ---------------------------------------------------------------------------
# V55: Landing page for "عرض جميع الألعاب" — shows every active game in one grid.
@bp.route("/all-games")
@bp.route("/legacy/all-games")
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


# ---------------------------------------------------------------------------
# /products/<provider>/<game_key>   (also /legacy/products/...)
# ---------------------------------------------------------------------------
@bp.route("/products/<provider>/<game_key>")
@bp.route("/legacy/products/<provider>/<game_key>")
def products(provider, game_key):
    game = get_game(provider, game_key)
    if not game:
        abort(404)
    groups = list_product_groups(provider, game_key, True)
    if groups:
        return render_template("product_groups.html", provider=provider, game=game, groups=groups)
    products = list_products(provider, game_key)
    return render_template("products.html", provider=provider, game=game, products=products, group=None)


# ---------------------------------------------------------------------------
# /products/<provider>/<game_key>/group/<int:group_id>
# ---------------------------------------------------------------------------
@bp.route("/products/<provider>/<game_key>/group/<int:group_id>")
def products_group(provider, game_key, group_id):
    game = get_game(provider, game_key)
    group = get_product_group(group_id)
    if not game or not group or group["provider"] != provider or group["game_key"] != game_key or not group.get("active", 1):
        abort(404)
    products = list_products(provider, game_key, group_id=group_id)
    return render_template("products.html", provider=provider, game=game, products=products, group=group)


# ---------------------------------------------------------------------------
# /checkout/<int:product_id>   (also /legacy/checkout/<int:product_id>)
# ---------------------------------------------------------------------------
@bp.route("/checkout/<int:product_id>", methods=["GET", "POST"])
@bp.route("/legacy/checkout/<int:product_id>", methods=["GET", "POST"])
@_rl("20 per minute")
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
            return redirect(safe_next_url("public.home"))
        except Exception as _e:
            log.exception("create_order failed: %s", _e)
            flash("حدث خطأ أثناء إنشاء الطلب، يرجى المحاولة مجدداً", "danger")
            return redirect(request.url)

        enqueue_order_job(order_id, product, player_id)
        # V67: clearer confirmation with a clickable link to the orders page
        # so the user can immediately track execution status. Also tells the
        # user the order has been received (matches the wording the user
        # asked for: "تم استلام طلب الشراء وبانتظار التنفيذ").
        track_url = url_for("public.orders")
        flash(Markup(
            f'تم استلام الطلب <strong>{code}</strong> وبانتظار بدء التنفيذ. '
            f'لمتابعة حالة طلبك <a href="{track_url}" class="alert-link"><strong>اضغط هنا</strong></a>.'
        ), "success")
        return redirect(url_for("public.orders"))
    return render_template(
        "checkout.html",
        product=product,
        game=game,
        show_server1=get_setting("show_server1", "1"),
        show_server2=get_setting("show_server2", "1"),
        # V71: نمرّر حالة الإعداد للقالب حتى يقرّر إظهار/إخفاء زر "تحقق من الاسم"
        player_validation_enabled=(get_setting("player_validation_api_enabled", "0") == "1"),
    )


# ---------------------------------------------------------------------------
# /profile
# ---------------------------------------------------------------------------
@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        new_email = request.form.get("new_email", "").strip().lower()

        if not name:
            flash("الاسم مطلوب", "danger")
            return redirect(url_for("public.profile"))

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
        return redirect(url_for("public.profile"))

    return render_template("profile.html", user=user)


# ---------------------------------------------------------------------------
# /orders   (also /legacy/orders)
# ---------------------------------------------------------------------------
@bp.route("/orders")
@bp.route("/legacy/orders")
@login_required
def orders():
    user = current_user()
    return render_template("orders.html", orders=list_user_orders(user["id"]))


# ---------------------------------------------------------------------------
# /games   (games_index — simple redirect to home)
# ---------------------------------------------------------------------------
@bp.route("/games")
def games_index():
    return redirect(url_for("public.home"))


__all__ = ["bp"]
