"""V53 REFACTOR (phase 1): auth routes extracted from app.py.

This Blueprint owns every route that deals with authentication:
  GET/POST /login
  GET/POST /logout
  GET/POST /register
  GET      /verify-email/<token>
  GET/POST /resend-verification
  GET/POST /forgot-password
  GET/POST /reset-password/<token>
  GET      /auth/google
  GET      /auth/google/callback

Endpoint names live under the `auth.` namespace, e.g. url_for("auth.login").

Design notes
------------
- This module imports a handful of helpers from app.py (limiter, current_user,
  safe_next_url, validate_password_strength, the email senders, etc.). To keep
  those imports working without a circular-import crash, `app.py` registers
  this blueprint at the *end* of its module body — after every helper has been
  defined. See the `register_blueprints(app)` call at the bottom of app.py.

- Only the HTTP layer (parse -> call helper -> render) lives here; the heavy
  business logic continues to live in `database.py` (create_user, authenticate,
  verify_user_email, etc.). A future phase will extract it into a dedicated
  services/auth_service.py module — for phase 1 we keep the behaviour 100%
  identical to pre-refactor app.py.
"""
from __future__ import annotations

import secrets

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from markupsafe import Markup

from app import (
    MAX_EMAIL_LEN,
    MAX_NAME_LEN,
    MAX_PASSWORD_LEN,
    MAX_PHONE_LEN,
    app as _flask_app,
    authenticate,
    create_user,
    email_verification_is_enabled,
    get_real_ip,
    get_setting,
    get_user_by_email,
    get_user_by_id,
    get_user_by_reset_token,
    limiter,
    log,
    reset_user_password,
    safe_next_url,
    send_password_reset_email,
    send_verification_email,
    set_password_reset_token,
    set_user_email_token,
    validate_password_strength,
    verify_user_email,
)
# Google OAuth helpers are namespaced in app.py
import app as _app_module

# Pre-wired DB helpers for Google OAuth
from database import (
    create_user_oauth as _db_create_user_oauth,
    get_user_by_google_sub as _db_get_user_by_google_sub,
    link_user_google_sub as _db_link_user_google_sub,
)

bp = Blueprint("auth", __name__)


def _rl(limit: str):
    """Return a decorator that applies a Flask-Limiter rule when available.

    Mirrors the `@(limiter.limit("…") if limiter else (lambda f: f))` pattern
    used throughout app.py so behaviour is unchanged.
    """
    if limiter is None:
        return lambda f: f
    return limiter.limit(limit)


# ---------------------------------------------------------------------------
# /register
# ---------------------------------------------------------------------------
@bp.route("/register", methods=["GET", "POST"])
@_rl("8 per minute")
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")
        # V50 SECURITY (HD): enforce upper length bounds before hashing the
        # password. Without this a 10MB password would consume CPU in
        # pbkdf2 and be a cheap DoS vector.
        if (
            len(password) > MAX_PASSWORD_LEN
            or len(email) > MAX_EMAIL_LEN
            or len(request.form.get("name", "")) > MAX_NAME_LEN
            or len(request.form.get("phone", "")) > MAX_PHONE_LEN
        ):
            flash("أحد الحقول تجاوز الحد المسموح به", "danger")
            return render_template(
                "register.html",
                hide_phone_on_register=get_setting("hide_phone_on_register", "0"),
            )
        if password != password_confirm:
            flash("كلمة المرور وتأكيدها غير متطابقين", "danger")
            return render_template(
                "register.html",
                hide_phone_on_register=get_setting("hide_phone_on_register", "0"),
            )
        pw_ok, pw_err = validate_password_strength(password)
        if not pw_ok:
            flash(pw_err, "danger")
            return render_template(
                "register.html",
                hide_phone_on_register=get_setting("hide_phone_on_register", "0"),
            )
        verification_enabled = email_verification_is_enabled()
        token = secrets.token_urlsafe(32) if verification_enabled else None

        ok, err = create_user(
            request.form.get("name", "").strip(),
            email,
            request.form.get("phone", "").strip()
            if get_setting("hide_phone_on_register", "0") != "1"
            else "",
            password,
            email_verified=0 if verification_enabled else 1,
            email_token=token,
        )
        if ok:
            if verification_enabled:
                try:
                    send_verification_email(email, token)
                    # V67.2: clear, elegant confirmation that also tells the
                    # user the link might land in Spam / Junk. We use Markup
                    # so the hint can be visually distinct (icon + smaller
                    # secondary line) without an extra template change.
                    flash(Markup(
                        '<div style="display:flex;align-items:flex-start;gap:10px">'
                        '<span style="font-size:20px;line-height:1.2">📧</span>'
                        '<div>'
                        '<strong>تم إنشاء حسابك بنجاح.</strong> '
                        'أرسلنا رابط التفعيل إلى بريدك الإلكتروني '
                        f'<strong>{email}</strong>.'
                        '<div style="margin-top:6px;font-size:13px;opacity:.9">'
                        '💡 إن لم تجد الرسالة في صندوق الوارد خلال دقيقة، '
                        'فمن فضلك تحقق من مجلّد '
                        '<strong>الرسائل غير المرغوب فيها (Spam / Junk)</strong>.'
                        '</div>'
                        '</div>'
                        '</div>'
                    ), "success")
                except Exception as exc:
                    log.warning("verification email failed for %s: %s", email, exc)
                    user = get_user_by_email(email)
                    if user:
                        set_user_email_token(user["id"], token)
                    flash(
                        "تم إنشاء الحساب، لكن لم يتم إرسال بريد التفعيل. افحص "
                        "إعدادات Gmail App Password في ملف .env، ثم استخدم "
                        "إعادة إرسال رابط التفعيل.",
                        "warning",
                    )
                return redirect("/login")

            flash("تم إنشاء الحساب. يمكنك تسجيل الدخول الآن.", "success")
            return redirect("/login")
        flash(err or "فشل إنشاء الحساب", "danger")
    return render_template(
        "register.html",
        hide_phone_on_register=get_setting("hide_phone_on_register", "0"),
    )


# ---------------------------------------------------------------------------
# /verify-email/<token>
# ---------------------------------------------------------------------------
@bp.route("/verify-email/<token>")
@_rl("20 per hour")  # V50.2 MEDIUM: was unlimited
def verify_email(token):
    ok, err = verify_user_email(token)
    if ok:
        flash(
            "تم تفعيل البريد الإلكتروني بنجاح. يمكنك تسجيل الدخول الآن.",
            "success",
        )
    else:
        flash(err or "تعذر تفعيل البريد الإلكتروني.", "danger")
    return redirect("/login")


# ---------------------------------------------------------------------------
# /resend-verification
# ---------------------------------------------------------------------------
@bp.route("/resend-verification", methods=["GET", "POST"])
@_rl("3 per minute;20 per hour")  # V50.2 MEDIUM: was unlimited
def resend_verification():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = get_user_by_email(email)
        if user and not user.get("email_verified"):
            token = secrets.token_urlsafe(32)
            set_user_email_token(user["id"], token)
            try:
                send_verification_email(email, token)
                flash("تم إرسال رابط تفعيل جديد إلى بريدك.", "success")
            except Exception as exc:
                flash(f"تعذر إرسال رابط التفعيل: {exc}", "danger")
        else:
            flash(
                "إذا كان البريد مسجلًا وغير مفعل، سيتم إرسال رابط تفعيل.",
                "info",
            )
        return redirect("/login")
    return render_template("resend_verification.html")


# ---------------------------------------------------------------------------
# /forgot-password
# ---------------------------------------------------------------------------
@bp.route("/forgot-password", methods=["GET", "POST"])
@_rl("5 per minute")
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = get_user_by_email(email)
        if user:
            token = secrets.token_urlsafe(32)
            set_password_reset_token(user["id"], token)
            try:
                send_password_reset_email(email, token)
            except Exception as exc:
                flash(f"تعذر إرسال رابط الاستعادة: {exc}", "danger")
                return redirect(url_for("auth.forgot_password"))
        flash("إذا كان البريد مسجلًا، أرسلنا رابط استعادة كلمة المرور.", "info")
        return redirect("/login")
    return render_template("forgot_password.html")


# ---------------------------------------------------------------------------
# /reset-password/<token>
# ---------------------------------------------------------------------------
@bp.route("/reset-password/<token>", methods=["GET", "POST"])
@_rl("10 per hour")  # V50.2 MEDIUM: was unlimited
def reset_password(token):
    user = get_user_by_reset_token(token)
    if not user:
        flash("رابط الاستعادة غير صحيح أو انتهت صلاحيته.", "danger")
        return redirect("/login")

    if request.method == "POST":
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")
        if password != password_confirm:
            flash("كلمة المرور وتأكيدها غير متطابقين", "danger")
            return render_template("reset_password.html", token=token)
        ok, err = reset_user_password(token, password)
        if ok:
            flash("تم تغيير كلمة المرور. يمكنك تسجيل الدخول الآن.", "success")
            return redirect("/login")
        flash(err or "تعذر تغيير كلمة المرور", "danger")
    return render_template("reset_password.html", token=token)


# ---------------------------------------------------------------------------
# /login
# ---------------------------------------------------------------------------
@bp.route("/login", methods=["GET", "POST"])
@_rl("10 per minute")
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        # V50 SECURITY (HD): reject oversized inputs before password hashing
        # to prevent CPU-DoS via huge passwords.
        if len(password) > MAX_PASSWORD_LEN or len(email) > MAX_EMAIL_LEN:
            log.warning(
                "Rejected oversized login inputs from %s", get_real_ip()
            )
            flash("بيانات الدخول غير صحيحة", "danger")
            return render_template(
                "login.html",
                prefill_email="",
                seo_title="تسجيل الدخول - TecnoGems شحن ألعاب",
                seo_description=(
                    "سجّل الدخول إلى حسابك في TecnoGems لمتابعة طلبات شحن "
                    "الألعاب وإدارة محفظتك."
                ),
            )
        user = authenticate(email, password)
        if user:
            if (
                email_verification_is_enabled()
                and user["role"] != "admin"
                and not user.get("email_verified")
            ):
                flash(
                    "يجب تفعيل بريدك الإلكتروني قبل تسجيل الدخول. راجع بريدك.",
                    "warning",
                )
                return redirect("/login")
            # PATCH-H2: prevent session fixation by clearing any pre-existing
            # session data before assigning the authenticated user id.
            session.clear()
            session["user_id"] = user["id"]
            session["sess_v"] = int(user.get("session_version") or 1)
            session.permanent = True
            # V51 task B: for admins with 2FA enabled, mark the session
            # as "password-only" (not yet 2FA-verified). admin_required
            # will bounce them to /admin/2fa/challenge on first admin hit.
            if (
                user.get("role") == "admin"
                and int(user.get("totp_enabled") or 0) == 1
            ):
                session["admin_2fa_verified"] = False
            # V67.1: removed the standalone "تم تسجيل الدخول بنجاح" flash.
            # The flash session entry was lingering and showing up on the
            # next page load (e.g. after submitting a top-up request), so
            # the user saw "تم تسجيل الدخول بنجاح" instead of the actual
            # confirmation we wanted them to see. The navbar already shows
            # the user is logged in (balance pill, profile icon, logout),
            # so the flash is redundant.
            return redirect(safe_next_url("home"))
        # V50 SECURITY (M10): log failed auth attempts for monitoring / fail2ban.
        log.warning(
            "Failed login attempt for email=%s from ip=%s",
            email,
            get_real_ip(),
        )
        flash("بيانات الدخول غير صحيحة", "danger")
        return render_template(
            "login.html",
            prefill_email=email,
            seo_title="تسجيل الدخول - TecnoGems شحن ألعاب",
            seo_description=(
                "سجّل الدخول إلى حسابك في TecnoGems لمتابعة طلبات شحن "
                "الألعاب وإدارة محفظتك."
            ),
        )
    return render_template(
        "login.html",
        seo_title="تسجيل الدخول - TecnoGems شحن ألعاب",
        seo_description=(
            "سجّل الدخول إلى حسابك في TecnoGems لمتابعة طلبات شحن الألعاب "
            "وإدارة محفظتك."
        ),
    )


# ---------------------------------------------------------------------------
# /logout
# ---------------------------------------------------------------------------
@bp.route("/logout", methods=["GET", "POST"])
def logout():
    # V50.2 LOW: prefer POST (CSRF-protected form) for logout. GET is kept
    # for backwards compatibility with existing <a href="/logout"> links
    # and email deep-links, but new UI code should POST. When the request
    # is a GET we still log it so we can track how many clients are using
    # the deprecated path.
    if request.method == "GET":
        log.info(
            "logout via GET from ip=%s user_id=%s — consider moving to POST form",
            get_real_ip(),
            session.get("user_id"),
        )
    session.clear()
    flash("تم تسجيل الخروج", "info")
    return redirect(url_for("home"))


# ---------------------------------------------------------------------------
# /auth/google  +  /auth/google/callback
# ---------------------------------------------------------------------------
@bp.route("/auth/google")
def auth_google_login():
    _oauth = getattr(_app_module, "_oauth", None)
    if not _oauth:
        flash("تسجيل الدخول بـ Google غير مفعّل حالياً", "warning")
        return redirect(url_for("auth.login"))
    return _oauth.google.authorize_redirect(
        getattr(_app_module, "GOOGLE_REDIRECT_URI", "")
    )


@bp.route("/auth/google/callback")
def auth_google_callback():
    _oauth = getattr(_app_module, "_oauth", None)
    if not _oauth:
        return redirect(url_for("auth.login"))
    try:
        token = _oauth.google.authorize_access_token()
        userinfo = token.get("userinfo") or _oauth.google.parse_id_token(token, None)
    except Exception as exc:
        _flask_app.logger.error("Google OAuth callback failed: %s", exc)
        flash("تعذر إكمال تسجيل الدخول بـ Google", "danger")
        return redirect(url_for("auth.login"))

    sub = (userinfo or {}).get("sub")
    email = ((userinfo or {}).get("email") or "").strip().lower()
    name = (userinfo or {}).get("name") or ""
    if not sub or not email:
        flash("لم يتم استلام بيانات كافية من Google", "danger")
        return redirect(url_for("auth.login"))

    user = _db_get_user_by_google_sub(sub)
    if not user:
        existing = get_user_by_email(email)
        if existing:
            _db_link_user_google_sub(existing["id"], sub)
            user = existing
        else:
            uid = _db_create_user_oauth(name, email, sub)
            if not uid:
                flash("فشل إنشاء حساب جديد من Google", "danger")
                return redirect(url_for("auth.login"))
            user = get_user_by_id(uid)

    # PATCH-H2: clear session before assigning user id (Google OAuth too)
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    # V67.1: same as the password-login path — drop the noisy flash so it
    # cannot leak onto a later page (deposit-submitted screen, checkout, etc).
    return redirect(safe_next_url("home"))
