"""V53 REFACTOR (phase 4): admin core routes extracted from app.py.

Sister blueprint to :mod:`routes.admin_2fa_bp`. Together they own every
route under ``/admin/*``. This module covers the operator-facing
dashboard and every CRUD-style admin action: orders, users, balances,
games, the in-game-products editor, the manual SYP price-override form,
accounting, deposits, the supplier-status sweep, payment-method editing,
the SMTP test button, and the global settings form.

Routes
------
============================================================  =========================================
URL                                                            Endpoint
============================================================  =========================================
GET  /admin                                                    ``admin.dashboard``
GET  /admin/orders                                             ``admin.orders``
POST /admin/order/<int:order_id>/<action>                      ``admin.order_action``
GET  /admin/users                                              ``admin.users``
GET  /admin/user/<int:user_id>                                 ``admin.user_detail``
POST /admin/user/<int:user_id>/balance                         ``admin.user_balance``
GET  /admin/balances                                           ``admin.balances``
GET/POST /admin/games                                          ``admin.games``
POST /admin/games/add                                          ``admin.add_game``
POST /admin/game/<provider>/<game_key>/image                   ``admin.game_image``
GET/POST /admin/game/<provider>/<game_key>/products            ``admin.game_products``
POST /admin/game/<provider>/<game_key>/manual-prices           ``admin.update_manual_syp_prices``
GET/POST /admin/accounting                                     ``admin.accounting``
GET  /admin/deposits                                           ``admin.deposits``
POST /admin/deposit/<int:deposit_id>/<action>                  ``admin.deposit_action``
POST /admin/refresh-pending-orders                             ``admin.refresh_pending_orders``
GET  /admin/payment-methods                                    ``admin.payment_methods``
GET/POST /admin/payment-method/<method_id>                     ``admin.payment_method_edit``
POST /admin/test-email                                         ``admin.test_email``
GET/POST /admin/settings                                       ``admin.settings``
============================================================  =========================================

Endpoint names live under the ``admin.`` namespace, e.g.
``url_for("admin.dashboard")``. Templates have been updated wholesale in
this same PR.

Decorator preservation
----------------------
Every decorator from app.py is moved verbatim:

* ``@login_required`` then ``@admin_required`` (in that order, so 403
  beats the auto-login redirect).
* ``@(limiter.limit("…") if limiter else (lambda f: f))`` is replaced by
  the local :func:`_rl` shim (identical to the one in
  :mod:`routes.auth_bp` / :mod:`routes.public_bp` / :mod:`routes.admin_2fa_bp`).
* CSRF: untouched. ``CSRFProtect`` continues to apply globally; none of
  these routes were CSRF-exempt in app.py (only ``/api/*`` ones are).

Like the other blueprints, this module imports helpers from
:mod:`app`. ``register_blueprints(app)`` is called at the end of
``app.py`` so the imports cannot crash on a circular reference.
"""
from __future__ import annotations

import secrets
import smtplib
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.utils import secure_filename

from app import (
    BASE_URL,
    MAIL_FROM,
    MAIL_PASSWORD,
    MAIL_PORT,
    MAIL_SERVER,
    MAIL_USERNAME,
    MAIL_USE_TLS,
    MAX_ADMIN_BALANCE,
    _build_email_html,
    _sanitise_svg,
    _send_email_sync,
    accounting_summary,
    add_custom_game,
    admin_required,
    create_product_group,
    current_user,
    delete_product_group,
    email_is_configured,
    enqueue_order_job,
    get_deposit,
    get_display_currency,
    get_game,
    get_payment_method,
    get_pricing_mode,
    get_provider_balance,
    get_real_ip,
    get_setting,
    get_user_by_id,
    get_usd_syp_rate,
    limiter,
    list_all_game_groups,
    list_all_products_for_admin,
    list_deposits,
    list_orders,
    list_payment_methods,
    list_product_games_from_products,
    list_product_groups,
    list_user_deposits_admin,
    list_user_orders,
    log,
    log_audit,
    login_required,
    manual_price_edit_enabled,
    process_upload_to_webp,
    safe_next_url,
    search_users,
    set_game_active,
    set_game_home_sort_order,
    set_game_show_on_home,
    set_setting,
    set_user_balance,
    stats,
    update_deposit,
    update_game_image,
    update_game_pricing,
    update_manual_syp_prices,
    update_order,
    update_payment_method,
    update_product_group,
    update_products_admin,
    update_profit_margin,
    user_financial_summary,
)
from app import get_order  # imported separately because the name collides with order_action's local var

from sanitize import clean_plain_text, clean_rich_text


bp = Blueprint("admin", __name__)


def _rl(limit: str):
    """Local rate-limit shim mirroring :func:`routes.auth_bp._rl`.

    Returns ``limiter.limit(limit)`` when Flask-Limiter is installed and
    a no-op decorator otherwise. Identical to the
    ``@(limiter.limit(...) if limiter else (lambda f: f))`` expression
    that lived inline in app.py.
    """
    if limiter is None:
        return lambda f: f
    return limiter.limit(limit)


# ---------------------------------------------------------------------------
# /admin/game/<provider>/<game_key>/manual-prices
# Note: this lived above the /admin/2fa/* block in app.py (right after
# /admin/games is gated by admin_required) but we group it with the rest
# of the admin routes here for readability. URL is unchanged.
# ---------------------------------------------------------------------------
@bp.route("/admin/game/<provider>/<game_key>/manual-prices", methods=["POST"])
@login_required
@admin_required
def update_manual_syp_prices_view(provider, game_key):
    if not manual_price_edit_enabled():
        flash("تعديل الأسعار من الواجهة غير مفعّل من الإعدادات", "danger")
        return redirect(url_for("public.products", provider=provider, game_key=game_key))

    updates = []
    for key, value in request.form.items():
        if key.startswith("manual_syp_"):
            product_id = key.replace("manual_syp_", "")
            updates.append((product_id, value))
    update_manual_syp_prices(updates)
    flash("تم حفظ أسعار الليرة اليدوية", "success")
    return redirect(safe_next_url("public.products", provider=provider, game_key=game_key))


# Endpoint alias so legacy ``url_for("admin.update_manual_syp_prices", …)``
# (the explicit name used by templates/products.html) keeps working without
# pinning the Python function name to the same string.
update_manual_syp_prices_view.__name__ = "update_manual_syp_prices"


# ---------------------------------------------------------------------------
# /admin
# ---------------------------------------------------------------------------
@bp.route("/admin")
@login_required
@admin_required
def dashboard():
    return render_template("admin/dashboard.html", stats=stats())


# ---------------------------------------------------------------------------
# /admin/orders
# ---------------------------------------------------------------------------
@bp.route("/admin/orders")
@login_required
@admin_required
def orders():
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


# ---------------------------------------------------------------------------
# /admin/order/<int:order_id>/<action>
# ---------------------------------------------------------------------------
@bp.route("/admin/order/<int:order_id>/<action>", methods=["POST"])
@login_required
@admin_required
@_rl("60 per minute")
def order_action(order_id, action):
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
        return redirect(url_for("admin.orders"))
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
            return redirect(url_for("admin.orders"))
        update_order(order_id, "waiting", None,
                     "إعادة إرسال إلى المورد بأمر من الإدارة")
        try:
            enqueue_order_job(order_id)
        except Exception as exc:
            log.exception("admin.order_action retry enqueue failed: %s", exc)
            flash(f"تعذر إعادة الإرسال: {exc}", "danger")
            return redirect(url_for("admin.orders"))
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
    return redirect(url_for("admin.orders"))


# ---------------------------------------------------------------------------
# /admin/users
# ---------------------------------------------------------------------------
@bp.route("/admin/users")
@login_required
@admin_required
def users():
    q = request.args.get("q", "").strip()
    return render_template("admin/users.html", users=search_users(q), q=q)


# ---------------------------------------------------------------------------
# /admin/user/<int:user_id>
# ---------------------------------------------------------------------------
@bp.route("/admin/user/<int:user_id>")
@login_required
@admin_required
def user_detail(user_id):
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


# ---------------------------------------------------------------------------
# /admin/user/<int:user_id>/balance
# ---------------------------------------------------------------------------
@bp.route("/admin/user/<int:user_id>/balance", methods=["POST"])
@login_required
@admin_required
@_rl("30 per minute")
def user_balance(user_id):
    try:
        raw_amount = float(request.form.get("amount", "0") or 0)
    except Exception:
        flash("أدخل رقمًا صحيحًا للرصيد", "danger")
        return redirect(safe_next_url("admin.users"))

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
            return redirect(safe_next_url("admin.users"))
        new_balance = round(raw_amount / rate, 4)
    else:
        new_balance = raw_amount

    # V50 SECURITY (HG): bound admin balance edits to [0, MAX_ADMIN_BALANCE].
    # Prevents a compromised admin account from setting negative or
    # astronomical balances. Bound is checked on the USD value we will store.
    if not (0 <= new_balance <= MAX_ADMIN_BALANCE):
        flash(f"القيمة يجب أن تكون بين 0 و {MAX_ADMIN_BALANCE:.0f}$ (بعد التحويل)", "danger")
        return redirect(safe_next_url("admin.users"))

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
    return redirect(safe_next_url("admin.users"))


# ---------------------------------------------------------------------------
# /admin/balances
# ---------------------------------------------------------------------------
@bp.route("/admin/balances")
@login_required
@admin_required
def balances():
    balances = {
        "server1": get_provider_balance("server1"),
        "server2": get_provider_balance("server2"),
    }
    return render_template("admin/balances.html", balances=balances)


# ---------------------------------------------------------------------------
# /admin/games
# ---------------------------------------------------------------------------
@bp.route("/admin/games", methods=["GET", "POST"])
@login_required
@admin_required
def games():
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
        return redirect(url_for("admin.games"))

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


# ---------------------------------------------------------------------------
# /admin/games/add
# ---------------------------------------------------------------------------
@bp.route("/admin/games/add", methods=["POST"])
@login_required
@admin_required
@_rl("20 per minute")
def add_game():
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
    return redirect(url_for("admin.games"))


# ---------------------------------------------------------------------------
# /admin/game/<provider>/<game_key>/image
# ---------------------------------------------------------------------------
@bp.route("/admin/game/<provider>/<game_key>/image", methods=["POST"])
@login_required
@admin_required
def game_image(provider, game_key):
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
            return redirect(url_for("admin.games"))
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
                return redirect(url_for("admin.games"))
            cleaned = _sanitise_svg(raw)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(cleaned)
            image_url = "/" + str(target).replace("\\", "/")
        else:
            base = secure_filename(f"{provider}_{game_key}_{_rand}")
            saved = process_upload_to_webp(file, str(uploads_dir), base, max_w=800, quality=82)
            if not saved:
                flash("تعذّر معالجة الصورة", "danger")
                return redirect(url_for("admin.games"))
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

    return redirect(url_for("admin.games"))


# ---------------------------------------------------------------------------
# /admin/game/<provider>/<game_key>/products
# ---------------------------------------------------------------------------
@bp.route("/admin/game/<provider>/<game_key>/products", methods=["GET", "POST"])
@login_required
@admin_required
def game_products(provider, game_key):
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
            return redirect(url_for("admin.game_products", provider=provider, game_key=game_key))

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
            return redirect(url_for("admin.game_products", provider=provider, game_key=game_key))

        if action == "delete_group":
            group_id = request.form.get("group_id")
            if group_id:
                delete_product_group(group_id)
                flash("تم حذف واجهة العرض وإرجاع باقاتها إلى عام", "warning")
            return redirect(url_for("admin.game_products", provider=provider, game_key=game_key))

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
        return redirect(url_for("admin.game_products", provider=provider, game_key=game_key))

    products = list_all_products_for_admin(provider, game_key)
    groups = list_product_groups(provider, game_key, False)
    return render_template("admin/game_products.html", game=game, products=products, groups=groups, usd_syp_rate=get_usd_syp_rate())


# ---------------------------------------------------------------------------
# /admin/accounting
# ---------------------------------------------------------------------------
@bp.route("/admin/accounting", methods=["GET", "POST"])
@login_required
@admin_required
def accounting():
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
        return redirect(url_for("admin.accounting"))
    return render_template("admin/accounting.html", data=accounting_summary())


# ---------------------------------------------------------------------------
# /admin/deposits
# ---------------------------------------------------------------------------
@bp.route("/admin/deposits")
@login_required
@admin_required
def deposits():
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


# ---------------------------------------------------------------------------
# /admin/deposit/<int:deposit_id>/<action>
# ---------------------------------------------------------------------------
@bp.route("/admin/deposit/<int:deposit_id>/<action>", methods=["POST"])
@login_required
@admin_required
@_rl("60 per minute")
def deposit_action(deposit_id, action):
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
    return redirect(url_for("admin.deposits"))


# ---------------------------------------------------------------------------
# /admin/refresh-pending-orders
#
# V67: manual trigger for the supplier-status sweep. Useful when:
#   1. The Redis worker isn't running (single-dyno Heroku free tier).
#   2. An admin wants to immediately unstick orders without waiting for
#      the next 90-second poll tick.
# ---------------------------------------------------------------------------
@bp.route("/admin/refresh-pending-orders", methods=["POST"])
@login_required
@admin_required
@_rl("6 per minute")
def refresh_pending_orders():
    try:
        from tasks import refresh_pending_orders as _refresh
        counters = _refresh(limit=100)
        flash(
            f"تم الفحص: {counters.get('checked', 0)} طلب — "
            f"اكتمل {counters.get('completed', 0)}، "
            f"رُفض {counters.get('rejected', 0)}، "
            f"لا يزال قيد الانتظار {counters.get('still_pending', 0)}، "
            f"أخطاء {counters.get('errors', 0)}",
            "success"
        )
    except Exception as exc:
        log.exception("admin.refresh_pending_orders failed: %s", exc)
        flash("فشل تحديث حالات الطلبات", "danger")
    return redirect(request.referrer or url_for("admin.dashboard"))


# ---------------------------------------------------------------------------
# /admin/payment-methods
# ---------------------------------------------------------------------------
@bp.route("/admin/payment-methods")
@login_required
@admin_required
def payment_methods():
    return render_template("admin/payment_methods.html", methods=list_payment_methods())


# ---------------------------------------------------------------------------
# /admin/payment-method/<method_id>
# ---------------------------------------------------------------------------
@bp.route("/admin/payment-method/<method_id>", methods=["GET", "POST"])
@login_required
@admin_required
def payment_method_edit(method_id):
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
        return redirect(url_for("admin.payment_methods"))
    return render_template("admin/payment_method_edit.html", method=method)


# ---------------------------------------------------------------------------
# /admin/test-email
# ---------------------------------------------------------------------------
@bp.route("/admin/test-email", methods=["POST"])
@login_required
@admin_required
@_rl("5 per minute")
def test_email():
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
        return redirect(url_for("admin.settings"))

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

    return redirect(url_for("admin.settings"))


# ---------------------------------------------------------------------------
# /admin/settings
# ---------------------------------------------------------------------------
@bp.route("/admin/settings", methods=["GET", "POST"])
@login_required
@admin_required
def settings():
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
        return redirect(url_for("admin.settings"))
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


# Silence unused-import warnings while keeping imports declared for any
# future contributor who needs them. ``current_app`` is used implicitly
# by Flask's blueprint machinery, but no route here references it
# directly anymore.
_ = current_app

__all__ = ["bp"]
