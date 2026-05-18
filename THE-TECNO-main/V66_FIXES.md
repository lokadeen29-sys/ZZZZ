# V66 — إصلاح الصور + إعدادات الصفحة الرئيسية

## المشكلة (V65 backlash)

V65 استبدل 125 صورة `.webp` بصور `.jpg` عالية الجودة، **لكن**
سجلّات قاعدة البيانات (`games.image_url`) لا زالت تشير إلى ملفات
`/static/img/games/<key>.webp` التي حُذفت من القرص. النتيجة:
معظم الألعاب كانت تعرض صورة مكسورة أو الـ SVG الافتراضي بدل الصورة
الجديدة.

## الإصلاحات

### 1) Self-heal للروابط القديمة في DB

[`database.attach_generated_posters()`](database.py) الآن يكتشف
ويصلح أي قيمة `image_url` تشير إلى ملف auto-generated غير موجود
على القرص:

- إذا كان الرابط `/static/img/games/<x>.webp` لكن `<x>.webp` غير
  موجود (وغالبًا `.jpg` موجود الآن) → يُعاد ربطه تلقائيًا بأحدث
  صيغة متوفّرة عبر `_resolve_poster_key`.
- إذا لم يوجد بديل → يُمسح الحقل ويعود التطبيق للـ SVG الذكي.
- روابط الأدمن (أي شيء لا يبدأ بـ `/static/img/games/` أو يحتوي
  مجلداً فرعياً مثل `/static/img/games/web/...`) **لا تُمَس**.

السكربت يعمل تلقائياً عند الإقلاع (`wsgi.py`) بفضل الـ V44 hook
الموجود سابقاً، لذا لا حاجة لأي تدخّل يدوي.

### 2) Self-heal على مستوى العرض

[`app.game_image_url()`](app.py) أصبح يتحقق من وجود الملف على
القرص قبل استعمال قيمة `image_url` المخزّنة عندما تكون من
العائلة auto-generated، وإذا كان الملف غير موجود يسقط مباشرة إلى
`_resolve_poster_for_display(key)`. هذا يضمن أن أي طلب — حتى قبل
أن يلامس `attach_generated_posters` صفّاً قديماً — يحصل على صورة
صحيحة.

## ميزات إضافية

### شريط "الأكثر طلباً" (toggle)

أُضيف إعداد `show_popular_bar` في
[`/admin/settings`](templates/admin/settings.html). الافتراضي:
**مُفعّل**. عند الإطفاء يُحذف الشريط بالكامل من الصفحة الرئيسية
دون التأثير على شبكة الألعاب.

### قسم آراء اللاعبين (toggle + محتوى قابل للتعديل)

أُضيف إعداد `show_testimonials` لإظهار/إخفاء القسم بأكمله،
بالإضافة إلى ثلاث مجموعات حقول قابلة للتعديل:
`testimonial_{1,2,3}_{name,game,text}`.

- اترك أي حقل فارغاً ⇒ يُستخدم النص الافتراضي ثنائي اللغة
  (يُختار حسب لغة المستخدم تلقائياً).
- جميع المدخلات تمر عبر `clean_plain_text` فلا يمكن حقن HTML/scripts.
- النصّ مقيّد بـ 400 حرفاً، الاسم 80، اللعبة 60.

## Cache busting

- نسخة الـ stylesheet: `?v=65 → ?v=66`
- نسخة الـ Service Worker: `tg-v65-1 → tg-v66-1`

## الملفات المُعدَّلة

- `database.py` — هَدر المسار القديم وإعادة ربطه.
- `app.py` — تَحقُّق ملف على القرص في `game_image_url`، تمرير
  toggles + testimonials إلى `home.html`، حفظ/قراءة الإعدادات
  الجديدة في `admin_settings()`.
- `templates/_popular_games.html` — حارس `show_popular_bar`.
- `templates/home.html` — حارس `show_testimonials` + استعمال قائمة
  `testimonials` المُمرَّرة من الـ view.
- `templates/admin/settings.html` — قسم جديد "أقسام الصفحة
  الرئيسية" بالـ toggles وحقول التعليقات.
- `templates/base.html` — bump `?v=66`.
- `static/sw.js` — bump cache to `tg-v66-1`.
