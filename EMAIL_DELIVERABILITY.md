# V67 — منع رسائل التفعيل من الوصول إلى Spam (Email Deliverability)

> ملخص سريع بالعربية: التحسينات على الكود (Headers / SPF alignment / نص أفضل
> / صفحة `/email-info`) تم تنفيذها بالكامل في هذا الـ release. **لكن** هذا
> وحده لا يكفي — يجب ضبط 4 سجلات DNS (SPF, DKIM, DMARC, MX) على نطاق
> `tecnogems.com` ليصدّق Gmail / Outlook / Yahoo أن الرسائل قادمة منك فعلاً.
> اتّبع الخطوات أدناه بالترتيب.

---

## 1. ما الذي تم تعديله في الكود؟

| التعديل | السبب |
|---|---|
| إزالة `Precedence: bulk` | كانت تُصنّف رسائل التفعيل كـ "نشرة بريدية" → Spam فوري في Gmail. |
| إزالة `List-Unsubscribe: <mailto:...>` | نفس السبب — تفعيل هذا الـ header يطلب من Gmail معاملتها كقوائم بريدية. |
| إضافة `Sender:` header | عند استخدام Gmail SMTP، يحفظ الاسم الظاهر في `From:` ويمنع إعادة كتابته. |
| إضافة `Auto-Submitted: auto-generated` و `X-Auto-Response-Suppress: All` | إشارات صريحة أن الرسالة تلقائية خدمية وليست تسويقية. |
| `envelope sender = MAIL_USERNAME` | يضمن توافق SPF (Gmail يفشل alignment إذا كان bounce-from مختلف). |
| نص plain text كامل ومماثل للـ HTML | نسبة نص/HTML منخفضة = إشارة spam قوية. |
| إضافة صفحة عامة `/email-info` ووضع رابطها في تذييل البريد | يعزز ثقة Gmail بأن النطاق حقيقي ومنشور. |
| `MAIL_FROM_NAME` و `MAIL_REPLY_TO` كمتغيرات بيئة | فصل الاسم/عنوان الرد عن مُرسل المصادقة. |

---

## 2. متغيرات البيئة المطلوبة في الإنتاج

في ملف `.env` على السيرفر:

```env
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=1
MAIL_USERNAME=noreply@tecnogems.com      # حساب Google Workspace على نطاقك
MAIL_PASSWORD=xxxxxxxxxxxxxxxx           # App Password 16 حرف (لا يحوي مسافات)
MAIL_FROM=noreply@tecnogems.com          # نفس MAIL_USERNAME أو alias معتمد
MAIL_FROM_NAME=TecnoGems
MAIL_REPLY_TO=support@tecnogems.com      # اختياري
BASE_URL=https://tecnogems.com
```

> ⚠️ **أهم نقطة:** `MAIL_FROM` يجب أن يكون على **نفس النطاق** الذي تملك فيه
> سجلات DNS. الإرسال من `@gmail.com` إلى مستخدمين آخرين لن يصدّق DKIM/SPF
> لنطاقك، وسيستمر الذهاب إلى Spam.

---

## 3. سجلات DNS المطلوبة (الجزء الأهم)

### 3.1 SPF — يصرّح بالخوادم المسموح لها بالإرسال نيابةً عن نطاقك

أضف سجلًا واحدًا فقط من نوع TXT على الـ root (`@`):

```
Type: TXT
Name: @
Value: v=spf1 include:_spf.google.com ~all
```

(إذا كنت تستخدم خدمة أخرى مثل SendGrid أو Mailgun استبدل `_spf.google.com`
بالقيمة التي يعطيها مزودك، أو أضفها معاً عبر `include:` متعدد، لكن يجب
أن تنتهي بـ `~all` أو `-all`).

### 3.2 DKIM — التوقيع الرقمي لكل رسالة

في Google Admin Console:

1. ادخل **Apps → Google Workspace → Gmail → Authenticate email**.
2. اختر النطاق `tecnogems.com` ثم **Generate new record** (طول 2048-bit).
3. ستحصل على Hostname (مثلاً `google._domainkey`) وقيمة TXT طويلة جدًا.
4. أضف السجل في DNS الخاص بنطاقك:
   ```
   Type: TXT
   Name: google._domainkey
   Value: v=DKIM1; k=rsa; p=MIIBIj...long-public-key...
   ```
5. ارجع إلى Google Admin بعد ~30 دقيقة واضغط **Start authentication**.

### 3.3 DMARC — السياسة عند فشل SPF/DKIM

أضف TXT على `_dmarc`:

```
Type: TXT
Name: _dmarc
Value: v=DMARC1; p=none; rua=mailto:dmarc-reports@tecnogems.com; pct=100; aspf=s; adkim=s
```

- ابدأ بـ `p=none` لمدة أسبوعين لقراءة التقارير.
- بعد التأكد أن SPF و DKIM يمرّان لكل الرسائل، انقل إلى `p=quarantine`،
  ثم لاحقاً إلى `p=reject`.

### 3.4 MX (لاستقبال DMARC reports وردود الدعم)

```
Type: MX
Name: @
Priority: 1
Value: smtp.google.com   # أو سجلات Google Workspace القياسية
```

---

## 4. التحقق بعد الضبط

### 4.1 أدوات مجانية

- **MXToolbox SuperTool**: أدخل `tecnogems.com` وافحص SPF, DKIM, DMARC.
- **mail-tester.com**: أرسل رسالة تفعيل تجريبية إلى العنوان الذي يعطيك إياه
  الموقع، يجب أن تحصل على **9/10 أو 10/10**.
- **Google Postmaster Tools** (postmaster.google.com): اربط النطاق وراقب
  Spam Rate يومياً (يجب أن تبقى تحت 0.1%).

### 4.2 اختبار يدوي

أرسل رسالة تفعيل إلى:

- Gmail (شخصي، Workspace)
- Outlook / Hotmail
- Yahoo Mail
- بريد آخر داخلي (icloud, ProtonMail)

في Gmail، افتح الرسالة → **⋮** → **Show original**. تحقق:

- `SPF: PASS`
- `DKIM: PASS with domain tecnogems.com`
- `DMARC: PASS`

---

## 5. أسباب شائعة لاستمرار الذهاب إلى Spam

| العَرَض | السبب الأرجح |
|---|---|
| `From:` يظهر "via gmail.com" بجانب الاسم | DKIM لم يُفعَّل لنطاقك في Workspace. |
| Gmail Original يقول `dkim=neutral (no signature)` | السجل DKIM موجود في DNS لكن لم تضغط Start authentication. |
| `spf=softfail` | بعض السيرفرات ترسل لكنها ليست في `v=spf1 include:` — أضفها. |
| الرسالة تصل لشخص واحد لكن تذهب لـ Spam عند آخر | سمعة IP الخاصة بـ Gmail. لا حل سوى الانتظار وزيادة الـ engagement. |
| لا تصل أبدًا بدون ظهور في Spam | غالبًا تم رفضها بعد SMTP — افحص logs السيرفر بحثًا عن `SMTPException`. |
| تصل لـ Outlook فقط في Spam | sign up في Microsoft SNDS لمراقبة الـ IP. |
| رسالة الاختبار تصل لكن التفعيل لا تصل | مرَّ القيود في Gmail لكلمات في الموضوع. الكود الحالي لا يستخدم كلمات spam-trigger. لو حدثت أضف logging حول `_send_email_sync`. |

---

## 6. ضبط مرة واحدة + مراقبة دورية

### مرة واحدة

- [ ] Google Workspace أو SMTP بنطاقك (لا تستخدم gmail.com مباشرة).
- [ ] SPF, DKIM, DMARC, MX على tecnogems.com.
- [ ] حساب Postmaster Tools مفعّل.
- [ ] حساب SNDS من Microsoft (للـ Outlook reputation).
- [ ] صفحة `/email-info` تعمل بـ HTTPS بدون 404.
- [ ] رابط `BASE_URL` في `.env` صحيح ومتطابق مع نطاق `MAIL_FROM`.

### دوريًا (شهريًا)

- [ ] راجع Postmaster: Spam Rate < 0.1%.
- [ ] راجع تقارير DMARC: لا توجد إرسالات مزيفة من جهات أخرى.
- [ ] اختبر deliverability عبر mail-tester مرة شهريًا.
- [ ] احذف الحسابات الراكدة من DB (إرسال إلى bounces متكرر يُتلف السمعة).

---

## 7. إذا استمر المشكل بعد كل ما سبق

غالبًا تكون المشكلة في **الـ IP** الذي يخرج منه الإرسال:

1. **Gmail SMTP** — لا تتحكم في الـ IP. يكفي الالتزام بـ Workspace + DKIM.
2. **VPS خاص** (ترسل بنفسك) — IP السيرفر قد يكون مدرجًا في blacklists.
   استعمل [MXToolbox blacklist check](https://mxtoolbox.com/blacklists.aspx).
3. **خدمة احترافية** — انتقل إلى **Postmark** (Transactional Streams)،
   **Resend** أو **SendGrid Pro**. أسعار البدء ~10$/شهر وتعطيك:
   - IPs مدارة وذات سمعة عالية.
   - DKIM/SPF تلقائي.
   - تقارير Open/Bounce/Spam.
   - Failover إذا فشل IP واحد.

   الكود الحالي يدعم أي SMTP — يكفي تغيير 4 متغيرات في `.env`.

---

## 8. مرجع سريع للـ Headers الحالية بعد التعديل

```
From: TecnoGems <noreply@tecnogems.com>
Sender: noreply@tecnogems.com
Reply-To: support@tecnogems.com
Subject: TecnoGems - تفعيل حسابك
Date: ...
Message-ID: <random@tecnogems.com>
MIME-Version: 1.0
X-Mailer: TecnoGems Transactional Mailer
X-Auto-Response-Suppress: All
Auto-Submitted: auto-generated
X-Entity-Ref-ID: <random@tecnogems.com>
Content-Type: multipart/alternative; ...
```

أي header يبدأ بـ `Precedence:` أو `List-Unsubscribe:` تم إزالته عمدًا
لأنها لا تنتمي إلى رسائل المصادقة الخدمية.
