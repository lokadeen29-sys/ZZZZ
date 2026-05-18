document.addEventListener('DOMContentLoaded', function(){
  document.getElementById('products-grid') && document.getElementById('products-grid').classList.remove('tg-products-skeleton');
});
(function filterProducts(){
  var input = document.getElementById('productSearch');
  if (!input) return;
  var cards = Array.from(document.querySelectorAll('.searchable-product'));
  var names = cards.map(function(c){ return (c.getAttribute('data-name') || c.innerText || '').toLowerCase(); });
  input.addEventListener('input', function(){
    var q = this.value.trim().toLowerCase();
    var visible = 0;
    cards.forEach(function(card, i){
      var show = names[i].indexOf(q) >= 0;
      card.style.display = show ? '' : 'none';
      if (show) visible++;
    });
    var noMsg = document.getElementById('no-products-msg');
    if (noMsg) noMsg.style.display = visible === 0 && q ? '' : 'none';
  });
})();
