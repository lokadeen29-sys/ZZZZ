# TecnoGems V47 — PATCH (Critical + High fixes only)

تم تطبيق إصلاحات جراحية على V47 لمعالجة المشاكل الحرجة والعالية الخطورة
المرصودة في تقرير التحليل، **بدون أي تغييرات تجميلية أو إعادة هيكلة**.

## الإصلاحات المُطبّقة

### A — حرج (يمنع إقلاع التطبيق)

#### PATCH-A1: NameError on `Queue` at module load
- **الملف:** `app.py` السطور 5
- **المشكلة:** `email_queue = Queue()` كان يُستدعى قبل استيراد `Queue`.
- **الحل:** إضافة `from queue import Queue` في أعلى الملف.
- **النتيجة:** التطبيق يقلع نظيفاً (تم التحقق فعلياً عبر `import app`).

---

### B — عالية الخطورة

#### PATCH-B2/B7: Race condition + 500 error in `/api/orders`
- **الملف:** `app.py` (route `api_orders`)
- **المشكلة:** التحقق من الرصيد في Python ثم استدعاء `create_order`
  يسمح بسباق طلبين متزامنين وحسم الرصيد مرتين. أيضاً
  `InsufficientBalance` غير ملتقطة → HTTP 500.
- **الحل:** إزالة الفحص الـ Python، الاعتماد على `BEGIN IMMEDIATE`
  داخل `create_order`، ولفّ الاستدعاء بـ `try/except InsufficientBalance`
  → عميل API يحصل على HTTP 400 برسالة واضحة.

#### PATCH-B4: `safe_next_url` TypeError on kwargs
- **الملف:** `app.py` (تعريف `safe_next_url`)
- **المشكلة:** الدالة كانت تقبل وسيطاً واحداً فقط، لكن تُستدعى
  بـ `safe_next_url("products", provider=p, game_key=k)` → TypeError.
- **الحل:** قبول `**url_for_kwargs` وتمريرها لـ `url_for`.

#### PATCH-B5: Circular import in `routes/lang_bp.py`
- **الملف:** `routes/lang_bp.py`
- **المشكلة:** `from app import safe_next_url` على مستوى الموديول
  أثناء تحميل `app.py` نفسه → `Blueprint registration failed`.
- **الحل:** نقل الاستيراد داخل دالة `set_language` (lazy import).

#### PATCH-B6: لا rate limit على `/api/login`, `/api/register`, `/api/orders`
- **الملف:** `app.py`
- **المشكلة:** نقاط API كانت مكشوفة للـ brute force والإغراق.
- **الحل:** إضافة `limiter.limit(...)`:
  - `/api/login`: 10/دقيقة
  - `/api/register`: 8/دقيقة
  - `/api/orders`: 20/دقيقة
- **التحقق:** burst test أعطى `[401×9, 429×4]` ✓

#### PATCH-B8: `ensure_indexes()` لا تعمل في الإنتاج
- **الملف:** `app.py` (`setup_once`)
- **المشكلة:** الفهارس كانت تُنشأ فقط في وضع `__main__`،
  ولم تُستدعى من `wsgi.py` ولا `setup_once`.
- **الحل:** إضافة استدعاء `ensure_indexes()` بعد `init_db()` في `setup_once`.

---

## المشاكل التي **لم** تُعالج (متروكة عمداً)

تركت لأنها تتطلب قرارات معمارية أو ليست حرجة للإنتاج الحالي:

| البند | السبب |
|---|---|
| فقدان طلبات الذاكرة عند restart | يحتاج Redis (موصى به) |
| CSRF على JSON API | يحتاج قرار: Bearer tokens أم X-CSRFToken |
| CSP `'unsafe-inline'` | يحتاج تنظيف كل scripts inline في القوالب |
| توحيد i18n | refactor كبير |
| Postgres بدلاً من SQLite | قرار بنية تحتية |
| OAuth password_hash عشوائي | تعديل سكيمة قاعدة البيانات |

---

## التحقق

```bash
# اختبار الإقلاع
python3 -c "import app; print('OK')"
# → OK (بدون NameError ولا تحذير circular import)

# اختبار rate limit
# burst 13 طلب على /api/login → [401×9, 429×4] ✓

# اختبار safe_next_url
safe_next_url('products', provider='server1', game_key='pubg_mobile')
# → /legacy/products/server1/pubg_mobile ✓
```

## كيفية النشر

```bash
cp .env.example .env
# عدّل .env (SECRET_KEY, ADMIN_PASSWORD, REDIS_URL للإنتاج)

pip install -r requirements.txt

# DEV:
python app.py

# PROD:
gunicorn wsgi:application -w 1 -b 0.0.0.0:5000 --timeout 60
# لمعالجة طلبات دائمة، شغّل أيضاً:
python worker_rq.py
```
