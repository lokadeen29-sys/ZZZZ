# V51 — Tests + CI (Task C)

> **الغرض:** إضافة شبكة أمان تلقائية لكل تعديل مستقبلي على المستودع عبر
> اختبارات pytest منظَّمة وسير عمل GitHub Actions يشغّل الاختبارات +
> فحص أمني ثابت (bandit) + فحص ثغرات في المكتبات (pip-audit).

## 1. ما الذي أُضيف

### Test suite — `tests/`

| الملف | عدد الاختبارات | المحاور المغطَّاة |
|------|---------------|-------------------|
| `tests/conftest.py` | (fixtures) | `app` + `client` + `make_user` + `login_as`، كل اختبار يستخدم DB منفصلة في `tmp_path` |
| `tests/test_security.py` | 28 | CSP nonce، HSTS، headers الأمنية، `safe_next_url` ضد open-redirect، `_sanitise_supplier_note`، CSRF، حجب `/static/uploads/` |
| `tests/test_auth.py` | 11 | `validate_password_strength`، `/register`، `/login` (success/bad/oversized)، `/logout`، `login_required` guard |
| `tests/test_admin_2fa.py` | 17 | TOTP verify، backup codes (تنسيق/هاش PBKDF2/one-time/normalisation)، `/admin/2fa/setup` + `/confirm`، إجبار التحدّي داخل `admin_required` |
| `tests/test_orders_wallet.py` | 11 | `create_order` atomic balance deduct، `InsufficientBalance`، 20 order_code فريد، `/wallet` validation (amount/proof/method/limit) |
| **المجموع** | **67** | — |

تشغيل محلي:

```bash
pip install -r requirements-dev.txt
pytest -v
```

### CI — `.github/workflows/ci.yml`

ثلاث وظائف تعمل بالتوازي على كل push/PR إلى `main`:

| Job | الأداة | الحالة |
|-----|-------|-------|
| `tests` | `pytest` على Python 3.11 | **إلزامي** (يكسر PR عند الفشل) |
| `bandit` | `bandit -ll -ii` (MEDIUM+/MEDIUM+ فقط) | **إلزامي** |
| `pip_audit` | `pip-audit -r requirements.txt --no-deps --disable-pip` | **استشاري** (`continue-on-error`) |

### Dev deps — `requirements-dev.txt`

```
-r requirements.txt
pytest==8.3.3
pytest-flask==1.3.0
bandit==1.7.10
pip-audit==2.7.3
```

### Config — `pytest.ini`

- `testpaths = tests`
- `--strict-markers --strict-config`
- `filterwarnings = ignore::DeprecationWarning` (ضوضاء من مكتبات Flask القديمة)

## 2. تعديلات مرافقة للكود

أقل ما يلزم لإرضاء `bandit -ll -ii` بدون تخفيف الفلاتر:

- `app.py` — `app.run(host="0.0.0.0", …)` حصل على `# nosec B104` (نقطة دخول dev-only فقط، محميّة بـ `if __name__ == "__main__":`؛ الإنتاج يستخدم gunicorn عبر `Procfile`).
- `database.py` — `search_users()` أعاد بناء SQL كـ سلسلة واحدة في متغيّر `_sql` بدل الـ triple-quoted concat؛ أضيف `# nosec B608` لأن الجزء المتغيّر هو حرفياً `" OR u.id=?"` ثابت، وكل مدخلات المستخدم مُمرَّرة عبر `?`.

## 3. قرارات

- **`pip_audit` استشاري:** الاعتماديات المثبّتة حالياً تحوي 21 CVE (Flask 3.0.3، Werkzeug 3.0.3، Authlib 1.3.2، Pillow 10.4.0، Requests 2.32.3، python-dotenv 1.0.1). جعلنا الفحص استشارياً (`continue-on-error: true`) لتوفير الرؤية بدون كسر الـ PRs. ترقية الاعتماديات = PR مستقل (يتطلّب اختبار تكامل يدوي).
- **DB منفصلة لكل اختبار:** `function` scope + `monkeypatch.setattr(database, "DB_PATH", …)` + `database._PRAGMAS_APPLIED = False` قبل كل اختبار. الثمن ~0.5 ثانية/اختبار مقابل عزل كامل وعدم تسرّب حالة.
- **CSRF + Flask-Limiter معطّلان في fixtures:** مغطَّيان بحالات اختبار مخصَّصة؛ تركهما فعّالَين في كل الاختبارات = ضجيج ورقائق.
- **setup_once bypassed:** `app._setup_done = True` بعد `init_db()` ليتجاوز الـ `@before_request` الذي يستدعي `seed_local_provider_catalog()` و`attach_generated_posters()` (بطيء + يحتاج شبكة/صور).

## 4. الخطوة التالية

- البند **D**: Sentry + `python-json-logger` + جدول `audit_log`.
- PR منفصل لترقية الاعتماديات (werkzeug → 3.1.6، flask → 3.1.x، pillow → 12.2.0، …) وإرجاع `pip_audit` إلى `--strict`.
