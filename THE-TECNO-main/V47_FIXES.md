# V47 — إصلاحات الأمان والأداء

## الإصلاحات المُطبَّقة

---

### 1. Race Condition في الدفع — BUG مالي حرج ✅
**الملفات:** `database.py`، `app.py`

**المشكلة:** كان `checkout()` يتحقق من الرصيد في Python ثم يمرر لـ `create_order()` التي تحسمه في SQLite. بين الخطوتين، يمكن لطلبين متزامنين أن يجتازا التحقق ويحسما الرصيد مرتين → رصيد سالب.

**الحل:**
- أُضيفت exception جديدة `InsufficientBalance` في `database.py`.
- `create_order()` الآن تفتح `BEGIN IMMEDIATE` transaction (تقفل الكتابة فوراً) وتنفذ:
  ```sql
  UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ?
  ```
  إذا كان `rowcount == 0` → الرصيد غير كافٍ → rollback + `InsufficientBalance`.
- `checkout()` في `app.py` أُزيل منه التحقق المزدوج وأصبح يعتمد على `InsufficientBalance`.
- جميع الطرق ملفوفة بـ `try/except/finally` لضمان إغلاق الاتصال حتى عند الخطأ.

---

### 2. كلمة مرور الأدمن الافتراضية الضعيفة ✅
**الملفات:** `.env.example`، `app.py`

**المشكلة:** `.env.example` يحتوي `ADMIN_PASSWORD=admin123456` — خطر حقيقي إذا نُسخ مباشرة للإنتاج.

**الحل:**
- `.env.example`: غُيّرت لـ `ADMIN_PASSWORD=<CHANGE-THIS-STRONG-PASSWORD>`.
- `setup_once()` في `app.py`: يتحقق من قوة كلمة المرور عند الاقلاع:
  - في `production`: يرفع `RuntimeError` إذا كانت ضعيفة أو افتراضية.
  - في `development`: يسجّل تحذيراً واضحاً في اللوق.

---

### 3. Connection Leaks في قاعدة البيانات ✅
**الملف:** `database.py`

**المشكلة:** الدوال المالية (`change_balance`، `update_order`) تغلق `conn` يدوياً — أي exception في المنتصف يتسبب في تسريب الاتصال.

**الحل:** أُضيف `try/finally: conn.close()` لكلتا الدالتين.

---

### 4. الشريط العلوي لا يثبت عند التمرير ✅
**الملف:** `static/css/tecnogems.unified.css`

**المشكلة:** قاعدة CSS قديمة من "V8 final navbar fix":
```css
.tg-nav { position: relative; z-index: 20 }
```
كانت تُلغي `position: sticky` المُعرَّفة لاحقاً في V43 وV44، فيتحرك الشريط مع الصفحة.

**الحل:**
- حُذفت `position: relative` من قاعدة V8 القديمة.
- أُضيف في الـ override الأخير (V44-neon section):
  ```css
  .tg-nav, .v43-nav {
    position: sticky !important;
    top: 0 !important;
    z-index: 50 !important;
  }
  ```
- نُسخ الملف إلى `tecnogems.min.css` (هو الملف المُحمَّل فعلياً).

---

### 5. تحذير واضح عند التشغيل بدون Redis ✅
**الملف:** `app.py`

**المشكلة:** عند تشغيل الموقع بدون `REDIS_URL`، الطلبات تُحفظ في queue بالذاكرة وتُفقد عند إعادة تشغيل العملية — بدون أي تحذير في اللوق.

**الحل:** أُضيف تحذير واضح في اللوق عند بدء التشغيل:
```
⚠️  REDIS_URL is not set. Orders are queued in-memory and will be LOST if the process restarts.
```

---

### 6. توثيق lang_bp Blueprint بوضوح ✅
**الملف:** `routes/__init__.py`

**المشكلة:** الـ blueprint معطّل بـ comment بدون شرح واضح للسبب أو خطوات التفعيل.

**الحل:** أُضيف تعليق مفصّل يشرح:
- لماذا lang_bp لا يُفعَّل الآن (تعارض مع legacy routes في app.py).
- ما الخطوات الدقيقة لتفعيله في المرحلة القادمة.

---

## ما تبقّى (مستقبلي)

| البند | السبب |
|-------|--------|
| SQLite → PostgreSQL | قرار بنية تحتية، يحتاج migration script كامل |
| تقسيم app.py إلى blueprints كاملة | ~1700 سطر + اختبار يدوي شامل |
| pybabel extract + ملء ترجمات | يتطلب مراجعة كل النصوص في القوالب |
| npm run build (ضغط CSS حقيقي) | يحتاج `npm install` في بيئة البناء |
| Unit/Integration Tests | يحتاج وقتاً للكتابة من الصفر |

## كيفية التشغيل بعد V47

```bash
cp .env.example .env
# ← عدّل .env بقيمك الحقيقية (ADMIN_PASSWORD مهم!)

pip install -r requirements.txt --break-system-packages

# في بيئة dev:
python app.py

# في الإنتاج:
gunicorn wsgi:app
```
