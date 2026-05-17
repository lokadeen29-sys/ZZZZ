/* ===========================================================================
 * TecnoGems V60.1 — Home page: minimal, mobile-friendly interactions only.
 * Removed legacy parallax, 3D-tilt, FAQ accordion (none of those elements
 * exist in the new design and the dead code burned CPU on every scroll).
 * V61: smooth fade-in for game cover images so lazy-loaded artwork no
 * longer "pops in" — also kills the white flash that looked like static
 * on Android/Huawei browsers.
 * =========================================================================== */
(function () {
  'use strict';

  // Smooth scroll for in-page anchor links (#games, #features, …)
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href^="#"]');
    if (!a) return;
    var href = a.getAttribute('href');
    if (!href || href === '#') return;
    var target = document.querySelector(href);
    if (!target) return;
    e.preventDefault();
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  // V61: progressive image reveal for game covers.
  // Uses 'load' + .complete fallback so cached images appear instantly
  // while fresh ones fade in once decoded.
  function markLoaded(img) {
    img.classList.add('is-loaded');
    img.setAttribute('data-loaded', '1');
  }
  function initCovers() {
    var imgs = document.querySelectorAll('.v60-game-cover img, .v60-hero-card-frame img');
    for (var i = 0; i < imgs.length; i++) {
      var img = imgs[i];
      if (img.complete && img.naturalWidth > 0) {
        markLoaded(img);
      } else {
        img.addEventListener('load', function () { markLoaded(this); }, { once: true });
        img.addEventListener('error', function () {
          // Show the broken image area anyway — still better than empty void.
          markLoaded(this);
        }, { once: true });
      }
    }
  }
  if (document.readyState !== 'loading') initCovers();
  else document.addEventListener('DOMContentLoaded', initCovers);
})();
