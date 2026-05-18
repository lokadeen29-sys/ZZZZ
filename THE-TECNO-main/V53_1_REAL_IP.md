# V53.1 — قراءة IP العميل الحقيقي خلف Cloudflare / Heroku

## الملخّص التنفيذي

كان `Flask-Limiter` و `log_audit(ip=...)` و سجلّات الـorigin guard تقرأ
`request.remote_addr` مباشرةً. خلف Cloudflare/Heroku هذه القيمة تساوي IP الـproxy
وليس العميل، ممّا يعني:

1. **Rate-limit ينهار:** كل العملاء يشتركون في نفس bucket عبر IP الـproxy
   ⇒ `10 per minute` على `/login` يصبح حدًّا واحداً للعالم بأسره.
2. **Audit logs بلا فائدة جنائية:** كل صفوف `audit_log.ip` تحمل نفس IP.
3. **Origin guards لا تُسجّل من قام بالطلب فعلاً.**

تم إضافة module مركزي `request_ip.py` يحلّ المشكلتين معاً عبر:

- `ProxyFix` (من `werkzeug.middleware.proxy_fix`) لقراءة `X-Forwarded-For` بأمان.
- `get_real_ip()` يفضّل `CF-Connecting-IP` ثم يقع على `remote_addr` المُصحَّح.

---

## الملفات المعدَّلة

| الملف | التغيير |
|--------|---------|
| `request_ip.py` (جديد) | `apply_proxy_fix(app)` + `get_real_ip()` مع تحقّق `ipaddress.ip_address()` لكل قيمة |
| `app.py` | استدعاء `apply_proxy_fix(app)` فور إنشاء Flask + استبدال Limiter key + استبدال 23 موضع `request.remote_addr` بـ`get_real_ip()` |
| `routes/auth_bp.py` | استيراد `get_real_ip` من `app` + استبدال 3 مواضع |
| `.env.example` | توثيق `TRUST_PROXY_HOPS` و `TRUST_CF_CONNECTING_IP` |
| `tests/conftest.py` | إضافة `request_ip` لقائمة الـmodules التي تُعاد قراءتها بين الاختبارات |

---

## الإعدادات الجديدة (env vars)

### `TRUST_PROXY_HOPS`

عدد الـreverse-proxies الموثوقة بين العميل والتطبيق.

| القيمة | متى تُستخدم |
|--------|-------------|
| `0` | تطوير محلي بدون proxy. يُعطّل ProxyFix كلياً |
| `1` | Heroku وحده (الافتراضي في الإنتاج) |
| `2` | Cloudflare → Heroku |

عند تركه فارغاً، يُستخدم الافتراضي حسب `FLASK_ENV`:
- `production` → `1`
- أي قيمة أخرى → `0`

### `TRUST_CF_CONNECTING_IP`

`1` (افتراضي) لقبول رأس `CF-Connecting-IP`، `0` لتجاهله.

> ⚠️ **يجب** ضبطه إلى `0` إذا كان origin Heroku قابلاً للوصول مباشرة من
> الإنترنت (تجاوز Cloudflare ممكن ⇒ مهاجم يزيّف الرأس بسهولة).
> للحماية الكاملة استخدم `cloudflared tunnel` أو firewall على
> [Cloudflare IP ranges](https://www.cloudflare.com/ips/).

---

## ترتيب القرار في `get_real_ip()`

```
1. CF-Connecting-IP   ← إذا TRUST_CF_CONNECTING_IP=1 و القيمة IP صالح
2. request.remote_addr ← مُصحَّح تلقائياً بـProxyFix إذا TRUST_PROXY_HOPS≥1
3. "0.0.0.0"           ← fallback عند فشل كل ما سبق
```

كل قيمة تُمرَّر عبر `ipaddress.ip_address()` لمنع رؤوس مشوّهة (مثل
`CF-Connecting-IP: <script>alert(1)</script>`) من تلويث Limiter keys
أو حقول `audit_log.ip` في DB.

---

## لماذا ProxyFix وحده لا يكفي

- `ProxyFix` يقرأ `X-Forwarded-For` فقط. خلف Cloudflare يكون السلسلة
  `client, cf-edge, heroku-router` ⇒ Heroku يستبدلها برأس واحد عند
  الـorigin، فيصعب استخراج IP العميل بثقة عبر hop count بسيط.
- `CF-Connecting-IP` يضعه Cloudflare من الـTCP socket مباشرة ⇒ يتجاوز كل
  معلومات `X-Forwarded-For` ويعطي القيمة الأوثق.
- نعتمد على ProxyFix كـfallback عند:
  - تطبيق على Heroku بدون Cloudflare.
  - طلبات صحّية من Heroku router مباشرةً.

---

## التحقّق ما بعد النشر

### 1. اختبار rate-limit حقيقي (بـ2 IP مختلفين)

```bash
# من جهازين مختلفين أو network مختلف:
for i in $(seq 1 11); do
  curl -X POST https://tecnogems.com/login \
    -d "email=fake@x.com&password=wrong" -o /dev/null -s -w "%{http_code}\n"
done
```

قبل V53.1: كل المحاولات من جميع العملاء يشاركون نفس bucket ⇒
كل العملاء يُحجَبون معاً عند 10 محاولات فاشلة من العالم بأسره.

بعد V53.1: كل عميل يحصل على bucket مستقل ⇒ تجربة مستخدم سليمة + حماية
فعّالة من brute-force.

### 2. فحص `audit_log.ip` في DB

```sql
SELECT ip, COUNT(*) FROM audit_log
WHERE ts > strftime('%s', 'now', '-1 hour')
GROUP BY ip ORDER BY 2 DESC;
```

قبل V53.1: قيمة موحَّدة (IP الـCloudflare/Heroku edge).
بعد V53.1: تنوّع IPs حقيقي يعكس قاعدة المستخدمين.

### 3. تحقّق من الإعدادات

في الـlogs عند الإقلاع:

```
INFO ProxyFix enabled with 1 hop(s)            # إذا TRUST_PROXY_HOPS≥1
INFO ProxyFix disabled (TRUST_PROXY_HOPS=0)    # إذا dev محلي
```

---

## التراجع (rollback)

```bash
git revert <commit-sha>
```

أو يدوياً عبر ضبط `TRUST_PROXY_HOPS=0` و `TRUST_CF_CONNECTING_IP=0`
⇒ السلوك يعود مطابقاً لـpre-V53.1 بدون نشر كود جديد.
