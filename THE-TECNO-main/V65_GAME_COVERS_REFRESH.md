# V65 — تحديث صور الألعاب + لمسة تصميم إضافية

## ما تم

تم استلام ملف `neon-gaming-topup-updated.zip` يحتوي على:

1. **169 صورة JPG عالية الجودة** (267×400, ~30–60 KB) للألعاب — صور أوفيشال
   من `steamgriddb` تحلّ محلّ صور الـ WebP الإفتراضية المُولَّدة في V63.
2. **مرجع تصميم مُحدَّث** (`upload/src/routes/index.tsx`) يضيف شريط
   "الأكثر طلباً" أفقياً قابلاً للسحب أعلى شبكة الألعاب.

## استبدال الصور

- استبدلنا **125** صورة WebP في `static/img/games/` بصور JPG الجديدة،
  مع الحفاظ على نفس `basename` كي يستمر `_resolve_poster_for_display`
  بإيجادها بدون تعديل في جدول الألعاب أو `_POSTER_ALIASES`.
- أُضيفت لعبتان جديدتان لم تكن موجودة سابقاً:
  - `fortnite.jpg`
  - `tower_of_fantasy.jpg`
- أُبقيت **8** ألعاب بصيغة WebP لأنه لا يوجد مقابل لها في الحزمة الجديدة:
  `acecraft`, `age_of_magic`, `arena_breakout_infinite`, `arknights_endfield`,
  `clash_of_clans`, `enhypen_world`, `overmortal`, `tiles_survive`.
- استُبدلت أيضاً صور:
  - `static/img/games/web/*.jpg` (7 صور رئيسية).
  - `static/img/games-neon/*.jpg` (7 صور — منها صورة الـ hero).

## تحديث منطق العرض

`_get_poster_available()` في [`app.py`](app.py) أصبح يخزّن **خريطة**
`{basename: extension}` بدل مجموعة بسيطة — هذا يسمح بخدمة `.jpg` و`.webp`
معاً، مع تفضيل JPG حين تتوفر النسختان.

نفس الترقية طُبّقت على `database.attach_generated_posters()` كي يربط
الصور الجديدة بحقل `image_url` في جدول `games` تلقائياً عند الإقلاع.

## لمسة التصميم الإضافية

أُضيف قسم **"الأكثر طلباً"** أعلى شبكة الألعاب في الصفحة الرئيسية
([`templates/_popular_games.html`](templates/_popular_games.html)):

- شريط أفقي قابل للسحب على الجوال (`scroll-snap-x`).
- بطاقات بنسبة 4:5 مع شارة "مميز" نيون.
- يُظهر أعلى 8 ألعاب من `featured_games` التي ترسلها صفحة الـ home.
- لا يظهر إذا كانت قائمة `featured_games` أصغر من 4 عناصر.

تنسيقاته أُضيفت في نهاية [`static/css/v60-neon.css`](static/css/v60-neon.css)
تحت العناوين `.v60-featured-row*` و`.v60-featured-card`.

## Cache busting

- نسخة الـ stylesheet: `?v=63 -> ?v=65`
- نسخة الـ Service Worker: `tg-v63-1 -> tg-v65-1`

## أداة المهمة

سكربت الاستبدال محفوظ في [`tools/_replace_images.py`](tools/_replace_images.py)
للمراجعة. يمكن حذفه بأمان لاحقاً بعد دمج الـ PR.
