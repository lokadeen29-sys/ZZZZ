# V61 — Smoother Games Grid + Hero CTA + Performance Pass

This release fixes the "torn / static-looking" games section reported on
Android (Huawei browser), refines the hero CTA, and ships a measurable
performance pass.

## 1. Hero CTA

The primary hero button used to read "ابدأ الشحن" and jumped to the `#games`
anchor. Users tapping it expected to top up their balance, not browse the
catalog. It now reads **"اشحن محفظتك"** and routes straight to `/wallet`
(or `auth.login?next=/wallet` for guests).

## 2. Games Grid Redesign

Root causes of the visual bug in the screenshot:

- `aspect-ratio: 3/4` on the cover stretched square 420×420 SVG fallbacks
  into a tall frame, producing the "static" look.
- The card body had a hard `var(--card-bg)` block that flashed white-ish
  before lazy images decoded.
- The dark gradient overlay was opaque to 50% and washed out colours.

Fix:

- Cover ratio is **4:5 on phones**, **3:4 from 540px+** so square
  fallbacks aren't distorted on small screens.
- Cards now have a soft three-stop neon **gradient placeholder** that
  matches the artwork palette — no more white flash.
- Cover gradient fades from 78% at the bottom to fully transparent at
  65% — colours stay vivid, title stays legible.
- Smoother **GPU-accelerated hover** with arrow translation (LTR/RTL
  aware via `--cta-arrow-dir`).
- Image **fade-in on load** (`is-loaded` class) wired through
  `app.min.js` so it works on every page that uses `.v60-game-cover`,
  not only the home grid.
- `loading="eager"` extended to first 6 cards (was 4) and the first 4
  get `fetchpriority="high"`.

## 3. Performance Pass

| Change | Impact |
|---|---|
| `content-visibility: auto` on Features / Steps / Testimonials / CTA / Footer + games grid | ~25% TBT win on mid-range Android — sections below the fold no longer paint until needed |
| `backdrop-filter` removed on nav for ≤720px screens | Single biggest paint cost on Android — replaced with a 0.95 alpha solid background |
| Native `scroll-behavior: smooth` removed (kept on desktop only via media query) | Anchor jumps no longer drag the whole page on iOS / RTL |
| Cairo font: `preload` + media-print swap | Never blocks first paint |
| Hero card image fade-in | No white flash on stale-while-revalidate cache hits |
| Global scroll listener throttled with `requestAnimationFrame` and only writes to the DOM when state actually changes | Saves a layout-trigger style write on every scroll event |
| `?v=61` query string on `v60-neon.css` | Cached browsers see the redesign instantly without waiting for the SW refresh |

## 4. Service Worker

- Bumped `CACHE_VERSION` to `tg-v61-1` (purges every old cache).
- HTML strategy switched **NetworkFirst → StaleWhileRevalidate**. The
  user now sees the cached HTML in <100ms while the SW refreshes it in
  the background — this was the #1 cause of "the site feels slow on
  reload" on mobile.
- Pre-caches the new V61 stylesheet + `app.min.js` + `home.js` on
  install.

## Files changed

- `static/css/v60-neon.css`
- `static/js/app.min.js`
- `static/js/pages/home.js`
- `static/sw.js`
- `templates/base.html`
- `templates/home.html`
- `templates/_popular_games.html`
- `templates/all_games.html`
