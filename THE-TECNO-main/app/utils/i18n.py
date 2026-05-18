"""V53 REFACTOR (phase 1): public-facing i18n helpers extracted from app.py.

Originally lived at app.py:487-593. The module exposes:

- ``PUBLIC_TRANSLATIONS`` — flat ar/en dictionary used by base templates.
- ``current_lang()``      — "en" iff session opted in; "ar" otherwise.
- ``tr(key)``             — lookup with Arabic fallback.
- ``lang_url(target)``    — URL that switches language preserving location.
- ``package_public_name(name)`` — friendly product-name translation that
  also strips supplier/category labels (e.g. "MENA Direct Topup").

Note: ``package_public_name`` is kept here (rather than in services/pricing)
because it is a *display* helper on top of ``current_lang()``. It does
import ``translate_product_name`` from ``database`` to fall back to the
DB-backed dictionary on unknown phrases.
"""
from __future__ import annotations

import re

from flask import request, session, url_for

from database import translate_product_name

# ---------------------------------------------------------------------------
# Public language system (Arabic default / English optional)
# ---------------------------------------------------------------------------
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
    """Arabic is the default. English only when explicitly selected."""
    return (
        "en"
        if session.get("lang") == "en" and session.get("lang_user_selected") == "1"
        else "ar"
    )


def tr(key):
    return PUBLIC_TRANSLATIONS.get(key, {}).get(
        current_lang(),
        PUBLIC_TRANSLATIONS.get(key, {}).get("ar", key),
    )


def lang_url(target):
    nxt = request.path
    if nxt.startswith("/lang/"):
        nxt = url_for("public.home")
    if request.query_string and not nxt.startswith("/lang/"):
        nxt = request.full_path
    return url_for("public.set_language", lang=target, next=nxt)


def package_public_name(name):
    """Friendly product name: strips supplier labels and translates common
    English/Arabic terms (Diamonds <-> جواهر, UC <-> شدات, ...).

    Falls back to ``database.translate_product_name`` for the Arabic-side
    DB-backed dictionary.
    """
    name = str(name or "")
    # Remove supplier/category labels in both languages.
    remove_terms = [
        "MENA Direct Topup",
        "Mena Direct Topup",
        "Direct Topup",
        "direct topup",
        "شحن مباشر",
    ]
    for old in remove_terms:
        name = name.replace(old, "")

    if current_lang() == "en":
        replacements = [
            ("جواهر", "Diamonds"), ("جوهرة", "Diamond"),
            ("شدات ببجي", "PUBG UC"), ("شدات", "UC"),
            ("بطاقات", "Cards"), ("بطاقة", "Card"),
            ("عملات", "Coins"), ("عملة", "Coin"),
            ("قسائم", "Vouchers"), ("قسيمة", "Voucher"),
            ("نقاط", "Points"), ("نقطة", "Point"),
        ]
        for old, new in replacements:
            name = name.replace(old, new)
        return re.sub(r"\s+", " ", name).strip(" -–—|")

    # Arabic display: translate provider/product English words even if
    # stored in DB in English.
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


__all__ = [
    "PUBLIC_TRANSLATIONS",
    "current_lang",
    "tr",
    "lang_url",
    "package_public_name",
]
