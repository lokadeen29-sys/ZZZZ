# V46 — إصلاحات مباشرة (بدون كسر أي وظيفة)

## الإصلاحات المُطبَّقة

### 1. مسار مكرر — BUG حرج ✅
**الملف:** `app.py`  
**المشكلة:** المسار `/admin/game/<provider>/<game_key>/image` كان مُعرَّفًا مرتين (`admin_game_image` و `admin_upload_game_image`) — Flask يتجاهل الثانية صامتًا مما يعني أن دعم SVG + مجلد `games/` في الدالة الثانية لم يكن يعمل أبدًا.  
**الحل:** حُذفت الدالة الأولى القديمة (التي لا تدعم SVG) وأُبقيت الثانية المتقدمة، وأُعيدت تسميتها `admin_game_image`.

---

### 2. init_db + app.run مكررة ✅
**الملف:** `app.py` (آخر الملف)  
**المشكلة:** كتلة `if __name__ == "__main__":` كانت تحتوي على `init_db()` و `app.run()` مكررتين بالكامل — الكود بعد `app.run()` الأولى لا يُنفَّذ أبدًا.  
**الحل:** حُذف التكرار.

---

### 3. كلمة المرور بلا حد أدنى عند التسجيل ✅
**الملف:** `app.py`  
**المشكلة:** `validate_password_strength()` كانت مُعرَّفة لكن لا تُستدعى في `register()` ولا في `api_register()` — المستخدم كان يستطيع تسجيل كلمة مرور "1" أو فارغة.  
**الحل:** استدعاء الدالة في كلا المسارين قبل إنشاء الحساب.

---

### 4. Race condition في setup_once ✅
**الملف:** `app.py`  
**المشكلة:** في gunicorn متعدد الـ threads، يمكن لـ `init_db()` أن تُشغَّل مرتين في آنٍ واحد.  
**الحل:** إضافة `threading.Lock()` (double-checked locking) حول كتلة الإعداد.

---

### 5. .secret_key يُكتب على القرص في containers ✅
**الملف:** `app.py`  
**المشكلة:** كان يُحاوَل كتابة `.secret_key` دائمًا دون معالجة `OSError` — يسبب crash في Heroku/Docker حيث الـ filesystem للقراءة فقط.  
**الحل:** `try/except OSError` حول الكتابة مع تحذير واضح بدلًا من crash.

---

### 6. _PRAGMAS_APPLIED غير thread-safe ✅
**الملف:** `database.py`  
**المشكلة:** المتغير العام كان يُعدَّل بدون lock في بيئة متعددة الـ threads.  
**الحل:** إضافة `threading.Lock()` مع double-checked locking.

---

### 7. ملف .env.example ✅
**الملف:** `.env.example` (جديد)  
**المشكلة:** لم يكن موجودًا — أي مطوّر جديد لا يعرف المتغيرات المطلوبة.  
**الحل:** إنشاء الملف بكل المتغيرات المدعومة مع شرح لكل منها.

---

### 8. featured_games.py — روابط الألعاب المميزة تعطي 404 ✅
**الملف:** `featured_games.py`  
**المشكلة:** كل الألعاب في القسم المميز بالصفحة الرئيسية كانت تستخدم `provider: "g2bulk"` و`"shop2topup"` وهي أسماء API خارجية وليست قيم `provider` في قاعدة البيانات. الصحيح دائمًا `"server1"`. كذلك الـ `game_key` كانت خاطئة (`"pubg"` بدل `"pubg_mobile"`، `"mlbb"` بدل `"mobile_legends"`... إلخ).  
**الحل:** تصحيح كل القيم لتطابق ما تُنشئه `seed_local_provider_catalog()` فعلياً في قاعدة البيانات. الصور والتصميم لم يُمسَّا.

| اللعبة | provider قبل | game_key قبل | بعد |
|--------|-------------|-------------|-----|
| Mobile Legends | g2bulk ❌ | mlbb ❌ | server1 / mobile_legends |
| PUBG Mobile | g2bulk ❌ | pubg ❌ | server1 / pubg_mobile |
| Genshin Impact | g2bulk ❌ | genshin ❌ | server1 / genshin_impact |
| Valorant | g2bulk ❌ | valorant ❌ | server1 / valorant_sg |
| Free Fire | shop2topup ❌ | freefire | server1 / freefire |
| League of Legends | g2bulk ❌ | lol ❌ | server1 / league_of_legends_sg |
| Honkai: Star Rail | g2bulk ❌ | honkai ❌ | server1 / honkai_star_rail |
| Roblox | g2bulk ❌ | roblox | server1 / roblox |

> **ملاحظة:** Valorant وLoL ليس لهما نسخة "global" عند المورد — استُخدمت النسخة الأقرب (SG). إذا فعّلت لعبة مختلفة من لوحة الأدمن، غيّر الـ game_key في `featured_games.py` ليطابقها.

| البند | السبب |
|-------|--------|
| Blueprints Phase 2 | يتطلب نقل ~1700 سطر ويحتاج اختبار يدوي شامل لكل route |
| process_order_task | يحتاج Redis حقيقي للاختبار |
| pybabel compile | يجب تشغيله في بيئتك: `pybabel extract -F babel.cfg -o translations/messages.pot .` |
| npm run build | شغّل في مجلد المشروع: `npm install && npm run build` |
| SQLite → Postgres | قرار بنية تحتية، يحتاج migration script |
| Tests | يحتاج وقتًا للكتابة من الصفر |

## كيفية التشغيل بعد V46

```bash
cp .env.example .env
# عدّل .env بقيمك الحقيقية

pip install -r requirements.txt --break-system-packages

# في بيئة dev:
python app.py

# في الإنتاج:
gunicorn wsgi:app
```
