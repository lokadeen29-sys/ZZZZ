---
inclusion: manual
---

# Playbook — تقسيم `app.py` (المرحلة الأولى)

> **متى يُستدعى:** تنفيذ البند High رقم 21 (Blueprint Split).
> **المدة المتوقعة:** أسبوع عمل (هذا ليس refactor صغير).
> **الوضع الحالي:** `app.py` = 2915 سطر. `database.py` = 1817 سطر. `routes/__init__.py` موجود كـno-op.

---

## 1) الفلسفة

**لا تقسّم كل شيء دفعة واحدة.** هذا refactor خطر يمكن أن يكسر الموقع. نقسّم **على مراحل**، كل مرحلة في PR مستقل، كل مرحلة نقلها آمن (behavior-preserving).

**المرحلة 1 (هذا الـplaybook):** استخراج الـauth routes فقط إلى `routes/auth_bp.py`. أصغر تغيير ممكن، أعلى قيمة من ناحية التعلُّم والثقة.

**المراحل اللاحقة (playbooks مستقبلية):**
- المرحلة 2: `admin_bp`
- المرحلة 3: `wallet_bp` + `deposits_bp`
- المرحلة 4: `games_bp` + `orders_bp`
- المرحلة 5: `api_bp`
- المرحلة 6: تفكيك `database.py` إلى `repositories/*`

---

## 2) الهيكل المستهدف (المرحلة 1)

```
routes/
├── __init__.py        (register_blueprints() helper)
└── auth_bp.py         ← routes: /login, /logout, /register, /reset-password, /verify-email, /auth/google/*
services/
└── auth_service.py    ← business logic (authenticate, register_user, send_verification_email)
```

**بعد المرحلة 1:**
- `app.py` ↓ من 2915 إلى ~2650 سطر (حذف ~265 سطر من routes الـauth).
- `routes/auth_bp.py` = ~280 سطر.
- `services/auth_service.py` = ~100 سطر.

---

## 3) routes المستهدفة للنقل

ابحث في `app.py` عن:

```bash
grep -n "^@app.route" app.py | grep -iE "login|logout|register|reset|verify|forgot|auth|google|2fa"
```

توقَّع هذه:
- `/login` (GET + POST)
- `/logout` (GET + POST)
- `/register` (GET + POST)
- `/reset-password` (GET + POST)
- `/reset-password/<token>` (GET + POST)
- `/forgot-password` (POST)
- `/verify-email/<token>` (GET)
- `/resend-verification` (POST)
- `/auth/google/login` (GET)
- `/auth/google/callback` (GET)
- **استثناء:** `/admin/2fa/*` routes تبقى مع admin_bp في مرحلة لاحقة.

---

## 4) خطوات التنفيذ (خطوة بخطوة)

### الخطوة 1 — أنشئ الهيكل

```bash
mkdir -p routes services
touch routes/__init__.py routes/auth_bp.py services/__init__.py services/auth_service.py
```

### الخطوة 2 — `routes/__init__.py`

```python
"""V53 REFACTOR: Blueprint registration (المرحلة 1 — auth فقط).

المراحل القادمة ستضيف admin_bp / wallet_bp / games_bp / api_bp إلى قائمة
_BLUEPRINTS أدناه دون لمس app.py.
"""
from flask import Flask

from .auth_bp import bp as auth_bp

_BLUEPRINTS = [auth_bp]


def register_blueprints(app: Flask) -> None:
    for bp in _BLUEPRINTS:
        app.register_blueprint(bp)
```

### الخطوة 3 — `services/auth_service.py`

استخرج البيزنس لوجيك من الـroutes (ليس كل السطور، فقط ما هو "قرار" وليس "HTTP plumbing").

```python
"""V53 REFACTOR: auth business logic — separated from HTTP layer."""
from typing import Optional, Tuple

from database import (
    authenticate,
    get_user_by_email,
    create_user,
    set_password,
    mark_email_verified,
    # ... etc
)
from tasks import enqueue_email


def attempt_login(email: str, password: str) -> Tuple[Optional[dict], Optional[str]]:
    """
    يُعيد (user, error_code). error_code إذا فشل:
      - "invalid"      — بيانات خاطئة
      - "inactive"     — حساب معطَّل
      - "unverified"   — يحتاج تحقق إيميل
    """
    user = authenticate(email, password)
    if not user:
        return None, "invalid"
    if not user.get("active"):
        return None, "inactive"
    if _email_verification_required() and not user.get("email_verified"):
        return None, "unverified"
    return user, None


def _email_verification_required() -> bool:
    from database import get_setting
    return (get_setting("email_verification_enabled") or "0") == "1"


def register_new_user(email: str, password: str, name: str, phone: str = "") -> Tuple[Optional[int], Optional[str]]:
    if get_user_by_email(email):
        return None, "email_taken"
    # ... validation
    uid = create_user(email=email, password=password, name=name, phone=phone)
    if _email_verification_required():
        _send_verification_email(uid, email)
    return uid, None


def _send_verification_email(user_id: int, email: str) -> None:
    # token generation + enqueue
    ...
```

### الخطوة 4 — `routes/auth_bp.py`

HTTP layer فقط — parse request, call service, return response.

```python
"""V53 REFACTOR: auth routes — thin HTTP layer on top of auth_service."""
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app

from services.auth_service import attempt_login, register_new_user
# استيرادات أخرى حسب الحاجة

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")
    
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    
    user, err = attempt_login(email, password)
    
    if err == "invalid":
        flash("بيانات الدخول غير صحيحة", "danger")
        return render_template("login.html"), 401
    if err == "inactive":
        flash("الحساب معطَّل. راسل الدعم.", "danger")
        return render_template("login.html"), 403
    if err == "unverified":
        flash("يرجى تفعيل حسابك من إيميل التحقق.", "warning")
        return redirect(url_for("auth.login"))
    
    # نجح — أنشئ الجلسة
    session.regenerate()  # منع session fixation (V50 fix)
    session["user_id"] = user["id"]
    session.permanent = True
    flash(f"مرحباً {user['name']}", "success")
    return redirect(url_for("home"))


# ... باقي الـroutes
```

### الخطوة 5 — `app.py` — تعديلات دنيا

في `app.py`:

```python
# في أعلى الملف، بعد إنشاء app:
from routes import register_blueprints
register_blueprints(app)

# احذف الـroutes التي نُقلت (login/logout/register/reset/verify/google)
```

**تحذير:** endpoint names تتغيّر من `login` إلى `auth.login`. كل `url_for("login")` في القوالب والـredirects يجب أن يصير `url_for("auth.login")`.

### الخطوة 6 — grep + fix

```bash
grep -rn "url_for('login'\|url_for(\"login\"\|url_for('register'\|url_for(\"register'" templates/
```

استبدل كل النتائج:
- `url_for('login')` → `url_for('auth.login')`
- `url_for('register')` → `url_for('auth.register')`
- وهكذا لكل route مُنتقل.

في `app.py` نفسه:
```bash
grep -n "redirect(url_for(\"\(login\|register\|logout\|verify\|reset\)" app.py
```

---

## 5) اختبار — إلزامي قبل الـmerge

### 5.1 — كل اختبارات الـauth الحالية تمر

```bash
pytest tests/test_auth.py -v
pytest tests/test_security.py -v
```

إذا فشل اختبار، هناك endpoint لم يُعدَّل. **لا تمرّر الـPR قبل 100% pass.**

### 5.2 — smoke test يدوي كامل

- [ ] Home → click Login → صفحة `/login` تفتح.
- [ ] Login بحساب صحيح → redirect للـhome.
- [ ] Login ببيانات خاطئة → الـerror message يظهر.
- [ ] Register → الحساب يُنشأ + (لو مفعّل) إيميل التحقق يصل.
- [ ] Forgot password → إيميل reset يصل.
- [ ] Reset password via token → يعمل.
- [ ] Logout → session تُحذَف.
- [ ] Google OAuth → يعمل.
- [ ] Admin login → يظل في نفس الصفحة (admin_2fa سليم).

### 5.3 — تأكّد من nonce CSP

Blueprints أحياناً تخلط بـtemplate context. تأكّد:
```python
# app.py — context processor لا يزال يطبّق على Blueprints
@app.context_processor
def inject_csp_nonce():
    from flask import g
    return {"csp_nonce": getattr(g, "csp_nonce", "")}
```

---

## 6) ملاحظات تشغيلية

### 6.1 — Rate limiter decorators

```python
# في app.py القديم:
@limiter.limit("5 per minute")
@app.route("/login", ...)
def login(): ...

# في routes/auth_bp.py:
from app import limiter  # ⚠️ circular import!

# الحل: مرّر limiter كـargument أو استخدم current_app:
from flask import current_app

@bp.route("/login", methods=["GET", "POST"])
def login():
    ...

# ثم في routes/__init__.py:
def register_blueprints(app):
    from app import limiter  # هنا آمن — بعد import الـapp
    limiter.limit("5 per minute")(auth_bp.view_functions["login"])
    ...
    app.register_blueprint(auth_bp)
```

> **حل أبسط:** أنشئ `extensions.py` يحوي `limiter`, `csrf`, `babel` كـglobal instances، ثم استورد منها في كل مكان. هذا النمط standard لـFlask Blueprints.

### 6.2 — CSRF exempt routes

لا routes auth يحتاج exempt. تأكّد أن CSRF لا تزال تعمل على POST /login و POST /register.

### 6.3 — Blueprints لا تدعم `before_request` على مستوى Flask global

الـ`before_request` hooks في `app.py` (مثل `_api_origin_guard`, language detection, session hardening) تظل تعمل على كل الطلبات لأنها مرتبطة بـ `app` لا بـ blueprint.

---

## 7) ما تبقى في `app.py` بعد المرحلة 1

- `admin_*` routes (ستنتقل في المرحلة 2)
- `wallet_*` routes
- `checkout_*` routes
- `orders_*` routes
- `games_*` routes
- `/api/*` routes
- `before_request` hooks
- `after_request` (CSP, headers)
- Error handlers (404, 500, CSRFError)
- `/healthz` إن أُضيف

---

## 8) تحديث `project-context.md`

- أضِف للمُنجزة: "المرحلة 1 من Blueprint split — auth routes".
- أضِف أرقاماً محدَّثة:
  > `app.py` ~2650 سطر (كان 2915). `routes/auth_bp.py` جديد + `services/auth_service.py` جديد.
- قرار معماري:
  > **طبقات نظيفة:** HTTP (Blueprint) → Business Logic (service) → Data (database.py). مرحلياً نطبّق المبدأ على auth فقط، ثم نوسِّع.
- أضِف لقسم "البنود المتبقية":
  - المرحلة 2 من Blueprint split: admin_bp.
  - المرحلة 3: wallet_bp + deposits_bp.
  - وهكذا.
