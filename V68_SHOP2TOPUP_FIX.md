# V68 — إصلاح: الطلبات تصل إلى Shop2Topup لكنها تبقى عالقة في "بانتظار التنفيذ"

## السبب الجذري

في `providers.py` الدالة `_extract_order_id_from_response` كانت تبحث عن
`order_id` في حقلين فقط:

```python
response.get("order")        # dict داخلي
response.get("order_id")     # المستوى الأعلى
```

لكن Shop2Topup يُرجع رد النجاح بصيغة:

```json
{ "success": true, "data": { "order_id": "...", "status": "pending" } }
```

النتيجة: الـ `provider_order_id` يُحفظ فارغًا، فيتخطّاه
`refresh_pending_orders` (لأنه يشترط ألا يكون فارغًا)، ويبقى الطلب
عالقًا في `supplier_pending` للأبد.

ظهور المشكلة في اللوج عندك:

```
Order 23: supplier response keys=['success', 'data']
Order 23 queued at supplier (provider id=)         ← فاضي ✗
```

## الإصلاحات في هذا الـ PR

1. **`providers.py::_extract_order_id_from_response`**:
   - يبحث الآن أيضًا داخل `data` و`result`.
   - يدعم `order_id`، `id`، و`orderId`.

2. **`providers.py::_extract_status_from_response` (جديدة)**:
   - تستخرج الحالة من `data.status` كذلك (ليس فقط `order.status`).

3. **`normalize_supplier_create_status` و`normalize_supplier_status`**:
   - تستخدمان الدوال المحسَّنة أعلاه.
   - تعرفان مرادفات `completed` (complete/done/success).

4. **`shop2topup_create_order`**:
   - عند الخطأ يستخرج `error.code` و`error.message` بدل `dict` خام.
   - يترجم الأكواد الشائعة للعربية:
     - `INSUFFICIENT_BALANCE` → "رصيد المورد (Shop2Topup) غير كافٍ..."
     - `INVALID_PLAYER_ID` → "معرّف اللاعب غير صحيح لدى المورد."
     - `INVALID_SUB_CATEGORY` → "الباقة غير متاحة لدى المورد..."
     - `OUT_OF_STOCK` → "الباقة غير متوفرة حاليًا لدى المورد."
     - `RATE_LIMIT` → "تم تجاوز حد الطلبات لدى المورد..."

5. **`shop2topup_get_order_status`**:
   - نفس معالجة الخطأ المحسَّنة.

6. **`scripts/fix_v68_stuck_orders.py` (جديد)**:
   - يطبع الطلبات العالقة الموجودة (server2 + supplier_pending + provider_order_id فارغ).
   - بـ `--apply` يحوّلها إلى `manual_pending` مع ملاحظة واضحة للإدارة.

## التحقق بعد النشر

على السيرفر:

```bash
# 1) سحب التحديث
cd /root/project
git pull origin main          # أو دمج فرع PR

# 2) إعادة تشغيل الخدمات
systemctl restart tecno-worker
# (وأعد تشغيل gunicorn بأي طريقة تستخدمها)

# 3) إصلاح الطلبات العالقة الحالية (dry-run أولًا)
.venv/bin/python scripts/fix_v68_stuck_orders.py
# لو النتائج صحيحة:
.venv/bin/python scripts/fix_v68_stuck_orders.py --apply

# 4) اختبار: اعمل طلب جديد على الموقع
journalctl -u tecno-worker -n 30 --no-pager
# توقّع: provider id=sht-XXXXX (ليس فاضيًا!)
```

## ملاحظة مهمة بخصوص بيئة الـ Sandbox

ملف `.env` لا يزال يستخدم:
```
SHOP2TOPUP_BASE_URL=https://v2sandbox.shop2topup.com/api/endpoints/v1
```

هذه بيئة اختبار — لن ينفّذ شحن حقيقي للعملاء. للإنتاج:
1. اتفق مع Shop2Topup على الـ URL الإنتاجي ومفتاح API الإنتاجي.
2. ضعهما في `.env` ثم `systemctl restart tecno-worker`.
