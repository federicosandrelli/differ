// The tooltip of every document the server sends: an element's title shows in .tooltip once the pointer rests on
// it for SHOW_MS, and right away when another tooltip closed less than WARM_MS ago. While the pointer is on an
// element, the titles of the element and of its ancestors move to data-tip, so the browser's slower tooltip never
// shows; they come back when the pointer leaves. A disabled button gets no mouse events in some browsers, so the
// element under the pointer comes from :hover. An open modal dialog sits above everything else, so a tooltip
// inside one is drawn inside it.
(() => {
  const SHOW_MS = 300, WARM_MS = 500, GAP = 6, EDGE = 8;
  let tip = null, held = null, moved = [], shown = null, dismissed = null, timer = 0, warmUntil = 0, x = 0;
  const titled = (el) => (el instanceof Element ? el.closest('[title], [data-tip]') : null);
  function hovered() { const all = document.querySelectorAll(':hover'); return titled(all[all.length - 1]); }
  function hold(el) {
    if (el === held) return;
    held = el;
    const keep = [];
    for (let e = el; e; e = titled(e.parentElement)) keep.push(e);
    for (const e of moved) if (!keep.includes(e)) { e.title = e.dataset.tip; delete e.dataset.tip; }
    for (const e of keep) if (e.hasAttribute('title')) { e.dataset.tip = e.title; e.removeAttribute('title'); }
    moved = keep;
  }
  function hide(warm = true) {
    clearTimeout(timer);
    if (!shown) return;
    shown = null;
    if (warm) warmUntil = performance.now() + WARM_MS;
    tip.classList.remove('on');
  }
  function show(el) {
    hold(el);
    if (!el || el === dismissed || !el.dataset.tip) return;
    if (!tip) {
      tip = document.createElement('div');
      tip.className = 'tooltip';
      tip.setAttribute('aria-hidden', 'true');
    }
    const host = el.closest('dialog[open]') || document.body;
    if (tip.parentNode !== host) host.appendChild(tip);
    tip.textContent = el.dataset.tip;
    tip.style.left = '0px';
    const r = el.getBoundingClientRect(), t = tip.getBoundingClientRect();
    const below = r.bottom + GAP + t.height <= innerHeight - EDGE;
    tip.style.left = `${Math.max(EDGE, Math.min(x - t.width / 2, innerWidth - t.width - EDGE))}px`;
    tip.style.top = `${below ? r.bottom + GAP : Math.max(EDGE, r.top - GAP - t.height)}px`;
    tip.classList.add('on');
    shown = el;
  }
  function schedule(delay = shown || performance.now() < warmUntil ? 0 : SHOW_MS) {
    clearTimeout(timer);
    timer = setTimeout(() => show(hovered()), delay);
  }
  document.addEventListener('mouseover', (e) => {
    const el = titled(e.target);
    hold(el);
    if (el !== dismissed) dismissed = null;
    if (el !== shown) { hide(); schedule(); }
  }, true);
  document.addEventListener('mousemove', (e) => { x = e.clientX; if (!shown) schedule(); }, true);
  document.addEventListener('mouseout', (e) => { if (!e.relatedTarget) { hide(); hold(null); dismissed = null; } }, true);
  for (const type of ['mousedown', 'keydown']) {
    document.addEventListener(type, () => { dismissed = shown || held; hide(); }, true);
  }
  document.addEventListener('scroll', () => { hide(false); schedule(SHOW_MS); }, true);
})();
