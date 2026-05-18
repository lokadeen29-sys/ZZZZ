"""V53 REFACTOR (phase 5): password + redirect security helpers.

Two pure utilities lifted from app.py:

* :func:`validate_password_strength` — PATCH-M4 complexity check.
* :func:`safe_next_url` — V50 SECURITY HF open-redirect defence.

Both are import-cheap and Flask-context aware (they read
``flask.request``) but otherwise stateless. Tests that imported them
via ``from app import validate_password_strength`` keep working through
the re-export in :mod:`app.__init__`.
"""
from __future__ import annotations

import re

from flask import request, url_for


# ---------------------------------------------------------------------------
# validate_password_strength
# ---------------------------------------------------------------------------
def validate_password_strength(password: str | None) -> tuple[bool, str | None]:
    """Return (ok, error) for a candidate password.

    PATCH-M4 enforces 8+ characters and at least 2 of: lowercase,
    uppercase, digit, symbol. The Arabic error strings match the
    legacy implementation byte-for-byte so unit tests pass unchanged.
    """
    password = password or ""
    if len(password) < 8:
        return False, "كلمة المرور يجب أن تكون 8 أحرف على الأقل"
    classes = sum(
        [
            bool(re.search(r"[a-z]", password)),
            bool(re.search(r"[A-Z]", password)),
            bool(re.search(r"\d", password)),
            bool(re.search(r"[^A-Za-z0-9]", password)),
        ]
    )
    if classes < 2:
        return False, (
            "كلمة المرور ضعيفة. استخدم مزيجاً من الأحرف والأرقام (أو رموزاً)"
        )
    return True, None


# ---------------------------------------------------------------------------
# safe_next_url — open-redirect protection
# ---------------------------------------------------------------------------
def safe_next_url(default_endpoint: str = "public.home", **url_for_kwargs) -> str:
    """Return a sanitised value for the ``?next=`` query / form parameter.

    PATCH-B4: accepts kwargs forwarded to ``url_for()`` so callers like
    ``safe_next_url("public.products", provider=p, game_key=k)`` no
    longer crash with TypeError.

    V50 SECURITY (HF): hardened against open-redirect variants:
      * reject backslashes (``\\evil.com``), null bytes, control chars
      * reject any ``:`` (blocks ``javascript:``, ``http://``, …)
      * reject ``/%2f%2f...`` (encoded protocol-relative)
      * cap length to avoid log/memory pollution
    """
    nxt = request.args.get("next") or request.form.get("next") or ""
    if not nxt or len(nxt) > 512:
        return url_for(default_endpoint, **url_for_kwargs)
    bad_chars = ("\\", "\x00", "\r", "\n", "\t", " ")
    if any(c in nxt for c in bad_chars) or ":" in nxt:
        return url_for(default_endpoint, **url_for_kwargs)
    low = nxt.lower()
    if low.startswith("//") or low.startswith("/%2f") or low.startswith("/\\"):
        return url_for(default_endpoint, **url_for_kwargs)
    if nxt.startswith("/") and not nxt.startswith("/legacy"):
        return nxt
    return url_for(default_endpoint, **url_for_kwargs)
