---
inclusion: manual
---

# Playbook — إصلاح IDOR و XSS

> **متى يُستدعى:** تنفيذ بنود High رقم 13 + 14 من القائمة الموحّدة.
> **المدة المتوقعة:** نصف يوم إلى يوم.

---

## الجزء 1 — IDOR على `/uploads/proof/<filename>`

### المشكلة

في `app.py` route لتنزيل إيصالات الإيداع:

```python
# الكود الحالي
safe = secure_filename(filename)
owner_ok = safe.startswith(f"{user['id']}_") or user.get("role") == "admin"
if not owner_ok:
    abort(403)
return send_from_directory(UPLOAD_DIR, safe)
```

**السلبيات:**
1. الفحص نصي (`startswith`) — لا يتحقق أن الـfile فعلاً مرتبط بـdeposit لهذا المستخدم.
2. إذا تم تخزين اسم الملف بشكل قابل للتخمين (حالياً `{uid}_{ts}_{name}`)، ذكاء اصطناعي + brute force يمكن أن يجد ملفات المستخدم نفسه من deposits قديمة.
3. لا سجل audit لـdownloads.

### الإصلاح

#### الخطوة 1 — أضِف عمود `proof_filename` إلى جدول `deposits`

تحقَّق من الـschema الحالي: قد يكون `proof` موجود بالفعل. في `database.py`:

```python
# ensure_indexes أو migration:
try:
    conn.execute("ALTER TABLE deposits ADD COLUMN proof_filename TEXT")
except Exception:
    pass  # موجود بالفعل
```

#### الخطوة 2 — دالة فحص صلاحية

```python
# في database.py
def can_download_proof(user_id: int, is_admin: bool, filename: str) -> bool:
    """V53: IDOR fix — تحقق من ملكية الإيصال عبر DB، لا عبر اسم الملف."""
    if is_admin:
        return True
    with db_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM deposits WHERE user_id=? AND proof_filename=? LIMIT 1",
            (user_id, filename),
        ).fetchone()
    return row is not None
```

#### الخطوة 3 — استبدِل فحص `startswith` بـ DB check

```python
# app.py
@app.route("/uploads/proof/<path:filename>")
@login_required
def download_proof(filename):
    safe = secure_filename(filename)
    if safe != filename:
        abort(400)
    
    user = current_user()
    is_admin = user.get("role") == "admin"
    
    if not can_download_proof(user["id"], is_admin, safe):
        log_audit(
            "PROOF_DOWNLOAD_DENIED",
            actor_id=user["id"],
            metadata={"filename": safe},
        )
        abort(403)
    
    log_audit(
        "PROOF_DOWNLOAD",
        actor_id=user["id"],
        metadata={"filename": safe, "admin_viewing": is_admin},
    )
    return send_from_directory(UPLOAD_DIR, safe, as_attachment=False)
```

#### الخطوة 4 — UUID لأسماء الملفات الجديدة (يقلّل التخمين)

في دالة الرفع، بدل:
```python
fname = f"{user_id}_{int(time.time())}_{secure_filename(original)}"
```

استبدل بـ:
```python
import secrets
ext = os.path.splitext(original)[1].lower()
fname = f"{user_id}_{secrets.token_urlsafe(16)}{ext}"
```

> **ملاحظة:** الـprefix `{user_id}_` يبقى للـoperability (sort/list)، لكن الـtoken لا يمكن تخمينه.

### الاختبار

```python
# tests/test_idor_proof.py
def test_cannot_download_other_user_proof(client, user_a, user_b, tmp_path):
    # a يرفع إيصالاً
    filename = "1_abc123.png"
    create_deposit(user_a.id, 10, "USD", filename)
    
    # b يحاول التنزيل
    with client.session_transaction() as s:
        s["user_id"] = user_b.id
    resp = client.get(f"/uploads/proof/{filename}")
    assert resp.status_code == 403

def test_admin_can_download_any_proof(client, admin_user):
    ...

def test_user_cannot_download_non_existent_file(client, user_a):
    with client.session_transaction() as s:
        s["user_id"] = user_a.id
    resp = client.get("/uploads/proof/1_fake.png")
    assert resp.status_code == 403  # not 404 (avoid enumeration)
```

### Migration القديم

الملفات المرفوعة سابقاً بالأسماء القديمة `{uid}_{ts}_{name}`:
1. سكربت `tools/migrate_proof_filenames.py` يمشي على DB: لكل `deposit.proof` موجود، يسجّل `deposits.proof_filename = proof`.
2. لا تُعيد تسمية الملفات على القرص (مكلف وخطر).

---

## الجزء 2 — Stored XSS في `payment_methods.address / instructions`

### المشكلة

الأدمن يُدخل نصاً عبر `/admin/payment_methods/edit` يُحفظ كـ raw text في DB. لاحقاً في `templates/wallet.html`:

```jinja
<option ... data-address="{{ m.address }}" data-instructions="{{ m.instructions }}">
```

ثم JS:
```javascript
document.getElementById('method-address').innerText = opt.dataset.address;
document.getElementById('method-instructions').innerText = opt.dataset.instructions;
```

**الإيجابيات الحالية:**
- Jinja2 autoescape يعمل ← الـquotes محمية ← لا XSS من قيمة الـattribute.
- `innerText` (لا `innerHTML`) ← آمن للعرض.

**المخاطر المتبقية:**
1. إذا استُبدل `innerText` بـ`innerHTML` لاحقاً (ناسي) = XSS فوراً.
2. إذا أضاف مطوّر قالب آخر `{{ m.instructions|safe }}` = XSS فوراً.
3. Markdown/rich text في المستقبل → يحتاج sanitize صريح.

**المبدأ:** Defense in depth — نظّف عند الإدخال، حتى لو الإخراج آمن اليوم.

### الإصلاح

#### الخطوة 1 — تثبيت bleach

```bash
# requirements.txt
bleach==6.1.0
```

#### الخطوة 2 — sanitizer helper

```python
# app.py أو utils/sanitize.py
import bleach

# للحقول النصية البسيطة (address, phone, IBAN): لا HTML أبداً
PLAIN_TEXT_TAGS = []
PLAIN_TEXT_ATTRS = {}

# للحقول الـrich (instructions): سماح محدود
RICH_TEXT_TAGS = ["br", "b", "i", "strong", "em", "ul", "ol", "li", "p", "a"]
RICH_TEXT_ATTRS = {"a": ["href", "title"]}
RICH_TEXT_PROTOCOLS = ["http", "https", "mailto"]


def clean_plain_text(value: str, max_len: int = 500) -> str:
    """V53: HTML-strip + length cap للحقول النصية البسيطة."""
    if not value:
        return ""
    cleaned = bleach.clean(value, tags=PLAIN_TEXT_TAGS, attributes=PLAIN_TEXT_ATTRS, strip=True)
    return cleaned[:max_len].strip()


def clean_rich_text(value: str, max_len: int = 2000) -> str:
    """V53: يسمح بـHTML محدود — للـinstructions فقط."""
    if not value:
        return ""
    cleaned = bleach.clean(
        value,
        tags=RICH_TEXT_TAGS,
        attributes=RICH_TEXT_ATTRS,
        protocols=RICH_TEXT_PROTOCOLS,
        strip=True,
    )
    # linkify روابط plain-text
    cleaned = bleach.linkify(cleaned)
    return cleaned[:max_len].strip()
```

#### الخطوة 3 — طبّق في routes الأدمن

```python
# app.py — admin_payment_method_edit / create
@app.route("/admin/payment_methods/<int:mid>/edit", methods=["POST"])
@admin_required
def admin_payment_method_edit(mid):
    address = clean_plain_text(request.form.get("address", ""), max_len=200)
    instructions = clean_rich_text(request.form.get("instructions", ""), max_len=1500)
    name = clean_plain_text(request.form.get("name", ""), max_len=100)
    currency = clean_plain_text(request.form.get("currency", ""), max_len=10)
    
    update_payment_method(mid, name=name, address=address,
                          instructions=instructions, currency=currency)
    log_audit("ADMIN_PAYMENT_METHOD_EDIT", target_id=mid,
              new_value={"name": name, "currency": currency})
    flash("تم التحديث", "success")
    return redirect(url_for("admin_payment_methods"))
```

طبّق نفس النمط على:
- `admin_add_game` / `admin_edit_game` (name, description)
- `admin_add_game_product` / `admin_edit_game_product` (display_name, description)
- `admin_settings` لأي حقل نصي حر
- `admin_game_visibility_reason` لو وجد

#### الخطوة 4 — Template — ترسيخ `|e`

تأكّد أن القوالب لا تستخدم `|safe` على قيم أدخلها المستخدم/الأدمن. استثناء وحيد يُسمح: SVG icons المدمجة في `base.html` (ثابتة).

```bash
grep -rn "|safe" templates/
```

لكل نتيجة، راجع: هل القيمة من DB/user-input → احذف `|safe` وترك autoescape يعمل.

#### الخطوة 5 — اختبار XSS

```python
# tests/test_xss_admin.py
def test_admin_payment_method_strips_script_tag(admin_client, db_path):
    admin_client.post("/admin/payment_methods/create", data={
        "name": "<script>alert(1)</script>Test",
        "address": "ADDR<img src=x onerror=alert(1)>",
        "instructions": "Click <a href='javascript:alert(1)'>here</a>",
        "currency": "USD",
    })
    # استعلم من DB مباشرة
    methods = list_payment_methods()
    m = next(x for x in methods if "Test" in x["name"])
    assert "<script>" not in m["name"]
    assert "onerror" not in m["address"]
    assert "javascript:" not in m["instructions"]
```

---

## 3) تحديث `project-context.md`

- بنود المُنجزة: "IDOR على uploads/proof + Stored XSS في payment_methods".
- قرار معماري:
  > **Defense in depth على user input:** sanitize عند الإدخال (`bleach.clean`) حتى لو الإخراج آمن اليوم. يحمي ضد refactors لاحقة تستبدل `innerText`/autoescape بـ`innerHTML`/`|safe` عن طريق الخطأ.
- قرار معماري:
  > **IDOR على تنزيل الإيصالات:** الفحص عبر DB (`deposits.proof_filename + user_id`) بدل startswith. أسماء ملفات جديدة تستخدم `secrets.token_urlsafe(16)`. الإيصالات القديمة تُسجَّل في DB عبر one-time migration.
