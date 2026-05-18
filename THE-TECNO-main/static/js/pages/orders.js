// V70 CSP: orders page wiring without inline event handlers.
// Buttons declare:
//   .filter-btn[data-filter="..."]           — filter pill
//   .copy-btn[data-code="..."][data-id="..."] — copy order code
// and listeners are bound here so the strict CSP (no 'unsafe-inline')
// does not silently kill them.

var COPY_ICON_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';

function copyCode(code, _id, btn) {
  if (!navigator.clipboard) return;
  navigator.clipboard.writeText(code).then(function() {
    if (!btn) return;
    btn.innerHTML = '\u2713';
    setTimeout(function(){ btn.innerHTML = COPY_ICON_SVG; }, 1500);
  });
}

function filterOrders(filter, btn) {
  document.querySelectorAll('.filter-btn').forEach(function(b){ b.classList.remove('active'); });
  if (btn) btn.classList.add('active');
  var cards = document.querySelectorAll('.order-card');
  var visible = 0;
  cards.forEach(function(card) {
    var s = card.dataset.status;
    var show = filter === 'all' || s === filter || (filter === 'processing' && (s === 'processing' || s === 'supplier_pending'));
    card.style.display = show ? '' : 'none';
    if (show) visible++;
  });
  var noMsg = document.getElementById('no-orders-msg');
  if (noMsg) noMsg.style.display = visible === 0 ? '' : 'none';
}

function _ordersInit() {
  // Filter pills — delegate to support translated labels and dynamic re-render.
  var filterBar = document.getElementById('orders-filter');
  if (filterBar) {
    filterBar.addEventListener('click', function(e){
      var btn = e.target.closest('.filter-btn');
      if (!btn || !filterBar.contains(btn)) return;
      filterOrders(btn.dataset.filter || 'all', btn);
    });
  }

  // Copy-code buttons — single delegated listener.
  document.addEventListener('click', function(e){
    var btn = e.target.closest('.copy-btn[data-code]');
    if (!btn) return;
    copyCode(btn.dataset.code, btn.dataset.id, btn);
  });
}

// The script tag is loaded with `defer`, so by the time it runs the DOM
// is already parsed. Guard with readyState anyway in case it's ever
// loaded synchronously or injected dynamically.
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _ordersInit);
} else {
  _ordersInit();
}
