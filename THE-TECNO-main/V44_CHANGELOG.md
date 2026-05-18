# V44 — Visual cleanup + Neon Cyberpunk posters

## Removed
- شريط "صفقات اليوم" (deals bar) من `templates/base.html` + JS + CSS.
- شريط البحث العلوي بالكامل من `templates/base.html`.
- (المسار `/games?q=...` لا يزال يعمل من جهة الخادم للتوافق مع الروابط القديمة.)

## Fixed
- تضارب اسم اللعبة مع صورة البوستر: تم تكثيف الـ overlay (height 70%، تدرج أسود قوي 0→95%) وزيادة وزن/ظل الخط (`font-weight:800`, `font-size:17px`, `text-shadow` مزدوج). الاسم الآن مقروء فوق أي خلفية.
- تحسين شامل لاستجابة الموبايل (≤900px و ≤640px و ≤380px): شبكة بطاقات عمودَين، أزرار قائمة 48px، خطوط الـ hero أصغر، تذييل بعمودين على الموبايل.

## Added
- **24 بوستر Neon Cyberpunk** بصيغة WebP 600×800 في `static/img/games/`:
  free_fire, pubg_mobile, fc_mobile, mobile_legends, genshin_impact,
  honkai_star_rail, valorant, league_of_legends, call_of_duty_mobile,
  arena_breakout, solo_leveling_arise, marvel_rivals, honor_of_kings,
  identity_v, stumble_guys, 8_ball_pool, yalla_ludo, bigo_live,
  telegram, pixel_gun_3d, asphalt_9, whiteout_survival,
  state_of_survival, clash_of_clans.
- `tools/gen_posters.py`: سكربت توليد قابل للتشغيل مجدداً لإكمال باقي الألعاب
  (يستخدم Lovable AI Gateway + موديل `google/gemini-3.1-flash-image-preview`).
  المتغير المطلوب: `LOVABLE_API_KEY`.
- `database.py::attach_generated_posters()`: يربط الصور تلقائياً بحقل
  `games.image_url` لكل لعبة `image_url` فارغ و`game_key` يطابق اسم ملف بوستر.
- استدعاء تلقائي لهذه الدالة عند إقلاع التطبيق (في `setup_once`).

## Deployment
```bash
systemctl stop game-topup
cp -a /root/project /root/project_backup_$(date +%Y%m%d_%H%M%S)

scp tecnogems_V44.zip root@SERVER:/root/
ssh root@SERVER 'rm -rf /root/new_project && mkdir /root/new_project && unzip -q /root/tecnogems_V44.zip -d /root/new_project'

# لاحظ: لا نستثني static/img/games/ هذه المرة — الصور الجديدة يجب أن تُنسخ
rsync -av \
  --exclude='data/site.db' \
  --exclude='.env' \
  --exclude='static/uploads/' \
  /root/new_project/tecnogems_V44/ /root/project/

systemctl start game-topup
journalctl -u game-topup --no-pager -n 30
```

## Generating remaining posters (optional)
لإكمال البقية لاحقاً على الخادم:
```bash
export LOVABLE_API_KEY="..."   # أو أضف الألعاب إلى GAMES list في الملف
cd /root/project && python3 tools/gen_posters.py
# ثم أعد تشغيل الخدمة لإعادة الربط
systemctl restart game-topup
```
