# V44.2 — Game card parity + nav icon visibility

## Fixes
1. **Game cards now match the neontopup reference exactly**
   - Removed the price overlay (`من X ل.س`) from `home.html` cards. Cards now
     show only the title + currency / group label, just like the reference.
   - HOT badge on the top 3, no provider/emoji badges on the rest.
2. **Navbar icons are now clearly visible**
   - SVG icons in the logged-in navbar now use a cyan tint with a subtle neon
     drop-shadow instead of inheriting the white text color at 85 % opacity.
   - Balance / deposits / logout keep their dedicated magenta / green / red.
3. **Removed the "old games layout" toggle**
   - Removed the checkbox from `admin/settings.html`.
   - Removed the corresponding `set_setting` call from `app.py`.
   - Removed the conditional `tg-game-grid-old` class from `home.html`.
   - The site now ships with a single, polished neon layout.
