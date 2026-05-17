---
inclusion: manual
---

# Playbook — إصلاح CSP Nonce على جميع inline scripts

> **متى يُستدعى:** تنفيذ البند Critical رقم 1 (CSP Nonce).
> **المدة المتوقعة:** يوم (~5-6 ساعات).
> **الخطر الحالي:** CSP في `app.py` يفرض `script-src 'self' 'nonce-<X>'` (بدون `unsafe-inline`) — لكن 12 قالباً فيها `<script>` بدون `nonce`. النتيجة: إما المتصفحات الحديثة تحجب الـJS (الصفحات مكسورة في الإنتاج) أو الـCSP لا يُطبَّق فعلياً (أمان وهمي).

---

## 1) السياق — القائمة الكاملة

تحقَّق من الكود مباشرةً عبر:
```bash
grep -rn "<script" templates --include="*.html" | grep -v "nonce=" | grep -v "application/ld+json"
```

### القوالب المصابة (12)

| # | الملف | السطر | الـscript block |
|---|-------|-------|------|
| 1 | `templates/home.html` | 48 | home JS |
| 2 | `templates/login.html` | 51 | password toggle |
| 3 | `templates/register.html` | 66 | form validation + password strength |
| 4 | `templates/reset_password.html` | 29 | password toggle |
| 5 | `templates/checkout.html` | 56 | player_id validation |
| 6 | `templates/wallet.html` | 55 | method selector |
| 7 | `templates/products.html` | 75 | filter/sort (ملاحظة: السطران 4 و 7 فيهما ld+json بـnonce صحيح، فقط السطر 75 ناقص) |
| 8 | `templates/orders.html` | 69 | copy-to-clipboard |
| 9 | `templates/games.html` | 42 | search filter |
| 10 | `templates/product_groups.html` | 38 | group selector |
| 11 | `templates/admin/games.html` | 78 | admin panel JS |
| 12 | `templates/admin/game_products.html` | 112 | admin panel JS |

### ما يعمل بشكل صحيح بالفعل (لا تلمسه)

- `templates/base.html` (كل الـscripts فيها `nonce`).
- `templates/products.html` السطر 4 و 7 (ld+json schemas).

---

## 2) خطة التنفيذ — 3 مسارات

اختر **المسار B**. المسار A سريع وسيئ التصميم، المسار C ممتاز لكن أطول من اللازم لهذا الـPR.

### المسار A — إضافة `nonce` فقط (سريع، 30 دقيقة)

```jinja
<!-- قبل -->
<script>
  ...
</script>

<!-- بعد -->
<script nonce="{{ csp_nonce }}">
  ...
</script>
```

**عيب:** يُبقي inline JS (أصعب cache + أصعب debug).

### المسار B — نقل JS إلى ملفات + إضافة `nonce` للمتبقي الضروري (مُختار)

1. لكل قالب فيه `<script>` كبير (> 10 أسطر)، انقل الكود إلى `static/js/pages/<name>.js`.
2. في القالب استبدل بـ:
   ```jinja
   <script src="{{ url_for('static', filename='js/pages/register.js') }}" 
           defer nonce="{{ csp_nonce }}"></script>
   ```
3. للـscripts القصيرة (< 10 أسطر، مثل password toggle) اتركها inline مع `nonce`.

### المسار C — Alpine.js / HTMX (refactor كامل)

مؤجَّل إلى PR مستقل. لا تدمجه هنا.

---

## 3) تنفيذ المسار B — خطوات محدَّدة

### الخطوة 1 — أنشئ `static/js/pages/`

```bash
mkdir -p static/js/pages
```

### الخطوة 2 — انقل JS الكبير

لكل قالب من (register, home, products, orders, games, admin/games, admin/game_products):

```bash
# مثال: register.html
# قبل الـediting، اقرأ السطور بين <script> و </script>
# احفظها في static/js/pages/register.js
# احذفها من القالب واستبدل بـ <script src=...>
```

### الخطوة 3 — القوالب القصيرة

login, reset_password, checkout, wallet, product_groups → فقط أضِف `nonce="{{ csp_nonce }}"` للـ`<script>` الموجود.

### الخطوة 4 — minification (اختياري في هذا الـPR)

أضِف في `package.json` / build pipeline: esbuild لملفات `static/js/pages/*.js` → `.min.js`.

### الخطوة 5 — cache-busting

تأكّد أن `base.html` يمرّر query param `?v=` لـJS. موجود بالفعل لـ`app.min.js`. طبّق نفس النمط:

```jinja
<script src="{{ url_for('static', filename='js/pages/register.js') }}?v={{ asset_version }}" 
        defer nonce="{{ csp_nonce }}"></script>
```

---

## 4) CI Guard — منع تكرار المشكلة

أضِف اختبار في `tests/test_csp_templates.py`:

```python
"""V53: اختبار يمنع inline <script> بدون nonce في أي قالب مستقبلاً."""
import re
from pathlib import Path

SCRIPT_RE = re.compile(
    r'<script(?![^>]*(?:type=["\']application/ld\+json["\']|nonce=["\']))[^>]*>',
    re.IGNORECASE,
)

def test_no_inline_script_without_nonce():
    offenders = []
    for html in Path("templates").rglob("*.html"):
        content = html.read_text(encoding="utf-8")
        # احذف تعليقات Jinja {# ... #} قبل البحث
        cleaned = re.sub(r"\{#.*?#\}", "", content, flags=re.DOTALL)
        for match in SCRIPT_RE.finditer(cleaned):
            offenders.append(f"{html}:{match.start()}: {match.group()[:80]}")
    assert not offenders, (
        "Inline <script> without nonce found. Add nonce=\"{{ csp_nonce }}\" or "
        "move JS to static/js/pages/:\n" + "\n".join(offenders)
    )
```

> الاستثناء الوحيد المسموح: `type="application/ld+json"` لأن schema.org data ليست قابلة للتنفيذ.

---

## 5) اختبار يدوي

1. شغّل التطبيق في production mode:
   ```bash
   FLASK_ENV=production SECRET_KEY=x REDIS_URL=redis://... \
     gunicorn -k gthread -w 1 wsgi:app
   ```
2. افتح كل صفحة في المتصفح مع DevTools → Console + Network.
3. تأكّد: **لا أخطاء CSP في الـConsole**، كل الـJS ينفَّذ.
4. صفحات حرجة للفحص:
   - `/register` — password strength meter يعمل؟
   - `/checkout/...` — validation يعمل؟
   - `/orders` — copy button يعمل؟
   - `/admin/games` — add/edit/delete game يعمل؟

---

## 6) خطوة اختيارية — CSP Reporting

بعد الإصلاح، أضِف CSP report endpoint للاصطياد المبكر لمخالفات مستقبلية:

```python
# app.py
response.headers["Content-Security-Policy"] += "; report-uri /csp-report"
```

```python
@app.route("/csp-report", methods=["POST"])
@csrf.exempt
def csp_report():
    report = request.get_json(silent=True) or {}
    log.warning("CSP_VIOLATION: %s", json.dumps(report, default=str))
    return "", 204
```

> لا تجعل هذا إلزامياً الآن — أضِفه في PR لاحق.

---

## 7) تحديث `project-context.md`

- أضِف البند للمُنجزة.
- قرار معماري:
  > **كل inline JS إمّا يخرج إلى `static/js/pages/*.js` أو يحصل على `nonce="{{ csp_nonce }}"`**. Schema.org ld+json يُستثنى لأنه ليس executable. اختبار `test_csp_templates.py` يحرس ضد التراجع.
- حذَّر القسم "style-src لا يزال `unsafe-inline`" (بند F في الـcontext الحالي) أنه الخطوة التالية.
