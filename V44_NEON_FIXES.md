# V44 NEON — fixes & redesign

## What changed

1. **Login no longer stuck on "جارٍ المعالجة..."**
   - `static/js/app.js` (+ `app.min.js`): submit button is now restored on
     `pageshow` (covers bfcache + server re-render with flashed error) and
     auto-recovers after 12s as a safety net.

2. **Profit margin actually applies, immediately**
   - `database.py › update_profit_margin()`: also resets
     `pricing_mode='fixed_syp'` rows back to `usd` and clears
     `manual_price_syp` overrides, so the new sell_price is what the user
     actually sees.
   - `app.py › admin_settings`: only re-applies the margin when the value
     actually changed (so the Save button is fast when you only changed
     other settings).

3. **Light/dark toggle removed (always dark)**
   - `templates/base.html`: theme toggle button + JS removed; pre-hydration
     script forces `data-theme="dark"`.

4. **Deposits = its own top-level section, separated from Settings**
   - `templates/admin/dashboard.html`: large priority card for طلبات الرصيد
     at the top of the admin dashboard.
   - `templates/base.html`: admin nav now has a direct "📥 طلبات الرصيد" link.

5. **Approve / Reject buttons spaced apart**
   - `templates/admin/deposits.html`: 28px gap between buttons,
     `min-width:110px`, plus `confirm()` on Reject to prevent misclicks.

6. **Removed "آخر طلباتك" section from the homepage**
   - `templates/home.html`.

7. **More visible Sign-in option in the navbar**
   - `templates/base.html`: dedicated styled "🔐 تسجيل الدخول" link next to
     the Register CTA, so visitors no longer see only the register button.

8. **Neon design pass over the whole site**
   - New `static/css/v44-neon.css` (loaded last) re-skins nav, hero, game
     cards, forms, buttons, KPIs, footer with the cyan→magenta neon palette
     from the `neontopup-site` design.

## Game posters

The text file lists 129 games — 24 already have posters in
`static/img/games/`, **112 are missing**. Those are not bundled in this
ZIP because the in-platform fast image model leaks misspelled text into
posters. Instead, `tools/gen_posters.py` has been updated:

- The `GAMES` list now contains every game from your text file.
- `slug()` matches the rest of the project (drops `(Multiple Regions)`,
  `MENA`, `Global`, etc. so variants collapse to one file).
- Existing files are skipped, so you can re-run safely.

Run locally with your key:

```bash
export LOVABLE_API_KEY=...
python tools/gen_posters.py
```
