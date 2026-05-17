---
inclusion: manual
---

# Playbook — إعداد Cloudflare (CDN + WAF + Bot)

> **متى يُستدعى:** تنفيذ البند High رقم 11 (Cloudflare).
> **المدة المتوقعة:** نصف يوم (configuration في الـdashboard، لا تعديل كود تقريباً).
> **الكلفة:** Cloudflare Free Plan يكفي تماماً. الترقية إلى Pro ($20/شهر) اختيارية لاحقاً.

---

## 1) لماذا Cloudflare

موقع شحن ألعاب عربي = هدف مُغرٍ لـ:
- L7 DDoS (منافس حاقد).
- Credential stuffing (account takeover).
- Scraping أسعار وكاتالوج من منافسين.
- Chargebacks bots لو ربطت بوابة دفع رسمية.

Cloudflare يحل:
- ✅ **CDN global** — LCP ينخفض 500-1500ms للخليج.
- ✅ **WAF** — OWASP rules + Managed rulesets.
- ✅ **Bot Management** — detect + challenge.
- ✅ **Rate Limiting** — طبقة إضافية فوق Flask-Limiter.
- ✅ **SSL مجاني** — Full (strict).
- ✅ **Under Attack Mode** — Challenge لكل زائر في حالة طوارئ.

---

## 2) المعمارية المستهدفة

```
[user browser]
     │
     │  DNS: tecnogems.com → Cloudflare anycast
     │
     ▼
[Cloudflare edge]  ← WAF + Bot + CDN cache + Rate limit
     │
     │  Argo Smart Routing (اختياري Pro)
     ▼
[origin: gunicorn]  ← يستقبل IP حقيقي عبر CF-Connecting-IP
```

---

## 3) إعداد DNS

### الخطوة 1 — ربط الدومين

1. سجّل في cloudflare.com → Add a Site → أدخِل `tecnogems.com`.
2. Cloudflare يفحص DNS الحالي → يستورد السجلات.
3. غيّر nameservers في سجلّ الدومين (GoDaddy/Namecheap) إلى الـ2 اللذين أعطاهما Cloudflare.
4. انتظر propagation (5 دقائق إلى 24 ساعة).

### الخطوة 2 — السجلات المطلوبة

| Type | Name | Content | Proxy |
|------|------|---------|-------|
| A | `@` | `<origin-ip>` | 🟠 Proxied |
| A | `www` | `<origin-ip>` | 🟠 Proxied |
| CNAME | `staging` | `<staging-host>` | ⚪ DNS only (للـhealth checks المباشرة) |
| TXT | `@` | SPF/DMARC للإيميل | ⚪ DNS only |

**ملاحظة:** `Proxied` (البرتقالي) هو المفعّل للـCDN/WAF. لا تضع origin IP في أي سجل DNS only إلا عبر subdomain صعب التخمين.

---

## 4) SSL/TLS

### Crypto → SSL/TLS → Overview

اختر **Full (strict)**. هذا يتطلب شهادة صالحة على الـorigin.

**إذا origin على Heroku/Railway** — موجودة شهادة. تمام.

**إذا origin على VPS خاص** — أصدر شهادة من Cloudflare Origin CA (مجاني، صلاحية 15 سنة):

1. SSL/TLS → Origin Server → Create Certificate.
2. انسخ الـcert + key إلى `/etc/ssl/tecnogems/`.
3. حدّث nginx/caddy config.

### Edge Certificates

- ✅ Always Use HTTPS: ON
- ✅ HTTP Strict Transport Security (HSTS): ON — max-age 12 months. ⚠️ **تأكّد أولاً أن كل subdomains مدعومة HTTPS**، وإلا HSTS يكسرها.
- ✅ Minimum TLS Version: TLS 1.2
- ✅ Opportunistic Encryption: ON
- ✅ TLS 1.3: ON
- ✅ Automatic HTTPS Rewrites: ON

---

## 5) WAF Configuration

### Security → WAF

#### Managed Rules (Free)

```
✅ Cloudflare Managed Ruleset  →  Set to "Challenge" (not Block)
✅ Cloudflare OWASP Core Ruleset  →  Paranoia Level 1, Anomaly Score Threshold 40
```

Paranoia Level 1 يكفي لتطبيق Flask؛ أعلى يُولّد false positives.

#### Custom Rules (مهمة لموقع شحن)

**Rule 1: حماية `/admin/*` من دول غير مستهدفة**

```
(http.request.uri.path contains "/admin") and 
(ip.geoip.country ne "SA" and 
 ip.geoip.country ne "AE" and 
 ip.geoip.country ne "KW" and 
 ip.geoip.country ne "SY" and
 ip.geoip.country ne "EG")
→ Action: Challenge (Managed)
```

> عدّل القائمة حسب بلدك وأدمنك.

**Rule 2: challenge لـTOR exit nodes**

```
(ip.src in $tor)
→ Action: Block
```

**Rule 3: rate limit على login/register**

انقل إلى قسم Rate Limiting أدناه.

**Rule 4: حجب user agents مشبوهة**

```
(http.user_agent contains "python-requests") or 
(http.user_agent contains "curl/") or 
(http.user_agent contains "wget/") and
(http.request.uri.path ne "/api/")
→ Action: Block
```

> استثنِ `/api/` لو كان لديك مستخدمو CLI شرعيون.

---

## 6) Rate Limiting (Free tier يسمح بـ1 قاعدة)

### Security → WAF → Rate limiting rules

**القاعدة الموصى بها:**

```
Expression: 
  (http.request.uri.path eq "/login") or 
  (http.request.uri.path eq "/register") or 
  (http.request.uri.path eq "/reset-password")

Characteristics: IP
Requests: 10
Period: 1 minute
Action: Block for 10 minutes
```

> Free tier = 1 rule. لقواعد أكثر ارقِ إلى Pro، أو اعتمد على Flask-Limiter بالإضافة.

---

## 7) Bot Management

### Security → Bots

- ✅ **Bot Fight Mode**: ON (Free tier).
- ✅ **Verified Bots**: Allow (Googlebot, Bingbot…).
- **Super Bot Fight Mode**: Pro plan فقط.

---

## 8) Caching

### Caching → Configuration

- Browser Cache TTL: **Respect Existing Headers** (Flask يتحكم).
- Always Online™: ON.
- Development Mode: **OFF** (شغّلها فقط عند الـdebug).

### Page Rules (Free tier: 3 rules)

**Rule 1: Cache كل الـstatic**
```
URL: tecnogems.com/static/*
→ Cache Level: Cache Everything
→ Edge Cache TTL: 1 month
```

**Rule 2: Never cache الـadmin والـAPI**
```
URL: tecnogems.com/admin/*
→ Cache Level: Bypass
→ Disable Performance
```

**Rule 3: Never cache auth**
```
URL: tecnogems.com/login
→ Cache Level: Bypass
```

---

## 9) Speed

### Speed → Optimization

- ✅ Auto Minify: HTML + CSS + JS
- ✅ Brotli: ON
- ✅ Early Hints: ON
- ✅ Rocket Loader: **OFF** (يكسر CSP nonce!)
- ✅ Mirage: Pro
- ✅ Polish: Pro

---

## 10) تغييرات مطلوبة في الكود

### أ) قراءة IP الحقيقي

`Flask-Limiter` يستخدم `get_remote_address` الذي يُرجع `request.remote_addr`. لكن Cloudflare يضع IP الحقيقي في `CF-Connecting-IP`.

أضِف في `app.py` بعد إنشاء Flask:

```python
# V53: خلف Cloudflare، IP الحقيقي في CF-Connecting-IP
# لا تستخدم X-Forwarded-For مباشرةً (يمكن للمستخدم تزويرها ما لم تكن خلف proxy موثوق).
from werkzeug.middleware.proxy_fix import ProxyFix
# ProxyFix يقرأ X-Forwarded-* فقط، ليس CF-Connecting-IP مباشرة.
# لهذا نستخدم callable مخصص للـLimiter.

def get_real_ip():
    # CF-Connecting-IP هو الأوثق خلف Cloudflare.
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip
    # fallback للـlocal dev
    return request.remote_addr or "0.0.0.0"

# ثم في Limiter config:
limiter = Limiter(get_real_ip, **_limiter_kwargs)
```

> **تحذير:** هذا يعمل فقط إذا الـorigin لا يقبل طلبات مباشرة من الإنترنت (تجاوز Cloudflare). إما:
> - استخدم `cloudflared tunnel` (مجاني، Zero Trust) — بدون IP عام.
> - أو في firewall اسمح فقط بـ[Cloudflare IP ranges](https://www.cloudflare.com/ips/).
> بدون هذا، مهاجم يجد IP الـorigin ويُزيّف `CF-Connecting-IP` → bypass.

### ب) Logging الـIP الصحيح في audit

في `audit.py` → `log_audit()`:

```python
ip = request.headers.get("CF-Connecting-IP") or request.remote_addr
```

### ج) `safe_next_url` — أضِف دومين CF staging إن وجد

لا تغييرات عادةً.

---

## 11) Analytics + Monitoring

### Analytics & Logs → Web Analytics

تفعيل مجاني. لا حاجة لـJS snippet (يأخذها من الـedge).

### Notifications

اشترك في:
- Origin Error Rate Alert (فوق 5% لمدة 5 دقائق).
- DDoS Attack Alert.
- SSL Certificate expiration.

---

## 12) اختبار

1. **DNS propagation:**
   ```bash
   dig tecnogems.com  
   # توقَّع Cloudflare IPs (104.16.x.x أو 104.21.x.x أو 172.67.x.x)
   ```

2. **SSL:**
   ```bash
   curl -I https://tecnogems.com
   # توقَّع: server: cloudflare + HTTP/2 200
   ```

3. **WAF اختبار سلبي (لا تُنفّذ في إنتاج لمدة طويلة):**
   ```bash
   curl "https://tecnogems.com/?id=1' UNION SELECT--"
   # توقَّع: 403 من Cloudflare
   ```

4. **CDN caching:**
   ```bash
   curl -I https://tecnogems.com/static/css/tecnogems.min.css
   # توقَّع: cf-cache-status: HIT (بعد الطلب الثاني)
   ```

5. **IP in Flask:** افتح `/api/me` بعد تسجيل دخول، افحص `audit_log` في DB:
   ```sql
   SELECT ip FROM audit_log ORDER BY ts DESC LIMIT 1;
   ```
   توقَّع IP حقيقي للعميل، ليس IP الـCloudflare edge.

---

## 13) Rollout Steps

1. [ ] Cloudflare account + domain linked.
2. [ ] DNS records موجودة + proxied.
3. [ ] SSL Full (strict) + origin cert valid.
4. [ ] WAF Managed Rules ON (challenge mode، ليس block مباشرة أول أسبوع).
5. [ ] Page rules للـstatic + admin.
6. [ ] كود `get_real_ip()` mergeed.
7. [ ] Firewall على origin يقبل CF IPs فقط (أو cloudflared tunnel).
8. [ ] Notifications مُعدّة.
9. [ ] 48 ساعة مراقبة → إذا false positives منخفضة، حوّل WAF إلى Block mode.

---

## 14) تحديث `project-context.md`

- أضِف للمُنجزة.
- قرار معماري:
  > **Cloudflare كطبقة أولى (CDN + WAF + Bot + Rate limit)**. الـorigin خلف cloudflared tunnel (أو firewall يسمح بـCF IPs فقط) لمنع bypass. `get_real_ip()` يقرأ `CF-Connecting-IP` للـFlask-Limiter و audit logs.
- حدّث قسم البنية على القرص (لا تغيير جذري).
- `DEPLOYMENT.md` (إن لم يكن موجوداً) يُنشأ ويحوي خطوات هذا الـplaybook مختصرة.
