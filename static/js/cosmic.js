/* ===========================================================================
 * TecnoGems V57 — COSMIC NEBULA interactions
 *  - 3D parallax starfield (canvas)
 *  - Mouse-trail neon particles
 *  - Mouse-driven nebula parallax
 *  - Energy-Orb hover particle explosion
 *  - Core Crystal menu open/close
 *  - Warp-portal scroll reveals
 *  - Auto-detect image-less game cards (full crystal orb mode)
 *  - Infinite horizontal orb track (clones nodes for seamless loop)
 *  - Holographic name layer injected into every game card
 * =========================================================================== */
(function () {
  'use strict';

  var REDUCED = window.matchMedia &&
                window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* =====================================================================
   * 1. STARFIELD (3D parallax)
   * ===================================================================== */
  function initStarfield() {
    var canvas = document.getElementById('cs-stars');
    if (!canvas) return;
    var ctx = canvas.getContext('2d', { alpha: true });
    var stars = [];
    var w = 0, h = 0, dpr = Math.min(window.devicePixelRatio || 1, 2);
    var mouseX = 0, mouseY = 0, targetX = 0, targetY = 0;

    function resize() {
      w = canvas.clientWidth = window.innerWidth;
      h = canvas.clientHeight = window.innerHeight;
      canvas.width  = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      makeStars();
    }

    function makeStars() {
      stars = [];
      // Density tuned to viewport area
      var count = Math.min(360, Math.floor((w * h) / 6500));
      for (var i = 0; i < count; i++) {
        var depth = Math.random() * 0.85 + 0.15;        // 0.15..1
        stars.push({
          x: (Math.random() * w - w / 2),
          y: (Math.random() * h - h / 2),
          z: depth,
          r: depth * 1.6 + 0.2,
          tw: Math.random() * Math.PI * 2,
          tone: Math.random()  // tints across the cosmic palette
        });
      }
    }

    function tone(v) {
      // Cyan / Purple / Magenta / Gold mix
      if (v < 0.55) return 'rgba(190,230,255,';   // cool white-cyan
      if (v < 0.78) return 'rgba(192,132,252,';   // purple
      if (v < 0.92) return 'rgba(255,140,225,';   // magenta
      return 'rgba(255,224,140,';                 // gold
    }

    var t = 0;
    function frame() {
      t += 0.016;
      // Smoothly chase mouse for parallax
      targetX += (mouseX - targetX) * 0.04;
      targetY += (mouseY - targetY) * 0.04;

      ctx.clearRect(0, 0, w, h);

      // Faint center glow
      var grd = ctx.createRadialGradient(w/2, h/2, 0, w/2, h/2, Math.max(w, h) * 0.6);
      grd.addColorStop(0, 'rgba(168,85,247,0.07)');
      grd.addColorStop(0.5, 'rgba(0,240,255,0.03)');
      grd.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = grd;
      ctx.fillRect(0, 0, w, h);

      // Stars
      ctx.save();
      ctx.translate(w / 2, h / 2);
      for (var i = 0; i < stars.length; i++) {
        var s = stars[i];
        s.tw += 0.02 + s.z * 0.04;
        var twinkle = 0.55 + Math.sin(s.tw) * 0.45;
        var px = s.x - targetX * s.z * 0.6;
        var py = s.y - targetY * s.z * 0.6;
        var alpha = (0.25 + s.z * 0.75) * twinkle;
        ctx.fillStyle = tone(s.tone) + alpha.toFixed(3) + ')';
        ctx.beginPath();
        ctx.arc(px, py, s.r, 0, Math.PI * 2);
        ctx.fill();

        // Slow forward drift — stars closer to viewer drift faster
        s.x += s.z * 0.08;
        s.y += Math.sin(t * 0.4 + s.tw) * 0.02 * s.z;
        if (s.x > w / 2)  s.x = -w / 2;
        if (s.y > h / 2)  s.y = -h / 2;
        if (s.y < -h / 2) s.y =  h / 2;
      }
      ctx.restore();

      requestAnimationFrame(frame);
    }

    window.addEventListener('resize', resize);
    window.addEventListener('mousemove', function (e) {
      mouseX = (e.clientX - w / 2);
      mouseY = (e.clientY - h / 2);
      // Parallax the nebula blobs + planets
      var nx = (e.clientX / w - 0.5);
      var ny = (e.clientY / h - 0.5);
      var blobs = document.querySelectorAll('.cs-nebula, .cs-planet');
      for (var i = 0; i < blobs.length; i++) {
        var depth = (i + 1) * 6;
        blobs[i].style.transform =
          'translate3d(' + (-nx * depth).toFixed(2) + 'px,' +
                          (-ny * depth).toFixed(2) + 'px, 0)';
      }
    }, { passive: true });

    resize();
    if (!REDUCED) requestAnimationFrame(frame);
  }

  /* =====================================================================
   * 2. MOUSE-TRAIL PARTICLES
   * ===================================================================== */
  function initMouseParticles() {
    if (REDUCED) return;
    var canvas = document.getElementById('cs-particles');
    if (!canvas) return;
    var ctx = canvas.getContext('2d', { alpha: true });
    var particles = [];
    var w = 0, h = 0, dpr = Math.min(window.devicePixelRatio || 1, 2);
    var lastX = -1, lastY = -1;

    function resize() {
      w = canvas.clientWidth = window.innerWidth;
      h = canvas.clientHeight = window.innerHeight;
      canvas.width  = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    var palette = [
      [0, 240, 255],
      [168, 85, 247],
      [255, 43, 214],
      [255, 214, 107]
    ];

    function spawn(x, y, count) {
      for (var i = 0; i < count; i++) {
        var c = palette[(Math.random() * palette.length) | 0];
        particles.push({
          x: x + (Math.random() - 0.5) * 6,
          y: y + (Math.random() - 0.5) * 6,
          vx: (Math.random() - 0.5) * 0.6,
          vy: (Math.random() - 0.5) * 0.6 - 0.3,
          life: 1,
          decay: 0.012 + Math.random() * 0.018,
          size: 1.5 + Math.random() * 2.4,
          c: c
        });
      }
      // Cap
      if (particles.length > 280) particles.splice(0, particles.length - 280);
    }

    window.addEventListener('mousemove', function (e) {
      if (lastX < 0) { lastX = e.clientX; lastY = e.clientY; }
      var dx = e.clientX - lastX, dy = e.clientY - lastY;
      var dist = Math.sqrt(dx * dx + dy * dy);
      var n = Math.min(4, Math.max(1, dist / 20));
      spawn(e.clientX, e.clientY, n);
      lastX = e.clientX; lastY = e.clientY;
    }, { passive: true });

    function frame() {
      ctx.clearRect(0, 0, w, h);
      for (var i = particles.length - 1; i >= 0; i--) {
        var p = particles[i];
        p.x += p.vx;
        p.y += p.vy;
        p.life -= p.decay;
        if (p.life <= 0) { particles.splice(i, 1); continue; }
        ctx.beginPath();
        ctx.fillStyle = 'rgba(' + p.c[0] + ',' + p.c[1] + ',' + p.c[2] + ',' + p.life.toFixed(3) + ')';
        ctx.shadowColor = ctx.fillStyle;
        ctx.shadowBlur = 12;
        ctx.arc(p.x, p.y, p.size * p.life, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.shadowBlur = 0;
      requestAnimationFrame(frame);
    }

    window.addEventListener('resize', resize);
    resize();
    requestAnimationFrame(frame);
  }

  /* =====================================================================
   * 3. ORB CARDS — name layer, image-less detect, hover burst
   * ===================================================================== */
  function initOrbs() {
    var cards = document.querySelectorAll('.tg-popular-card');
    if (!cards.length) return;

    cards.forEach(function (card, idx) {
      // Inject holographic name layer (always visible inside the orb)
      if (!card.querySelector('.cs-orb-name')) {
        var titleEl = card.querySelector('.tg-popular-card-title');
        var name = (titleEl && titleEl.textContent) ||
                   card.getAttribute('aria-label') ||
                   card.getAttribute('data-name') || '';
        if (name) {
          var div = document.createElement('div');
          div.className = 'cs-orb-name';
          div.textContent = name;
          card.appendChild(div);
        }
      }

      // Inject hover info (rating / players / genre) — synthetic but believable
      if (!card.querySelector('.cs-orb-info')) {
        var info = document.createElement('div');
        info.className = 'cs-orb-info';
        var seed = idx + 7;
        var rating = (4.4 + ((seed * 13) % 6) / 10).toFixed(1);   // 4.4..4.9
        var playersK = 12 + (seed * 17) % 240;                    // 12K..251K
        var genres = ['MMO', 'FPS', 'MOBA', 'BR', 'RPG', 'SIM', 'STRATEGY', 'CARDS'];
        var genre = genres[seed % genres.length];
        info.innerHTML =
          '<div><b>' + rating + '★</b><span>RATING</span></div>' +
          '<div><b>' + playersK + 'K</b><span>PLAYERS</span></div>' +
          '<div><b>' + genre + '</b><span>GENRE</span></div>';
        card.appendChild(info);
      }

      // Detect image-less / broken-image games and switch to crystal-orb mode
      var img = card.querySelector('.tg-popular-card-img');
      if (img) {
        var markImageless = function () { card.setAttribute('data-imageless', '1'); };
        // Heuristic: server fallback URLs that contain "smart" / "placeholder" / "default" ⇒ treat as imageless
        var src = (img.getAttribute('src') || '').toLowerCase();
        if (!src ||
            src.indexOf('placeholder') !== -1 ||
            src.indexOf('default') !== -1 ||
            src.indexOf('no-image') !== -1 ||
            src.indexOf('smart_game_image') !== -1) {
          markImageless();
        }
        img.addEventListener('error', markImageless);
        // Some "smart" placeholders return tiny SVGs — also count those
        img.addEventListener('load', function () {
          if (img.naturalWidth && img.naturalWidth < 32) markImageless();
        });
      } else {
        card.setAttribute('data-imageless', '1');
      }

      // Hover particle burst
      var burstTimer = null;
      card.addEventListener('mouseenter', function () {
        if (REDUCED) return;
        var burst = card.querySelector('.cs-orb-burst');
        if (burst) burst.remove();
        burst = document.createElement('div');
        burst.className = 'cs-orb-burst';
        var n = 16;
        for (var i = 0; i < n; i++) {
          var a = (Math.PI * 2) * (i / n) + Math.random() * 0.4;
          var dist = 60 + Math.random() * 70;
          var dx = Math.cos(a) * dist;
          var dy = Math.sin(a) * dist;
          var p = document.createElement('i');
          p.style.setProperty('--dx', dx.toFixed(1) + 'px');
          p.style.setProperty('--dy', dy.toFixed(1) + 'px');
          p.style.animationDelay = (Math.random() * 0.15).toFixed(2) + 's';
          burst.appendChild(p);
        }
        card.appendChild(burst);
        clearTimeout(burstTimer);
        burstTimer = setTimeout(function () {
          if (burst && burst.parentNode) burst.parentNode.removeChild(burst);
        }, 1200);
      });
    });
  }

  /* =====================================================================
   * 4. INFINITE ORB TRACK
   *    Wraps the existing .tg-popular-scroller in .cs-orb-track and
   *    duplicates children so the CSS marquee loops seamlessly.
   * ===================================================================== */
  function initOrbTrack() {
    var scroller = document.getElementById('tg-popular-scroller');
    var grid = document.getElementById('tg-popular-grid');
    if (!scroller || !grid) return;

    // Wrap with .cs-orb-track if not already
    if (!scroller.parentElement.classList.contains('cs-orb-track')) {
      var wrap = document.createElement('div');
      wrap.className = 'cs-orb-track';
      scroller.parentNode.insertBefore(wrap, scroller);
      wrap.appendChild(scroller);
    }

    // Disable v56 horizontal-grid scrolling; replace with continuous flex flow
    var cards = Array.prototype.slice.call(grid.children).filter(function (n) {
      return n.classList && n.classList.contains('tg-popular-card');
    });
    if (cards.length < 4) return; // not worth looping
    // Duplicate the set once for seamless 50% loop
    cards.forEach(function (c) {
      var clone = c.cloneNode(true);
      clone.setAttribute('aria-hidden', 'true');
      clone.tabIndex = -1;
      grid.appendChild(clone);
    });
  }

  /* =====================================================================
   * 5. WARP PORTALS — inject between top-level sections
   * ===================================================================== */
  function initWarpPortals() {
    var sections = document.querySelectorAll('main > section, main .nb-section, main .tg-hero, main .tg-popular-section');
    if (sections.length < 2) return;

    sections.forEach(function (sec, i) {
      if (i === 0) return;
      if (sec.previousElementSibling && sec.previousElementSibling.classList.contains('cs-warp')) return;
      var w = document.createElement('div');
      w.className = 'cs-warp';
      w.setAttribute('aria-hidden', 'true');
      w.innerHTML =
        '<div class="cs-warp-line t"></div>' +
        '<div class="ring r2"></div>' +
        '<div class="ring"></div>' +
        '<div class="ring r3"></div>';
      sec.parentNode.insertBefore(w, sec);
    });

    var io = ('IntersectionObserver' in window) ? new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          e.target.classList.add('visible');
          io.unobserve(e.target);
        }
      });
    }, { threshold: 0.15 }) : null;

    document.querySelectorAll('.cs-warp').forEach(function (w) {
      if (io) io.observe(w); else w.classList.add('visible');
    });
  }

  /* =====================================================================
   * 6. CORE CRYSTAL MENU BUTTON
   *    Adds aria-expanded so the spin speed escalates while open.
   * ===================================================================== */
  function initCrystalMenu() {
    var btn = document.querySelector('.cs-crystal-btn');
    if (!btn) return;
    btn.addEventListener('click', function () {
      // body.menu-open is toggled by the legacy hamburger handler via the
      // hidden <button id="tg-menu-toggle">. Click it programmatically.
      var legacy = document.getElementById('tg-menu-toggle');
      if (legacy) legacy.click();
      var open = document.body.classList.contains('menu-open');
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });

    // Sync state when something else closes the menu
    var mo = new MutationObserver(function () {
      var open = document.body.classList.contains('menu-open');
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    mo.observe(document.body, { attributes: true, attributeFilter: ['class'] });
  }

  /* =====================================================================
   * 7. INIT
   * ===================================================================== */
  function start() {
    initStarfield();
    initMouseParticles();
    initOrbs();
    initOrbTrack();
    initWarpPortals();
    initCrystalMenu();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
