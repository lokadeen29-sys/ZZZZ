from __future__ import annotations

import os
from dotenv import load_dotenv
load_dotenv()

import uuid
import requests
from typing import Dict, Any

G2BULK_API_KEY = os.getenv("G2BULK_API_KEY", "").strip()
G2BULK_API_URL = os.getenv("G2BULK_API_URL", "https://api.g2bulk.com/api/v2")

# V70: ترجمة عربية لرسائل أخطاء G2Bulk الشائعة لتظهر مفهومة للإدارة بدل
# الإنجليزية الخام. الـ API لا يرجع كود ثابت، بل نص حر — لذا نطابق
# substring بحساسية حالة منخفضة. أي خطأ غير مطابق يبقى كما ورد من المورد.
_G2BULK_AR_TRANSLATIONS = [
    ("not enough funds",      "رصيد المورد (G2Bulk) غير كافٍ. يرجى شحن الحساب."),
    ("insufficient",          "رصيد المورد (G2Bulk) غير كافٍ. يرجى شحن الحساب."),
    ("incorrect link",        "معرّف اللاعب غير صحيح لدى المورد."),
    ("invalid link",          "معرّف اللاعب غير صحيح لدى المورد."),
    ("invalid service",       "الباقة غير متاحة لدى المورد. يلزم إعادة مزامنة الكتالوج."),
    ("service is disabled",   "الباقة معطّلة حاليًا لدى المورد."),
    ("service disabled",      "الباقة معطّلة حاليًا لدى المورد."),
    ("not active",            "الباقة غير متاحة حاليًا لدى المورد."),
    ("out of stock",          "الباقة غير متوفرة حاليًا لدى المورد."),
    ("rate limit",            "تم تجاوز حد الطلبات لدى المورد، أعد المحاولة بعد قليل."),
    ("too many requests",     "تم تجاوز حد الطلبات لدى المورد، أعد المحاولة بعد قليل."),
    ("incorrect quantity",    "الكمية المطلوبة غير صحيحة لدى المورد."),
    ("invalid api key",       "مفتاح API الخاص بـ G2Bulk غير صحيح."),
    ("invalid key",           "مفتاح API الخاص بـ G2Bulk غير صحيح."),
    ("incorrect api key",     "مفتاح API الخاص بـ G2Bulk غير صحيح."),
]


def _translate_g2bulk_error(message: str) -> str:
    if not message:
        return message
    low = str(message).lower()
    for needle, ar in _G2BULK_AR_TRANSLATIONS:
        if needle in low:
            return ar
    return message

SHOP2TOPUP_API_KEY = os.getenv("SHOP2TOPUP_API_KEY", "").strip()
if SHOP2TOPUP_API_KEY.lower().startswith("bearer "):
    SHOP2TOPUP_API_KEY = SHOP2TOPUP_API_KEY.split(" ", 1)[1].strip()
SHOP2TOPUP_BASE_URL = os.getenv("SHOP2TOPUP_BASE_URL", "https://v2sandbox.shop2topup.com/api/endpoints/v1")


def g2bulk_request(action: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    G2Bulk يعمل بصيغة POST + x-www-form-urlencoded.
    لا تستخدم json=payload هنا؛ لأن إنشاء الطلبات يفشل عند بعض الأكشنات.
    """
    payload = {"key": G2BULK_API_KEY, "action": action}
    if params:
        payload.update(params)
    try:
        r = requests.post(G2BULK_API_URL, data=payload, timeout=45)
        try:
            data = r.json()
        except Exception:
            # PATCH-L6: do not echo raw response (may contain reflected key)
            return {"error": f"Non-JSON response (HTTP {r.status_code})"}

        # توحيد شكل الأخطاء
        if isinstance(data, dict) and data.get("error"):
            # V70: ترجم رسالة الخطأ للعربية إذا كانت ضمن القائمة المعروفة.
            return {"error": _translate_g2bulk_error(data.get("error")), "raw": data}
        return data
    except Exception as exc:
        return {"error": str(exc)}


def g2bulk_services() -> Any:
    return g2bulk_request("services")


def g2bulk_balance() -> Dict[str, Any]:
    # نجرب أكثر من action لأن بعض نسخ API تستخدم user بدلاً من balance
    attempts = ["balance", "user", "profile"]
    last = None
    for action in attempts:
        res = g2bulk_request(action)
        last = res
        if isinstance(res, dict) and not res.get("error"):
            balance = (
                res.get("balance")
                or res.get("funds")
                or res.get("money")
                or (res.get("data", {}) if isinstance(res.get("data"), dict) else {}).get("balance")
            )
            if balance is not None:
                return {"balance": balance, "currency": "USD", "raw": res}
            return {"raw": res, "message": "تم الاتصال بالمورد لكن لم يتم العثور على حقل balance واضح"}
    return {"error": f"فشل الاستعلام عن رصيد المورد 1. آخر رد: {last}"}


def g2bulk_create_order(service_id: str, player_id: str, quantity: int = 1) -> Dict[str, Any]:
    # الحقول الصحيحة التي اختبرتها في Postman:
    # key / action=add / service / link / quantity
    return g2bulk_request("add", {
        "service": str(service_id),
        "link": str(player_id),
        "quantity": int(quantity)
    })


def shop2topup_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {SHOP2TOPUP_API_KEY}",
        "Content-Type": "application/json",
    }


def shop2topup_balance() -> Dict[str, Any]:
    # نجرب نقاط شائعة لأن اسم endpoint قد يختلف بين sandbox والحقيقي
    endpoints = ["/account", "/wallet", "/balance", "/user"]
    last_error = None
    for ep in endpoints:
        try:
            r = requests.get(f"{SHOP2TOPUP_BASE_URL}{ep}", headers=shop2topup_headers(), timeout=30)
            try:
                data = r.json()
            except Exception:
                data = {"text": r.text[:300]}

            if r.status_code >= 400:
                last_error = f"{ep}: HTTP {r.status_code} {data}"
                continue

            if isinstance(data, dict):
                account = data.get("account") if isinstance(data.get("account"), dict) else {}
                wallet = (
                    account.get("wallet")
                    or account.get("balance")
                    or data.get("balance")
                    or data.get("wallet")
                    or (data.get("data", {}) if isinstance(data.get("data"), dict) else {}).get("balance")
                )
                if wallet is not None:
                    return {"balance": wallet, "currency": data.get("currency", "USD"), "raw": data}
                return {"raw": data, "message": "تم الاتصال بالمورد لكن لم يتم العثور على حقل balance واضح"}
        except Exception as exc:
            last_error = f"{ep}: {exc}"
    return {"error": f"فشل الاستعلام عن رصيد المورد 2. آخر خطأ: {last_error}"}


def shop2topup_create_order(item_id: str, player_id: str, quantity: int = 1, expected_unit_price=None) -> Dict[str, Any]:
    payload = {
        "order_id": str(uuid.uuid4()),
        "sub_category_id": int(item_id),
        "quantity": int(quantity),
        "requirements": {"player_id": str(player_id)},
    }
    if expected_unit_price is not None:
        payload["expected_unit_price"] = str(expected_unit_price)
    try:
        r = requests.post(f"{SHOP2TOPUP_BASE_URL}/orders/create", headers=shop2topup_headers(), json=payload, timeout=45)
        data = r.json()
        if isinstance(data, dict) and data.get("success") is False:
            # V68 FIX: shop2topup يرجع الخطأ بصيغة:
            #   {"success": false, "error": {"code": "...", "message": "..."}}
            # أو أحيانًا message مباشرة. نستخرج رسالة بشرية واضحة بدل dict خام،
            # مع ترجمة الأخطاء الشائعة للعربية لتظهر مفهومة في لوحة الإدارة.
            err_obj = data.get("error")
            if isinstance(err_obj, dict):
                code = str(err_obj.get("code") or "").upper()
                message = err_obj.get("message") or ""
                _ar_translations = {
                    "INSUFFICIENT_BALANCE": "رصيد المورد (Shop2Topup) غير كافٍ. يرجى شحن الحساب.",
                    "INVALID_PLAYER_ID": "معرّف اللاعب غير صحيح لدى المورد.",
                    "INVALID_SUB_CATEGORY": "الباقة غير متاحة لدى المورد. يلزم إعادة مزامنة الكتالوج.",
                    "OUT_OF_STOCK": "الباقة غير متوفرة حاليًا لدى المورد.",
                    "RATE_LIMIT": "تم تجاوز حد الطلبات لدى المورد، أعد المحاولة بعد قليل.",
                }
                pretty = _ar_translations.get(code) or message or code or "خطأ من المورد"
            else:
                pretty = data.get("message") or err_obj or "خطأ من المورد"
            return {"error": pretty, "raw": data}
        return data
    except Exception as exc:
        return {"error": str(exc)}


def create_provider_order(provider: str, product_id: str, player_id: str, quantity: int = 1) -> Dict[str, Any]:
    if provider == "server1":
        return g2bulk_create_order(product_id, player_id, quantity)
    if provider == "server2":
        return shop2topup_create_order(product_id, player_id, quantity)
    return {"error": "Unknown provider"}


def get_provider_balance(provider: str) -> Dict[str, Any]:
    if provider == "server1":
        return g2bulk_balance()
    if provider == "server2":
        return shop2topup_balance()
    return {"error": "Unknown provider"}


# --- Player Validation ---

def shop2topup_validate_player(item_id: str, player_id: str) -> Dict[str, Any]:
    """
    التحقق من اسم اللاعب عبر Shop2Topup.
    """
    payload = {
        "sub_category_id": int(item_id),
        "player_id": str(player_id)
    }
    try:
        r = requests.post(
            f"{SHOP2TOPUP_BASE_URL}/player/validate",
            headers=shop2topup_headers(),
            json=payload,
            timeout=30
        )
        data = r.json()
        if data.get("success"):
            player = data.get("player", {}) or {}
            # بعض الردود لا ترجع player_name وتكتفي بنجاح التحقق
            player_name = (
                player.get("player_name")
                or player.get("name")
                or player.get("username")
                or player.get("nickname")
                or data.get("player_name")
                or data.get("name")
                or ""
            )
            return {
                "success": True,
                "player_name": player_name,
                "verified_only": False if player_name else True,
                "raw": data
            }
        return {"success": False, "error": data.get("message") or data.get("error") or "تعذر التحقق من اللاعب", "raw": data}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def g2bulk_validate_player(service_id: str, player_id: str) -> Dict[str, Any]:
    """
    بعض حسابات G2Bulk تدعم تحقق اللاعب بأكشنات مختلفة.
    لذلك نجرب أكثر من action، وإذا لم يدعم المزود التحقق نرجع رسالة واضحة ولا نمنع الشراء.
    """
    attempts = [
        ("validate", {"service": service_id, "link": player_id}),
        ("check", {"service": service_id, "link": player_id}),
        ("getUser", {"service": service_id, "link": player_id}),
        ("username", {"service": service_id, "link": player_id}),
    ]

    last_response = None
    for action, params in attempts:
        res = g2bulk_request(action, params)
        last_response = res

        if not isinstance(res, dict):
            continue

        if res.get("error"):
            # إذا الأكشن غير مدعوم نكمل تجربة الأكشن التالي
            continue

        # أسماء محتملة ترجعها APIs مختلفة
        name = (
            res.get("username")
            or res.get("player_name")
            or res.get("name")
            or res.get("nickname")
            or (res.get("user") if isinstance(res.get("user"), str) else None)
        )
        if name:
            return {"success": True, "player_name": name, "raw": res}

        # أحياناً يرجع nested player
        player = res.get("player")
        if isinstance(player, dict):
            name = player.get("player_name") or player.get("name") or player.get("username") or player.get("nickname")
            if name:
                return {"success": True, "player_name": name, "raw": res}

    return {
        "success": False,
        "unsupported": True,
        "error": "السيرفر 1 لم يرجع اسم اللاعب لهذه الباقة. هذا لا يعني أن ID خطأ، يمكنك المتابعة إذا كان ID صحيحًا",
        "raw": last_response
    }


def validate_player_provider(provider: str, product_id: str, player_id: str) -> Dict[str, Any]:
    if provider == "server1":
        return g2bulk_validate_player(product_id, player_id)
    if provider == "server2":
        return shop2topup_validate_player(product_id, player_id)
    return {"success": False, "error": "Unknown provider"}


# --- Supplier Order Status ---

def _extract_order_id_from_response(provider: str, response: Dict[str, Any]) -> str:
    """
    استخراج رقم طلب المورد من الرد بأكثر من شكل محتمل.

    V68 FIX: Shop2Topup يرجع الرد بصيغة:
        {"success": true, "data": {"order_id": "...", "status": "..."}}
    أو أحيانًا:
        {"success": true, "data": {"id": "..."}}
        {"success": true, "order": {...}}  (شكل قديم)
    سابقًا كنا نبحث في `response["order"]` و`response["order_id"]` فقط،
    فيُفقد رقم الطلب لأنه داخل `data` — وآلية المتابعة `refresh_pending_orders`
    تتخطّاه لاحقًا لأن `provider_order_id` فارغ، فيبقى الطلب عالقًا.
    """
    if not isinstance(response, dict):
        return ""

    if provider == "server1":
        # G2Bulk: قد يأتي إما في المستوى الأعلى أو داخل data.
        oid = response.get("order") or response.get("order_id") or response.get("id")
        if not oid and isinstance(response.get("data"), dict):
            d = response["data"]
            oid = d.get("order") or d.get("order_id") or d.get("id")
        return str(oid or "")

    # server2 = Shop2Topup
    # نجرب كل الأشكال المعروفة بالترتيب الأكثر شيوعًا.
    for container_key in ("data", "order", "result"):
        container = response.get(container_key)
        if isinstance(container, dict):
            oid = container.get("order_id") or container.get("id") or container.get("orderId")
            if oid:
                return str(oid)
    return str(response.get("order_id") or response.get("id") or response.get("orderId") or "")


def _extract_status_from_response(provider: str, response: Dict[str, Any]) -> str:
    """
    استخراج حقل status من الرد بأكثر من شكل محتمل.
    V68 FIX: Shop2Topup يضع الحالة داخل data.status، وكنا نقرأ من response.status فقط.
    """
    if not isinstance(response, dict):
        return ""
    for container_key in ("data", "order", "result"):
        container = response.get(container_key)
        if isinstance(container, dict):
            s = container.get("status")
            if s:
                return str(s).lower()
    return str(response.get("status") or "").lower()


def normalize_supplier_create_status(provider: str, response: Dict[str, Any]) -> Dict[str, Any]:
    """
    عند إنشاء الطلب:
    - لا نعتبر الطلب مكتملًا إلا إذا كان status=completed صراحة.
    - إذا status=pending/processing أو الرد ناجح دون completed، نتركه قيد التنفيذ لدى المورد.
    """
    if not isinstance(response, dict):
        return {"ok": False, "status": "manual_pending", "error": "Invalid supplier response", "provider_order_id": ""}

    if response.get("error") or response.get("success") is False:
        return {
            "ok": False,
            "status": "manual_pending",
            "error": response.get("error") or response.get("message") or str(response),
            "provider_order_id": _extract_order_id_from_response(provider, response)
        }

    provider_order_id = _extract_order_id_from_response(provider, response)
    status = _extract_status_from_response(provider, response)

    # توحيد المرادفات: completed/complete/done/success كلها مكتمل.
    if status in ("completed", "complete", "done", "success"):
        return {"ok": True, "status": "completed", "error": "", "provider_order_id": provider_order_id}

    # أي حالة أخرى (pending/processing/queued/فارغ) = مقبول لدى المورد بانتظار التنفيذ.
    return {"ok": True, "status": "supplier_pending", "error": status or "Order accepted by supplier", "provider_order_id": provider_order_id}


def g2bulk_get_order_status(order_id: str) -> Dict[str, Any]:
    # أكشن status شائع في APIs المشابهة لـ G2Bulk/SMM
    res = g2bulk_request("status", {"order": str(order_id)})
    if isinstance(res, dict) and res.get("error"):
        return {"error": res.get("error"), "raw": res}
    return res if isinstance(res, dict) else {"error": "Invalid status response", "raw": res}


def shop2topup_get_order_status(order_id: str) -> Dict[str, Any]:
    try:
        r = requests.get(f"{SHOP2TOPUP_BASE_URL}/orders/{order_id}", headers=shop2topup_headers(), timeout=30)
        data = r.json()
        if isinstance(data, dict) and data.get("success") is False:
            # V68 FIX: نفس منطق rsponse الخطأ في shop2topup_create_order.
            err_obj = data.get("error")
            if isinstance(err_obj, dict):
                pretty = err_obj.get("message") or err_obj.get("code") or "خطأ من المورد"
            else:
                pretty = data.get("message") or err_obj or "خطأ من المورد"
            return {"error": pretty, "raw": data}
        return data
    except Exception as exc:
        return {"error": str(exc)}


def get_provider_order_status(provider: str, provider_order_id: str) -> Dict[str, Any]:
    if provider == "server1":
        return g2bulk_get_order_status(provider_order_id)
    if provider == "server2":
        return shop2topup_get_order_status(provider_order_id)
    return {"error": "Unknown provider"}


def normalize_supplier_status(provider: str, response: Dict[str, Any]) -> Dict[str, Any]:
    """
    تحويل رد المورد إلى حالة داخلية موحدة.
    V68 FIX: يقرأ status من data.status أيضًا (شكل Shop2Topup الحالي).
    """
    if not isinstance(response, dict):
        return {"status": "supplier_pending", "note": "Invalid status response"}

    if response.get("error"):
        return {"status": "supplier_pending", "note": response.get("error")}

    # يدعم: response.status / response.order.status / response.data.status / response.result.status
    status = _extract_status_from_response(provider, response)

    # G2Bulk/SMM غالباً: Pending, In progress, Completed, Canceled, Partial
    s = (status or "").replace("_", " ").strip().lower()

    if s in ("completed", "complete", "done", "success"):
        return {"status": "completed", "note": "Supplier completed"}
    if s in ("failed", "fail", "canceled", "cancelled", "refunded", "rejected"):
        return {"status": "manual_pending", "note": f"Supplier status: {status}"}
    if s in ("partial",):
        return {"status": "manual_pending", "note": "Supplier status: partial"}

    return {"status": "supplier_pending", "note": f"Supplier status: {status or 'pending'}"}
