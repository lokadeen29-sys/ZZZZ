# 🔧 دليل Alembic — إدارة هجرات قاعدة البيانات

> **هذا الدليل يكمّل `MIGRATION_PLAN.md`.**
> الخطة تشرح **لماذا** ننقل من SQLite إلى Postgres.
> هذا الملف يشرح **كيف** نضيف ونطبّق تغييرات الـ schema يومياً.

---

## 📋 ما هو Alembic؟

Alembic هو أداة إدارة الهجرات (migrations) المرافقة لـ SQLAlchemy.
بدلاً من تعديل `database.py` يدوياً عبر `ALTER TABLE` ملفوفة بـ `try/except`،
نكتب **سكربت هجرة لكل تغيير**، ونطبّقه بأمر واحد:

```bash
alembic upgrade head
```

كل سكربت يحتوي على:
- `upgrade()` — كيفية تطبيق التغيير.
- `downgrade()` — كيفية التراجع عنه.

ملفّات الهجرات تُحفَظ في `migrations/versions/` ويجب أن تُرفع جميعها على git.

---

## 🗂️ بنية المجلدات

```
THE-TECNO-main/
├── alembic.ini                           # الإعدادات الرئيسية
├── migrations/
│   ├── env.py                            # يربط Alembic بـ app.db.base
│   ├── script.py.mako                    # قالب توليد ملفات الهجرات
│   ├── README                            # ملاحظات سريعة
│   └── versions/
│       ├── .gitkeep
│       └── 20260518_0000_0001_baseline_schema.py   ← الهجرة الأولى (baseline)
└── app/
    └── db/
        ├── base.py    ← engine + Base + DATABASE_URL
        ├── models.py  ← ORM models — مرجع `--autogenerate`
        └── session.py
```

---

## 🚀 الأوامر الأساسية

> **شغّل كل الأوامر من جذر المستودع** (المجلد الذي يحتوي على `alembic.ini`):
> ```bash
> cd /path/to/THE-TECNO-main
> ```

### 1️⃣ معرفة حالة قاعدة البيانات الحالية
```bash
alembic current
```
**يطبع:** رقم الـ revision المطبَّق حالياً، مثل: `0001_baseline (head)`.
إذا لم يُطبع شيء، فالقاعدة لم تُهجَّر بعد (أو ليست تحت إدارة Alembic).

### 2️⃣ تطبيق كل الهجرات حتى آخر إصدار
```bash
alembic upgrade head
```
**هذا هو الأمر الأكثر استخداماً.** يُنفَّذ بعد كل `git pull` على الخادم.

### 3️⃣ التراجع خطوة واحدة
```bash
alembic downgrade -1
```
يُلغي آخر هجرة. **استخدمه فقط إذا تأكدت** أن `downgrade()` في تلك الهجرة سليم.

### 4️⃣ معرفة كل الهجرات الموجودة
```bash
alembic history
```

### 5️⃣ توليد سكربت SQL بدل التطبيق المباشر
```bash
alembic upgrade head --sql > /tmp/migration.sql
```
مفيد للمراجعة قبل التنفيذ على الإنتاج، أو للتطبيق اليدوي عبر `psql`.

---

## ✏️ كيف تضيف هجرة جديدة؟

### الحالة الأشيع: غيّرتُ ORM model (في `app/db/models.py`)

مثال: أضفت عمود `phone_verified` على `User`.

**1.** عدّل النموذج:
```python
class User(Base):
    ...
    phone_verified = Column(Integer, nullable=False, default=0)
```

**2.** ولّد الهجرة تلقائياً:
```bash
alembic revision --autogenerate -m "add phone_verified to users"
```
يُنشأ ملف جديد في `migrations/versions/` باسم مثل:
`20260601_1430_a1b2c3d4_add_phone_verified_to_users.py`

**3.** ⚠️ **افتح الملف وراجعه قبل أي شيء.**
Autogenerate يفوّت أحياناً:
- القيم الافتراضية على مستوى الخادم (`server_default`).
- قيود `CHECK`.
- إعادة تسمية الأعمدة (يحوّلها إلى DROP + ADD، مما يفقد البيانات!).

**4.** طبّقها محلياً واختبر:
```bash
alembic upgrade head
pytest tests/test_alembic.py -v
pytest tests/test_orm_models.py -v
```

**5.** ارفع الملف الجديد على git مع نموذجك المعدَّل:
```bash
git add app/db/models.py migrations/versions/20260601_1430_*.py
git commit -m "feat(db): add phone_verified column"
```

---

### الحالة الثانية: تغيير لا يحتاج ORM model (مثلاً seed جديد)

```bash
alembic revision -m "seed default countries"
```
يُولِّد ملفاً فارغاً. عبّئ `upgrade()` و`downgrade()` يدوياً:

```python
def upgrade() -> None:
    countries = sa.table(
        "countries",
        sa.column("code", sa.Text),
        sa.column("name", sa.Text),
    )
    op.bulk_insert(countries, [
        {"code": "SY", "name": "Syria"},
        {"code": "SA", "name": "Saudi Arabia"},
    ])

def downgrade() -> None:
    op.execute("DELETE FROM countries WHERE code IN ('SY', 'SA')")
```

---

## 🛡️ نصائح ذهبية

### ⚠️ **لا تعدّل ملف هجرة مرفوع على git.**
بمجرّد دمج الـ PR، الهجرة "تاريخ مدفون".
لتصحيح خطأ، أضف هجرة جديدة (`alembic revision -m "fix ..."`) ودَع الأقدم كما هي.

### ⚠️ **اختبر `downgrade()` قبل الدمج.**
```bash
alembic upgrade head
alembic downgrade -1
alembic upgrade head      # يجب أن يعمل بدون أخطاء
```
لو فشل، فإن `downgrade()` ناقص.

### ⚠️ **أضف الهجرات بترتيب الفروع.**
عند العمل على فرعين متوازيين يضيف كل منهما هجرة، Alembic سيشتكي من
"multiple heads". الحل:
```bash
alembic merge -m "merge heads" head_a head_b
```
أو الأفضل: rebase أحد الفرعين على الآخر.

### ⚠️ **تطبيق الهجرات على بيانات الإنتاج خلال نافذة قصيرة.**
خاصةً على الجداول الكبيرة. إذا الهجرة قد تستغرق دقائق، أعلِم المستخدمين
بصيانة قصيرة أو نفّذها في ساعة قليلة الزحام.

---

## 🚢 عملية الـ deploy على Hetzner

> هذا الإجراء سيُستخدَم في **الجلسة 6** من `MIGRATION_PLAN.md` وما بعدها.

### 🟢 الحالة الحالية (SQLite — لم نُهاجر بعد)

```bash
# 1. ادفع الكود
scp ZZZZ-main.zip root@46.224.87.50:/root/
ssh root@46.224.87.50
/root/deploy.sh /root/tecnogems_latest.zip

# 2. أعلم Alembic أن baseline منطبق فعلاً (أوّل مرة فقط — لأن DB موجود سابقاً)
cd /root/project
.venv/bin/alembic stamp 0001_baseline

# 3. تحقق
.venv/bin/alembic current
# يجب أن يُظهر: 0001_baseline (head)
```

> ✅ **`stamp` لا يُنفّذ `upgrade()`** — فقط يُسجّل في جدول `alembic_version`
> أن هذه الهجرة "مطبَّقة". هذا ما نريده لأن الجداول موجودة بالفعل من
> `database.init_db()`.

### 🟡 عند إضافة هجرات لاحقة (بعد الـ baseline)

```bash
# على جهازك المحلي
git checkout -b feat/add-something
# ... عدّل النماذج، ولّد هجرة، اختبر ...
git push origin feat/add-something
# افتح PR على GitHub، ادمج بعد المراجعة

# على الخادم
ssh root@46.224.87.50
cd /root/project
git pull origin main
.venv/bin/pip install -r requirements.txt    # في حال إضافة حزم
.venv/bin/alembic upgrade head
systemctl restart game-topup tecno-worker
```

### 🔴 في حالة الكارثة (rollback)

```bash
# 1. توقّف عن الكتابة
systemctl stop game-topup tecno-worker

# 2. استرجع نسخة احتياطية من قاعدة البيانات
# (لـ SQLite: استبدل data/site.db بنسخة احتياطية)
# (لـ Postgres: pg_restore)

# 3. تراجع عن الهجرة
.venv/bin/alembic downgrade -1

# 4. أعد التشغيل
systemctl start game-topup tecno-worker
```

> 💡 إذا كان `downgrade()` غير موثوق، الأسرع:
> 1. استرجاع الـ backup كاملاً.
> 2. `alembic stamp <revision_قبل_الهجرة>`.

---

## 🧪 الاختبارات

```bash
# اختبارات Alembic فقط (الأسرع — بدون استيراد التطبيق)
pytest tests/test_alembic.py -v

# اختبارات ORM models (تأكّد من تطابق النماذج مع schema)
pytest tests/test_orm_models.py -v

# الكامل (88 اختبار + اختبارات الهجرة)
pytest -v
```

| الاختبار | ماذا يفحص |
|---------|-----------|
| `test_upgrade_head_creates_all_tables` | بعد `upgrade head` كل الجداول الـ10 موجودة. |
| `test_upgrade_head_creates_expected_indexes` | كل الـ indexes (مثل `idx_orders_user_created`) موجودة. |
| `test_seed_payment_methods_and_settings` | 6 طرق دفع + 11 إعداد افتراضي مزروعة. |
| `test_alembic_version_table_records_revision` | جدول `alembic_version` يحوي `0001_baseline`. |
| `test_downgrade_base_drops_all_tables` | `downgrade base` يحذف كل شيء. |
| `test_round_trip_upgrade_downgrade_upgrade_is_idempotent` | upgrade→downgrade→upgrade يعمل بدون أخطاء. |
| `test_baseline_matches_orm_metadata` | كل الجداول في ORM موجودة في الـ schema. |

---

## 🐘 خصائص Postgres مقابل SQLite

| الميزة | SQLite | Postgres |
|--------|--------|----------|
| `ALTER COLUMN` | ❌ غير مدعوم | ✅ مدعوم |
| `render_as_batch` | مطلوب لتعديل عمود | غير مطلوب |
| `BOOLEAN` فعلي | يُخزَّن كـ INTEGER | نوع منفصل |
| `JSON` فعلي | TEXT يدوي | `JSONB` |
| فحوص قيود (`CHECK`) | محدودة | كاملة |

`migrations/env.py` يُفعّل `render_as_batch=True` تلقائياً عند الكشف على
SQLite. يعني: الهجرات التي تستخدم `op.alter_column(...)` ستعمل على
**كلا** الـbackend دون تعديل.

---

## 📚 مراجع

- التوثيق الرسمي: <https://alembic.sqlalchemy.org/>
- خطة الهجرة الكاملة: [`MIGRATION_PLAN.md`](./MIGRATION_PLAN.md)
- نماذج ORM: [`app/db/models.py`](./app/db/models.py)
- إعدادات Alembic: [`alembic.ini`](./alembic.ini)
- بيئة Alembic: [`migrations/env.py`](./migrations/env.py)

---

**آخر تحديث:** 2026-05-18 (الجلسة 2 من MIGRATION_PLAN.md)
