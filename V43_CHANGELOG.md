# V43 — Trim + Performance + SEO + Full Redesign

## محذوف بناءً على طلبك
- **المفضلة (Wishlist) بالكامل**: زر ⭐ على البطاقات، صفحة `/wishlist`، endpoint `/api/wishlist/toggle`، رابط القائمة، context processor.
  - ملاحظة: جدول `wishlist` يبقى في قاعدة البيانات دون استخدام (لا migration هدم — لا يضر).
- **اقتراحات البحث الفورية (Autocomplete)**: تم حذف `<div id="tg-search-results">` و JS الـ debounce و endpoint `/api/search/suggest`.
- شريط البحث **بقي** أعلى الصفحة، عند Enter يذهب إلى `/games?q=...`.

## مضاف
1. **Flask-Compress** (Brotli + Gzip) لكل HTML/CSS/JS — ضغط ~70% (`COMPRESS_MIN_SIZE=500`, level 6/5).
2. **Pillow** + دالة `process_upload_to_webp(...)`:
   - فحص magic bytes (أمان حقيقي بدل الامتداد).
   - تحويل إلى **WebP** + إزالة EXIF + تصغير لـ 800px (أيقونات الألعاب).
   - استبدال نقاط رفع صور الألعاب في `admin_game_image` و`admin_upload_game_image`.
   - SVG يمر دون تحويل.
3. **SEO**:
   - `sitemap.xml` ديناميكي مع `<xhtml:link rel="alternate" hreflang="ar/en/x-default">` لكل URL.
   - وسوم `<link rel="alternate" hreflang>` في `base.html`.
   - JSON-LD: `Organization`, `WebSite + SearchAction`, `BreadcrumbList`, `ItemList` من Products.
   - `twitter:card` + `twitter:image`.
4. **إعادة تصميم شاملة (`static/css/v43-redesign.css`)**:
   - خلفية `#0a0814` + طبقة noise SVG خفيفة.
   - تدرج رئيسي `#7c3aed → #06b6d4` (Violet/Cyan) في الأزرار، الشارات، الأسعار.
   - **Cairo** للعناوين (800/900) + **Tajawal** للنص.
   - Header شفاف يصبح صلباً عند التمرير (backdrop-filter blur).
   - شريط "صفقات اليوم" أعلى الصفحة (قابل للإغلاق، يحفظ في localStorage).
   - **بطاقات ألعاب 3:4 بوستر**: صورة ممتلئة + overlay متدرج + اسم اللعبة فوق + hover scale + glow.
   - شريط ثقة (إحصائيات) داخل Hero.
   - Footer 4 أعمدة + شارات دفع.
   - **Toggle داكن/فاتح** يحفظ التفضيل ويحترم `prefers-color-scheme`.
   - badges لحالات الطلبات بألوان دلالية.
   - shimmer skeleton.
   - `prefers-reduced-motion` يلغي الحركات.
   - تباين AA في الوضعين.

## قاعدة البيانات
- لا تغييرات في الجداول. لا migrations.

## المتغيرات
- لا متغيرات `.env` جديدة. إعدادات Google OAuth من V42 لا تزال تعمل (اختيارية).

## أوامر النشر

```bash
# على جهازك
scp tecnogems_V43.zip root@SERVER:/root/

# على السيرفر
systemctl stop game-topup
BACKUP_DIR="/root/project_backup_$(date +%Y%m%d_%H%M%S)"
cp -a /root/project "$BACKUP_DIR" && echo "Backup: $BACKUP_DIR"

rm -rf /root/new_project && mkdir /root/new_project
unzip -q /root/tecnogems_V43.zip -d /root/new_project

rsync -av \
  --exclude='data/site.db' \
  --exclude='.env' \
  --exclude='.secret_key' \
  --exclude='static/uploads/' \
  /root/new_project/tecnogems_V43/ /root/project/

cd /root/project && pip install -r requirements.txt
rm -rf /root/project/__pycache__
systemctl start game-topup
journalctl -u game-topup --no-pager -n 40
```

## التحقق بعد النشر
```bash
grep -c "Flask-Compress\|flask_compress" /root/project/app.py        # > 0
grep -c "process_upload_to_webp" /root/project/app.py                # >= 3
grep -c "wishlist_toggle\|api_search_suggest" /root/project/app.py   # = 0 (محذوف)
ls /root/project/static/css/v43-redesign.css                         # موجود
curl -sI https://tecnogems.com/ -H 'Accept-Encoding: br' | grep -i content-encoding   # br
```

## ملاحظات
- إذا واجهت `PIL` غير مثبّتة: `pip install Pillow==10.4.0` ثم إعادة التشغيل.
- جدول `wishlist` يبقى دون استخدام؛ إن أردت حذفه يدوياً: `DROP TABLE wishlist;` (اختياري).
