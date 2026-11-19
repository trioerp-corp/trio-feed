// Go Green ERP — small helpers: searchable lookups (accounts, cost centers, equipment, partners, items) and line rows.
(function () {
  function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

  function initLookup(wrap) {
    if (wrap.dataset.ready) return;
    wrap.dataset.ready = '1';
    const kind = wrap.dataset.lookup;
    const hidden = wrap.querySelector('input[type=hidden]');
    const input = wrap.querySelector('input[type=text]');
    const menu = document.createElement('div');
    menu.className = 'menu';
    menu.setAttribute('role', 'listbox');
    wrap.appendChild(menu);
    let items = [], act = -1;
    const pick = (it) => {
      hidden.value = it.id; input.value = it.label; menu.classList.remove('open');
      input.dispatchEvent(new CustomEvent('lookup:picked', { detail: it, bubbles: true }));
    };
    const draw = () => {
      menu.innerHTML = '';
      items.forEach((it, i) => {
        const b = document.createElement('button');
        b.type = 'button'; b.setAttribute('role', 'option');
        b.textContent = it.label;
        if (it.hint) { const s = document.createElement('small'); s.textContent = it.hint; b.appendChild(s); }
        if (i === act) b.classList.add('act');
        b.addEventListener('mousedown', (e) => { e.preventDefault(); pick(it); });
        menu.appendChild(b);
      });
      menu.classList.toggle('open', items.length > 0);
    };
    const search = debounce(async () => {
      const q = input.value.trim();
      const extra = wrap.dataset.filter ? '&' + wrap.dataset.filter : '';
      try {
        const r = await fetch(`/ui/lookup/${kind}/?q=${encodeURIComponent(q)}${extra}`, { credentials: 'same-origin' });
        items = (await r.json()).results; act = -1; draw();
      } catch (e) { items = []; draw(); }
    }, 180);
    input.addEventListener('input', () => { hidden.value = ''; search(); });
    input.addEventListener('focus', search);
    input.addEventListener('blur', () => setTimeout(() => menu.classList.remove('open'), 150));
    input.addEventListener('keydown', (e) => {
      if (!menu.classList.contains('open')) return;
      if (e.key === 'ArrowDown') { act = Math.min(items.length - 1, act + 1); draw(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { act = Math.max(0, act - 1); draw(); e.preventDefault(); }
      else if (e.key === 'Enter' && act >= 0) { pick(items[act]); e.preventDefault(); }
      else if (e.key === 'Escape') menu.classList.remove('open');
    });
  }

  function initAll(root) { root.querySelectorAll('[data-lookup]').forEach(initLookup); }

  // add a row to a Django formset: <tbody data-formset="prefix"> + <template id="prefix-empty">
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-add-row]');
    if (!btn) return;
    const prefix = btn.dataset.addRow;
    const total = document.getElementById(`id_${prefix}-TOTAL_FORMS`);
    const tpl = document.getElementById(`${prefix}-empty`);
    const body = document.querySelector(`[data-formset="${prefix}"]`);
    const n = parseInt(total.value, 10);
    const html = tpl.innerHTML.replace(/__prefix__/g, n);
    body.insertAdjacentHTML('beforeend', html);
    total.value = n + 1;
    initAll(body.lastElementChild);
  });

  // show the equipment field only where the account needs it (fuel, oil, maintenance)
  document.addEventListener('lookup:picked', (e) => {
    const row = e.target.closest('tr, .line');
    if (!row || !e.detail.needs) return;
    const eq = row.querySelector('[data-lookup="equipment"]');
    if (eq) { eq.closest('td, .field').classList.toggle('need', e.detail.needs.includes('dim2')); }
  });

  // live total of voucher lines
  document.addEventListener('input', (e) => {
    if (!e.target.matches('[data-amount]')) return;
    const sum = [...document.querySelectorAll('[data-amount]')].reduce((s, i) => s + (parseFloat(i.value) || 0), 0);
    const out = document.getElementById('lines-total');
    if (out) out.textContent = sum.toFixed(2);
  });

  document.addEventListener('DOMContentLoaded', () => initAll(document));
})();
