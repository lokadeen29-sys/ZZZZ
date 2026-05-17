#!/usr/bin/env python3
"""
V68 — إصلاح الطلبات العالقة بسبب فقدان provider_order_id.

السبب الأصلي:
    قبل V68، كان `_extract_order_id_from_response` لا يبحث في `data.order_id`
    (الشكل الفعلي لرد Shop2Topup الناجح). فالطلب يُرسل ويُقبل لدى المورد،
    لكن موقعنا لا يحفظ رقم طلب المورد، فيبقى عالقًا في `supplier_pending`
    إلى الأبد لأن `refresh_pending_orders` يتخطّى الصفوف بدون provider_order_id.

ماذا يفعل هذا السكربت:
    1. يطبع الطلبات العالقة (status=supplier_pending أو processing وprovider_order_id فارغ).
    2. يعرض عليك خيارين:
       (a) DRY-RUN     — يعرض ما سيُفعل دون تغيير الـ DB.
       (b) APPLY       — يحدّث حالة الطلبات العالقة لـ manual_pending مع ملاحظة واضحة
                          بحيث الإدارة تستطيع تنفيذها يدويًا (لأن رقم طلب المورد ضائع
                          ولا يمكن استرجاعه — Shop2Topup لا يدعم البحث بـ uuid عميلنا
                          في كل الحالات).

الاستخدام على السيرفر:
    cd /root/project
    .venv/bin/python scripts/fix_v68_stuck_orders.py            # dry-run
    .venv/bin/python scripts/fix_v68_stuck_orders.py --apply    # نفّذ الإصلاح
"""
import os
import sys
import sqlite3
import time

# نسمح بتشغيل السكربت من أي مسار، طالما متغير DB_PATH أو الافتراضي صحيح.
DB_CANDIDATES = [
    os.environ.get("DB_PATH"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tecnogems.db"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data.db"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app.db"),
    "/root/project/tecnogems.db",
    "/root/project/data.db",
    "/root/project/app.db",
]


def find_db():
    for p in DB_CANDIDATES:
        if p and os.path.isfile(p):
            return os.path.abspath(p)
    print("[ERROR] لم يتم العثور على ملف قاعدة البيانات.")
    print("        مرّر المسار صراحة عبر متغير البيئة DB_PATH:")
    print("            DB_PATH=/root/project/your.db python scripts/fix_v68_stuck_orders.py")
    sys.exit(2)


def main():
    apply_changes = "--apply" in sys.argv
    db = find_db()
    print(f"[INFO] يستخدم قاعدة البيانات: {db}")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT id, order_code, status, provider, provider_order_id,
               COALESCE(note,'') AS note, player_id, price, created_at
        FROM orders
        WHERE provider='server2'
          AND status IN ('supplier_pending','processing')
          AND (provider_order_id IS NULL OR provider_order_id = '')
        ORDER BY id ASC
    """).fetchall()

    if not rows:
        print("[OK] لا توجد طلبات عالقة بهذه الحالة. كل شيء سليم.")
        return

    print(f"[INFO] تم العثور على {len(rows)} طلب(ات) عالق(ة):")
    for r in rows:
        print(f"   - #{r['id']} ({r['order_code']}) | status={r['status']} | "
              f"player={r['player_id']} | price={r['price']} | note={r['note'][:80]}")

    if not apply_changes:
        print()
        print("[DRY-RUN] لم يتم تغيير شيء. لتطبيق الإصلاح، أعد التشغيل مع --apply")
        return

    new_note = ("V68: تم استلام الطلب لدى المورد لكن رقم طلب المورد ضاع بسبب bug. "
                "الرجاء التحقق يدويًا من الكتالوج لدى المورد وتنفيذ الطلب أو رفضه.")
    now = int(time.time())
    conn.executemany(
        "UPDATE orders SET status=?, note=?, updated_at=? WHERE id=?",
        [("manual_pending", new_note, now, r["id"]) for r in rows]
    )
    conn.commit()
    print(f"[DONE] تم تحديث {len(rows)} طلب(ات) إلى manual_pending مع ملاحظة واضحة للإدارة.")


if __name__ == "__main__":
    main()
