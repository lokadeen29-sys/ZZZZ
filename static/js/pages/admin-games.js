(function(){
  const search = document.getElementById('gameSearch');
  const counter = document.getElementById('gameCounter');
  // V68: rows now live inside two server columns; collect them all.
  const rows = Array.from(document.querySelectorAll('.admin-game-row[data-provider]'));
  let mode = 'active';

  function normalize(s){ return (s || '').toLowerCase().trim(); }

  function render(){
    const q = normalize(search ? search.value : '');
    let visible = 0;

    rows.forEach(row => {
      const input = row.querySelector('input.js-active-cb');
      const isActive = row.classList.contains('is-active') || (input && input.checked);
      const hay = normalize(row.dataset.gameName);
      let show = false;

      if (q.length > 0) {
        show = hay.includes(q);
      } else if (mode === 'all') {
        show = true;
      } else {
        show = isActive;
      }

      row.style.display = show ? '' : 'none';
      if (show) visible++;
    });

    if (!counter) return;
    if (q.length > 0) {
      counter.textContent = '\u0646\u062a\u0627\u0626\u062c \u0627\u0644\u0628\u062d\u062b: ' + visible;
    } else if (mode === 'all') {
      counter.textContent = '\u064a\u062a\u0645 \u0639\u0631\u0636 \u062c\u0645\u064a\u0639 \u0627\u0644\u0623\u0644\u0639\u0627\u0628: ' + visible;
    } else {
      counter.textContent = '\u064a\u062a\u0645 \u0639\u0631\u0636 \u0627\u0644\u0623\u0644\u0639\u0627\u0628 \u0627\u0644\u0645\u0641\u0639\u0651\u0644\u0629 \u0641\u0642\u0637: ' + visible + '. \u0627\u0643\u062a\u0628 \u0641\u064a \u0627\u0644\u0628\u062d\u062b \u0644\u0625\u0638\u0647\u0627\u0631 \u0644\u0639\u0628\u0629 \u063a\u064a\u0631 \u0645\u0641\u0639\u0651\u0644\u0629.';
    }
  }

  if (search) search.addEventListener('input', render);
  const showAllBtn = document.getElementById('showAllGames');
  const hideInactiveBtn = document.getElementById('hideInactiveGames');
  if (showAllBtn) showAllBtn.addEventListener('click', () => {
    mode = 'all'; if (search) search.value = ''; render();
  });
  if (hideInactiveBtn) hideInactiveBtn.addEventListener('click', () => {
    mode = 'active'; if (search) search.value = ''; render();
  });

  // Active checkbox: keep "show on home" checkbox in sync with active state.
  rows.forEach(row => {
    const input = row.querySelector('input.js-active-cb');
    if (!input) return;
    input.addEventListener('change', () => {
      row.classList.toggle('is-active', input.checked);
      const homeCb = row.querySelector('input.js-home-cb');
      if (homeCb) {
        homeCb.disabled = !input.checked;
        if (!input.checked) homeCb.checked = false;
      }
      render();
    });
  });

  // ---------------------------------------------------------------------
  // V68: server enable/disable toggle — visual feedback on the column.
  // The actual filtering of games on the homepage happens server-side via
  // the show_server1 / show_server2 settings. Here we just dim the column
  // so the operator immediately sees the effect of switching it off.
  // ---------------------------------------------------------------------
  document.querySelectorAll('.js-server-toggle').forEach(cb => {
    const target = cb.getAttribute('data-target');
    const col = document.querySelector('.admin-server-col[data-provider="' + target + '"]');
    if (!col) return;
    cb.addEventListener('change', () => {
      col.classList.toggle('server-off', !cb.checked);
    });
  });

  // ---------------------------------------------------------------------
  // V68: easy ordering — Up/Down arrows reorder the row visually AND
  // auto-renumber every sort_order input in that column starting at 1.
  // The user can still edit the numeric input directly.
  // ---------------------------------------------------------------------
  function renumberColumn(listEl) {
    const rows = Array.from(listEl.querySelectorAll('.admin-game-row'));
    rows.forEach((row, idx) => {
      const inp = row.querySelector('input.js-sort-input');
      if (inp) inp.value = String(idx + 1);
    });
  }

  document.querySelectorAll('.server-games').forEach(listEl => {
    listEl.addEventListener('click', (ev) => {
      const upBtn = ev.target.closest('.js-move-up');
      const downBtn = ev.target.closest('.js-move-down');
      if (!upBtn && !downBtn) return;
      ev.preventDefault();
      const row = ev.target.closest('.admin-game-row');
      if (!row) return;
      if (upBtn) {
        const prev = row.previousElementSibling;
        if (prev && prev.classList.contains('admin-game-row')) {
          listEl.insertBefore(row, prev);
        }
      } else if (downBtn) {
        const next = row.nextElementSibling;
        if (next && next.classList.contains('admin-game-row')) {
          listEl.insertBefore(next, row);
        }
      }
      renumberColumn(listEl);
      // brief highlight so the user sees what moved
      row.classList.add('row-flash');
      setTimeout(() => row.classList.remove('row-flash'), 350);
    });
  });

  render();
})();
