"""V53 REFACTOR (phase 1): pricing helpers extracted from app.py.

Originally lived at app.py:538-728 (mixed with the i18n helpers). The
module exposes:

Core (settings-driven):
- ``get_pricing_mode()``        — "usd" | "auto_syp"
- ``get_display_currency()``    — "USD" | "SYP"
- ``manual_price_edit_enabled()``
- ``get_usd_syp_rate()``
- ``display_price_value(usd_amount, currency=None)``
- ``display_price_text(usd_amount, currency=None)``
- ``product_manual_syp(product)``
- ``manual_syp_override_active(product)``
- ``product_sell_usd(product, game=None)``
- ``product_display_price(product, game=None)``
- ``product_profit_percent(product, game=None)``
- ``wallet_money_text(amount)``

Public-facing wrappers (language-aware, used by templates):
- ``public_price_text(usd_amount)``
- ``product_public_price(product, game=None)``

Dependencies:
- ``utils.settings_cache.get_setting`` — reads pricing toggles.
- ``utils.i18n.current_lang``         — switches USD-vs-localised display.
"""
from __future__ import annotations

from app.utils.i18n import current_lang
from app.utils.settings_cache import get_setting


def get_pricing_mode():
    """Base pricing mode: 'usd' or 'auto_syp'."""
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


# ---------------------------------------------------------------------------
# Public-facing wrappers
# ---------------------------------------------------------------------------
def public_price_text(usd_amount):
    """Same as ``display_price_text`` but always English when current_lang is en."""
    try:
        amount = float(usd_amount or 0)
    except Exception:
        amount = 0.0
    if current_lang() == "en":
        return f"${amount:.2f}"
    return wallet_money_text(amount)


def product_public_price(product, game=None):
    return product_display_price(product, game)


__all__ = [
    "get_pricing_mode",
    "get_display_currency",
    "manual_price_edit_enabled",
    "get_usd_syp_rate",
    "display_price_value",
    "display_price_text",
    "product_manual_syp",
    "manual_syp_override_active",
    "product_sell_usd",
    "product_display_price",
    "product_profit_percent",
    "wallet_money_text",
    "public_price_text",
    "product_public_price",
]
