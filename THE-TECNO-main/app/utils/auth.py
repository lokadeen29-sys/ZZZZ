"""V53 REFACTOR (phase 5): authentication helpers extracted from app.py.

Pure HTTP-layer helpers — they read ``flask.session`` and ``flask.request``
but don't touch extensions or DB connections directly. Routes import these
via ``from app.utils.auth import login_required, current_user, ...``.

Everything here used to live inline in ``app.py``. Behaviour is preserved
1-for-1: same flash messages, same redirect targets, same error codes.
"""
from __future__ import annotations

from functools import wraps

from flask import abort, flash, redirect, request, session, url_for


# ---------------------------------------------------------------------------
# current_user — single source of truth for "who am I right now?"
# ---------------------------------------------------------------------------
def current_user():
    """Return the currently logged-in user dict or ``None``.

    Side effect: clears the session if the user was deactivated or had
    their password changed under us (V50 SECURITY HH + V53 SECURITY).
    The deferred import of ``database.get_user`` keeps this module
    cheap to import and avoids the legacy circular bridge.
    """
    uid = session.get("user_id")
    if not uid:
        return None
    from database import get_user  # local — avoid circular at import time
    user = get_user(uid)
    if not user:
        session.clear()
        return None
    # V50 SECURITY (HH): deactivated accounts must not retain access on a
    # long-lived session. authenticate() checks active=1 at login but the
    # per-request guard did not — until V50.
    try:
        if int(user.get("active", 1)) != 1:
            session.clear()
            return None
    except Exception:
        pass
    # V53 SECURITY: invalidate session when password was changed (the
    # session_version mismatch means the password was reset *after* this
    # session was created).
    db_version = int(user.get("session_version") or 1)
    sess_version = session.get("sess_v")
    if sess_version is not None and int(sess_version) != db_version:
        session.clear()
        return None
    return user


# ---------------------------------------------------------------------------
# login_required — gate a route on a valid logged-in session
# ---------------------------------------------------------------------------
def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            flash("يرجى تسجيل الدخول أولًا", "warning")
            return redirect("/login")
        if not current_user():
            # V53 SECURITY: re-validate even if user_id is in the session.
            flash("يرجى تسجيل الدخول أولًا", "warning")
            return redirect("/login")
        return fn(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# admin_required — gate a route on admin role + 2FA gate
# ---------------------------------------------------------------------------
def admin_required(fn):
    """Require admin role; if 2FA is enabled (or globally required), enforce it.

    Behaviour matches the legacy ``app.py`` admin_required exactly:

      * 403 for non-admins.
      * If the user has ``totp_enabled=1`` they MUST pass the challenge
        once per session (``session["admin_2fa_verified"] = 1``).
      * If they haven't enabled 2FA but the global setting
        ``admin_2fa_required`` is "1", redirect to the setup page.
      * The 2FA endpoints themselves (``admin_2fa.*``) are whitelisted so
        the user can actually complete setup / challenge.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        from app.utils.settings_cache import get_setting

        user = current_user()
        if not user or user["role"] != "admin":
            abort(403)
        endpoint = request.endpoint or ""
        whitelist = {
            "admin_2fa.setup",
            "admin_2fa.confirm",
            "admin_2fa.challenge",
            "admin_2fa.disable",
            "admin_2fa.regenerate_backup_codes",
        }
        if endpoint not in whitelist:
            if int(user.get("totp_enabled") or 0) == 1:
                if not session.get("admin_2fa_verified"):
                    flash(
                        "يرجى إدخال رمز المصادقة الثنائية للمتابعة.",
                        "warning",
                    )
                    return redirect(
                        url_for("admin_2fa.challenge", next=request.full_path)
                    )
            else:
                if get_setting("admin_2fa_required", "0") == "1":
                    flash(
                        "يجب تفعيل المصادقة الثنائية لحسابات الإدارة.",
                        "warning",
                    )
                    return redirect(url_for("admin_2fa.setup"))
        return fn(*args, **kwargs)

    return wrapper
