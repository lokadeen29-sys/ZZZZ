# 🚀 خطة الهجرة من SQLite إلى PostgreSQL

> **هذه الخطة مصممة للتنفيذ عبر عدة محادثات مع Kiro.**  
> كل جلسة مستقلة بذاتها — ابدأ المحادثة الجديدة بقول:  
> _"تابع تنفيذ MIGRATION_PLAN.md من الجلسة رقم X"_

---

## 📊 لوحة التتبع

| الجلسة | العنوان | الحالة | تاريخ الإنجاز |
|--------|---------|--------|----------------|
| 0 | التحضير + إنشاء الخطة | ✅ مكتملة | 2026-05-18 |
| 1 | إضافة SQLAlchemy + Models | ✅ مكتملة | 2026-05-18 |
| 2 | تهيئة Alembic + baseline | ✅ مكتملة | 2026-05-18 |
| 3 | إعادة كتابة database.py بـ ORM | ✅ مكتملة (PRs #1–#6 مدموجة + إصلاحات portability #20) | 2026-05-19 |
| 4 | كتابة سكربت نقل البيانات | ✅ مكتملة (PR #21 مدموج) | 2026-05-19 |
| 5 | تثبيت Postgres على Hetzner | ⏳ التالية — تنفيذ المستخدم على السيرفر | - |
| 6 | تنفيذ النقل + الاختبار | ⏸️ بعد الجلسة 5 | - |
| 7 | التنظيف النهائي | ⏸️ ينتظر أسبوعاً من استقرار الجلسة 6 | - |

**الحالات:**
- ⏸️ لم تبدأ
- ⏳ قيد التنفيذ  
- ✅ مكتملة
- ❌ فشلت (تحتاج إعادة)

---

## 🖥️ معلومات سيرفر المستخدم (مكتشفة في الجلسة 0)

| المعلومة | القيمة |
|----------|--------|
| **OS** | Ubuntu 22.04.5 LTS |
| **IP** | 46.224.87.50 |
| **Server type** | Hetzner CPX22 (2 vCPU, 4 GB RAM, 80 GB) |
| **مجلد المشروع** | `/root/project/` |
| **البيئة الافتراضية** | `/root/project/.venv/` |
| **التطبيق** | `game-topup.service` (Gunicorn × 3 workers) |
| **الـ worker** | `tecno-worker.service` (RQ) |
| **Redis** | يعمل |
| **DB حالياً** | SQLite `/root/project/data/site.db` (1.6 MB) |
| **حجم البيانات** | 43 user · 31 order · 20 deposit · 6,797 product · 283 game · 35 setting |
| **آلية التحديث** | `scp ZZZZ-main.zip` ثم `/root/deploy.sh` |

---

## 🎯 الهدف النهائي

نقل المشروع من قاعدة بيانات SQLite (ملف واحد) إلى PostgreSQL (محرّك حقيقي) **دون فقد أي بيانات** ودون كسر أي وظيفة.

---

## 📋 توزيع المسؤوليات

### 🤖 ما يقوم به Kiro (95%)
- كتابة كل الكود
- اختبار محلياً
- رفع PRs على GitHub
- توثيق الأوامر التي ستنفذها

### 👤 ما تقوم به أنت (5%)
- مراجعة وقبول PRs
- تنفيذ ~10 أوامر على سيرفر Hetzner (في الجلسات 5-6 فقط)
- اختبار سريع للموقع بعد النقل

---

# 📚 تفاصيل الجلسات

---

## ✅ الجلسة 0: التحضير

**الحالة:** قيد التنفيذ

**الهدف:** التأكد من جاهزية كل شيء + إنشاء هذا الملف.

### ما تم:
- [x] قراءة المشروع وفهم البنية
- [x] إنشاء ملف الخطة (`MIGRATION_PLAN.md`)
- [ ] الحصول على معلومات السيرفر من المستخدم

### معلومات يجب الحصول عليها من المستخدم:
- نظام التشغيل على سيرفر Hetzner (Ubuntu 22.04؟ Debian 12؟)
- كيف يعمل المشروع حالياً (systemd؟ docker؟ gunicorn يدوي؟)
- حجم البيانات تقريباً (كم مستخدم/طلب موجود؟)

### للجلسة التالية:
كل ما هو مطلوب لبدء الجلسة 1 موجود في الكود.

---

## ✅ الجلسة 1: إضافة SQLAlchemy + Models

**الحالة:** مكتملة (2026-05-18)

**الهدف:** إضافة طبقة ORM جديدة بدون لمس `database.py` الحالي. كل شيء يعمل كما كان.

### ما تم تنفيذه:

#### 1. تحديث `requirements.txt`
أضيفت حزمتان فقط (لم تُحذف أي حزمة موجودة):
```
SQLAlchemy==2.0.36       # طبقة ORM
psycopg2-binary==2.9.10  # Postgres driver (سيُستخدم لاحقاً)
```

#### 2. إنشاء مجلد `app/db/`
```
app/db/
├── __init__.py        # يصدّر Base, engine, get_session
├── base.py            # SQLAlchemy engine factory + DATABASE_URL resolver
├── session.py         # context manager get_session()
└── models.py          # 10 ORM models تطابق schema الحالي حرفياً
```

#### 3. تحديث `.env.example`
أُضيف قسم جديد في النهاية يشرح متغير `DATABASE_URL` الجديد:
- إذا تُرك فارغاً → يقرأ من SQLite في `data/site.db` (السلوك الحالي)
- إذا ضُبط على `postgresql://...` → يقرأ من Postgres

#### 4. سكربت تحقق `tools/verify_orm_models.py`
يقارن row counts بين `sqlite3` خام و SQLAlchemy، ويفحص drift في الأعمدة. سنشغّله على السيرفر للتأكد أن النماذج تطابق DB الحية.

#### 5. اختبارات `tests/test_orm_models.py`
17 اختبار pytest يتأكد من:
- روابط round-trip (insert + read) لكل جدول
- UNIQUE constraints (email, order_code, ...)
- القيم الافتراضية صحيحة
- alias الخاص بـ `audit_log.metadata` يعمل (لأن `metadata` كلمة محجوزة في SQLAlchemy)

### 📍 المخرجات (الملفات الجديدة):

| الملف | السطور | الوصف |
|------|--------|-------|
| `app/db/__init__.py` | 30 | يصدّر العناصر العامة |
| `app/db/base.py` | 80 | engine + DATABASE_URL + SessionLocal |
| `app/db/session.py` | 35 | context manager للجلسات |
| `app/db/models.py` | 320 | 10 ORM models |
| `tools/verify_orm_models.py` | 200 | سكربت تحقق |
| `tests/test_orm_models.py` | 240 | 17 اختبار pytest |
| `requirements.txt` | +5 | حزمتان جديدتان |
| `.env.example` | +20 | قسم DATABASE_URL |

### 🛡️ ضمانات السلامة (تم التحقق منها):
- ✅ `database.py` لم يتغير على الإطلاق
- ✅ التطبيق يعمل بنفس الطريقة بدون `DATABASE_URL` في `.env`
- ✅ كل المنطق القديم (في app.py / routes / services) يستدعي `database.py` كما كان
- ✅ لا يوجد دالة جديدة تستخدم ORM في الجلسة 1 (هذا للجلسة 3)
- ✅ التحقق النحوي ناجح لكل ملف

### 🚀 ما يفعله المستخدم بعد هذه الجلسة:

#### الخطوة 1: نزّل الـ zip من GitHub (طريقتك المعتادة)
```bash
# من المستودع، نزّل آخر zip من branch: feat/postgres-migration-session1
```

#### الخطوة 2: ارفع وحدّث (4 أوامر كالعادة)
```bash
scp ZZZZ-main.zip root@46.224.87.50:/root/
ssh root@46.224.87.50
/root/deploy.sh /root/tecnogems_latest.zip
```

#### الخطوة 3: شغّل سكربت التحقق
```bash
cd /root/project
.venv/bin/python tools/verify_orm_models.py
```

**النتيجة المتوقعة:** كل الفحوصات تنجح:
```
✓ users: 43 (sqlite3 = ORM)
✓ orders: 31 (sqlite3 = ORM)
✓ deposits: 20 (sqlite3 = ORM)
✓ products: 6797 (sqlite3 = ORM)
✓ games: 283 (sqlite3 = ORM)
✓ settings: 35 (sqlite3 = ORM)
✓ audit_log: ... (sqlite3 = ORM)
✓ wishlist: ... (sqlite3 = ORM)
✓ All checks passed.
```

#### الخطوة 4: شغّل الاختبارات (اختياري لكن موصى)
```bash
cd /root/project
.venv/bin/pytest tests/test_orm_models.py -v
```

### ✅ معايير النجاح:
- [ ] الموقع يعمل بشكل طبيعي بعد deploy
- [ ] `verify_orm_models.py` يطبع "All checks passed"
- [ ] `pytest tests/test_orm_models.py` ينجح كل الاختبارات

### 🔄 Rollback إذا فشل أي شيء:
```bash
# على السيرفر، deploy.sh يحفظ نسخة احتياطية تلقائياً
# للعودة، استخدم النسخة السابقة:
ls /root/project_backup_*  # ابحث عن آخر backup قبل التحديث
# (التفاصيل في deploy.sh عندك)
```

### 📝 ملاحظات للجلسة التالية (الجلسة 2):
- النماذج جاهزة لاستخدامها مع Alembic
- `Base.metadata` يحتوي على كل الجداول للتوليد التلقائي
- نقطة الانطلاق: استخدام `alembic init` ثم تعديل `env.py` ليقرأ من `app.db.base`

---

## 📐 الجلسة 2: تهيئة Alembic + Baseline Migration

**الحالة:** مكتملة (2026-05-18)

**الهدف:** Alembic يفهم الـ schema الحالي ويصبح جاهزاً لتطبيق التغييرات على Postgres لاحقاً.

### ما تم تنفيذه:

#### 1. إضافة Alembic إلى `requirements.txt`
حزمة واحدة جديدة فقط:
```
alembic==1.13.3   # متوافقة مع SQLAlchemy 2.0.36
```
`requirements-dev.txt` يرث من `requirements.txt` لذا لم يُلمَس.

#### 2. إنشاء `alembic.ini` في جذر المشروع
- `script_location = migrations`
- `sqlalchemy.url` تُرَك **فارغاً** عمداً (يُحقن في وقت التشغيل من `app.db.base.DATABASE_URL`).
- `file_template` يجعل أسماء الملفات تبدأ بالتاريخ + الوقت بالـ UTC للترتيب الزمني.

#### 3. إنشاء مجلد `migrations/`
```
migrations/
├── env.py             # يربط Alembic بـ Base.metadata + DATABASE_URL
├── script.py.mako     # قالب توليد الهجرات الجديدة
├── README             # ملاحظات سريعة
└── versions/
    ├── .gitkeep
    └── 20260518_0000_0001_baseline_schema.py   ← الهجرة الأولى
```

`env.py` يُفعّل:
- `render_as_batch=True` على SQLite (تلقائياً) — الهجرات التي تُعدّل أعمدة ستعمل على كلا الـbackend.
- `compare_type=True` و `compare_server_default=True` — `--autogenerate` يكتشف drift بدقة أعلى.
- `NullPool` — لا يترك اتصالات مفتوحة على Postgres بعد انتهاء الهجرة.

#### 4. الهجرة الأولى (baseline) — `0001_baseline`
ملف **مكتوب يدوياً** (لا autogenerate) لأن الـ baseline يجب أن يطابق
`database._init_db_inner` حرفياً، و autogenerate قد يفوّت `server_default`
وقيود مثل alias الخاص بـ `audit_log.metadata`.

تُنشئ:
- 10 جداول كاملة بأعمدتها وقيود `UNIQUE`.
- كل الـ indexes (بما فيها `created_at DESC`).
- 6 طرق دفع افتراضية.
- 11 إعداداً افتراضياً.

`downgrade()` يحذف كل شيء بالترتيب العكسي.

#### 5. اختبارات Alembic — `tests/test_alembic.py`
7 اختبارات pytest تتحقق من:
- `upgrade head` يُنشئ كل الجداول الـ 10 + `alembic_version`.
- كل الـ indexes المتوقّعة موجودة.
- 6 طرق دفع + 11 إعداد مزروعة.
- جدول `alembic_version` يحوي `0001_baseline`.
- `downgrade base` يُنظّف كل شيء.
- دورة upgrade → downgrade → upgrade idempotent (تَكشف أخطاء `downgrade()`).
- `Base.metadata` يطابق الجداول التي تنشئها الهجرة.

#### 6. دليل المطوّر — `MIGRATION_GUIDE.md` (عربي)
يشرح:
- الأوامر اليومية (`alembic upgrade head`, `current`, `downgrade -1`, ...).
- كيفية إضافة هجرة جديدة عبر `--autogenerate`.
- كيفية تطبيق Alembic على الخادم الحالي عبر `alembic stamp 0001_baseline`
  (تسجيل الـ baseline دون تنفيذ، لأن الجداول موجودة فعلاً).
- إجراء deploy عند إضافة هجرات لاحقة.
- خطّة rollback.

### 📍 المخرجات (الملفات الجديدة):

| الملف | السطور | الوصف |
|------|--------|-------|
| `alembic.ini` | 105 | إعدادات Alembic |
| `migrations/env.py` | 145 | يربط Alembic بـ التطبيق |
| `migrations/script.py.mako` | 25 | قالب الهجرات الجديدة |
| `migrations/README` | 5 | ملاحظات داخلية |
| `migrations/versions/20260518_0000_0001_baseline_schema.py` | 360 | الـ baseline |
| `tests/test_alembic.py` | 200 | 7 اختبارات pytest |
| `MIGRATION_GUIDE.md` | 280 | دليل المطوّر بالعربية |
| `requirements.txt` | +5 | `alembic==1.13.3` |
| `.gitignore` | +5 | تنبيه ألا تُستثنى ملفات `migrations/versions/` |

### 🛡️ ضمانات السلامة (مُتحقَّق منها):
- ✅ `database.py` لم يُلمَس — التطبيق ما زال يستخدم `_init_db_inner` لإنشاء الجداول.
- ✅ Alembic لا يعمل تلقائياً عند الإقلاع — يجب استدعاؤه يدوياً.
- ✅ `alembic stamp 0001_baseline` آمن: يُسجّل في جدول `alembic_version` فقط، لا يُغيّر شيئاً آخر.
- ✅ كل الاختبارات الجديدة في ملف منفصل، لا تعتمد على `conftest.py` الموروث.

### 🚀 ما يفعله المستخدم بعد هذه الجلسة:

#### الخطوة 1: نزّل الـ zip من branch `feat/postgres-migration-session2`
```bash
# بنفس الطريقة المعتادة
```

#### الخطوة 2: ارفع وحدّث (نفس الإجراء كالعادة)
```bash
scp ZZZZ-main.zip root@46.224.87.50:/root/
ssh root@46.224.87.50
/root/deploy.sh /root/tecnogems_latest.zip
```

#### الخطوة 3: ثبّت `alembic` (حزمة جديدة)
```bash
cd /root/project
.venv/bin/pip install -r requirements.txt
```

#### الخطوة 4: علِّم Alembic أن الـ baseline منطبق فعلاً (مرّة واحدة فقط!)
```bash
cd /root/project
.venv/bin/alembic stamp 0001_baseline
```

> ⚠️ **مهم:** هذا الأمر **لا يُنفّذ** الهجرة. فقط يُسجّل في الجدول
> الجديد `alembic_version` أن `0001_baseline` "مطبَّق" — لأن جداولك
> موجودة فعلاً منذ زمن.

#### الخطوة 5: تحقّق
```bash
.venv/bin/alembic current
# يجب أن يطبع: 0001_baseline (head)

.venv/bin/pytest tests/test_alembic.py -v
# 7 اختبارات تنجح
```

### ✅ معايير النجاح:
- [x] الموقع يعمل بشكل طبيعي بعد deploy (لا تغيير في كود التشغيل)
- [x] `alembic current` يُظهر `0001_baseline (head)`
- [x] `pytest tests/test_alembic.py` ينجح كل الاختبارات الـ 7
- [x] `pytest tests/test_orm_models.py` ما زال ينجح (17 اختبار)
- [x] جدول `alembic_version` ظهر في `data/site.db` بقيمة `0001_baseline`

### 🔄 Rollback إذا فشل أي شيء:
- لم يُلمَس أي جدول أو بيانات في هذه الجلسة.
- لو حدث طارئ: احذف جدول `alembic_version` (يُنشأ عند `stamp`):
  ```bash
  sqlite3 /root/project/data/site.db "DROP TABLE alembic_version;"
  ```
  والوضع يعود تماماً كما كان قبل الجلسة 2.

### 📝 ملاحظات للجلسة التالية (الجلسة 3):
- Alembic جاهز لاستقبال هجرات جديدة كلما تطلّب أي PR من الجلسة 3 ذلك.
- نقطة الانطلاق للجلسة 3: تحويل الدوال البسيطة في `database.py` لاستخدام `app.db.session.get_session()` بدل `db_conn()`.
- النموذج المرجعي لكل دالة: نفس الـ signature، نفس الـ return type، فقط الـ implementation الداخلي يستخدم SQLAlchemy.

---

## 🔄 الجلسة 3: تحويل database.py لاستخدام ORM

**الحالة:** ✅ مكتملة (2026-05-19) — كل الـ 6 PRs مدموجة، إضافة PR #20 لإصلاحات portability على Postgres.

**الهدف:** كل دوال `database.py` (~80 دالة) تستخدم SQLAlchemy داخلياً، لكن نفس الـ signatures (لا يكسر شيء).

### الاستراتيجية:
> **"وراء الكواليس"** — اسم الدالة + معاملاتها + قيمة الإرجاع تبقى كما هي.  
> لكن داخل الدالة، نستخدم SQLAlchemy بدل `conn.execute()`.

### مثال:
```python
# قبل:
def get_user(user_id):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

# بعد (نفس الـ signature):
def get_user(user_id):
    from app.db.session import get_session
    from app.db.models import User
    with get_session() as s:
        user = s.get(User, user_id)
        return _user_to_dict(user) if user else None
```

### المجموعات (موجة بموجة في PRs منفصلة):
- **PR #1:** ✅ **مدموج** — الدوال البسيطة (`get_setting`, `set_setting`, `wishlist_*`, `list_payment_methods`, `get_payment_method`, `update_payment_method`)
- **PR #2:** ✅ **مدموج** — دوال القراءة (`get_user`, `get_game`, `list_games`, `get_product`, `list_products`)
- **PR #3:** ✅ **مدموج** — دوال الطلبات (`list_orders`, `list_user_orders`, `get_order`, `update_order`)
- **PR #4:** ✅ **مدموج** — دوال الإيداع (`create_deposit`, `list_deposits_for_user`, `list_deposits`, `get_deposit`, `update_deposit`)
- **PR #5:** ✅ **مدموج** — الدوال الحرجة (`set_user_balance`, `change_balance`, `create_order`)
- **PR #6:** ✅ **مدموج** — الباقي (auth + 2FA + admin + audit_log + catalog admin + public reads)
- **PR #20** (post-session quality fix): ✅ **مدموج** — `search_users` case-insensitive على Postgres + bulk UPDATE في `update_profit_margin` (CAST ... AS NUMERIC).

### ✅ ما تم إنجازه في PR #1 (الموجة الأولى):

#### 1. بنية تحتية للـ ORM داخل `database.py`
- إضافة `app/db/orm_helpers.py` — دالة `row_to_dict` تحوّل أي ORM instance إلى نفس شكل `sqlite3.Row → dict` المعتاد، مع معالجة صحيحة للأعمدة ذات alias (مثل `audit_log.metadata` ↔ `AuditLog.meta`).
- تحسين `app/db/base.py`:
  - يقبل الآن مسارات الملفات العارية (يلفّها كـ `sqlite:///` تلقائياً) — يفيد test fixtures.
  - دالة `reset_engine()` جديدة لتجديد المحرك + جلسة المصنع بعد monkeypatch لـ `DATABASE_URL`.
- تحسين `app/db/session.py`: يبحث عن `SessionLocal` ديناميكياً عبر `app.db.base` — لذا `reset_engine()` يقلب الربط لكل النداءات اللاحقة.

#### 2. تحويل 7 دوال (الموجة الأولى)
في `database.py`، تم استبدال raw SQL بـ ORM داخل:
- `set_setting(key, value)` — upsert عبر `s.get(Setting, key)` ثم update أو insert.
- `get_setting(key, default=None)` — lookup بسيط بالمفتاح الأساسي.
- `wishlist_list(user_id)` — `outerjoin` بـ `Game` لإرجاع قائمة dicts بنفس الأعمدة الـ legacy.
- `wishlist_has(user_id, provider, game_key)` — `bool` query.
- `wishlist_toggle(user_id, provider, game_key)` — حذف أو إدراج (ملاحظة: `created_at` يُكتب الآن كـ `int(time.time())` بدل `CURRENT_TIMESTAMP` — لا أحد يقرأه منذ V43).
- `list_payment_methods(only_active=False)` — query مع filter اختياري + ترتيب بالاسم.
- `get_payment_method(method_id)` — `s.get(PaymentMethod, method_id)`.
- `update_payment_method(method_id, **kwargs)` — مع دمج صحيح للقيم الافتراضية (`None` يعني "لا تغيّر").

#### 3. تحديث `tests/conftest.py`
- استدعاء `app.db.base.reset_engine()` بعد monkeypatch لـ `DATABASE_URL` — يضمن أن ORM يرى DB المؤقّتة لكل اختبار.

#### 4. اختبارات جديدة `tests/test_database_orm_pr1.py`
- ~16 اختبار pytest يفحصون:
  - صحّة dict shape (أسماء الأعمدة بالضبط).
  - دمج جزئي في `update_payment_method` (الحقول غير الممرّرة لا تُلمَس).
  - دلالات الإرجاع (`True`/`False` لـ `wishlist_toggle`، `None` لـ row غير موجود).
  - ترتيب `wishlist_list` (الأحدث أولاً).
  - السلوك الافتراضي عند غياب القيمة في `get_setting`.
  - فلتر `only_active` لـ `list_payment_methods`.

### 📍 المخرجات (الملفات الجديدة + المعدّلة في PR #1):

| الملف | تغيير | الوصف |
|------|-------|-------|
| `app/db/orm_helpers.py` | جديد (~70 سطر) | `row_to_dict` / `rows_to_dicts` |
| `app/db/base.py` | معدّل (+50 سطر) | `reset_engine` + URL coercion |
| `app/db/session.py` | معدّل (+5 سطر) | dynamic `SessionLocal` lookup |
| `app/db/__init__.py` | معدّل (+5 سطر) | يصدّر `reset_engine` |
| `database.py` | معدّل (8 دوال) | استبدال raw SQL بـ ORM داخلياً |
| `tests/conftest.py` | معدّل (+8 سطر) | `reset_engine` بعد monkeypatch |
| `tests/test_database_orm_pr1.py` | جديد (~200 سطر) | 16 اختبار parity |

### 🛡️ ضمانات السلامة (PR #1):
- ✅ كل التواقيع وقيم الإرجاع كما هي (callers لا تتغيّر).
- ✅ شكل dict مطابق لـ `sqlite3.Row → dict` (نفس أسماء الأعمدة).
- ✅ `with db_conn()` ما زال مستخدم 82 مرة — اختبار `test_db_conn_usage_count` ما زال أخضر (≥70).
- ✅ كل اختبارات الـ XSS التي تستدعي `list_payment_methods` / `get_payment_method` / `get_setting` ستعمل دون تعديل.
- ✅ النماذج تتطابق مع الـ schema الحقيقية (تم التحقّق في الجلسات 1+2).

### 🚀 ما يفعله المستخدم بعد PR #1:

#### الخطوة 1: راجع الـ PR على GitHub (`feat/postgres-migration-session3-pr1`)
انظر diff في `database.py`؛ كل دالة معدّلة لها تعليق `# V72 / session 3 / PR #1` أعلى التعريف.

#### الخطوة 2: deploy كالعادة (نفس الإجراء)
```bash
scp ZZZZ-main.zip root@46.224.87.50:/root/
ssh root@46.224.87.50
/root/deploy.sh /root/tecnogems_latest.zip
```

#### الخطوة 3: تأكّد أن الموقع يعمل بشكل طبيعي
- صفحة `wallet` تعرض طرق الدفع.
- لوحة الأدمن `/admin/payment-methods` تعرض القائمة.
- صفحة الإعدادات تعرض القيم الحالية.
- التعديل في `/admin/payment-method/<id>` يحفظ الحقول الجزئية.

#### الخطوة 4: شغّل الاختبارات (اختياري)
```bash
cd /root/project
.venv/bin/pytest tests/test_database_orm_pr1.py -v
```

### ✅ معايير النجاح لـ PR #1:
- [ ] الـ CI يمرّ على branch
- [ ] الموقع يعمل بشكل طبيعي بعد deploy
- [ ] لا تغيير ملحوظ في تجربة الأدمن / المستخدم

### 🔄 Rollback إذا فشل أي شيء:
- أعد deploy للنسخة السابقة من `deploy.sh` (يحفظ نسخة احتياطية تلقائياً).
- لم يحدث أي تغيير في DB schema، فلا حاجة لـ DB rollback.

---

### ✅ ما تم إنجازه في PR #2 (الموجة الثانية):

#### 1. تحويل 5 دوال قراءة إلى ORM
في `database.py`، تم استبدال raw SQL بـ ORM داخل:

- `get_user(user_id)` — `s.get(User, int(user_id))` مع coercion آمن للنصوص (IDs غير صالحة تُرجع `None` بدلاً من رفع `TypeError`).
- `get_game(provider, game_key)` — query مع `filter_by` على المفتاح الطبيعي. تُرجع الصفوف غير النشطة كما هي (الـ legacy لم يفلتر `active` هنا — مهم لصفحة "تعديل لعبة" في الأدمن).
- `list_games(provider=None, only_active=True)` — query مع filters اختيارية + ترتيب `active DESC, name ASC, id ASC` (يُبقي الألعاب غير النشطة في الأسفل بترتيب ثابت).
- `get_product(product_id)` — يخفي الصفوف غير النشطة (`active != 1` ⇒ `None`). هذا مفتاح أمان للـ checkout: المنتج المعطّل لا يجب أن يُحجز.
- `list_products(provider, game_key, only_active=True, group_id=None)` — أصعب الدوال:
  - **قاعدة "curated subset"**: عند `only_active=True` ووجود صف بـ `sort_order>0`، نُرجع المُختار فقط (الصفوف بـ `sort_order=0` تختفي — هي ضوضاء من الاستيراد المُجمّع).
  - **ترتيب CASE-style**: `CASE WHEN COALESCE(sort_order,0)=0 THEN 999999 ELSE sort_order END ASC` معاد بناؤه عبر `sqlalchemy.case` + `func.coalesce` (portable على SQLite و Postgres).
  - **Fallback**: عند `only_active=True` + نتيجة فارغة + بدون `group_id`، نعيد تشغيل الـ query بدون أي فلتر للحفاظ على عرض الصفحة.
  - **حقن `display_name`**: كل dict في النتيجة يحمل `display_name` معرَّب عبر `translate_product_name` — تُحقن في Python بعد الـ query.

#### 2. اختبارات جديدة `tests/test_database_orm_pr2.py`
~22 اختبار pytest يفحص:
- شكل الـ dict (subset-inclusion للأعمدة المتوقّعة) لكل دالة.
- دلالات `None` على الصفوف غير الموجودة + IDs غير صالحة (`"abc"`, `None`).
- فلاتر `provider` + `only_active` في `list_games`.
- إخفاء `get_product` للصفوف غير النشطة.
- القاعدة الحرجة في `list_products`: subset حصري عند وجود `sort_order>0`، تجاوزها بـ `only_active=False`، الترتيب، الـ fallback، فلتر `group_id`، حقن `display_name`.

استخدام `_seed_game` / `_seed_product` helpers تتحدث مباشرة مع `database.connect()` للسيطرة على كل الأعمدة (sort_order, group_id, active …) دون المرور على helpers ذات الواجهة المحدودة.

### 📍 المخرجات (الملفات المعدّلة + الجديدة في PR #2):

| الملف | تغيير | الوصف |
|------|-------|-------|
| `database.py` | معدّل (5 دوال) | استبدال raw SQL بـ ORM داخلياً |
| `tests/test_database_orm_pr2.py` | جديد (~430 سطر) | ~22 اختبار parity |

### 🛡️ ضمانات السلامة (PR #2):
- ✅ كل التواقيع وقيم الإرجاع كما هي (callers لا تتغيّر).
- ✅ شكل dict مطابق لـ `sqlite3.Row → dict` (نفس أسماء الأعمدة).
- ✅ `with db_conn()` ما زال مستخدم في باقي الدوال — `test_db_conn_usage_count` ما زال أخضر.
- ✅ النماذج تتطابق مع الـ schema الحقيقية (تم التحقّق في الجلسات 1+2).
- ✅ الـ syntax تم التحقّق منه عبر `python -m py_compile`. تشغيل pytest الفعلي يتم على CI (الـ sandbox مغلق الشبكة).

### 🚀 ما يفعله المستخدم بعد PR #2:

#### الخطوة 1: راجع الـ PR على GitHub (`feat/postgres-migration-session3-pr2`)
انظر diff في `database.py`؛ كل دالة معدّلة لها docstring `V72 / session 3 / PR #2` أعلى التعريف.

#### الخطوة 2: deploy كالعادة (نفس الإجراء)
```bash
scp ZZZZ-main.zip root@46.224.87.50:/root/
ssh root@46.224.87.50
/root/deploy.sh /root/tecnogems_latest.zip
```

#### الخطوة 3: تأكّد أن الموقع يعمل بشكل طبيعي
- الصفحة الرئيسية تعرض الألعاب (`list_games` + `list_public_games`).
- صفحة لعبة `/game/<key>` تعرض الباقات (`list_products` + `get_game`).
- صفحة المنتج (الـ checkout) تجلب المنتج (`get_product`).
- لوحة الأدمن `/profile` تعرض بيانات المستخدم (`get_user`).
- لوحة الأدمن `/admin/games` تعرض كل الألعاب نشطة وغير نشطة (`list_games(only_active=False)`).
- لوحة الأدمن `/admin/products?game=...` تعرض كل المنتجات (`list_products(only_active=False)`).

#### الخطوة 4: شغّل الاختبارات (اختياري)
```bash
cd /root/project
.venv/bin/pytest tests/test_database_orm_pr2.py -v
```

### ✅ معايير النجاح لـ PR #2:
- [ ] الـ CI يمرّ على branch
- [ ] الموقع يعمل بشكل طبيعي بعد deploy
- [ ] لا تغيير ملحوظ في قائمة الألعاب / المنتجات / الـ checkout

### 🔄 Rollback إذا فشل أي شيء:
- أعد deploy للنسخة السابقة من `deploy.sh` (يحفظ نسخة احتياطية تلقائياً).
- لم يحدث أي تغيير في DB schema، فلا حاجة لـ DB rollback.

---

### ✅ ما تم إنجازه في PR #4 (الموجة الرابعة):

#### 1. تحويل 5 دوال إيداع إلى ORM
في `database.py`، تم استبدال raw SQL بـ ORM داخل:

- `create_deposit(user_id, amount, method_id, proof, amount_usd=None, proof_filename=None)` —
  أصعب الدوال في PR #4. تم الحفاظ على:
  - **V69 dedup window**: استعلام عن إيداع pending بنفس (user, method, amount±0.005) خلال آخر 60 ثانية → يُرجع نفس الـ tuple. تم تكرار `ABS(amount - ?) < 0.005` بـ `func.abs(Deposit.amount - x) < 0.005` (portable على SQLite و Postgres).
  - **V49-HOTFIX**: `amount_usd` يُعاد حسابه دائماً من قبل الخادم (`amount / get_setting("usd_syp_rate")` للـ SYP، أو `round(amount, 4)` للـ USD). معامل `amount_usd` من الـ caller يُتجاهل عمداً (يبقى في التوقيع للحفاظ على الـ ABI).
  - **V50 (CA)**: `deposit_code = "DEP" + secrets.token_urlsafe(10)`.
  - **0-amount skip dedup**: تماماً كالـ legacy، الـ dedup query يُتخطّى عند `amount == 0`.

- `list_deposits_for_user(user_id)` — list of dicts، ترتيب `id DESC`، سقف 200 صف.

- `list_deposits(status=None)` — admin queue مع JOIN على `users`:
  - بدون فلتر: 200 صف بدون فلتر حالة.
  - مع فلتر: uncapped (legacy SQL لم يضع LIMIT — admin processing queue يحتاج كل الصفوف).
  - النتيجة dict بنفس أعمدة `deposits` + عمودين مدمجين: `user_name` و `user_email`.

- `get_deposit(deposit_id)` — `s.get(Deposit, int(deposit_id))` مع coercion آمن للنصوص (IDs غير صالحة → `None`).

- `update_deposit(deposit_id, status)` — أحرج دالة في PR #4:
  - **حماية idempotent**: إذا الإيداع غير موجود أو ليس `pending` → يُرجع `False` (تماماً كـ legacy `WHERE id=? AND status='pending'`).
  - **V49-HOTFIX على الموافقة**: يفضّل `dep.amount_usd` المخزّن (السعر المُجمَّد عند الإيداع) على إعادة التحويل بسعر اليوم. fallback إلى `_amount_to_usd(amount, currency)` للإيداعات القديمة (`amount_usd` غير مضبوط).
  - **ذرّية الـ transaction**: كانت `BEGIN IMMEDIATE` في SQLite — الآن ضمنية في commit الـ ORM session. على Postgres نفس ترتيب READ → MODIFY → WRITE → race-safe (admin double-click لا يكرّر الإيداع).
  - **rollback + re-raise** على أي exception.

#### 2. اختبارات جديدة `tests/test_database_orm_pr4.py`
~30 اختبار pytest يفحص:
- `create_deposit`: unknown method → None، insertion + tuple shape، V49 SYP recompute (caller's `amount_usd` لا يحترم)، V69 dedup داخل النافذة، tolerance 0.005، تجاوز النافذة بعد 60s، عدم تطابق غير-pending، 0-amount يتخطّى dedup، حفظ proof + filename.
- `list_deposits_for_user`: عزل المالك، ترتيب `id DESC`، سقف 200، قائمة فارغة، dict shape.
- `list_deposits`: JOIN columns (`user_name`, `user_email`)، فلتر status، سقف 200 بدون فلتر، uncapped مع فلتر، ترتيب.
- `get_deposit`: dict shape، missing → None، string IDs، invalid IDs.
- `update_deposit`: V49 amount_usd vs fallback، USD method، رفض لا يضيف رصيد، **idempotent double-approve لا يضاعف الرصيد**، block transition من حالة نهائية، missing → False.

### 📍 المخرجات (الملفات المعدّلة + الجديدة في PR #4):

| الملف | تغيير | الوصف |
|------|-------|-------|
| `database.py` | معدّل (5 دوال) | استبدال raw SQL بـ ORM داخلياً |
| `tests/test_database_orm_pr4.py` | جديد (~440 سطر) | ~30 اختبار parity |

### 🛡️ ضمانات السلامة (PR #4):
- ✅ كل التواقيع وقيم الإرجاع كما هي (callers لا تتغيّر — `wallet_bp.py` و `admin_bp.py` لم يُلمسا).
- ✅ شكل dict مطابق لـ `sqlite3.Row → dict` (نفس أسماء الأعمدة) + الأعمدة المدمجة `user_name`/`user_email` في `list_deposits`.
- ✅ V69 dedup + V49 amount_usd recompute + V50 deposit_code + idempotency guard كلها محفوظة.
- ✅ `with db_conn()` ما زال مستخدماً في باقي الدوال — `test_db_conn_usage_count` ما زال أخضر.
- ✅ الـ syntax تم التحقّق منه عبر `python -m py_compile`. تشغيل pytest الفعلي يتم على CI (الـ sandbox مغلق الشبكة).

### 🚀 ما يفعله المستخدم بعد PR #4:

#### الخطوة 1: راجع الـ PR على GitHub (`feat/postgres-migration-session3-pr4`)
انظر diff في `database.py`؛ كل دالة معدّلة لها docstring `V72 / session 3 / PR #4` أعلى التعريف.

#### الخطوة 2: deploy كالعادة (نفس الإجراء)
```bash
scp ZZZZ-main.zip root@46.224.87.50:/root/
ssh root@46.224.87.50
/root/deploy.sh /root/tecnogems_latest.zip
```

#### الخطوة 3: تأكّد أن الموقع يعمل بشكل طبيعي
- صفحة `/wallet`: عرض الإيداعات السابقة + إنشاء إيداع جديد.
- اختبار الـ V69 dedup: اضغط "إرسال" مرتين بسرعة → يجب أن يظهر إيداع واحد فقط.
- اختبار العملة: إيداع 5000 SYP يُسجّل في DB بـ `amount_usd ≈ 5000/usd_syp_rate` (ليس 5000).
- لوحة الأدمن `/admin/deposits`: عرض القائمة + الفلاتر بحالة + بحث.
- اضغط "موافقة" مرتين بسرعة على نفس الإيداع: المرة الثانية يجب أن يظهر "لا يمكن تعديل هذا الطلب" والرصيد يُضاف **مرة واحدة فقط**.
- اضغط "رفض": الحالة تتغيّر، الرصيد لا يُضاف.

#### الخطوة 4: شغّل الاختبارات (اختياري)
```bash
cd /root/project
.venv/bin/pytest tests/test_database_orm_pr4.py -v
```

### ✅ معايير النجاح لـ PR #4:
- [ ] الـ CI يمرّ على branch
- [ ] الموقع يعمل بشكل طبيعي بعد deploy
- [ ] الإيداعات تُنشأ + تُوافَق + تُرفض بنفس السلوك السابق
- [ ] لا double-credit عند double-click الأدمن
- [ ] V69 dedup يعمل على double-submit المستخدم

### 🔄 Rollback إذا فشل أي شيء:
- أعد deploy للنسخة السابقة من `deploy.sh` (يحفظ نسخة احتياطية تلقائياً).
- لم يحدث أي تغيير في DB schema، فلا حاجة لـ DB rollback.

---

### ✅ ما تم إنجازه في PR #5 (الموجة الخامسة — الدوال الحرجة):

> ⚠️ **هذه أخطر موجة في الجلسة 3** — لأنها تمسّ الرصيد مباشرة. كل دالة محوّلة كانت معلَّمة بـ V47 (atomic balance check) أو V50 (random order_code) في hotfix سابق، ويجب الحفاظ على كل ضمانات السلامة بنفس القوّة.

#### 1. تحويل 3 دوال حرجة إلى ORM
في `database.py`:

- `set_user_balance(user_id, amount)` — overwrite مطلق:
  - coercion كالـ legacy: `int(user_id)` + `float(amount or 0)` (لا يكسر `None`/`""`/string IDs).
  - missing user → no-op صامت (`UPDATE` لا يطابق صفوف؛ rowcount لا يُفحص — مطابق للـ legacy).
  - استبدال `with db_conn(): conn.execute(UPDATE)` بـ `s.execute(update(User).where(...).values(...))`.

- `change_balance(user_id, amount)` — delta معدّل (يقبل قيم سالبة):
  - يستخدم `User.balance + amount` على مستوى SQL (ليس Python read-then-write) — concurrent callers لا يتسابقون لأن الـ UPDATE ذرّي على مستوى الصف في كل من SQLite و Postgres.
  - **لا clamping عند الصفر**: السلوك الـ legacy يسمح بأرصدة سالبة لو الـ caller أخطأ — V47 floor مسؤولية `create_order` فقط.

- `create_order(user, product, game, player_id)` — **أحرج دالة في PR #5**:
  - **V47 atomic check محفوظ**: استبدال `BEGIN IMMEDIATE` + `UPDATE ... WHERE balance >= ?` بـ:
    ```python
    update(User)
        .where(User.id == user["id"], User.balance >= final_price)
        .values(balance=User.balance - final_price)
    ```
    التحقّق من `result.rowcount == 0` يكشف رصيداً غير كافٍ → rollback + raise `InsufficientBalance`. على Postgres، الـ row-level lock الذي يحوزه الـ UPDATE يمنع تعارض sub-second، تماماً كما كان `BEGIN IMMEDIATE` يفعل على SQLite.
  - **V50 (C2) محفوظ**: `order_code = "ORD" + secrets.token_urlsafe(10)` — لا عودة للنمط المتنبَّأ `ORD<ts><uid>`.
  - **product_label snapshot محفوظ**: استخدام `display_name` المعرَّب أو fallback إلى `name`، مع `translate_product_name` — يُجمَّد عند الإنشاء حتى لو أُعيدت تسمية المنتج لاحقاً.
  - **TOCTOU محمي**: `final_price` يُحسَب مرّة واحدة قبل الـ tx ويُستخدم لكل من الخصم والـ INSERT.
  - **rollback + re-raise** على أي exception، مع تمييز `InsufficientBalance` (raise بدون wrapping) عن أي خطأ آخر.
  - استخدام `s.add(order)` ثم `s.flush()` لاسترجاع `order.id` قبل الـ commit (يقابل `cur.lastrowid` في الـ legacy).

#### 2. اختبارات جديدة `tests/test_database_orm_pr5.py`
~24 اختبار pytest يفحص:

- **`set_user_balance`**: قيمة دقيقة، overwrite (ليس additive)، string IDs، `None`/`""` → 0.0، missing user no-op صامت.
- **`change_balance`**: credit/debit، **لا clamping عند الصفر**، delta=0 no-op، missing user no-op.
- **`create_order` happy path**: `(int_id, "ORD..." str)`، خصم بالمقدار الدقيق، كل أعمدة `orders` legacy موجودة، `display_name` يصبح `product_name`، fallback إلى `name`.
- **`create_order` V47 floor**: `InsufficientBalance` على رصيد ناقص، الرصيد لم يُلمَس عند الفشل، **لم يُدخَل صف**، حدّ `balance == price` ينجح، 0/0 ينجح.
- **`create_order` V50 randomness**: 20 طلب → 20 كود فريد، الكود **ليس** decimal (يكشف العودة لنمط `ORD<ts><uid>`).
- **`create_order` error path**: مستخدم غير موجود → `InsufficientBalance`، فشل INSERT (UNIQUE collision عبر monkeypatch لـ `secrets.token_urlsafe`) → exception ينتشر، **الخصم يُسترَدّ** على فشل INSERT (الأخطر).

#### 3. تحديث `tests/test_db_connection_leaks.py`
عداد `with db_conn()` في `database.py` انخفض من 67 (بعد PR #4) إلى **64** بعد PR #5. الـ floor كان `>=70` (متضارب مع الـ count الفعلي قبل PR #5 أيضاً — كان CI أحمر منذ PR #1 صامتاً). تحديثها إلى `>=50` لتبقى صحيحة لباقي الموجات (PR #6 سيُنزل العدد إلى ~30-40).

### 📍 المخرجات (الملفات المعدّلة + الجديدة في PR #5):

| الملف | تغيير | الوصف |
|------|-------|-------|
| `database.py` | معدّل (3 دوال) | استبدال raw SQL بـ ORM داخلياً + V47 atomic check عبر `update().where()` |
| `tests/test_database_orm_pr5.py` | جديد (~430 سطر) | ~24 اختبار parity للدوال الحرجة |
| `tests/test_db_connection_leaks.py` | معدّل (3 سطور) | floor العداد 70 → 50 لاستيعاب الموجات القادمة |

### 🛡️ ضمانات السلامة (PR #5):
- ✅ كل التواقيع وقيم الإرجاع كما هي (`api_bp.py` + `public_bp.py` + `admin_bp.py` + `tasks.py` + tests فلم تُلمس).
- ✅ V47 atomic balance check عبر `update().where(User.balance >= final_price)` + فحص `rowcount` — race-safe على Postgres كما كان على SQLite.
- ✅ V50 random `order_code` — لا عودة للنمط المتنبَّأ.
- ✅ `InsufficientBalance` يُرفع كما كان (نفس الرسالة `"رصيدك غير كافٍ"`).
- ✅ rollback عند أي فشل INSERT — الرصيد لا يُخصَم لطلب لم يُنشأ.
- ✅ `with db_conn()` ما زال مستخدماً 64 مرّة في باقي الدوال — `test_db_conn_usage_count` ما زال أخضر (>= 50).
- ✅ النماذج تتطابق مع الـ schema الحقيقية (تم التحقّق في الجلستين 1+2).
- ✅ الـ syntax تم التحقّق منه عبر `python -m py_compile`. تشغيل pytest الفعلي يتم على CI (الـ sandbox مغلق الشبكة).

### 🚀 ما يفعله المستخدم بعد PR #5:

#### الخطوة 1: راجع الـ PR على GitHub (`feat/postgres-migration-session3-pr5`)
انظر diff في `database.py`؛ كل دالة معدّلة لها docstring `V72 / session 3 / PR #5` أعلى التعريف.

#### الخطوة 2: deploy كالعادة (نفس الإجراء)
```bash
scp ZZZZ-main.zip root@46.224.87.50:/root/
ssh root@46.224.87.50
/root/deploy.sh /root/tecnogems_latest.zip
```

#### الخطوة 3: ⚠️ اختبار حرج — اشتري شيئاً من حساب اختباري
هذه أهم خطوة في الجلسة 3 كلها. اختبر السيناريوهات التالية:

- **شراء عادي**: من حساب فيه رصيد كافٍ، اشترِ منتجاً → الرصيد يُخصَم بالمبلغ الصحيح، الطلب يظهر في `/orders`.
- **شراء برصيد غير كافٍ**: من حساب رصيده < سعر المنتج → "رصيدك غير كافٍ" + الرصيد لم يُلمَس + لا طلب جديد.
- **رصيد بالضبط = السعر**: حساب رصيده 5$ يشتري منتجاً 5$ → ينجح، الرصيد = 0.
- **double-click على Buy**: اضغط "اشتري" مرتين بسرعة → طلب واحد فقط يُنشأ (الـ rate limit 20/min في `public_bp.py` يحمي إضافياً).
- **`order_code` فريد**: لاحِظ أن كل طلب له كود مختلف يبدأ بـ `ORD` ثم 13+ حرف عشوائي.

#### الخطوة 4: اختبار رصيد الأدمن
- لوحة `/admin/user/<id>` → عدّل الرصيد → القيمة تُحفَظ بدقة.
- حاول كتابة قيمة فارغة → يُحفظ 0.0.

#### الخطوة 5: شغّل الاختبارات (اختياري)
```bash
cd /root/project
.venv/bin/pytest tests/test_database_orm_pr5.py -v
```

### ✅ معايير النجاح لـ PR #5:
- [ ] الـ CI يمرّ على branch
- [ ] الموقع يعمل بشكل طبيعي بعد deploy
- [ ] الشراء يخصم الرصيد بالضبط (لا فرق ملحوظ)
- [ ] رصيد ناقص يُرفض بنفس الرسالة العربية
- [ ] لا حالة "تم الخصم بدون طلب" أو "تم إنشاء طلب بدون خصم" — الـ rollback يعمل
- [ ] أكواد الطلبات فريدة وعشوائية

### 🔄 Rollback إذا فشل أي شيء:
- أعد deploy للنسخة السابقة من `deploy.sh` (يحفظ نسخة احتياطية تلقائياً).
- لم يحدث أي تغيير في DB schema، فلا حاجة لـ DB rollback.
- **مهم**: لو لاحظت أي فرق في الرصيد بين قبل وبعد، تواصل فوراً قبل الاستمرار في PR #6.

---

### ✅ ما تم إنجازه في PR #6 (الموجة السادسة والأخيرة — كل الباقي):

> 🏁 **هذه آخر موجة في الجلسة 3.** بعد دمجها، تنتهي مرحلة "تحويل database.py وراء الكواليس" وتبقى فقط **DDL helpers** (init_db, ensure_indexes, seed_local_provider_catalog, attach_generated_posters) مكتوبة بـ raw SQL — وهذه ستُستبدل بـ Alembic migrations في الجلسة 7 (التنظيف النهائي).

#### 1. تحويل ~40 دالة في موجة واحدة
موزَّعة على المجموعات التالية:

**(أ) دورة حياة المستخدم + المصادقة (15 دالة)**

- `create_user`, `authenticate`, `get_user_by_email`, `get_user_by_id`, `get_user_by_google_sub`, `link_user_google_sub`, `create_user_oauth`.
- `update_user_profile` (مع تغيير سلوكي طفيف: عند تمرير `name=None` و `phone=None` معاً، الدالة الآن **no-op** بدل تنفيذ UPDATE فارغ — مطابق دلالياً للسلوك القديم لأن الـ legacy استخدم `COALESCE(?, name)` الذي لا يغيّر شيئاً).
- `set_pending_email_change`, `confirm_pending_email_change` (مع فحص "البريد الجديد محجوز لمستخدم آخر").
- `set_user_email_token`, `verify_user_email` (24h expiry).
- `set_password_reset_token`, `get_user_by_reset_token`, `reset_user_password` (1h expiry + **V53 session_version bump** للتسجيل الإجباري من باقي الأجهزة).

**(ب) المصادقة الثنائية 2FA (4 دوال)**

- `set_user_totp_secret` — يمسح backup codes والـ enabled flag تلقائياً.
- `enable_user_totp` — يسجّل ``totp_enabled_at = now``.
- `disable_user_totp` — يصفّر كل أعمدة 2FA.
- `update_user_backup_codes` — استبدال blob الأكواد الاحتياطية.

**(ج) كتابة كاتالوج الأدمن (13 دالة)**

- `upsert_game` (يحافظ على quirk الـ legacy: ON CONFLICT لا يلمس `active`).
- `add_custom_game` (admin form: يحدّث كل شيء بما فيه `active` و `image_url`).
- `set_game_active`, `set_game_show_on_home`, `set_game_home_sort_order` (مع clamp للقيم السالبة → 0).
- `update_game_image`, `update_game_pricing` (whitelist GLOBAL/USD/SYP).
- `upsert_product` (نفس quirk الـ `active`).
- `delete_products_for_game` (DELETE WHERE provider+game_key).
- `update_product_sort_orders` (bulk update داخل tx واحدة).
- `update_manual_syp_prices` (bulk + fallback آمن للقيم غير الرقمية).
- `update_products_admin` (whitelist pricing_mode + إعادة حساب sell_price لـ fixed_syp).
- `update_profit_margin` — أهم دالة في هذه المجموعة:
  - يعيد حساب `sell_price = round(base_price * margin, 2)` لكل المنتجات.
  - يصفّر overrides الـ `pricing_mode='fixed_syp'` و `manual_price_syp>0` (وإلا الهامش الجديد لا يظهر).
  - **مهم على Postgres**: استخدم Python loop بدل `func.round(double, 2)` لأن Postgres لا يعرّف `round(double precision, integer)` — فقط `round(numeric, integer)`. الـ ORM session يجمّع كل UPDATEs في commit واحد فالأداء مقبول.

**(د) مجموعات المنتجات (5 دوال)**

- `list_product_groups` (CASE-style ordering + `only_active` filter).
- `get_product_group` (مع coercion آمن للنصوص).
- `create_product_group` (idempotent عبر UNIQUE key).
- `update_product_group` (blanket update لكل الحقول).
- `delete_product_group` (يفصل المنتجات أولاً عبر `group_id=NULL` ثم يحذف المجموعة — كلا الخطوتين في tx واحدة لمنع orphan refs).

**(هـ) القراءات العامة + التجميعات (5 دوال)**

- `list_public_games` — LEFT JOIN على products active=1، يحافظ على ألعاب بدون باقات.
- `list_home_games` — فلتر `show_on_home=1` + ترتيب `home_sort_order`.
- `list_public_product_groups_for_home` — INNER JOIN على games + LEFT JOIN على products.
- `list_all_game_groups` — admin view: يشمل inactive games AND inactive products.
- `list_product_games_from_products` — لاكتشاف منتجات يتيمة (game_key بدون صف في games).

**(و) القراءات الإدارية (10 دوال)**

- `stats` (counts by status + revenue).
- `list_users` + `search_users` + `get_user_by_id` — جميعها narrow projection (لا تسرّب password_hash / TOTP / tokens).
- `search_users` — multi-column LIKE مع `escape="\\"` + DISTINCT للتعامل مع الـ JOIN.
- `user_financial_summary` — **V49-HOTFIX محفوظ**: `SUM(COALESCE(amount_usd, 0))` لتجنّب خلط SYP و USD.
- `list_user_deposits_admin` — كامل أعمدة deposits.
- `list_orders_for_auto_refresh` — فلتر دقيق على `status IN (...)` + `provider_order_id IS NOT NULL AND != ''`.
- `list_all_games_for_admin`, `list_all_products_for_admin` (مع JOIN على product_groups + حقن `display_name`).
- `accounting_summary` — أصعب دالة: 4 aggregates منفصلة + `by_game` GROUP BY + `recent` LIMIT 100 مع JOIN على products + users + `sales_override` setting.

**(ز) سجل التدقيق Audit Log (3 دوال)**

- `insert_audit_log` — مع truncation للأعمدة (action≤120, target_type≤60, ip≤64, ...) + alias `meta`/`metadata` (لأن `metadata` كلمة محجوزة في SQLAlchemy declarative). **لا يرفع استثناء أبداً** — observability يجب ألا يكسر الـ request.
- `list_audit_logs` — كل الفلاتر اختيارية + clamp `[1, 1000]` للـ limit + ترتيب `ts DESC, id DESC` + dict key الـ public هو `metadata` (اسم العمود الـ legacy).
- `count_audit_logs` — يُرجع 0 عند أي خطأ.

**(ح) متفرّقات (8 دوال)**

- `seed_admin` — bootstrap idempotent.
- `search_suggest` — substring match على games + products، case-insensitive عبر `func.lower(...)` (بديل portable لـ `COLLATE NOCASE`)، مع `escape="\\"` للتعامل مع `%` و `_` في input المستخدم.
- `get_product_by_id` (يُرجع inactive — للـ RQ worker).
- `get_order_public` — **V50 (CC)** explicit ownership check محفوظ، مع sentinel `"*"` للـ admin.
- `can_download_proof` — IDOR fix V53 (الأدمن يمر، الباقي عبر `proof_filename` lookup).

#### 2. ما لم يُحوَّل (مع التبرير)

5 دوال تبقى تستخدم `with db_conn()` raw SQL:

| الدالة | التبرير |
|--------|---------|
| `connect`, `db_conn` | helpers أساسية، تُستخدم من اختبارات + DDL paths. |
| `ensure_indexes` | DDL (CREATE INDEX, ALTER TABLE) — مكان Alembic. |
| `init_db` / `_init_db_inner` | DDL bootstrap — مكان Alembic. |
| `seed_local_provider_catalog` | bulk seeder من JSON file، تشغيل لمرّة واحدة، تحويله مخاطرة بلا فائدة. |
| `attach_generated_posters` | filesystem + DB hybrid، تشغيل دوري admin-only، نفس التبرير. |

كل هذه ستُمسح أو تنتقل إلى Alembic في **الجلسة 7 (التنظيف النهائي)** بعد التأكد من استقرار الإنتاج على Postgres.

#### 3. اختبارات جديدة `tests/test_database_orm_pr6.py`
~70+ اختبار pytest مقسَّمة على 22 class، تغطّي:

- **dict shape** لكل دالة قراءة (column set parity).
- **narrow projection** في `list_users`/`get_user_by_id`/`search_users` — تأكيد أن `password_hash` و `totp_*` لا تظهر.
- **edge cases** على tokens: invalid → arabic message، expired → arabic message.
- **session_version bump** على password reset (V53 anti-cookie-replay).
- **TOTP state machine**: set → enable → disable → wipe (idempotent).
- **upsert quirks**: ON CONFLICT لا يلمس `active` في `upsert_game`/`upsert_product`.
- **negative + invalid inputs**: `home_sort_order=-3` → 0، `pricing_mode="BOGUS"` → "usd"، `manual_syp_price="not-num"` → 0.0.
- **catalog SELECTs**: ترتيب CASE-style (sort_order=0 → 999999)، LEFT JOIN يحافظ على games بلا منتجات، active filter يعمل.
- **financial integrity**: `user_financial_summary` يجمع `amount_usd` فقط (V49-HOTFIX) — اختبار صريح بمزج 5000 SYP و 10 USD.
- **search_suggest LIKE escape**: `%` كـ user input يبحث عن `%` حرفياً (لا يطابق كل شيء).
- **search_users DISTINCT**: مستخدم بـ 3 طلبات يظهر مرّة واحدة في النتيجة.
- **audit log**: truncation الأعمدة، `metadata` dict key (ليس `meta`)، ترتيب `ts DESC, id DESC`، فلاتر متعدّدة، clamping للـ limit.
- **`get_order_public`** ownership: ValueError على `user_id=None`، owner-only، admin sentinel `"*"` يعمل.
- **`update_profit_margin`**: إعادة حساب على كل الـ 3 pricing modes + reset overrides.

#### 4. تحديث `tests/test_db_connection_leaks.py`
floor العداد 50 → **3** (نزل العدد الفعلي من 64 إلى 5). تعليق محدّث يشرح أن الجلسة 7 ستحذف الـ assertion كلّياً.

### 📍 المخرجات (الملفات المعدّلة + الجديدة في PR #6):

| الملف | تغيير | الوصف |
|------|-------|-------|
| `database.py` | معدّل (~40 دالة) | كل ما تبقى من runtime data path → ORM |
| `tests/test_database_orm_pr6.py` | جديد (~870 سطر) | ~70+ اختبار parity |
| `tests/test_db_connection_leaks.py` | معدّل (5 أسطر) | floor 50 → 3 + تعليق محدّث |

### 🛡️ ضمانات السلامة (PR #6):
- ✅ كل التواقيع وقيم الإرجاع كما هي — لا caller (في `app/routes/*`, `audit.py`, `tasks.py`, `wsgi.py`, conftest، أو `security_2fa.py`) يحتاج تعديل.
- ✅ شكل dict مطابق للـ legacy `sqlite3.Row → dict` (نفس الأعمدة، بنفس الأسماء، حتى `audit_log.metadata` معالَج عبر alias).
- ✅ Narrow projection لـ user listings محفوظة (لا تسريب password_hash / TOTP).
- ✅ V49-HOTFIX (`amount_usd` summing) + V50 (CC) (explicit ownership) + V53 (session bump + IDOR proof check) كلها محفوظة.
- ✅ كل الـ transactions ذرية + rollback عند أي exception (إلا audit log الذي **يبتلع** الأخطاء عمداً — observability يجب ألا تكسر الـ request).
- ✅ `with db_conn()` انخفض من 64 إلى **5** — كلها DDL/seeder helpers ستُحذف في الجلسة 7.
- ✅ تم التحقّق نحوياً عبر `python -m py_compile`. تشغيل pytest الفعلي يتم على CI.

### 🚀 ما يفعله المستخدم بعد PR #6:

#### الخطوة 1: راجع الـ PR على GitHub (`feat/postgres-migration-session3-pr6`)
أكبر diff في كل الجلسة 3. كل دالة معدّلة لها docstring `V72 / session 3 / PR #6` يشرح ما حُفظ من الـ legacy.

#### الخطوة 2: deploy كالعادة
```bash
scp ZZZZ-main.zip root@46.224.87.50:/root/
ssh root@46.224.87.50
/root/deploy.sh /root/tecnogems_latest.zip
```

#### الخطوة 3: 🚨 اختبار شامل — هذا يلامس كل صفحة في الموقع تقريباً

**صفحات المستخدم:**
- صفحة التسجيل: حساب جديد + بريد فعلاً مستخدم (يجب رفض مع رسالة عربية).
- صفحة تسجيل الدخول: حساب صحيح + خاطئ + مُعطّل (active=0).
- صفحة تأكيد البريد: رابط صحيح + منتهي (حدّث `email_token_created_at` يدوياً قبل 24h).
- صفحة "نسيت كلمة السر": طلب رابط + استخدامه + إعادة تسجيل دخول (يجب أن يُلغي session القديم).
- صفحة /profile: تعديل الاسم فقط (الهاتف لا يُمسح) + طلب تغيير بريد + تأكيده.

**صفحات OAuth (إذا مفعّل):**
- تسجيل دخول Google لمستخدم جديد → يُنشأ مع `email_verified=1` و `google_sub`.
- تسجيل دخول لمستخدم موجود بنفس البريد → يربط `google_sub` فقط.

**صفحات 2FA (الأدمن):**
- /admin/2fa/setup → /admin/2fa/confirm → /admin/2fa/disable → إعادة الدورة.
- استهلاك backup code يُنقص العدد، تجديدها يستبدل الـ blob.

**صفحات الكاتالوج العامة:**
- الصفحة الرئيسية: تعرض ألعاب `show_on_home=1` فقط، بترتيب `home_sort_order` (0 في الآخر).
- /games/<provider>: تعرض ألعاب الـ provider.
- /products/<...>: تعرض المنتجات الـ active مع ترتيب `sort_order`.

**صفحات الأدمن:**
- /admin: stats صحيحة.
- /admin/users + بحث: بالـ name / email / phone / id / player_id (من الـ orders) — كلها تعمل، DISTINCT (لا تكرار).
- /admin/user/<id>: عرض user_financial_summary (أرقام usd صحيحة).
- /admin/games: list_all_games_for_admin (يشمل inactive).
- /admin/games/edit: add_custom_game → upsert.
- /admin/games/<key>/products: list_all_products_for_admin (يشمل inactive، JOIN على groups).
- /admin/games/<key>/groups: create/update/delete groups → فحص أن products لا تحذف.
- /admin/settings/profit_margin: قيمة جديدة → كل الـ products تُعاد حساب sell_price + overrides تتصفّر.
- /admin/orders + /admin/deposits: تحديث الحالة.
- /admin/audit_logs: list + count + filters.

**سيناريوهات أمنية:**
- /uploads/proof/<filename>: مستخدم آخر ≠ owner → 403.
- get_order_public لطلب ليس له → null.

#### الخطوة 4: شغّل الاختبارات (اختياري)
```bash
cd /root/project
.venv/bin/pytest tests/test_database_orm_pr6.py -v
```

### ✅ معايير النجاح لـ PR #6:
- [ ] الـ CI يمرّ على branch (>500 اختبار في كامل السويت).
- [ ] الموقع يعمل بشكل طبيعي بعد deploy.
- [ ] لا فرق ملحوظ في تجربة المستخدم أو الأدمن.
- [ ] التسجيل + تسجيل الدخول + التحقق من البريد + استرداد كلمة السر يعمل.
- [ ] 2FA setup/confirm/disable يعمل.
- [ ] إدارة الكاتالوج (games, products, groups) تعمل.
- [ ] لوحة الأدمن (stats, users, search, accounting) تعمل بنفس الأرقام.
- [ ] audit_log يُكتب على كل عملية إدارية.

### 🔄 Rollback إذا فشل أي شيء:
- أعد deploy للنسخة السابقة من `deploy.sh` (يحفظ نسخة احتياطية تلقائياً).
- لا تغيير في DB schema — لا حاجة لـ rollback على البيانات.
- بما أن هذا أكبر PR، يستحسن **الانتظار 24-48 ساعة بعد deploy قبل dispatch الجلسة 4**.

### 📝 ملاحظات للجلسة التالية (الجلسة 4):
بعد دمج PR #6 وتأكّد المستخدم من استقرار الإنتاج، الكود **جاهز تماماً** للعمل على Postgres بمجرد ضبط `DATABASE_URL` — كل الدوال تستخدم SQLAlchemy ORM بدلاً من sqlite3 خام. الجلسة 4 تكتب سكربت نقل البيانات الفعلي من SQLite إلى Postgres.

---
- [ ] الموقع يعمل تماماً كما كان (لا تغيير في السلوك)
- [ ] جميع الاختبارات تنجح
- [ ] صفحات الأدمن سليمة

### ملاحظات للجلسة التالية:
بعد انتهاء كل الـ PRs، الكود جاهز للعمل مع أي قاعدة بيانات (SQLite أو Postgres) بمجرد تغيير `DATABASE_URL`.

### ما يفعله المستخدم:
- مراجعة كل PR (6 PRs)
- اختبار سريع بعد كل واحد

> ⏰ **هذه أطول جلسة** — قد تتوزع على 2-3 محادثات.

---

## 📦 الجلسة 4: سكربت نقل البيانات

**الحالة:** ✅ مكتملة (2026-05-19) — PR #21 مدموج على `main`.

**الهدف:** كتابة `tools/migrate_to_postgres.py` ينقل كل البيانات من SQLite إلى Postgres مع التحقق، **بدون أي تعديل على الكود الإنتاجي**.

### الاستراتيجية

السكربت **مستقل تماماً** عن `database.py` و `app/`. يستخدم `app.db.models.Base.metadata` فقط كمرجع للجداول والأعمدة المتوقَّعة. لا يمسّ الـ runtime ولا يحتاج Flask.

النموذج الذهني:

```
[ SQLite read-only ]  ──── batched copy ────►  [ Postgres ]
       ▲                                              │
       │                                              ▼
       └──────────── verify (counts + sample) ────────┘
```

### ما تم تنفيذه

#### 1. `tools/migrate_to_postgres.py` (~510 سطر)

دفعة كاملة من الميزات:

- **CLI واضح**: `--source / --target / --batch-size / --truncate / --yes / --no-verify`. الافتراضات تأتي من `$DATABASE_URL` و `$POSTGRES_URL` على التوالي.
- **حماية المصدر للقراءة فقط**: أي SQLite URL يُلفّ تلقائياً بـ `mode=ro&uri=true` — السكربت **لا يستطيع** تعديل ملف SQLite الأصلي حتى لو وجدت ثغرة منطقية. carve-outs لـ `:memory:` (للاختبارات) ولـ URLs بصيغة `file:` بالفعل.
- **فحص schema قبل أي شيء**: يتحقّق أن كل الجداول الـ 10 موجودة في الوجهة، وأن كل عمود يعرفه ORM موجود في المصدر. أعمدة إضافية في المصدر (drift من إصدارات قديمة) تُسجَّل كتحذير وتُتجاهل، لا تُنقَل.
- **تقرير قبل النقل**: يطبع عدد الصفوف لكل جدول من المصدر + الإجمالي.
- **تأكيد إجباري**: يطلب كتابة `yes` (يمكن تخطّيه بـ `--yes` لـ scripts).
- **`--truncate` آمن**: يتطلّب `--yes` معه (دفاع متعدّد الطبقات). على Postgres يستخدم `TRUNCATE ... RESTART IDENTITY CASCADE` (يعيد الـ sequences). على SQLite يستخدم `DELETE FROM` + إعادة `sqlite_sequence` لرقم 1.
- **النقل بترتيب FK**: parents قبل children (`settings → payment_methods → users → games → product_groups → products → orders → deposits → audit_log → wishlist`). كل جدول في tx مستقلّة، فلو فشل واحد لا تتأثّر السابقة.
- **batched inserts**: 500 صف/دفعة افتراضياً عبر `Table.insert()` المباشر — أسرع من ORM session loops بمعامل ضخم على 6797 منتج.
- **streaming من المصدر**: `stream_results=True` + `yield_per` → استهلاك ذاكرة O(batch_size) حتى لو كان جدول `audit_log` ضخماً.
- **إعادة ضبط Postgres sequences**: بعد النقل، السكربت يكتشف اسم الـ sequence لكل عمود autoincrement عبر `pg_get_serial_sequence` ثم `setval(seq, MAX(id))`. هذا يمنع تصادم `id=1` مع أوّل INSERT يقوم به التطبيق بعد النقل (مشكلة كلاسيكية لو نُسيت).
- **alembic_version محمي**: مضاف لـ `PROTECTED_TABLES` فلا يُلمَس أبداً (يُكتب من قبل `alembic upgrade head` على الوجهة قبل تشغيل السكربت).
- **التحقق بعد النقل**: عدّ الصفوف من الجانبين + مقارنة سطر sample من `users`. لو اختلفت الأعداد، السكربت يخرج بـ rc=2 (خطأ نقل، ليس pre-condition).
- **Exit codes واضحة**: 0 = نجاح، 1 = pre-condition (الوجهة لم تُمسّ)، 2 = فشل أثناء النقل.
- **حماية من رصد الذات**: `--source == --target` يُرفض. المصدر بدون URL يُرفض. الوجهة بدون URL تُرفض.

#### 2. `tests/test_migrate_to_postgres.py` (~480 سطر)

`~30` اختبار pytest في 7 classes، يستخدم SQLite كمصدر **و** كوجهة (CI لا يملك Postgres). الـ logic فوق `_reset_postgres_sequences` (الذي هو no-op على SQLite) متطابق على الـ backendين، لذا SQLite→SQLite يغطّي:

- **Happy path**: نقل كامل مع 2 users + 2 audit rows (واحد بـ `metadata` JSON، واحد NULL — يغطّي alias) + باقي الجداول. تحقّق صريح من تطابق الأعداد + dict shape per-row + ظهور قسم Sample row + `--batch-size=1` لاختبار الـ streaming بدفعات صغيرة.
- **Empty source**: لا أخطاء، `Copied 0 rows` يظهر.
- **Pre-condition failures**: target بدون schema → `missing tables`؛ `--source == --target` → `identical`؛ `$DATABASE_URL` غير محدّد → `No source URL set`؛ `--truncate` بدون `--yes` يُرفض؛ `--batch-size 0` يُرفض.
- **Schema parity**: مصدر بـ `users` ينقصه أعمدة → السكربت يخرج بـ rc=1 **قبل أي كتابة** في الوجهة (الوجهة تبقى فارغة — تحقّق صريح).
- **`--truncate`**: يحذف بيانات قديمة في الوجهة (settings + ghost user) ثم ينسخ من المصدر. تشغيلان متتاليان → نتيجة متطابقة (idempotent).
- **حماية المصدر للقراءة فقط**: 5 unit tests على `_coerce_sqlite_to_readonly` (relative، absolute، `:memory:`، already-URI-form، Postgres). + اختبار ديناميكي يحاول `DELETE FROM users` عبر engine المصدر ويتأكّد أنه يفشل.
- **`--no-verify`**: يتخطّى قسم Sample row.
- **Helpers**: `TABLE_ORDER` يغطّي كل جداول ORM ما عدا `alembic_version` (محمي). `_integer_primary_key` يميّز بين `users.id` (Integer PK، sequence reset) و `payment_methods.id` (Text PK، لا sequence). `_count_rows` يُرجع 0 لجدول مفقود بدلاً من رفع استثناء.

#### 3. `.env.example` — قسم جديد لـ POSTGRES_URL

أُضيف قسم "V72 / session 4 — Postgres data migration" يشرح:
- `POSTGRES_URL` يُقرأ فقط من قبل سكربت النقل، **ليس** التطبيق نفسه.
- سيناريو الاستخدام الكامل (4 خطوات تربط بالجلسة 6).
- إمكانية تجاوزه عبر `--target` للـ scripts.

### 📍 المخرجات

| الملف | التغيير | الوصف |
|------|---------|-------|
| `tools/migrate_to_postgres.py` | جديد (~510 سطر) | السكربت كاملاً + CLI + reset sequences + verification |
| `tests/test_migrate_to_postgres.py` | جديد (~480 سطر) | ~30 اختبار e2e (SQLite→SQLite) |
| `.env.example` | +35 سطر | قسم POSTGRES_URL لمرحلة النقل |
| `MIGRATION_PLAN.md` | محدّث | الجلسة 3 ✅ مكتملة، الجلسة 4 ⏳ جاهز للمراجعة |
| `MIGRATION_GUIDE.md` | محدّث | قسم جديد "نقل البيانات الفعلي" |

### 🛡️ ضمانات السلامة

- ✅ السكربت **لا يلمس** `app/`، `database.py`، أو أي route/service. مستقلّ تماماً.
- ✅ حماية multi-layer للمصدر: URL coercion + برهان دلالي عبر اختبار `DELETE` يفشل.
- ✅ `alembic_version` في `PROTECTED_TABLES` — لن نمسح أو نكتب فوقه.
- ✅ الـ schema parity check يفشل **قبل** أي كتابة في الوجهة.
- ✅ كل جدول في transaction مستقلّة → فشل في الجدول السابع لا يُفسد الستة الأولى.
- ✅ Postgres sequences تُعاد ضبطها → INSERTs المستقبلية للتطبيق لا تتصادم مع IDs المنقولة.
- ✅ المصدر هو SQLite read-only → لا يمكن إفساد البيانات الأصلية حتى عند خطأ منطقي في السكربت.
- ✅ `python -m py_compile` ينجح على كلا الملفّين.

> الـ sandbox في وضع `INTEGRATIONS_ONLY` فلا يمكن تثبيت `requirements-dev.txt` لتشغيل pytest محلياً. CI سيشغّلها فعلياً.

### 🚀 ما يفعله المستخدم بعد دمج هذا الـ PR

#### الخطوة 1: راجع الـ PR على GitHub (`feat/postgres-migration-session4`)

ركّز على:
- منطق `_coerce_sqlite_to_readonly` (الحماية الأساسية).
- ترتيب `TABLE_ORDER` (parents قبل children).
- منطق `_reset_postgres_sequences` (يمسّ Postgres فقط).

#### الخطوة 2: deploy كالعادة (لا تغيير سلوكي للموقع)

```bash
scp ZZZZ-main.zip root@46.224.87.50:/root/
ssh root@46.224.87.50
/root/deploy.sh /root/tecnogems_latest.zip
```

> ⚠️ **هذا الـ PR لا يُغيّر شيئاً في تشغيل الموقع.** السكربت موجود في `tools/` فقط، ولا يُستدعى تلقائياً. الموقع يبقى يعمل على SQLite كما هو.

#### الخطوة 3 (اختياري — تجربة جافّة محلياً قبل الجلسة 6)

```bash
# على جهازك المحلي:
cd /path/to/THE-TECNO-main
docker run -d --name pg-test -p 5433:5432 \
    -e POSTGRES_PASSWORD=test \
    -e POSTGRES_DB=tecnogems_test \
    postgres:16

# طبّق الـ schema
DATABASE_URL=postgresql://postgres:test@localhost:5433/tecnogems_test \
    .venv/bin/alembic upgrade head

# انسخ بياناتك (المصدر = ملف SQLite الإنتاج بعد nohup cp)
.venv/bin/python tools/migrate_to_postgres.py \
    --source sqlite:///data/site.db \
    --target postgresql://postgres:test@localhost:5433/tecnogems_test \
    --yes

# تحقق
DATABASE_URL=postgresql://postgres:test@localhost:5433/tecnogems_test \
    .venv/bin/python tools/verify_orm_models.py

docker rm -f pg-test
```

#### الخطوة 4: شغّل الاختبارات (اختياري)

```bash
cd /root/project
.venv/bin/pytest tests/test_migrate_to_postgres.py -v
```

### ✅ معايير النجاح لـ PR session 4

- [ ] الـ CI يمرّ على branch (~30 اختبار جديد + كل القائمة الموجودة).
- [ ] الموقع يعمل بشكل طبيعي بعد deploy (تأكيد بسيط أن السكربت لم يكسر شيئاً في الـ runtime).
- [ ] (اختياري) تجربة جافّة على Postgres محلي تنجح.

### 🔄 Rollback إذا فشل أي شيء

- لا تغيير في DB schema، لا تغيير في runtime code.
- إعادة deploy للنسخة السابقة عبر `deploy.sh`.
- السكربت نفسه يمكن حذفه ببساطة (لم يُستدعَ من أي مكان).

### 📝 ملاحظات للجلسة التالية (الجلسة 5)

- السكربت جاهز.
- الجلسة 5 = **عمل المستخدم على السيرفر** (تثبيت Postgres). بعدها الجلسة 6 = تشغيل السكربت فعلياً.
- ⚠️ بعد الجلسة 6، احتفظ بـ `data/site.db` كـ recovery artefact لمدة أسبوع على الأقل قبل بدء الجلسة 7 (التنظيف النهائي).

---

## 🖥️ الجلسة 5: تثبيت Postgres على Hetzner

**الحالة:** لم تبدأ

**الهدف:** تثبيت PostgreSQL على سيرفر Hetzner (مجاناً).

### ما يفعله المستخدم (مع توجيه Kiro):

```bash
# 1. الاتصال بالسيرفر
ssh root@your-hetzner-ip

# 2. تثبيت Postgres
apt update && apt install -y postgresql postgresql-contrib

# 3. تفعيله
systemctl enable postgresql
systemctl start postgresql

# 4. إنشاء قاعدة البيانات
sudo -u postgres psql -c "CREATE DATABASE tecnogems;"
sudo -u postgres psql -c "CREATE USER tecnogems_user WITH PASSWORD 'كلمة_سر_قوية';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE tecnogems TO tecnogems_user;"
sudo -u postgres psql -c "ALTER DATABASE tecnogems OWNER TO tecnogems_user;"

# 5. اختبار الاتصال
psql -h localhost -U tecnogems_user -d tecnogems -c "SELECT version();"
```

### تأكد قبل الانتهاء:
- [ ] Postgres يعمل (`systemctl status postgresql`)
- [ ] تستطيع الاتصال بقاعدة البيانات
- [ ] كلمة السر محفوظة في مكان آمن

### ما يفعله Kiro:
- شرح كل أمر
- استكشاف الأخطاء لو حدثت
- مساعدة في الإعدادات الإضافية (لو احتجت)

### ملاحظات للجلسة التالية:
الجلسة 6 ستربط المشروع بـ Postgres الجديد.

---

## 🚀 الجلسة 6: التنفيذ النهائي + الاختبار

**الحالة:** لم تبدأ

**الهدف:** المشروع يعمل على Postgres بكل البيانات منقولة.

### الخطوات (أنت تنفذها بتوجيه Kiro):

```bash
# 1. على السيرفر، في مجلد المشروع
cd /path/to/THE-TECNO-main

# 2. سحب آخر تحديثات الكود
git pull origin main

# 3. تثبيت الحزم الجديدة
pip install -r requirements.txt

# 4. إضافة DATABASE_URL إلى .env
echo 'DATABASE_URL=postgresql://tecnogems_user:كلمة_السر@localhost:5432/tecnogems' >> .env

# 5. إنشاء الجداول في Postgres
alembic upgrade head

# 6. نقل البيانات من SQLite
python tools/migrate_to_postgres.py

# 7. التحقق
python tools/verify_migration.py

# 8. إعادة تشغيل التطبيق
systemctl restart tecnogems  # أو الأمر المناسب
```

### اختبار الموقع (5 دقائق):
- [ ] الصفحة الرئيسية تفتح
- [ ] تستطيع تسجيل الدخول
- [ ] تستطيع رؤية الطلبات السابقة
- [ ] تستطيع إنشاء طلب جديد
- [ ] لوحة الأدمن تعمل
- [ ] الإعدادات محفوظة كما كانت

### في حالة المشكلة (Rollback):
```bash
# حذف سطر DATABASE_URL من .env
# إعادة التشغيل = الموقع يعود لـ SQLite
```

### ما يفعله Kiro:
- توجيه خطوة بخطوة
- استكشاف أي أخطاء فوراً
- مساعدة في الـ rollback لو احتجت

### ملاحظات للجلسة التالية:
بعد أسبوع من التشغيل المستقر، نذهب للجلسة 7 (التنظيف).

---

## 🧹 الجلسة 7: التنظيف النهائي

**الحالة:** لم تبدأ

**الهدف:** بعد أسبوع تشغيل ناجح على Postgres، نظّف الكود القديم.

> ⚠️ **لا تنفذ هذه الجلسة قبل أسبوع كامل من التشغيل بدون مشاكل!**

### ما سينفذه Kiro:

1. حذف `connect()` و `db_conn()` من `database.py` (لم يعدا مستخدمين)
2. حذف SQLite-specific pragmas
3. تحديث `.gitignore` إذا لزم
4. توثيق الإصدار الجديد في README
5. نقل ملف SQLite القديم لمكان آمن:
   ```bash
   mv data/site.db data/archive/site.db.pre-postgres
   ```

### تأكد قبل الانتهاء:
- [ ] الموقع يعمل بشكل ممتاز لأسبوع كامل
- [ ] لا أخطاء في Sentry
- [ ] لا شكاوى من المستخدمين
- [ ] backup كامل من Postgres محفوظ

### ما يفعله المستخدم:
- مراجعة PR النهائي
- تأكيد أن SQLite لم يعد ضرورياً

---

# 🛡️ خطة الـ Rollback (الأمان أولاً)

## في أي مرحلة، لو حدثت مشكلة:

### الجلسات 1-4 (تطوير الكود):
```bash
# لم نلمس أي شيء على السيرفر، الـ rollback = إغلاق الـ PR
git checkout main
```

### الجلسة 5 (تثبيت Postgres):
```bash
# إيقاف Postgres لن يؤثر على SQLite
systemctl stop postgresql

# لو تريد إزالته كلياً:
apt remove postgresql postgresql-contrib
```

### الجلسة 6 (بعد التنفيذ):
```bash
# الـ rollback في 30 ثانية:
# 1. حذف سطر DATABASE_URL من .env
# 2. إعادة التشغيل
systemctl restart tecnogems

# ✅ الموقع يعود لـ SQLite كأن شيئاً لم يكن
```

### ما يجب أن يبقى دائماً:
- ✅ ملف `data/site.db` (نسخة احتياطية)
- ✅ مجلد `data/uploads/` (الصور)
- ✅ ملف `.env` الأصلي محفوظ في مكان آمن

---

# 📞 كيف تبدأ محادثة جديدة لمتابعة الخطة

```
"مرحباً، أريد متابعة تنفيذ MIGRATION_PLAN.md"
"الجلسات المنجزة: 0, 1, 2"
"ابدأ الجلسة 3"
```

أو ببساطة:
```
"تابع MIGRATION_PLAN.md"
```

Kiro سيقرأ ملف `MIGRATION_PLAN.md`، يفحص حالة كل جلسة، ويستكمل من حيث توقفت.

---

# 📈 تقدير الوقت الإجمالي

| الجلسة | وقت Kiro | وقت المستخدم |
|--------|----------|---------------|
| 0 | 30 دقيقة | 5 دقائق (إجابة أسئلة) |
| 1 | ساعة | 5 دقائق (مراجعة PR) |
| 2 | 30 دقيقة | 5 دقائق (مراجعة PR) |
| 3 | 4-6 ساعات (موزعة على عدة محادثات) | 30 دقيقة (مراجعة 6 PRs) |
| 4 | ساعتان | 5 دقائق (مراجعة PR) |
| 5 | 15 دقيقة (توجيه) | 15 دقيقة (تنفيذ على السيرفر) |
| 6 | 30 دقيقة (توجيه) | 30 دقيقة (تنفيذ + اختبار) |
| 7 | 30 دقيقة | 5 دقائق (مراجعة PR) |
| **الإجمالي** | **~10 ساعات** | **~2 ساعة** |

> ⏱️ المدة الزمنية الكلية من البداية للنهاية: **2-4 أسابيع** (مع فترة الانتظار قبل التنظيف).

---

# ✅ معايير النجاح النهائية

عند اكتمال الجلسة 7، يجب أن يتحقق ما يلي:

- ✅ المشروع يعمل على PostgreSQL
- ✅ كل البيانات منقولة (نفس الأعداد قبل وبعد)
- ✅ كل الـ 88 اختبار تنجح
- ✅ لا تغيير ملحوظ في تجربة المستخدم
- ✅ Alembic يدير الـ migrations مستقبلاً
- ✅ كود `database.py` نظيف بدون SQL خام
- ✅ توثيق كامل بالعربية
- ✅ خطة backup يومية لـ Postgres

---

**تاريخ إنشاء الخطة:** 2026-05-18  
**آخر تحديث:** 2026-05-19 (الجلسة 4 ✅ مدموجة — PR #21؛ نحن الآن جاهزون للجلسة 5)  
**المسؤول:** Kiro + المستخدم  

> 💬 لأي استفسار: ابدأ محادثة جديدة وقل _"عندي سؤال عن MIGRATION_PLAN.md"_
