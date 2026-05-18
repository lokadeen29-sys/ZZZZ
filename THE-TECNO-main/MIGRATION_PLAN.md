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
| 2 | تهيئة Alembic + baseline | ⏸️ لم تبدأ | - |
| 3 | إعادة كتابة database.py بـ ORM | ⏸️ لم تبدأ | - |
| 4 | كتابة سكربت نقل البيانات | ⏸️ لم تبدأ | - |
| 5 | تثبيت Postgres على Hetzner | ⏸️ لم تبدأ | - |
| 6 | تنفيذ النقل + الاختبار | ⏸️ لم تبدأ | - |
| 7 | التنظيف النهائي | ⏸️ لم تبدأ | - |

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

**الحالة:** لم تبدأ

**الهدف:** Alembic يفهم الـ schema الحالي ويصبح جاهزاً لتطبيق التغييرات على Postgres لاحقاً.

### ما سينفذه Kiro:

1. تشغيل `alembic init migrations/`
2. تعديل `alembic/env.py` ليقرأ من `app/db/base.py`
3. إنشاء baseline migration:
   ```bash
   alembic revision --autogenerate -m "baseline"
   alembic stamp head
   ```
4. توثيق في `MIGRATION_GUIDE.md` كيف تضيف migration جديد مستقبلاً.

### تأكد قبل الانتهاء:
- [ ] `alembic upgrade head` يعمل بدون أخطاء على SQLite
- [ ] `alembic downgrade -1` ثم `upgrade head` لا يفقد بيانات
- [ ] جميع الـ 88 اختبار تنجح

### ملاحظات للجلسة التالية:
في الجلسة 3 سنبدأ تدريجياً تحويل دوال `database.py` لتستخدم ORM.

### ما يفعله المستخدم:
- مراجعة PR وقبوله

---

## 🔄 الجلسة 3: تحويل database.py لاستخدام ORM

**الحالة:** لم تبدأ

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
- **PR #1:** الدوال البسيطة (`get_setting`, `set_setting`, `wishlist_*`, `list_payment_methods`)
- **PR #2:** دوال القراءة (`list_games`, `list_products`, `get_game`, `get_product`, `get_user`)
- **PR #3:** دوال الطلبات (`list_orders`, `list_user_orders`, `get_order`, `update_order`)
- **PR #4:** دوال الإيداع (`create_deposit`, `list_deposits_*`)
- **PR #5:** الدوال الحرجة (`create_order`, `change_balance`, `set_user_balance`)
- **PR #6:** الباقي (audit_log, admin functions)

### تأكد بعد كل PR:
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

**الحالة:** لم تبدأ

**الهدف:** كتابة سكربت `tools/migrate_to_postgres.py` ينقل كل البيانات من SQLite إلى Postgres مع التحقق.

### مميزات السكربت:
1. **يفتح SQLite بـ read-only mode** (لا يلمس البيانات الأصلية)
2. **يطبع تقرير** قبل النقل: "سأنقل 145 user, 892 order ..."
3. **يطلب تأكيد** قبل المتابعة
4. **ينقل بـ batches** (500 صف في كل دفعة) للسرعة
5. **يتحقق بعد النقل** أن الأعداد متطابقة
6. **يعمل بترتيب صحيح** (users قبل orders بسبب FK)

### تأكد قبل الانتهاء:
- [ ] السكربت يعمل على نسخة محلية من SQLite ضد Postgres محلي (Docker)
- [ ] التحقق ينجح (نفس الأعداد)
- [ ] `.env.example` محدث بـ `DATABASE_URL`
- [ ] دليل بالعربية في `MIGRATION_GUIDE.md`

### ما يفعله المستخدم:
- مراجعة PR وقبوله
- (اختياري) تجربة السكربت محلياً

### ملاحظات للجلسة التالية:
كل الكود جاهز. الجلسة 5 ستكون **عمل المستخدم على السيرفر**.

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
**آخر تحديث:** 2026-05-18  
**المسؤول:** Kiro + المستخدم  

> 💬 لأي استفسار: ابدأ محادثة جديدة وقل _"عندي سؤال عن MIGRATION_PLAN.md"_
