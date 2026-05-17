# V63 — 107 Game Covers Added

## ما تم

- استخراج 107 صورة JPG (600×800) من حزمة `game-covers.zip` المرفوعة من قِبل المستخدم.
- تحويل كل الصور إلى صيغة `WebP` بجودة 78 لتطابق نفس مواصفات الصور الموجودة سابقًا في `static/img/games/`:
  - الأبعاد: **600×800** (نفس الأبعاد الموجودة).
  - متوسط الحجم: **~46 KB** بدل **~64 KB** كـ JPG (توفير ~29%).
  - أصغر/أكبر ملف: 18 KB / 89 KB — ضمن نفس نطاق الـ 24 صورة الأصلية.
- زيادة عدد الصور من **24** إلى **131** ملف داخل `static/img/games/`.
- حذف ملف `game-covers.zip` بعد الانتهاء من معالجته.

## أين تُستعمل الصور؟

`database.attach_generated_posters()` يقرأ الملفات تلقائيًا وقت الإقلاع
ويربط كل لعبة (في جدول `games`) بصورة بنفس اسم `game_key` بدون أن يُلمس
أي رفع مخصّص أنشأه الأدمن.

## ترقية منطق المطابقة

كانت النسخة السابقة تطابق `game_key` مع اسم الملف بشكل مباشر فقط — هذا يفي
بالألعاب ذات الأسماء البسيطة، لكن كتالوج المورد (server1) يحتوي على عشرات
المتغيّرات الإقليمية (مثلاً 13 نسخة من `freefire`، 12 نسخة من
`genshin_impact`)، وكلها كانت تظهر بدون صورة.

تم تحديث `attach_generated_posters()` في [`database.py`](database.py) ليستعمل:

1. **مطابقة مباشرة** على `game_key`.
2. **جدول مرادفات صريح** (`_POSTER_ALIASES`) لـ:
   - عائلة Free Fire (كل المناطق → `free_fire`).
   - عائلة EA FC (كل النسخ → `fc_mobile`).
   - حالات اختلاف بسيط في الاسم بين slug الكتالوج واسم الملف
     (مثل `arknight_endfield` → `arknights_endfield`،
     `gov_nikke` → `goddess_of_victory_nikke`،
     `puzzles_and_survival` → `puzzles_survival`، إلخ).
3. **مطابقة جذر الاسم** بإسقاط اللواحق واحدة واحدة من النهاية:
   - `genshin_impact_brazil` → يحاول `genshin_impact_brazil` ←
     `genshin_impact` ✅
   - `bleach_soul_resonance_americas` → `bleach_soul_resonance` ✅

## النتيجة

- قبل الإصلاح: 91 من 204 لعبة في الكتالوج كانت تجد صورة (45%).
- بعد الإصلاح: **201 من 204** (98.5%).
- الـ 3 المتبقّية (`gov_nikke`، `t3_arena`، `test`) لا توجد لها صورة في
  حزمة الصور؛ تستمر بالاعتماد على الـ SVG الذكي
  (`static/img/smart-games/*.svg`) كما كان.

## ملاحظات

- لم يُغيَّر أي تعديل قام به الأدمن: الشرط `image_url IS empty` يحمي
  الصور المرفوعة يدويًا.
- لاستعمال الصور الجديدة على قاعدة بيانات قائمة، شغّل لمرّة واحدة:
  ```python
  from database import attach_generated_posters
  attach_generated_posters()
  ```
- النّداء يحدث تلقائيًا عند تشغيل التطبيق (راجع `app.py`).
