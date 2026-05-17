# V52 — Observability: Sentry + Structured Logging + Audit Table

**Scope:** البند D من خطة الـ steering.
**الفرع:** `feat/v52-audit-sentry-logging`
**الغرض:** ترقية الرؤية التشغيلية للمتجر بإضافة ثلاث طبقات متكاملة لتتبُّع الإجراءات الإدارية والأخطاء، دون كسر أي سلوك حالي.

---

## الغرض

قبل V52 كانت كل الإجراءات الإدارية (تغيير رصيد، رفض طلب، إعداد 2FA، إلخ)
تُسجَّل عبر `log.warning("ADMIN_…")` نصّاً فقط. هذا يصعّب:
- البحث والفلترة (نصّ حرّ بلا schema).
- ربط الحدث بالضحية (target user / order) في لوحة تحليل.
- التعافي من سجلّات مكتوبة بملف محلي قد يُفقَد.
- رصد استثناءات الإنتاج (Sentry غائب).

V52 يحلّ الثلاث في PR واحد مع **تفعيل اختياري بالكامل** عبر متغيّرات البيئة.

---

## التغييرات

### ملفّات جديدة
| الملف | الغرض |
|------|------|
| `audit.py` | `init_sentry()` + `init_json_logging()` + `log_audit(...)` |
| `tests/test_audit.py` | 21 اختبار (schema, redaction, safety, end-to-end) |
| `V52_AUDIT_SENTRY.md` | هذا التوثيق |

### ملفّات معدَّلة
| الملف | التعديل |
|------|---------|
| `database.py` | جدول `audit_log` + 4 indexes + `insert_audit_log()` / `list_audit_logs()` / `count_audit_logs()` |
| `app.py` | استيراد `audit` + استدعاء `init_*` عند startup + استبدال 11 مكانًا لـ `log.warning("ADMIN_…")` بـ `log_audit()` |
| `requirements.txt` | `sentry-sdk[flask]==2.14.0` + `python-json-logger==2.0.7` |
| `.env.example` | 5 متغيّرات جديدة موثَّقة |

### جدول `audit_log` (جديد)

```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,        -- unix epoch seconds
    action TEXT NOT NULL,       -- e.g. "ADMIN_BALANCE_CHANGE"
    actor_id INTEGER,           -- who
    actor_email TEXT,
    target_type TEXT,           -- "user" | "order" | "game" | ...
    target_id TEXT,
    ip TEXT,
    user_agent TEXT,
    old_value TEXT,             -- JSON blob (already redacted)
    new_value TEXT,             -- JSON blob (already redacted)
    metadata TEXT               -- JSON blob (already redacted)
);
-- Indexes: idx_audit_ts, idx_audit_actor, idx_audit_target, idx_audit_action
```

الجدول **append-only** — لا يوجد API عام لـ `UPDATE` أو `DELETE`. الصفوف يُقرأ منها فقط.

### مواقع استدعاء `log_audit()` في `app.py`

| Action code | المكان | الاستبدال |
|------------|--------|----------|
| `ADMIN_ORDER_COMPLETE` | `admin_order_action(complete)` | old/new status + user_id |
| `ADMIN_ORDER_REJECT` | `admin_order_action(reject)` | + amount |
| `ADMIN_BALANCE_CHANGE` | `admin_user_balance` | old/new balance |
| `ADMIN_GAME_ADD` | `admin_add_game` | provider+game_key+name |
| `ADMIN_GAME_IMAGE` | `admin_game_image` | new image_url |
| `ADMIN_2FA_SETUP_FAIL` | `admin_2fa_confirm` | — |
| `ADMIN_2FA_ENABLED` | `admin_2fa_confirm` | — |
| `ADMIN_2FA_PASS` × 2 | `admin_2fa_challenge` (totp / backup) | `method` + `remaining` |
| `ADMIN_2FA_FAIL` | `admin_2fa_challenge` | — |
| `ADMIN_2FA_DISABLE_BAD_PW` | `admin_2fa_disable` | — |
| `ADMIN_2FA_DISABLE_BAD_CODE` | `admin_2fa_disable` | — |
| `ADMIN_2FA_DISABLED` | `admin_2fa_disable` | — |
| `ADMIN_2FA_REGEN_BAD_CODE` | `admin_2fa_regenerate_backup_codes` | — |
| `ADMIN_2FA_BACKUP_CODES_REGEN` | `admin_2fa_regenerate_backup_codes` | — |

> `log.warning("ADMIN_PASSWORD …")` المتبقي في دالتي الإقلاع هو **تحذير إعداد**، ليس حدث audit.

---

## معمارية `log_audit()`

استدعاء واحد يرسل إلى ثلاث قنوات:

```
log_audit(action, actor_id=…, target_type=…, old={…}, new={…}, metadata={…})
        │
        ├─► database.insert_audit_log()         صفّ دائم في audit_log
        ├─► logger.warning("AUDIT … ")          يحافظ على grep contract القديم
        └─► sentry_sdk.add_breadcrumb()         يُرفَق بأي استثناء لاحق
```

### قواعد حرجة
- **لا ترفع استثناءً أبداً.** فشل الرصد يجب ألا يكسر الطلب الذي استدعاه.
- **Redaction مركزية.** أي مفتاح بالاسم `password|token|secret|otp|code|backup_code|totp_*|api_key|cookie|csrf_token` (case-insensitive) يُستبدَل بـ `[REDACTED]` قبل الكتابة.
- **Truncation.** `metadata/old/new` يُقصَّر إلى 4096 حرفاً لكل حقل لتجنّب log-bomb.
- **Sentry before_send hook** يمسح نفس المفاتيح من أي event قبل إرساله.

---

## متغيّرات البيئة الجديدة

جميعها **اختيارية**. الغياب = سلوك V51 السابق بدقّة.

```bash
SENTRY_DSN=                          # فعّل بالربط بمشروع Sentry
SENTRY_TRACES_SAMPLE_RATE=0          # 0 = لا tracing. 0.1 بداية مقترحة للإنتاج.
SENTRY_ENVIRONMENT=                  # افتراضياً FLASK_ENV
SENTRY_RELEASE=                      # وسم الإصدار لربط الأخطاء بـ git
LOG_JSON=                            # 1/true/yes لتفعيل JSON logs
```

---

## الاختبار

```bash
pytest tests/test_audit.py -v
```

| النوع | العدد |
|-------|-------|
| DB schema (جدول + فهارس) | 2 |
| `insert_audit_log` / `list_audit_logs` / `count_audit_logs` | 8 |
| Redaction (password/token/totp/case) | 4 |
| Safety (never-raises + nulls) | 2 |
| End-to-end admin routes (balance/2FA) | 3 |
| Lazy init guards | 2 |
| **المجموع** | **21** |

الإجمالي بعد الدمج: **88 اختبار** (67 سابق + 21 جديد). كلها خضراء.
bandit MEDIUM+: `No issues identified`.

---

## Breaking changes

**لا يوجد.**

- `audit_log` جدول جديد — مخالفة schema صفر.
- `log.warning("ADMIN_…")` استُبدل، لكن النص `AUDIT <ACTION> …` من `log_audit` يحافظ على grep contract العام (`grep ADMIN_` لا يزال يعمل بسبب أنّ action codes لم تتغيّر).
- Sentry/JSON logs opt-in، لا يتفعّلان إلا بعد تعيين env vars.

---

## قرارات تصميم

| القرار | السبب |
|--------|-------|
| جدول `audit_log` منفصل لا تكامل مع `orders/users` | فصل الاهتمامات: table audit يجب أن يبقى append-only قابلاً للأرشفة مستقلّاً. |
| JSON blobs في `old/new/metadata` بدل أعمدة منفصلة | مرونة الـ schema — لا حاجة لـ migration مع كل نوع حدث جديد. |
| `log_audit` نفسها تفعل redaction قبل الـ DB | "defense in depth": حتى لو استُدعيت `insert_audit_log` مباشرة من مكان آخر، البيانات الحسّاسة لن تمرّ به إذا مرّت بـ `log_audit`. |
| `insert_audit_log` بدون redaction محلّية | تتعامل فقط مع سلاسل جاهزة (JSON strings)؛ الـ layer الأعلى (`audit.log_audit`) هو المسؤول. |
| Clamp على limit إلى 1000 | صفحة admin audit لاحقاً ستعتمد pagination؛ لا نسمح بسحب الجدول كاملاً دفعة واحدة. |
| pin `sentry-sdk==2.14.0` لا latest | استقرار: SDK 2.x مستقر، و 2.14 أحدث قبل ترقية قد تكسر Flask integration. |
| `_MAX_METADATA_LEN=4096` | كافٍ لـ 99% من الحالات (user_id+email+old+new)، ويحمي من log-bomb. |

---

## ما هو مؤجَّل للمستقبل

- **صفحة `/admin/audit/` في UI** لعرض السجلّ مع فلترة. حالياً يُقرَأ من DB مباشرة عند الحاجة.
- **تدوير تلقائي (log rotation / archival) لـ `audit_log`** — يُترك لـ backup.sh في البند G.
- **Sentry performance transactions** — مفعَّل بصمت عند `SENTRY_TRACES_SAMPLE_RATE > 0` لكن لم يُفحَص في هذا الـ PR.
- **ربط Sentry user context** (`sentry_sdk.set_user(...)` من `current_user()`) — مبسَّط إلى breadcrumb فقط حالياً.
