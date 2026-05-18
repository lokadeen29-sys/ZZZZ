"""V53 REFACTOR (phase 4): admin two-factor-auth routes extracted from app.py.

This Blueprint owns every route under ``/admin/2fa/*``. It is kept
deliberately separate from the main :mod:`routes.admin_bp` module because
the 2FA flow is self-contained, has its own session keys
(``admin_2fa_verified``), its own audit-log event family
(``ADMIN_2FA_*``), and its own rate-limit budget. Splitting it makes
the surface easier to audit and to test in isolation.

Routes
------
==================================================  ====================================
URL                                                 Endpoint
==================================================  ====================================
GET  /admin/2fa/setup                               ``admin_2fa.setup``
POST /admin/2fa/confirm                             ``admin_2fa.confirm``
GET/POST /admin/2fa/challenge                       ``admin_2fa.challenge``
POST /admin/2fa/disable                             ``admin_2fa.disable``
POST /admin/2fa/backup-codes/regenerate             ``admin_2fa.regenerate_backup_codes``
==================================================  ====================================

Endpoint names are namespaced under ``admin_2fa.*`` following the
convention introduced in phases 2 and 3. The
:func:`app.admin_required` decorator's whitelist of endpoints that
must NOT loop into the 2FA gate is updated in lock-step.

Decorator preservation
----------------------
Every decorator from the original is moved verbatim:

* ``@login_required`` / ``@admin_required`` — same order as before.
* ``@(limiter.limit("…") if limiter else (lambda f: f))`` — replaced by
  the local :func:`_rl` shim (identical to the one in
  :mod:`routes.auth_bp` / :mod:`routes.public_bp`) so behaviour is a
  no-op when Flask-Limiter is missing.
* CSRF: untouched. ``CSRFProtect`` continues to apply globally; none of
  these routes were CSRF-exempt in app.py.

Like the other phase-3 / phase-4 blueprints, this module imports
helpers from :mod:`app`. ``register_blueprints(app)`` is called at the
end of ``app.py`` after every helper has been defined so the imports
cannot crash on a circular reference.
"""
from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app import (
    MAX_PASSWORD_LEN,
    current_user,
    get_real_ip,
    get_setting,
    limiter,
    login_required,
    safe_next_url,
)
# admin_required is also defined in app.py; re-imported by name so the
# `@admin_required` decorator below resolves to the same callable.
from app import admin_required

from audit import log_audit
from database import (
    authenticate,
    disable_user_totp,
    enable_user_totp,
    set_user_totp_secret,
    update_user_backup_codes,
)
from security_2fa import (
    consume_backup_code,
    deserialize_backup_codes,
    generate_backup_codes,
    generate_totp_secret,
    provisioning_uri,
    qr_svg,
    serialize_backup_codes,
    verify_totp,
)


bp = Blueprint("admin_2fa", __name__)


def _rl(limit: str):
    """Local rate-limit shim mirroring the auth_bp / public_bp pattern.

    Returns ``limiter.limit(limit)`` when Flask-Limiter is installed and
    a no-op decorator otherwise. Identical to the
    ``@(limiter.limit(...) if limiter else (lambda f: f))`` expression
    that lived inline in app.py.
    """
    if limiter is None:
        return lambda f: f
    return limiter.limit(limit)


def _is_admin_user():
    """Return the current user dict only if it has the admin role.

    This used to live in app.py at module top-level. It is only used by
    the 2FA routes (the rest of /admin/* relies on ``@admin_required``
    which performs a stricter check including a 2FA gate), so it moves
    here with them.
    """
    u = current_user()
    return u if (u and u.get("role") == "admin") else None


# ---------------------------------------------------------------------------
# /admin/2fa/setup
# ---------------------------------------------------------------------------
@bp.route("/admin/2fa/setup", methods=["GET"])
@login_required
@admin_required
@_rl("10 per minute")
def setup():
    """Show the QR + secret. If the admin already has 2FA enabled, we
    redirect to the dashboard — regeneration goes through /disable first
    to force the password + current-TOTP confirmation path."""
    user = _is_admin_user()
    if not user:
        abort(403)
    if int(user.get("totp_enabled") or 0) == 1:
        flash("المصادقة الثنائية مفعّلة بالفعل. عطّلها أولاً لإعادة الإعداد.", "info")
        return redirect(url_for("admin.dashboard"))
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


# ---------------------------------------------------------------------------
# /admin/2fa/confirm
# ---------------------------------------------------------------------------
@bp.route("/admin/2fa/confirm", methods=["POST"])
@login_required
@admin_required
@_rl("10 per minute")
def confirm():
    """Confirm setup: verify the user can read codes from the authenticator,
    THEN flip totp_enabled and show the backup codes ONCE."""
    user = _is_admin_user()
    if not user:
        abort(403)
    if int(user.get("totp_enabled") or 0) == 1:
        return redirect(url_for("admin.dashboard"))
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
        return redirect(url_for("admin_2fa.setup"))
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


# ---------------------------------------------------------------------------
# /admin/2fa/challenge
# ---------------------------------------------------------------------------
@bp.route("/admin/2fa/challenge", methods=["GET", "POST"])
@login_required
@_rl("15 per minute")
def challenge():
    """Post-login gate for admins who have 2FA enabled. Accepts either a
    current TOTP code or a one-time backup code."""
    user = current_user()
    if not user or user.get("role") != "admin":
        abort(403)
    if int(user.get("totp_enabled") or 0) != 1:
        # Nothing to challenge — skip to admin.
        return redirect(url_for("admin.dashboard"))
    if session.get("admin_2fa_verified"):
        return redirect(safe_next_url("admin.dashboard"))

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
            return redirect(safe_next_url("admin.dashboard"))
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
            return redirect(safe_next_url("admin.dashboard"))
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


# ---------------------------------------------------------------------------
# /admin/2fa/disable
# ---------------------------------------------------------------------------
@bp.route("/admin/2fa/disable", methods=["POST"])
@login_required
@admin_required
@_rl("5 per minute")
def disable():
    """Disable 2FA. Requires both the current password AND a valid TOTP
    code (or backup code) — same strength as the challenge itself."""
    user = _is_admin_user()
    if not user or int(user.get("totp_enabled") or 0) != 1:
        flash("المصادقة الثنائية غير مفعّلة.", "info")
        return redirect(url_for("admin.dashboard"))
    password = request.form.get("password", "")
    code = (request.form.get("code") or "").strip()
    if len(password) > MAX_PASSWORD_LEN or not password:
        flash("كلمة المرور مطلوبة.", "danger")
        return redirect(url_for("admin.dashboard"))
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
        return redirect(url_for("admin.dashboard"))
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
        return redirect(url_for("admin.dashboard"))
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
    return redirect(url_for("admin.dashboard"))


# ---------------------------------------------------------------------------
# /admin/2fa/backup-codes/regenerate
# ---------------------------------------------------------------------------
@bp.route("/admin/2fa/backup-codes/regenerate", methods=["POST"])
@login_required
@admin_required
@_rl("3 per hour")
def regenerate_backup_codes():
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
        return redirect(url_for("admin.dashboard"))
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


# Silence the (unused) ``get_setting`` import warning while keeping the
# import there for any future 2FA gating that needs to read settings —
# we don't want a future contributor to remove it and trigger a circular
# import dance.
_ = get_setting

__all__ = ["bp"]
