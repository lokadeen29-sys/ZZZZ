"""V53 REFACTOR (phase 1): Jinja template filters extracted from app.py.

Originally lived at app.py:1334-1492 as ``@app.template_filter`` decorated
functions. This module exposes the bare functions (no Flask coupling) plus
a ``register_filters(app)`` helper that wires them onto a Flask app.

Filters:
- ``public_package_name``  — display-friendly product name (i18n + cleanup).
- ``syria_time``           — Unix-ts → "YYYY-MM-DD HH:MM" in UTC+3.
- ``money``                — display_price_text wrapper.
- ``clean_package_name``   — strip provider/category labels only.
- ``order_status_label``   — Arabic label for an order status code.
- ``order_status_class``   — CSS class for an order status code.

Backwards-compatibility note: the original decorators in ``app.py`` are
preserved as thin wrappers that delegate here, so any direct
``from app import money`` import keeps working during phase 1.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app.services.pricing import display_price_text
from app.utils.i18n import package_public_name


def public_package_name_filter(value):
    return package_public_name(value)


def syria_time(value):
    """تحويل timestamp إلى توقيت سوريا UTC+3."""
    try:
        ts = int(value)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(
            timezone(timedelta(hours=3))
        )
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return value


def money(amount):
    return display_price_text(amount)


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


def register_filters(app) -> None:
    """Wire all filters onto a Flask app's Jinja environment.

    Provided for the eventual ``create_app()`` factory in phase 5. Today
    ``app.py`` keeps its own ``@app.template_filter`` decorators (pointing
    here for the implementation) so this helper is unused — but adding it
    now keeps the future plumbing trivially small.
    """
    app.template_filter("public_package_name")(public_package_name_filter)
    app.template_filter("syria_time")(syria_time)
    app.template_filter("money")(money)
    app.template_filter("clean_package_name")(clean_package_name)
    app.template_filter("order_status_label")(order_status_label)
    app.template_filter("order_status_class")(order_status_class)


__all__ = [
    "public_package_name_filter",
    "syria_time",
    "money",
    "clean_package_name",
    "order_status_label",
    "order_status_class",
    "register_filters",
]
