// The tooltip of every document the server sends: an element's title shows in .tooltip once the pointer rests on
// it for SHOW_MS, and right away when another tooltip closed less than WARM_MS ago. The title moves to data-tip
// while the pointer is on its element, so the browser's slower tooltip never shows, and comes back when it leaves.
// A disabled button gets no mouse events in some browsers, so the element under the pointer comes from :hover.
(() => {
  const SHOW_MS = 300, WARM_MS = 500, GAP = 6, EDGE = 8;
  let tip = null, held = null, shown = null, dismissed = null, timer = 0, warmUntil = 0, x = 0;
  const titled = (el) => (el instanceof Element ? el.closest('[title], [data-tip]') : null);
  function hovered() { const all = document.querySelectorAll(':hover'); return titled(all[all.length - 1]); }
  function hold(el) {
    if (el === held) return;
    if (held && held.dataset.tip !== undefined) { held.title = held.dataset.tip; delete held.dataset.tip; }
    held = el;
    if (el && el.hasAttribute('title')) { el.dataset.tip = el.title; el.removeAttribute('title'); }
  }
  function hide() {
    clearTimeout(timer);
    if (!shown) return;
    shown = null; warmUntil = performance.now() + WARM_MS;
    tip.classList.remove('on');
  }
  function show(el) {
    hold(el);
    if (!el || el === dismissed || !el.dataset.tip) return;
    if (!tip) {
      tip = document.createElement('div');
      tip.className = 'tooltip';
      tip.setAttribute('aria-hidden', 'true');
      document.body.appendChild(tip);
    }
    tip.textContent = el.dataset.tip;
    tip.style.left = '0px';
    const r = el.getBoundingClientRect(), t = tip.getBoundingClientRect();
    const below = r.bottom + GAP + t.height <= innerHeight - EDGE;
    tip.style.left = `${Math.max(EDGE, Math.min(x - t.width / 2, innerWidth - t.width - EDGE))}px`;
    tip.style.top = `${below ? r.bottom + GAP : Math.max(EDGE, r.top - GAP - t.height)}px`;
    tip.classList.add('on');
    shown = el;
  }
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(() => show(hovered()), shown || performance.now() < warmUntil ? 0 : SHOW_MS);
  }
  document.addEventListener('mouseover', (e) => {
    const el = titled(e.target);
    hold(el);
    if (el !== dismissed) dismissed = null;
    if (el !== shown) { hide(); schedule(); }
  }, true);
  document.addEventListener('mousemove', (e) => { x = e.clientX; if (!shown) schedule(); }, true);
  document.addEventListener('mouseout', (e) => { if (!e.relatedTarget) { hide(); hold(null); dismissed = null; } }, true);
  for (const type of ['mousedown', 'keydown', 'wheel']) {
    document.addEventListener(type, () => { dismissed = shown || held; hide(); }, true);
  }
  document.addEventListener('scroll', hide, true);
})();
