# V42 — Batch 2: Async Email + Search + Wishlist + PWA + Google OAuth

## ما تم تطبيقه

### 1) قائمة بريد غير متزامنة (Email Async Queue)
- `send_email()` لم يعد يحجز الطلب. أصبح يضع الرسالة في طابور (`email_queue`) و**خيطان عمل** (worker threads) يرسلانها في الخلفية.
- **التسجيل / استعادة كلمة السر / تأكيد البريد** أصبحت فورية من ناحية المستخدم. لا انتظار 5–10 ثوانٍ لـ SMTP.
- فشل SMTP يُسجَّل في اللوغ ولا يكسر الطلب.

### 2) بحث فوري بالاقتراحات (Search Autocomplete)
- شريط بحث جديد أعلى كل صفحة (تحت القائمة).
- API: `GET /api/search/suggest?q=...` يُرجع حتى 8 نتائج (ألعاب + منتجات).
- يدعم لوحة المفاتيح (↑ ↓ Enter Esc).
- تأخير 180ms لتقليل الطلبات.

### 3) المفضلة (Wishlist)
- جدول `wishlist(user_id, provider, game_key)` + فهرس مركّب.
- زر ⭐ على كل بطاقة لعبة في الصفحة الرئيسية (للمستخدمين المسجَّلين فقط).
- صفحة `/wishlist` تعرض ألعابك المحفوظة.
- API: `POST /api/wishlist/toggle` (مع CSRF عبر `X-CSRFToken`).
- رابط "⭐ المفضلة" في القائمة.

### 4) PWA (تثبيت + offline أساسي)
- `static/sw.js` جديد (Service Worker) مخدوم من جذر الموقع `/sw.js` مع `Service-Worker-Allowed: /`.
- استراتيجية:
  - **HTML**: NetworkFirst → cache fallback عند انقطاع النت.
  - **Static (CSS/JS/IMG)**: CacheFirst مع تحديث في الخلفية.
  - **API/Admin/Auth/Checkout/Wallet/Profile**: bypass كامل (دائماً من الشبكة).
- `manifest.json` جاهز مسبقاً (يدعم "Add to Home Screen" على iOS/Android).

### 5) تسجيل الدخول بـ Google (Google OAuth)
- اعتمد على مكتبة **Authlib** (مضافة في requirements).
- مسارات: `/auth/google` و `/auth/google/callback`.
- ربط تلقائي إذا كان الإيميل موجوداً بحساب عادي.
- إنشاء حساب جديد تلقائياً مع `email_verified=1` (لا حاجة لتفعيل البريد لمن سجّل بـ Google).
- زر "متابعة باستخدام Google" يظهر في صفحتي **تسجيل الدخول** و**إنشاء حساب** فقط إذا تم ضبط المتغيرات.

#### المتغيرات المطلوبة في `.env`:
```
GOOGLE_CLIENT_ID=xxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=xxxxxxxxxxxxxxxxxx
GOOGLE_REDIRECT_URI=https://tecnogems.com/auth/google/callback
```

#### كيف تحصل على Client ID و Secret:
1. ادخل إلى https://console.cloud.google.com/
2. أنشئ مشروعاً جديداً (أو استخدم موجوداً).
3. **APIs & Services → OAuth consent screen** → External → املأ اسم التطبيق + الإيميل.
4. **APIs & Services → Credentials → + CREATE CREDENTIALS → OAuth client ID**.
5. Application type: **Web application**.
6. **Authorized redirect URIs**: ضع بالضبط `https://tecnogems.com/auth/google/callback`
7. انسخ Client ID و Client Secret إلى `.env`.

## قاعدة البيانات
- إضافة جدول `wishlist` + فهرس `idx_wishlist_user`.
- إضافة عمود `google_sub` على `users` + فهرس `idx_users_google_sub`.
- كل التغييرات `IF NOT EXISTS` / `try-except` — آمن مع قاعدة موجودة.

## أوامر النشر على السيرفر
```bash
systemctl stop game-topup
BACKUP_DIR="/root/project_backup_$(date +%Y%m%d_%H%M%S)"
cp -a /root/project "$BACKUP_DIR"

# انسخ الملفات الجديدة (مع الحفاظ على .env و data/ و uploads/)
scp tecnogems_V42_BATCH2.zip root@SERVER:/root/
ssh root@SERVER 'cd /root && rm -rf new_project && mkdir new_project && unzip -q tecnogems_V42_BATCH2.zip -d new_project'
rsync -av --exclude='data/site.db' --exclude='.env' --exclude='static/uploads/' /root/new_project/tecnogems_V42/ /root/project/

# ثبّت Authlib الجديدة
cd /root/project && pip install -r requirements.txt

# نظّف الكاش القديم
rm -rf /root/project/__pycache__

# شغّل
systemctl start game-topup
journalctl -u game-topup --no-pager -n 30
```

## التحقق بعد النشر
```bash
grep -c "email_queue" /root/project/app.py        # > 0
grep -c "api_search_suggest" /root/project/app.py # > 0
grep -c "wishlist_toggle" /root/project/app.py    # > 0
grep -c "auth_google_callback" /root/project/app.py # > 0
ls /root/project/static/sw.js                     # موجود
```

## ملاحظات
- **Service Worker** يعمل فقط على HTTPS (الموقع كذلك ✓).
- إذا لم تضع متغيرات Google، الزر لن يظهر (ولن يحدث خطأ).
- المفضلة تتطلب تسجيل دخول (302 إلى /login إن لم يكن).
- Email queue يعمل داخل عملية gunicorn — كل worker له طابوره الخاص (مقبول لأن SMTP خفيف).

## المتبقي لـ Batch 3 (مقترح)
- تقسيم app.py إلى Blueprints
- استبدال database.py بـ SQLAlchemy
- اختبارات pytest
- لوحة "إحصائيات حية" بـ WebSocket / SSE
- ضغط الصور تلقائياً عند الرفع
