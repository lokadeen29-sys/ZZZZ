import os
import json
import requests
from dotenv import load_dotenv
from database import init_db, upsert_game, upsert_product, set_setting, delete_products_for_game

load_dotenv()

G2BULK_API_KEY = os.getenv("G2BULK_API_KEY", "")
G2BULK_API_URL = os.getenv("G2BULK_API_URL", "https://api.g2bulk.com/api/v2")

SHOP2TOPUP_API_KEY = os.getenv("SHOP2TOPUP_API_KEY", "")
SHOP2TOPUP_BASE_URL = os.getenv("SHOP2TOPUP_BASE_URL", "https://v2sandbox.shop2topup.com/api/endpoints/v1")
PROFIT_MARGIN = float(os.getenv("PROFIT_MARGIN", "1.20"))


def g2bulk_request(action, params=None):
    payload = {"key": G2BULK_API_KEY, "action": action}
    if params:
        payload.update(params)
    r = requests.post(G2BULK_API_URL, data=payload, timeout=60)
    data = r.json()
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data.get("error"))
    return data


def headers():
    return {"Authorization": f"Bearer {SHOP2TOPUP_API_KEY}", "Content-Type": "application/json"}


def get_json(url):
    r = requests.get(url, headers=headers(), timeout=45)
    data = r.json()
    if r.status_code >= 400:
        raise RuntimeError(data)
    return data


def _safe_float(value):
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def sync_server2():
    # الأرقام المؤكدة:
    # Free Fire  = categoryId 4
    # PUBG Mobile شحن مباشر = categoryId 2
    if not SHOP2TOPUP_API_KEY or SHOP2TOPUP_API_KEY == "PUT_SHOP2TOPUP_KEY_HERE":
        print("SHOP2TOPUP_API_KEY not configured; skipped server2")
        return

    mapping = [
        {"category_id": 4, "game_key": "freefire", "name": "Free Fire", "emoji": "🔥"},
        {"category_id": 2, "game_key": "pubg_mobile", "name": "PUBG Mobile", "emoji": "🔫"},
    ]

    for game in mapping:
        upsert_game("server2", game["game_key"], game["name"], game["emoji"], 1)
        try:
            data = get_json(f"{SHOP2TOPUP_BASE_URL}/catalog/subcategories?categoryId={game['category_id']}")
        except Exception as exc:
            print(f"Failed to sync server2 {game['name']}: {exc}")
            continue

        items = data.get("subcategories") or data.get("data") or []
        print(f"Server 2 / {game['name']}: {len(items)} packages")
        for item in items:
            item_id = item.get("item_id") or item.get("id")
            if not item_id:
                continue
            base_price = _safe_float(item.get("price", 0))
            sell_price = round(base_price * PROFIT_MARGIN, 2)
            upsert_product("server2", game["game_key"], item_id, item.get("name", ""), base_price, sell_price, 1)


def sync_server1_from_api_or_json():
    """
    الأفضل: جلب خدمات G2Bulk من API مباشرة action=services.
    الاحتياط: إذا فشل API، نقرأ g2bulk_services.json.
    """
    services = None

    if G2BULK_API_KEY and G2BULK_API_KEY != "PUT_G2BULK_KEY_HERE":
        try:
            services = g2bulk_request("services")
            print(f"Server 1: fetched {len(services)} services from API")
        except Exception as exc:
            print(f"Server 1 API sync failed: {exc}")

    if services is None:
        path = os.path.join(os.path.dirname(__file__), "g2bulk_services.json")
        if not os.path.exists(path):
            print("g2bulk_services.json not found; skipped server1")
            return
        with open(path, "r", encoding="utf-8") as f:
            services = json.load(f)
        print(f"Server 1: loaded {len(services)} services from local JSON")

    # ألعاب السيرفر 1 الأساسية
    # مهم: Free Fire يجب أن يعرض Middle East فقط، وليس كل مناطق Free Fire.
    featured = [
        {
            "game_key": "freefire",
            "name": "Free Fire",
            "emoji": "🔥",
            "category_exact": ["freefire middle east", "free fire middle east"],
            "name_contains": ["freefire middle east", "free fire middle east"],
        },
        {
            "game_key": "pubg_mobile",
            "name": "PUBG Mobile",
            "emoji": "🔫",
            "category_contains": ["pubg mobile", "pubg"],
        },
        {
            "game_key": "fc_mobile",
            "name": "FC Mobile",
            "emoji": "⚽",
            "category_contains": ["fc mobile", "ea fc mobile"],
        },
    ]

    for game in featured:
        game_key = game["game_key"]
        name = game["name"]
        emoji = game["emoji"]

        upsert_game("server1", game_key, name, emoji, 1)
        # حذف الباقات القديمة حتى لا تبقى مناطق غير مطلوبة بعد تغيير الفلتر
        delete_products_for_game("server1", game_key)

        count = 0
        for s in services:
            s_name = str(s.get("name", ""))
            category = str(s.get("category", ""))
            cat_low = category.lower().strip()
            name_low = s_name.lower().strip()

            matched = False

            # تطابق دقيق للتصنيف، مثل Freefire Middle East فقط
            for p in game.get("category_exact", []):
                if cat_low == p:
                    matched = True

            # تطابق داخل اسم الخدمة كاحتياط
            for p in game.get("name_contains", []):
                if p in name_low:
                    matched = True

            # تطابق عام للألعاب الأخرى مثل PUBG و FC
            for p in game.get("category_contains", []):
                if p in cat_low:
                    matched = True

            if matched:
                service_id = s.get("service")
                if not service_id:
                    continue
                base_price = _safe_float(s.get("rate", 0))
                sell_price = round(base_price * PROFIT_MARGIN, 2)

                # تنظيف الاسم للمستخدم: نحذف اسم المنطقة من بداية الباقة
                display_name = s_name
                if game_key == "freefire":
                    display_name = display_name.replace("Freefire Middle East - ", "")
                    display_name = display_name.replace("Free Fire Middle East - ", "")

                upsert_product("server1", game_key, service_id, display_name, base_price, sell_price, 1)
                count += 1

        print(f"Server 1 / {name}: {count} packages")


def sync_server1_all_categories(limit_per_category=0):
    """
    مزامنة واسعة للمورد 1: ينشئ لعبة لكل category موجودة في services.
    يستخدم category كاسم اللعبة و game_key آمن.
    """
    services = None
    if G2BULK_API_KEY and G2BULK_API_KEY != "PUT_G2BULK_KEY_HERE":
        services = g2bulk_request("services")
    else:
        path = os.path.join(os.path.dirname(__file__), "g2bulk_services.json")
        if not os.path.exists(path):
            print("g2bulk_services.json not found; skipped server1 all")
            return
        with open(path, "r", encoding="utf-8") as f:
            services = json.load(f)

    grouped = {}
    for s in services:
        category = str(s.get("category") or "Other").strip()
        if not category:
            category = "Other"
        grouped.setdefault(category, []).append(s)

    for category, items in grouped.items():
        game_key = "".join(ch.lower() if ch.isalnum() else "_" for ch in category).strip("_")[:60] or "other"
        upsert_game("server1", game_key, category, "🎮", 0)
        count = 0
        for s in items:
            if limit_per_category and count >= limit_per_category:
                break
            service_id = s.get("service")
            if not service_id:
                continue
            base_price = _safe_float(s.get("rate", 0))
            sell_price = round(base_price * PROFIT_MARGIN, 2)
            upsert_product("server1", game_key, service_id, s.get("name", ""), base_price, sell_price, 1)
            count += 1
        print(f"Server 1 / {category}: {count} packages")


def sync_server2_all_known_categories():
    """
    المورد 2 لا توجد لدينا قائمة كل categoryId من الواجهة الحالية،
    لذلك نحفظ المعروف ونترك إضافة المزيد من لوحة الإدارة عند معرفة categoryId.
    """
    sync_server2()


def sync_server2_category(category_id, game_key=None, name=None, emoji="🎮", active=0):
    if not SHOP2TOPUP_API_KEY or SHOP2TOPUP_API_KEY == "PUT_SHOP2TOPUP_KEY_HERE":
        print("SHOP2TOPUP_API_KEY not configured; skipped server2 category")
        return
    game_key = game_key or f"category_{category_id}"
    name = name or f"Shop2Topup Category {category_id}"
    upsert_game("server2", game_key, name, emoji, active)
    data = get_json(f"{SHOP2TOPUP_BASE_URL}/catalog/subcategories?categoryId={int(category_id)}")
    items = data.get("subcategories") or data.get("data") or []
    print(f"Server 2 / {name}: {len(items)} packages")
    for item in items:
        item_id = item.get("item_id") or item.get("id")
        if not item_id:
            continue
        base_price = _safe_float(item.get("price", 0))
        sell_price = round(base_price * PROFIT_MARGIN, 2)
        upsert_product("server2", game_key, item_id, item.get("name", ""), base_price, sell_price, 1)


def sync_server2_range(start=1, end=30):
    """
    يحاول اكتشاف فئات المورد 2 عبر categoryId.
    لا يفعّل الألعاب تلقائيًا؛ تظهر في الإدارة وتختار ما تريد.
    """
    for category_id in range(int(start), int(end) + 1):
        try:
            sync_server2_category(category_id, active=0)
        except Exception as exc:
            print(f"Server 2 category {category_id}: skipped ({exc})")


def _slugify_game_key(name):
    out = []
    for ch in str(name).lower():
        if ch.isalnum():
            out.append(ch)
        else:
            out.append("_")
    key = "".join(out).strip("_")
    while "__" in key:
        key = key.replace("__", "_")
    return key[:70] or "game"


def sync_server2_all_catalog():
    """
    مزامنة كل ألعاب Shop2Topup:
    - يقرأ /catalog/big-categories للألعاب.
    - يقرأ /catalog/categories للفئات/طرق الشحن داخل كل لعبة.
    - يجلب الباقات عبر /catalog/subcategories?categoryId=<category id>.
    - يضيف الألعاب للإدارة كغير مفعلة افتراضيًا، حتى تختار أنت ما يظهر في الواجهة.
    """
    if not SHOP2TOPUP_API_KEY or SHOP2TOPUP_API_KEY == "PUT_SHOP2TOPUP_KEY_HERE":
        print("SHOP2TOPUP_API_KEY not configured; skipped server2 all catalog")
        return

    big = get_json(f"{SHOP2TOPUP_BASE_URL}/catalog/big-categories")
    cats = get_json(f"{SHOP2TOPUP_BASE_URL}/catalog/categories")

    big_items = big.get("data") or big.get("big_categories") or []
    cat_items = cats.get("data") or cats.get("categories") or []

    big_by_id = {}
    for b in big_items:
        bid = b.get("id")
        if bid is not None:
            big_by_id[int(bid)] = b

    cats_by_big = {}
    for c in cat_items:
        bid = c.get("big_category_id")
        if bid is None:
            continue
        cats_by_big.setdefault(int(bid), []).append(c)

    total_games = 0
    total_products = 0

    for big_id, b in big_by_id.items():
        game_name = b.get("name") or f"Game {big_id}"
        game_key = _slugify_game_key(game_name)
        upsert_game("server2", game_key, game_name, "🎮", 0)
        total_games += 1

        categories = cats_by_big.get(big_id, [])
        for c in categories:
            cat_id = c.get("id")
            cat_name = c.get("name") or ""
            if not cat_id:
                continue
            try:
                data = get_json(f"{SHOP2TOPUP_BASE_URL}/catalog/subcategories?categoryId={int(cat_id)}")
            except Exception as exc:
                print(f"Server 2 / {game_name} / category {cat_id}: skipped ({exc})")
                continue

            items = data.get("subcategories") or data.get("data") or []
            if not items:
                continue

            for item in items:
                item_id = item.get("item_id") or item.get("id")
                if not item_id:
                    continue
                base_price = _safe_float(item.get("price", 0))
                sell_price = round(base_price * PROFIT_MARGIN, 2)

                item_name = item.get("name", "")
                display_name = item_name
                if cat_name and cat_name.lower() not in display_name.lower():
                    display_name = f"{cat_name} - {item_name}"

                upsert_product("server2", game_key, item_id, display_name, base_price, sell_price, 1)
                total_products += 1

        print(f"Server 2 / {game_name}: {len(categories)} categories synced")

    print(f"Server 2 all catalog done: {total_games} games, {total_products} products")



if __name__ == "__main__":
    init_db()
    set_setting("profit_margin", PROFIT_MARGIN)
    if os.getenv("SYNC_ALL_G2BULK", "0") == "1":
        sync_server1_all_categories()
    else:
        sync_server1_from_api_or_json()
    if os.getenv("SYNC_ALL_SHOP2TOPUP", "0") == "1":
        sync_server2_all_catalog()
    elif os.getenv("SYNC_SERVER2_RANGE", "0") == "1":
        sync_server2_range(int(os.getenv("SYNC_SERVER2_START", "1")), int(os.getenv("SYNC_SERVER2_END", "30")))
    else:
        sync_server2()
    print("Done.")
