# V55 — Home page games: admin control + horizontal carousel + "View all games"

## ما تم إصلاحه

### 1. استعادة عرض الألعاب الأفقي (scroll carousel)
في V54 تحوّل عرض الألعاب في الصفحة الرئيسية من **carousel أفقي مع سكرول يمين/يسار**
إلى **grid عمودي** (3-4 أعمدة). المستخدم أبدى أن العرض السابق كان أفضل.

- `_popular_games.html` الآن يُغلِّف الـ grid داخل `.tg-popular-scroller`
  مع زرّي سابق/التالي (`.tg-scroll-prev` / `.tg-scroll-next`).
- CSS جديد في `tecnogems.min.css` + `tecnogems.unified.css`:
  - `.tg-popular-grid` يتحوّل إلى `display: flex` + `overflow-x: auto`.
  - `scroll-snap-type: x mandatory` لضمان توقُّف السكرول على بطاقة كاملة.
  - الأزرار تختفي على الشاشات < 720px (swipe على الهاتف أسهل).
  - scrollbar رقيق مُنسَّق مع الثيم.

### 2. التحكُّم بالألعاب المعروضة في الرئيسية من Admin
جديد في جدول `games`:
```sql
ALTER TABLE games ADD COLUMN show_on_home INTEGER NOT NULL DEFAULT 0;
ALTER TABLE games ADD COLUMN home_sort_order INTEGER NOT NULL DEFAULT 0;
```

- لوحة `/admin/games` أضيف checkbox جديد **"إظهار في الرئيسية"** بجانب كل لعبة.
- الخيار معطَّل تلقائياً إذا اللعبة ليست مفعّلة.
- عند حفظ النموذج يتم تحديث الحقلين معاً (`set_game_active` + `set_game_show_on_home`).

- دوال جديدة في `database.py`:
  - `set_game_show_on_home(provider, game_key, show)`
  - `list_home_games()` — يُرجِع الألعاب التي `show_on_home=1 AND active=1`.

- منطق `home()` في `app.py`:
  - الأولوية لـ `list_home_games()`.
  - **Fallback:** إذا الأدمن لم يختر أيّ لعبة بعدُ → يُعرَض أول 8 ألعاب مفعّلة
    تحتوي على باقات (نفس سلوك V54)، لكي لا تكون الصفحة الرئيسية فارغة بعد
    الترقية مباشرة.

### 3. زر "عرض جميع الألعاب"
- زر CTA جديد تحت الـ carousel في `_popular_games.html`.
- route جديد `/all-games` → قالب `templates/all_games.html`.
- يعرض جميع الألعاب المفعّلة النشطة (بغضّ النظر عن `show_on_home`) في grid
  مع بحث live JS على العميل (نفس نمط `games.html`).

### الباقات (packages)
**لم تُلمَس.** `products.html` + flow الشراء يعملان كما كانا.

---

## الملفات المعدَّلة
| الملف | التغيير |
|-----|--------|
| `database.py` | `ALTER TABLE games ADD COLUMN show_on_home / home_sort_order` + دالّتان جديدتان |
| `app.py` | استيراد الدوال الجديدة، تحديث `home()` + `admin_games()`, route جديد `/all-games` |
| `templates/_popular_games.html` | شبكة → carousel أفقي + أزرار سكرول + زر "عرض جميع الألعاب" |
| `templates/admin/games.html` | checkbox "إظهار في الرئيسية" |
| `templates/all_games.html` | **جديد** |
| `static/css/tecnogems.min.css` | قواعد carousel + أزرار سكرول + CTA |
| `static/css/tecnogems.unified.css` | نفس القواعد في المصدر غير المُصغَّر |
| `static/js/pages/admin-games.js` | مزامنة checkbox الـ home مع الـ active |

## Breaking changes
لا يوجد. عمودان جديدان بقيمة افتراضية 0؛ fallback في `home()` يضمن عرض
الألعاب حتى لو لم يُحدِّد الأدمن شيئاً.
