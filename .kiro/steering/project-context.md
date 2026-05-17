---
inclusion: always
---

# TecnoGems — سياق المشروع

> **ملف steering حيّ** — يُحدَّث بعد كل PR لتعكس حالة المشروع الراهنة.
> لا تحذف الأقسام الثابتة (نظرة عامة، بنية، قرارات معمارية) حتى لو صارت بديهية.

---

## 1) نظرة عامة

**TecnoGems** متجر شحن ألعاب إلكتروني (gaming topup store) موجَّه للسوق العربي الخليجي.

- **المنصة:** Flask 3 + SQLite + RQ (Redis queue اختياري)
- **اللغة:** Python 3.11+
- **اللغات المدعومة في الواجهة:** عربي / إنجليزي (Flask-Babel)
- **المزوِّدون:** G2Bulk (سيرفر 1) + Shop2Topup (سيرفر 2)
- **المستودع:** `alexkline3322-byte/tecnogems`
- **الفرع الأساسي:** `main`

## 2) البنية على القرص

الكود موجود مباشرة في جذر المستودع (بعد تنظيف البند A):

```
tecnogems/
├── app.py                   ~2580 سطر  — routes + security + logic
├── database.py              ~1625 سطر  — SQLite schema + queries
├── providers.py              ~353 سطر  — G2Bulk + Shop2Topup
├── tasks.py                  ~230 سطر  — RQ + email + supplier sanitise
├── sync_products.py          ~360 سطر  — catalog sync
├── featured_games.py          ~90 سطر
├── wsgi.py                    ~35 سطر
├── worker_rq.py               ~22 سطر
├── requirements.txt
├── Procfile                  Heroku entrypoint
├── .env.example              متغيرات البيئة
├── .gitignore
├── routes/__init__.py        (placeholder — blueprint split مستقبلاً)
├── templates/                22 قالب Jinja2 (+ admin/)
├── static/css/               9 ملفات CSS (v35 → v44 neon)
├── static/js/                app.js + app.min.js
├── static/img/               صور الألعاب + icons
├── translations/             ar/ + en/ (LC_MESSAGES)
├── tools/gen_posters.py      توليد صور المنتجات
├── V*.md / V*.txt            سجلات التغيير لكل إصدار
└── .kiro/steering/           ملفات steering
```

## 3) تقنيات رئيسية

- **Web:** Flask 3.0.3, Flask-WTF, Flask-Limiter, Flask-Babel
- **DB:** SQLite (عبر `sqlite3` القياسي، لا ORM)
- **Auth:** session-based + Werkzeug password hashing + Authlib (Google OAuth)
- **Queue:** RQ 1.16 + Redis (اختياري؛ يوجد fallback داخلي بـ threading)
- **Server:** Gunicorn (gthread × 4)
- **Images:** Pillow 10.4
- **Mail:** SMTP (Flask-Mail)

## 4) الإصلاحات المُنجزة ✅

| الإصدار | PR | ما الذي تم | الملف المرجعي |
|--------|----|-----------|--------------|
| **V50** | [#1](https://github.com/alexkline3322-byte/tecnogems/pull/1) | 14 إصلاح حرج/عالٍ (Critical + High) | `V50_SECURITY_FIXES.md` |
| **V50.2** | [#2](https://github.com/alexkline3322-byte/tecnogems/pull/2) | 22 إصلاح متوسط/منخفض (Medium + Low) | `V50_2_SECURITY_FIXES.md` |
| **A** | [#4](https://github.com/alexkline3322-byte/tecnogems/pull/4) | تنظيف هيكل المستودع: نقل الكود من `src/tecnogems_V49_STABLE/` إلى الجذر + حذف الـ zip القديم | — |
| **B** | [#5](https://github.com/alexkline3322-byte/tecnogems/pull/5) | 2FA (TOTP) لحسابات الأدمن: pyotp + QR + 10 رموز استرداد + `admin_2fa_required` switch | `security_2fa.py` |
| **C** | [#6](https://github.com/alexkline3322-byte/tecnogems/pull/6) | Tests + CI: 67 اختبار pytest + GitHub Actions (pytest + bandit + pip-audit) | `tests/`, `.github/workflows/ci.yml`, `V51_TESTS_CI.md` |
| **D** | [#7](https://github.com/alexkline3322-byte/tecnogems/pull/7) | Sentry + JSON logs + `audit_log` table + `log_audit()` helper (11 admin actions مُنتقَلة) | `audit.py`, `V52_AUDIT_SENTRY.md` |

**أبرز ما طُبِّق أمنياً:**
- `secrets.token_urlsafe` لـ `order_code` و `deposit_code`
- Rate-limit على auth + admin routes (Flask-Limiter + Redis backend)
- حدود طول على كل مدخلات المستخدم (email/password/name/phone/proof/player_id)
- `safe_next_url` مقوَّى ضد open-redirect
- Upload path نُقل خارج `static/` إلى `data/uploads/`
- CSP محكَم + HSTS 2 سنة + COOP/CORP/XPCDP
- CSRF SSL-strict في الإنتاج + Origin/Referer guard على `/api/*`
- Audit logs على كل admin actions (`log.warning("ADMIN_..."))`
- `current_user()` يفحص `active=1`
- Supplier errors تُنظَّف قبل التخزين (`_sanitise_supplier_note` في `tasks.py`)
- `.gitignore` يحمي `.secret_key`, `*.sqlite`, `*.log`, `rq.db`
- Session 7 أيام (كان 14)

## 5) البنود المتبقية ⏳

مرتَّبة **حسب الأولوية**:

### أولوية عالية

- [x] ~~**A. تنظيف هيكل المستودع**~~ ✅ [PR #4](https://github.com/alexkline3322-byte/tecnogems/pull/4)
  - ~~نقل محتويات `src/tecnogems_V49_STABLE/*` إلى جذر المستودع~~
  - ~~حذف `tecnogems_V49_STABLE(1).zip`~~

- [x] ~~**B. 2FA لحسابات الأدمن**~~ ✅ [PR #5](https://github.com/alexkline3322-byte/tecnogems/pull/5)
  - ~~`pyotp` + أعمدة `users.totp_*` (secret, enabled, backup_codes, enabled_at)~~
  - ~~`/admin/2fa/setup` مع QR code + 10 backup codes~~
  - ~~`/admin/2fa/challenge` + `/admin/2fa/disable` + `/admin/2fa/backup-codes/regenerate`~~
  - ~~حارس 2FA داخل `admin_required` (session["admin_2fa_verified"])~~
  - ~~setting `admin_2fa_required` (0/1) للتدرج في الإجبار~~

- [x] ~~**C. Tests + CI**~~ ✅ [PR #6](https://github.com/alexkline3322-byte/tecnogems/pull/6)
  - ~~`tests/` + pytest: auth, security, admin_2fa, orders_wallet (67 اختبار)~~
  - ~~`.github/workflows/ci.yml`: pytest + bandit (SAST) + pip-audit (CVE)~~

### أولوية متوسطة

- [x] ~~**D. Sentry + Structured Logging + Audit Table**~~ ✅ [PR #7](https://github.com/alexkline3322-byte/tecnogems/pull/7)
  - ~~`sentry-sdk[flask]` + `SENTRY_DSN` + breadcrumbs + scrubbing hook~~
  - ~~JSON logs عبر `python-json-logger` (مفعَّل بـ `LOG_JSON=1`)~~
  - ~~جدول `audit_log(id, ts, action, actor_id, actor_email, target_type, target_id, ip, user_agent, old_value, new_value, metadata)`~~
  - ~~`audit.log_audit()` helper يكتب DB + logger + Sentry breadcrumb مع redaction~~
  - ~~11 موقع ADMIN_* مُنتقَل من `log.warning` إلى `log_audit()`~~
  - ~~21 اختبار جديد (المجموع 88)~~

- [ ] **E. ترحيل SQLite → PostgreSQL**
  - استبدال `sqlite3` بـ `psycopg2` أو SQLAlchemy
  - Alembic migrations
  - تحديث `DATABASE_URL` في `.env`

### أولوية منخفضة (لكن ضرورية)

- [ ] **F. إزالة `style="…"` inline**
  - 22 قالب يحتاج تمشيط
  - نقل إلى classes في `static/css/`
  - تشديد CSP: `style-src 'self'` (حذف `unsafe-inline`)

- [ ] **G. WAF + نسخ احتياطية**
  - `backup.sh` cron → S3 / مخزن خارجي
  - Cloudflare rules (معظمه خارج الكود)
  - `DEPLOYMENT.md`

### الختامي

- [ ] **H. Release v51+ نهائي**
  - tag `v51.0-stable` بعد اكتمال A-G
  - GitHub Release مع CHANGELOG موحَّد
  - تنظيف ملفات `V*.md` القديمة إلى `docs/history/`

## 6) القرارات المعمارية المُتخذة 🏗️

| القرار | السبب |
|-------|-------|
| **إبقاء SQLite مؤقتاً** | المشروع لم يصل حجم يستدعي PG. التأجيل يقلّل مخاطر ترحيل مبكّر. |
| **Rate-limit عبر Redis اختيارياً** | Redis موجود أصلاً لـ RQ. إعادة استخدامه مجاني عملياً. |
| **Audit في logs فقط حالياً** | DB audit table مؤجَّل لبند D. |
| **إبقاء GET /logout للتوافق** | روابط قديمة في الإيميلات + bookmarks. يُسجَّل كـ deprecated. |
| **حذف PDF من uploads** | PDFs يمكن أن تحوي JS/XSS payloads. PNG/JPG/WEBP فقط. |
| **CSP لا يزال يسمح `style-src unsafe-inline`** | بسبب inline styles في القوالب. بند F سيرفع هذا. |
| **`FLASK_ENV=production` يفعّل كل السلوكيات الصارمة** | قرار مركزي: debugger off, CSRF strict, إلخ. |
| **Admin 2FA opt-in ثم forced** | `admin_2fa_required` setting يبدأ `"0"` (اختياري) ثم يُرفع إلى `"1"` بعد تسجيل كل admin. يتجنّب lockout فوري عند النشر. |
| **Backup codes PBKDF2-hashed** | DB leak لا يكشف رموز الاسترداد غير المستخدمة. الرموز one-time، iteration ثابت على كل الـ 10 لتجنب timing oracle. |
| **pip-audit advisory-only في CI** | الاعتماديات المثبّتة تحوي 21 CVE (flask 3.0.3, werkzeug 3.0.3, authlib 1.3.2, pillow 10.4.0, …). بدلاً من تعطيل CI، شغّلنا الفحص كـ `continue-on-error` للرؤية بدون حجب الـ PRs. PR لاحق يخصّص لترقية الاعتماديات. |
| **bandit بعتبة `-ll -ii`** | يبلّغ فقط عن القضايا MEDIUM+ من ناحيتَي الشدة والثقة. يحجب ضوضاء Low-severity التي تراكمت تاريخياً (sqlite pragmas, assert stmts, إلخ). |
| **اختبارات تستخدم DB منفصلة لكل test** | `function` scope + `tmp_path` + monkeypatch على `database.DB_PATH`. يزيد الزمن قليلاً (~0.5 ثانية/اختبار) لكنه يضمن عزلاً كاملاً. |
| **`audit_log` جدول منفصل append-only مع JSON blobs لـ old/new** | فصل الاهتمامات + مرونة schema (لا migration مع كل نوع حدث جديد). Redaction مركزية داخل `audit.log_audit()` قبل أن تصل القيم للـ DB. |
| **Sentry + JSON logs opt-in بالكامل** | `SENTRY_DSN` فارغ / `LOG_JSON` غير مضبوط ⇒ سلوك V51 السابق بدقّة. يتيح rollout تدريجياً بدون مخاطر. |
| **`log_audit()` لا ترفع استثناءً أبداً** | الرصد يجب ألا يكسر الطلب. فشل الـ DB أو Sentry يُبتَلَع بـ try/except مع تحذير في logger. |

## 7) متغيرات البيئة المهمة

```bash
# أمنية (إلزامية في الإنتاج)
SECRET_KEY=<secrets.token_urlsafe(48)>
FLASK_ENV=production
BASE_URL=https://tecnogems.com

# حدود
MAX_DEPOSIT_USD=10000
MAX_ADMIN_BALANCE=1000000
SESSION_LIFETIME_DAYS=7

# بنية تحتية (اختيارية)
REDIS_URL=redis://localhost:6379/0   # للـ RQ + Flask-Limiter

# رصد وأخطاء (اختيارية — V52)
SENTRY_DSN=                          # فعّل بالربط بمشروع Sentry
SENTRY_TRACES_SAMPLE_RATE=0          # 0..1 — 0.1 بداية مقترحة للإنتاج
SENTRY_ENVIRONMENT=                  # افتراضياً FLASK_ENV
SENTRY_RELEASE=                      # وسم الإصدار
LOG_JSON=                            # 1/true لتفعيل JSON logs

# مزوِّدون
G2BULK_API_KEY=...
SHOP2TOPUP_API_KEY=...

# OAuth
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=https://tecnogems.com/auth/google/callback

# Mail
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=...
MAIL_PASSWORD=...
```

## 8) تشغيل المشروع محلياً

```bash
cp .env.example .env
# حرِّر .env وأضف SECRET_KEY

pip install -r requirements.txt

# تطوير
FLASK_ENV=development python app.py

# إنتاج (Gunicorn)
gunicorn -k gthread -w 2 --threads 4 -b 0.0.0.0:8000 wsgi:app
```

## 9) موقع الملفات الحساسة 📍

| المحتوى | الملف / الموقع |
|--------|----------------|
| Security policies (CSP, HSTS, rate-limits) | `app.py` — ابحث عن `V50` أو `V50.2` |
| Admin routes | `app.py` — `admin_*` functions |
| DB schema | `database.py` — بداية الملف (`CREATE TABLE`) |
| Supplier sanitization | `tasks.py` — `_sanitise_supplier_note` |
| Upload validation | `app.py` — `_PROOF_MAGIC`, `ALLOWED_UPLOAD_EXTS` |
| `safe_next_url` | `app.py` — ابحث عن الاسم |
| Templates base (CSP nonce) | `templates/base.html` |
| Admin 2FA helpers (TOTP, backup codes) | `security_2fa.py` |
| Admin 2FA routes + guard | `app.py` — `admin_2fa_*` + inside `admin_required` |
| Admin 2FA templates | `templates/admin/2fa_setup.html`, `2fa_challenge.html`, `2fa_backup_codes.html` |
| Audit helper (redaction + Sentry + JSON logs) | `audit.py` — `log_audit()`, `init_sentry()`, `init_json_logging()` |
| Audit DB helpers | `database.py` — `insert_audit_log()`, `list_audit_logs()`, `count_audit_logs()` |
| Robots.txt (حجب /admin و /api) | `static/robots.txt` |
| Test fixtures (app + DB isolation) | `tests/conftest.py` |
| Pytest config | `pytest.ini` |
| Dev / CI dependencies | `requirements-dev.txt` |
| CI workflow (pytest + bandit + pip-audit) | `.github/workflows/ci.yml` |

## 10) آخر تحديث 📌

- **Commit:** (سيُحدَّث بعد دمج PR الخاص بالبند D) — Branch: `feat/v52-audit-sentry-logging`
- **الحالة:** V50.2 + البنود A + B + C + **D** (pending PR) مكتملة. 88 اختبار pytest (67 + 21 جديد للـ audit). Sentry + JSON logs + audit_log table + log_audit() helper — كلها opt-in عبر env.
- **التالي:** البند **E** (ترحيل SQLite → PostgreSQL) — استبدال `sqlite3` بـ `psycopg2`/SQLAlchemy + Alembic migrations.
