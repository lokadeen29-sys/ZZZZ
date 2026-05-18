---
inclusion: manual
---

# Playbook — تحسينات Mobile UX

> **متى يُستدعى:** تنفيذ البند High رقم 24 (Mobile UX Overhaul).
> **المدة المتوقعة:** 2-3 أيام.
> **الأولوية:** Mobile traffic لمواقع شحن الألعاب العربية = **85-95% من الزيارات**. كل تحسين هنا = تأثير مباشر على التحويل.

---

## 1) الوضع الحالي — المشاكل المعروفة

### مراجعة القوالب الحالية

- ✅ viewport meta موجود في `base.html`.
- ✅ CSS `@media (max-width: 860px)` + `@media (max-width: 480px)` يتعامل مع العمود الواحد.
- ⚠️ `.tg-hero h1` بـ `clamp(42px, 7vw, 78px)` — على 320px يُعطي `~22px` لكنه قد يقطع عنواناً عربياً طويلاً.
- ⚠️ `.tg-nav` في الجوال يتحوّل إلى vertical → ارتفاع كبير → يأخذ 30% من الشاشة.
- ❌ **لا Bottom Navigation** — المعيار الذهبي لمواقع الشحن mobile.
- ❌ **لا Sticky CTA** ("اشحن الآن") على صفحات المنتجات.
- ❌ **لا PWA install prompt** حقيقي (مع أن `manifest.json` موجود).
- ❌ **Touch targets صغيرة** في بعض الأماكن (tg-game-badge, language switcher).
- ❌ **لا haptic feedback** على التفاعلات المهمة.

---

## 2) خطة التحسينات

### أ) Bottom Navigation Bar

أضِف في `templates/base.html` قبل `</body>`:

```html
<nav class="mobile-bottom-nav" aria-label="{{ 'Main' if is_en else 'القائمة الرئيسية' }}">
  <a href="{{ url_for('home') }}" class="mbn-item {% if request.endpoint == 'home' %}active{% endif %}">
    <svg viewBox="0 0 24 24" {{ _ico|safe }}>
      <path d="M3 9.5 12 3l9 6.5V21a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z"/>
    </svg>
    <span>{{ _('home') }}</span>
  </a>
  <a href="{{ url_for('games') }}" class="mbn-item {% if request.endpoint == 'games' %}active{% endif %}">
    <svg viewBox="0 0 24 24" {{ _ico|safe }}>
      <path d="M6 8h12v10H6z M10 14h4"/>
    </svg>
    <span>{{ _('games') }}</span>
  </a>
  <a href="{{ url_for('wallet') if _is_authed else url_for('auth.login') }}" 
     class="mbn-item mbn-cta">
    <svg viewBox="0 0 24 24" {{ _ico|safe }}>
      <rect x="2" y="6" width="20" height="14" rx="3"/>
      <path d="M2 11h20"/>
    </svg>
    <span>{{ _('wallet') }}</span>
  </a>
  <a href="{{ url_for('orders') if _is_authed else url_for('auth.login') }}" 
     class="mbn-item {% if request.endpoint == 'orders' %}active{% endif %}">
    <svg viewBox="0 0 24 24" {{ _ico|safe }}>
      <path d="M6 2h9l5 5v13a2 2 0 0 1-2 2H6z M15 2v5h5"/>
    </svg>
    <span>{{ _('orders') }}</span>
  </a>
  <a href="{{ url_for('profile') if _is_authed else url_for('auth.login') }}" 
     class="mbn-item {% if request.endpoint == 'profile' %}active{% endif %}">
    <svg viewBox="0 0 24 24" {{ _ico|safe }}>
      <circle cx="12" cy="8" r="4"/>
      <path d="M4 21a8 8 0 0 1 16 0"/>
    </svg>
    <span>{{ _('profile') }}</span>
  </a>
</nav>
```

CSS في `static/css/v53-mobile.css` جديد:

```css
/* V53: Bottom navigation — يظهر فقط في الجوال */
.mobile-bottom-nav {
  display: none;
}

@media (max-width: 860px) {
  .mobile-bottom-nav {
    display: flex;
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    z-index: 100;
    background: hsl(var(--background) / 0.92);
    backdrop-filter: blur(20px);
    border-top: 1px solid hsl(var(--border));
    padding: 8px 0 max(8px, env(safe-area-inset-bottom));
    justify-content: space-around;
    align-items: center;
  }
  
  .mbn-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    padding: 6px 10px;
    color: hsl(var(--muted-foreground));
    font-size: 11px;
    font-weight: 700;
    min-width: 56px;
    min-height: 48px;   /* WCAG touch target */
    border-radius: 12px;
    transition: 0.2s;
  }
  
  .mbn-item svg {
    width: 22px;
    height: 22px;
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
  }
  
  .mbn-item.active {
    color: hsl(var(--primary));
    background: hsl(var(--primary) / 0.12);
  }
  
  .mbn-item.mbn-cta {
    color: hsl(var(--foreground));
    background: var(--gradient-primary);
    margin-top: -24px;   /* elevate مركزياً */
    border-radius: 50%;
    width: 56px;
    height: 56px;
    box-shadow: var(--shadow-glow);
  }
  .mbn-item.mbn-cta span { display: none; }
  .mbn-item.mbn-cta svg { width: 26px; height: 26px; }
  
  /* أضِف padding-bottom للـbody كي لا تختفي المحتويات خلف الـnav */
  body {
    padding-bottom: 80px;
  }
}
```

### ب) Sticky CTA في صفحة المنتج

في `templates/products.html`، بعد `</main>` (أو آخر block):

```html
{% if products %}
<div class="sticky-buy-bar">
  <button onclick="document.querySelector('.tg-products, .products-grid').scrollIntoView({behavior:'smooth'});" 
          class="tg-btn tg-primary">
    {{ _('choose_package') }} ⚡
  </button>
</div>
{% endif %}
```

CSS:

```css
.sticky-buy-bar { display: none; }

@media (max-width: 860px) {
  .sticky-buy-bar {
    display: block;
    position: fixed;
    bottom: 76px;   /* فوق bottom-nav */
    left: 12px;
    right: 12px;
    z-index: 90;
  }
  .sticky-buy-bar button {
    width: 100%;
    padding: 14px;
    font-size: 16px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
  }
}
```

### ج) Hero h1 لا يقطع على 320px

في `static/css/v53-mobile.css`:

```css
@media (max-width: 400px) {
  .tg-hero h1 {
    font-size: 28px !important;   /* كان clamp 42px+ */
    line-height: 1.2;
    padding: 0 10px;
    word-break: keep-all;   /* يمنع قطع كلمات عربية في منتصفها */
    overflow-wrap: break-word;
  }
  .tg-hero p {
    font-size: 15px;
    padding: 0 10px;
  }
  .tg-hero-content {
    padding: 48px 0 64px;
  }
}
```

### د) Touch targets = 48×48 px minimum

مُراجعة كل الأزرار في الـCSS:

```css
@media (max-width: 860px) {
  .btn, .link-btn, .tg-btn, 
  .lang-switcher a, 
  .tg-game-card {
    min-height: 48px;
  }
  
  /* language switcher خصوصاً */
  .lang-switcher a {
    min-width: 48px;
    min-height: 48px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }
  
  /* form inputs — أكبر للـsoft keyboard */
  input, select, textarea {
    min-height: 48px;
    font-size: 16px;   /* يمنع iOS zoom-on-focus */
  }
}
```

### هـ) PWA install prompt

في `templates/base.html` قبل `</body>`:

```html
<div id="pwa-install-banner" class="pwa-banner" hidden>
  <div class="pwa-banner-content">
    <img src="{{ url_for('static', filename='img/logo-32.webp') }}" width="32" height="32" alt="">
    <div>
      <b>{{ _('install_app') }}</b>
      <span>{{ _('quick_access_from_home_screen') }}</span>
    </div>
    <button id="pwa-install-btn" class="tg-btn tg-primary tg-btn-small">
      {{ _('install') }}
    </button>
    <button id="pwa-install-dismiss" class="tg-btn tg-glass tg-btn-small" aria-label="Dismiss">×</button>
  </div>
</div>

<script nonce="{{ csp_nonce }}">
(function() {
  let deferredPrompt;
  const banner = document.getElementById('pwa-install-banner');
  const installBtn = document.getElementById('pwa-install-btn');
  const dismissBtn = document.getElementById('pwa-install-dismiss');
  const DISMISS_KEY = 'pwa_dismissed_at';
  
  // لا تُظهر إذا رُفض خلال آخر 30 يوم
  const dismissed = localStorage.getItem(DISMISS_KEY);
  if (dismissed && Date.now() - parseInt(dismissed) < 30 * 864e5) return;
  
  window.addEventListener('beforeinstallprompt', function(e) {
    e.preventDefault();
    deferredPrompt = e;
    // أظهر فقط إذا المستخدم تفاعل (تجنب الإعلان الفوري المزعج)
    setTimeout(() => { if (deferredPrompt) banner.hidden = false; }, 8000);
  });
  
  installBtn?.addEventListener('click', async function() {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    await deferredPrompt.userChoice;
    deferredPrompt = null;
    banner.hidden = true;
  });
  
  dismissBtn?.addEventListener('click', function() {
    banner.hidden = true;
    localStorage.setItem(DISMISS_KEY, String(Date.now()));
  });
})();
</script>
```

CSS:
```css
.pwa-banner {
  display: none;
}

@media (max-width: 860px) {
  .pwa-banner {
    display: block;
    position: fixed;
    bottom: 86px;   /* فوق bottom-nav */
    left: 8px;
    right: 8px;
    z-index: 95;
    background: hsl(var(--card));
    border: 1px solid hsl(var(--primary));
    border-radius: 16px;
    padding: 12px;
    box-shadow: 0 12px 36px rgba(0, 0, 0, 0.5);
    animation: slide-up 0.3s ease-out;
  }
  .pwa-banner-content {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .pwa-banner-content > div {
    flex: 1;
    min-width: 0;
  }
  .pwa-banner-content b {
    display: block;
    font-size: 14px;
  }
  .pwa-banner-content span {
    display: block;
    font-size: 11px;
    color: hsl(var(--muted-foreground));
  }
  .tg-btn-small {
    padding: 8px 12px;
    font-size: 13px;
  }
  @keyframes slide-up {
    from { transform: translateY(100%); opacity: 0; }
    to { transform: translateY(0); opacity: 1; }
  }
}
```

### و) Haptic feedback للتفاعلات الحرجة

أضِف في `static/js/app.js`:

```javascript
// V53: haptic feedback على الأزرار المهمة (checkout, confirm)
document.addEventListener('click', function(e) {
  const el = e.target.closest('[data-haptic]');
  if (!el) return;
  if (navigator.vibrate) {
    const type = el.dataset.haptic;
    if (type === 'light') navigator.vibrate(10);
    else if (type === 'medium') navigator.vibrate([15, 10, 15]);
    else if (type === 'success') navigator.vibrate([20, 40, 20]);
  }
});
```

ثم في القوالب:
```html
<button class="tg-btn tg-primary" data-haptic="success" type="submit">
  تأكيد الشحن ⚡
</button>
```

### ز) Pull-to-refresh على صفحة الطلبات

```javascript
// static/js/pages/orders.js — جديد (بعد playbook CSP)
let startY = 0;
let pulling = false;

document.addEventListener('touchstart', e => {
  if (window.scrollY === 0) { startY = e.touches[0].pageY; pulling = true; }
});

document.addEventListener('touchmove', e => {
  if (!pulling) return;
  const deltaY = e.touches[0].pageY - startY;
  if (deltaY > 80) {
    pulling = false;
    window.location.reload();
  }
});

document.addEventListener('touchend', () => pulling = false);
```

### ح) Optimize images للـmobile

في `templates/home.html` — Hero image:

```html
<picture>
  <source media="(max-width: 480px)" 
          srcset="{{ url_for('static', filename='react-assets/hero-banner-480.webp') }}" 
          type="image/webp">
  <source media="(max-width: 860px)" 
          srcset="{{ url_for('static', filename='react-assets/hero-banner-860.webp') }}" 
          type="image/webp">
  <source srcset="{{ url_for('static', filename='react-assets/hero-banner.webp') }}" 
          type="image/webp">
  <img src="{{ url_for('static', filename='react-assets/hero-banner.jpg') }}" 
       alt="" loading="eager" fetchpriority="high">
</picture>
```

يتطلب توليد responsive images. أضِف في `tools/gen_responsive_images.py` — سكربت يستخدم Pillow لإنتاج 480/860/1920 versions.

### ط) Language switcher مؤشِّر أوضح

الحالي بـlink عادي. في الجوال نحتاج icon + label واضحان:

```html
<!-- base.html -->
<a href="{{ url_for('set_language', lang='en' if is_ar else 'ar') }}" 
   class="lang-switcher link-btn"
   data-haptic="light">
  <span aria-hidden="true">{{ '🇸🇦' if is_en else '🇬🇧' }}</span>
  <span>{{ 'العربية' if is_en else 'English' }}</span>
</a>
```

---

## 3) اختبار الأجهزة الفعلية

**اختبر على:**
- iPhone SE (2020) — الأصغر شاشة (375×667)
- iPhone 13/14 — العادي
- Samsung Galaxy A10/A20 — الأكثر انتشاراً في الخليج
- iPad (tablet breakpoint)

**BrowserStack مجاناً للمطوّرين المستقلّين** — جرّب قبل الشراء.

**قائمة فحص:**
- [ ] النص لا يقطع في 320px.
- [ ] لا scroll أفقي في أي صفحة.
- [ ] Bottom nav ظاهرة + touch targets 48px+.
- [ ] Hero CTA يوصل لـgames section عند الضغط.
- [ ] Forms: لا iOS zoom on input focus.
- [ ] Soft keyboard لا يغطي زر الإرسال.
- [ ] PWA install banner يظهر مرة واحدة بعد 8 ثوانٍ فقط.
- [ ] Haptic يعمل على Android (iOS يحتاج user gesture إضافي).
- [ ] Bottom nav CTA يضيء عند النشاط.

---

## 4) مقاييس النجاح

قبل/بعد — قِس:
- **Conversion rate** على mobile (أول شراء).
- **Bounce rate** على `/` في mobile.
- **Time to first interaction** (Mixpanel/PostHog).
- **Core Web Vitals** — LCP, INP, CLS على mobile (PageSpeed Insights).

هدف واقعي بعد هذا الـPR:
- Conversion +15%
- Bounce -10%
- INP < 200ms (من 400ms+ على الأرجح)

---

## 5) تحديث `project-context.md`

- أضِف البند للمُنجزة.
- قرار معماري:
  > **Mobile-first design system:** Bottom nav + Sticky CTA + PWA install prompt + touch targets 48px. أي قالب جديد يجب أن يختبر على 320px width قبل الـPR.
- أضِف `static/css/v53-mobile.css` إلى قسم "البنية على القرص".
- ذكِّر بـplaybooks تالية مرتبطة: AVIF conversion (اقتراح 39)، Self-host Arabic fonts (اقتراح 45)، PurgeCSS (اقتراح 40).
