# V45 — Refactor & Design Update

التحديثات المُنفّذة في هذا الإصدار، حسب طلبك (النقاط 4، 5، 11، 13، 14 + إعادة بناء عرض الألعاب).

---

## ✅ إعادة بناء عرض الألعاب (Popular games)

- **حُذف** قسم الـ grid الديناميكي القديم من `templates/home.html` (الحلقة `{% for g in home_groups %}` … `{% for game in games %}`).
- **بُني قسم جديد** مطابق لتصميم الـ screenshot المرفقة (NEONTOPUP):
  - ملف بيانات: [`featured_games.py`](featured_games.py) — قائمة بـ 9 ألعاب مع العنوان، العملة، الصورة، الشارة (HOT)، نسبة الخصم، ومُعرّف اللعبة في قاعدة البيانات.
  - قالب القسم: [`templates/_popular_games.html`](templates/_popular_games.html).
  - الصور: نُسخت الـ 9 صور إلى `static/img/games/web/` من المجلد المرفق.
  - تنسيقات جديدة (gradient، hover، شارات HOT/OFF نيونية) في نهاية ملف CSS الموحّد.
- الكروت تنتقل إلى الـ URL الحقيقي للمنتج عبر `provider` + `game_key` — عدّل `featured_games.py` فقط لتعديل القائمة.

## 4) تجزئة `app.py` إلى Blueprints

- أضيف الحزمة [`routes/`](routes/) مع:
  - `routes/__init__.py` ← `register_blueprints(app)`.
  - `routes/lang_bp.py` ← أول Blueprint (تبديل اللغة، نموذج عملي للنمط).
- `app.py` يستدعي `register_blueprints(app)` تلقائيًا بعد تهيئة CSRF.
- **المرحلة الأولى** فقط: الـ scaffold جاهز ومُختبر للاستيراد، لكن التسجيل الفعلي لـ `lang_bp` معطّل في الكود لتجنّب التعارض مع المسارات القديمة في `app.py` (لها منطق Referer أكثر تعقيدًا). ألغِ التعليق على `app.register_blueprint(lang_bp)` في `routes/__init__.py` بعد حذف المسارات الأصلية في `app.py` (السطور ~380-404).
- **المرحلة الثانية** (مقترح): نقل `auth_bp`, `admin_bp`, `api_bp`, `games_bp`, `wallet_bp` بنفس النمط — كل blueprint في ملف ~150–300 سطرًا بدلًا من ملف `app.py` بحجم 2086.

## 5) RQ + Redis بدلًا من `Queue + Thread`

- ملف جديد [`tasks.py`](tasks.py): تحديد `send_email_task` و `process_order_task` كدوال نقية قابلة للتسلسل (serializable).
- ملف جديد [`worker_rq.py`](worker_rq.py) لتشغيل عامل RQ مستقل.
- `app.send_email()` تم تعديلها: إذا كان `REDIS_URL` معرّفًا → ترسل عبر RQ (دائمة، تعمل بين عمّال متعددين). إذا لم يكن → تعود تلقائيًا إلى الـ thread queue الأصلي (توافق رجعي كامل).
- إضافة `redis==5.0.8` و `rq==1.16.2` إلى [`requirements.txt`](requirements.txt).

### للتفعيل:
```bash
export REDIS_URL=redis://localhost:6379/0
# في عملية منفصلة:
python worker_rq.py
```

## 11) i18n حقيقي عبر Flask-Babel

- إضافة `Flask-Babel==4.0.0` إلى المتطلبات.
- تهيئة Babel في `app.py` مع locale selector يقرأ من `session["lang"]` ثم cookie `lang`.
- `_()` و `gettext()` متاحان الآن في جميع القوالب.
- ملف الإعدادات [`babel.cfg`](babel.cfg) جاهز لاستخراج النصوص.
- مجلد [`translations/`](translations/) فيه `ar/` و `en/` مع نموذج `messages.po` لكل لغة + [`README.md`](translations/README.md) يشرح workflow الاستخراج/التحديث/الترجمة.
- `tr()` القديمة لم تُمسّ — توافق رجعي.

## 13) توحيد ملفات الـ CSS

- ملف موحّد جديد [`static/css/tecnogems.unified.css`](static/css/tecnogems.unified.css) (2493 سطرًا) يجمع بالترتيب التاريخي:
  `style.css` → `v35-overrides.css` → `v40-improvements.css` → `v41-polish.css` → `v43-redesign.css` → `v44-neon.css` + كتلة V45 الجديدة.
- نسخة مُصغّرة (placeholder حتى تُشغّل `npm run build:css`) في `static/css/tecnogems.min.css`.
- `templates/base.html` يحمّل **ملفًا واحدًا فقط** الآن بدلًا من 6 طلبات.
- الملفات الأصلية محفوظة (لم تُحذف) لمراجعة تاريخية ولتسهيل التراجع.

## 14) Build pipeline حقيقي

- ملف [`package.json`](package.json) جديد:
  - `npm run build:css` → `lightningcss` يضغط CSS الموحّد.
  - `npm run build:js` → `esbuild` يضغط `static/js/app.js` إلى `app.min.js`.
  - `npm run build` يشغّل الاثنين.
  - `watch:js` و `watch:css` للتطوير المباشر.
- الآن أي تعديل على JS أو CSS لا يحتاج تحريرًا يدويًا للملف المُصغّر.

### الاستخدام:
```bash
npm install
npm run build
```

---

## الخلاصة

| النقطة | الحالة |
|--------|--------|
| إعادة بناء "Popular games" | ✅ مكتمل |
| 4 — Blueprints | ⚠️ Phase 1 (scaffold + lang_bp جاهز، لم يُفعَّل) |
| 5 — RQ + Redis | ✅ مكتمل (مع fallback) |
| 11 — i18n / Babel | ✅ مكتمل (مع توافق رجعي مع `tr()`) |
| 13 — توحيد CSS | ✅ مكتمل |
| 14 — Build pipeline | ✅ مكتمل |
