---
inclusion: manual
---

# Playbook — إصلاح تسرّب اتصالات SQLite

> **متى يُستدعى:** عند تنفيذ البند Critical رقم 2 (SQLite connection leaks).
> **المدة المتوقعة:** يوم كامل (~6-8 ساعات تحرير + اختبار).
> **الخطر الحالي:** 85 `connect()` / 0 `with connect` في `database.py` — أي استثناء في منتصف دالة = connection leak 100%.

---

## 1) السياق الحالي — تشخيص دقيق

### الأرقام المتحقَّقة
```bash
$ grep -c "connect()" database.py
85
$ grep -c "conn.close()" database.py
98
$ grep -c "with connect" database.py
0
```

> **لماذا 98 > 85؟** بعض الدوال تضع `close()` في فرعين (try/else أو in-if-else) — هذا طبيعي. **المشكلة الحقيقية** أن ولا دالة واحدة تستخدم `try/finally` أو context manager. المسار السعيد سليم، المسار غير السعيد يُسرِّب.

### الدوال الأعلى خطورة (تنفّذ استعلامات قد ترفع استثناءً)

- `wishlist_toggle`, `wishlist_list`, `wishlist_has` (سطور 100-140)
- `search_users` (سطر 1269)
- `_autocomplete` (سطر 146)
- `create_order`, `update_deposit` (استعلامات تعديل)
- كل دوال `*_admin_*`
- `accounting_summary` (استعلامات JOIN معقّدة)

### سبب الخطر العملي

gunicorn + gthread: كل thread يحمل اتصالاً مسرَّباً → pool عدد handles محدود → بعد N استثناءات، كل الـthreads مشغولة باتصالات ميتة → الـserver يتعطّل بـ "unable to open database" أو "database is locked".

---

## 2) خطة التنفيذ — 3 مراحل

### المرحلة 1 — Context manager موحّد

أضِف في أعلى `database.py` بعد `import sqlite3`:

```python
from contextlib import contextmanager


@contextmanager
def db_conn():
    """V53 CRITICAL: context manager يضمن إغلاق الاتصال حتى مع الاستثناءات.
    
    استخدم بدلاً من النمط القديم:
        conn = connect()
        ...
        conn.close()   # قد لا يُنفَّذ عند الاستثناء
    
    الصيغة الجديدة:
        with db_conn() as conn:
            ...   # الإغلاق مضمون
    """
    conn = connect()
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass
```

**لا تُعدِّل `connect()` نفسها** — إبقاؤها كـ factory تسمح بـ backward compatibility أثناء الترحيل التدريجي.

### المرحلة 2 — استبدال تدريجي

بدلاً من:
```python
def wishlist_list(user_id):
    conn = connect()
    rows = [dict(r) for r in conn.execute("SELECT ...").fetchall()]
    conn.close()
    return rows
```

استبدل بـ:
```python
def wishlist_list(user_id):
    with db_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT ...").fetchall()]
```

**القاعدة الذهبية:** جميع الدوال القصيرة (< 15 سطر) تُحوَّل بشكل كامل. الدوال الطويلة (create_order, update_deposit) تحتاج حذر — إذا كان فيها `conn.commit()` يدوي أو `BEGIN IMMEDIATE` فاحتفظ به داخل الـwith block.

### المرحلة 3 — معالجة الترتيب الصحيح

**رتّب حسب الخطر والتعقيد:**

1. ✅ **الدوال القصيرة SELECT-only** (~50 دالة): wishlist_*, search_*, list_*, get_*, count_*, _autocomplete
2. ⚠️ **الدوال مع INSERT/UPDATE بسيط** (~25 دالة): change_*, set_*, insert_*, delete_*
3. 🔴 **الدوال المعقّدة مع transactions يدوية** (~10 دالة): `create_order`, `update_deposit`, `update_order`, `create_deposit`, `change_balance`, `admin_approve_deposit`

للمجموعة الثالثة: راجع كل دالة بعناية وتأكد من:
- `BEGIN IMMEDIATE` لا يزال في البداية.
- `conn.commit()` قبل الخروج من الـwith.
- أي `rollback` في except branches لا يزال يُستدعى قبل الـwith يُنهي الاتصال.

### مثال تحويل دالة معقّدة — `create_order`

**قبل:**
```python
def create_order(user_id, ...):
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # ... أسطر كثيرة ...
        conn.commit()
        return order_id
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()   # جيد هنا
```

**بعد (أنظف):**
```python
def create_order(user_id, ...):
    with db_conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            # ... أسطر كثيرة ...
            conn.commit()
            return order_id
        except sqlite3.Error:
            conn.rollback()
            raise
```

### حالة خاصة — `ensure_indexes`

الدالة تشغّل 20+ `CREATE INDEX IF NOT EXISTS` داخل `try/except Exception: pass` لكل واحد. لا تحذف الـtry/except (نمط Idempotent intentional)، فقط لف الكل في `with db_conn()`.

---

## 3) Grep checklist قبل الـmerge

تأكد من أن هذه الأرقام نهائياً:

```bash
# يجب أن يكون 1 فقط (context manager الجديد)
grep -n "^def connect\|^@contextmanager" database.py

# يجب أن يكون 0 (لا استعمال مباشر للـclose)
grep -n "conn = connect()" database.py
# → إذا ظهر شيء، لم تنتهِ الهجرة بعد

# يجب أن يكون > 70
grep -c "with db_conn()" database.py
```

---

## 4) الاختبار

### أ) اختبار leak مباشر

أضف في `tests/test_database_leaks.py`:

```python
import pytest
import gc
import sqlite3

def test_connection_leak_on_exception(db_path):
    """V53: تأكد أن اتصال SQLite يُغلَق حتى عند استثناء في منتصف الدالة."""
    from database import wishlist_toggle
    
    # نفّذ 1000 استدعاء يُفشل
    for _ in range(1000):
        with pytest.raises(Exception):
            wishlist_toggle(user_id=999999, provider="x", game_key="y")
    
    gc.collect()
    
    # إذا كان هناك leak، سنجد اتصالات sqlite3.Connection معلّقة
    open_conns = [o for o in gc.get_objects() if isinstance(o, sqlite3.Connection)]
    # نسمح بعدد صغير للـpool الداخلي
    assert len(open_conns) < 10, f"Leaked {len(open_conns)} connections"
```

### ب) اختبار وظيفي سريع

بعد تحويل كل دالة، شغّل الـsuite الحالية:

```bash
pytest tests/ -x --ff
```

لا يجب أن يفشل أي اختبار. إذا فشل، راجع الدالة المُحوَّلة — على الأرجح فقدت `commit()` داخل الـwith.

---

## 5) التزامات Rollout

- [ ] 3 مراحل في PR واحد (تسهيلاً للـreview) أو PRs متتالية (للـsafety).
- [ ] `pytest tests/ -v` كل الاختبارات تمر.
- [ ] smoke test يدوي: تسجيل دخول، إيداع، طلب، admin actions.
- [ ] مراقبة الإنتاج 24 ساعة ضد "database is locked".

---

## 6) تحديث `project-context.md`

- أضف القرار المعماري:
  > **Context manager `db_conn()` إلزامي** — كل استعلام DB يمر عبر `with db_conn()`. `connect()` الخام يُستعمل فقط من داخل الـcontext manager. استثناء واحد: اختبارات fixtures.
- أضف البند للمُنجزة.
- أضف `audit.py` و `tasks.py` و `sync_products.py` للـgrep التالي — تأكد ألّا أحد يستدعي `connect()` مباشرةً.
