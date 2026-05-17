# V41 — Batch 1: Performance + UX Quick Wins

## ما الذي تم تطبيقه

### الأداء (Database)
- **SQLite WAL mode**: تمكين Write-Ahead Logging مما يسمح للقراءة والكتابة بالتوازي. تحسّن سرعة الاستجابة بشكل ملحوظ تحت الحمل.
- **PRAGMA tuning**: `synchronous=NORMAL`, `temp_store=MEMORY`, `mmap_size=128MB`, `cache_size=20MB`.
- **busy_timeout=15s**: لا مزيد من أخطاء "database is locked".
- **فهارس مركّبة جديدة**:
  - `orders(user_id, created_at DESC)` — تسريع صفحة "طلباتي"
  - `orders(status, created_at DESC)` — تسريع لوحة الإدارة
  - `deposits(user_id, created_at DESC)` — تسريع سجل المحفظة
  - `users(email_token)`, `users(reset_token)` — تسريع تفعيل البريد واستعادة كلمة المرور
  - `products(game_key)`, `products(is_active, sort_order)` — تسريع صفحات الألعاب
  - `games(is_active, sort_order)`, `settings(key)`

### التصميم (UI/UX)
- **خط Tajawal** للعربية و **Inter** للاتينية، محمّلة بشكل غير حاجب (`media="print"` trick).
- **Preconnect** إلى Google Fonts لتسريع أول رسم.
- **CSP محدّث** للسماح بـ Google Fonts بأمان.
- **ملف CSS جديد** `static/css/v41-polish.css` يحتوي:
  - Skeleton loaders جاهزة للاستخدام (`.skeleton`, `.skeleton-text`, `.skeleton-card`)
  - Hover lift للبطاقات
  - Focus rings محسّنة على المدخلات
  - Page enter animation خفيفة
  - شريط تمرير مخصّص بألوان العلامة التجارية
  - احترام `prefers-reduced-motion`
- **زر العودة للأعلى** عائم (يظهر بعد 380px scroll).

## ما لم يُلمَس
- منطق الأعمال
- مسارات Flask
- قوالب الصفحات (عدا base.html)
- نظام المصادقة، CSRF، الإيميل

## التالي (Batch 2 المقترح)
- OAuth (Google + Apple) — يحتاج تأكيدك للحصول على client_id/secret
- Email queue async (لتسريع التسجيل)
- PWA service worker (offline + installable)
- Search autocomplete على الألعاب
- Wishlist / Favorites

## ما يبقى لـ Batch 3
- إعادة هيكلة `app.py` إلى Blueprints (admin/, auth/, wallet/, store/)
- استبدال `database.py` بـ SQLAlchemy ORM
- اختبارات pytest
