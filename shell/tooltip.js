// The tooltip of every document the server sends: an element's title shows in .tooltip once the pointer rests on
// it for SHOW_MS, and right away when the pointer comes back to the same element less than WARM_MS after its
// tooltip closed. A title that repeats text the element shows in full adds nothing, so it shows only while some of
// that text is cut off. While the pointer is on an element, the titles of the element and of its ancestors move to
// data-tip, so the browser's slower tooltip never shows; they come back when the pointer leaves. A disabled button
// gets no mouse events in some browsers, so the element under the pointer comes from :hover. An open modal dialog
// sits above everything else, so a tooltip inside one is drawn inside it. A click or a key press closes the tooltip
// until the pointer reaches an element with a different tooltip or moves off titled elements. A redraw under a
// still pointer fires mouseover on whatever is there first, so only a mousemove counts as moving off.
(() => {
  const SHOW_MS = 300, WARM_MS = 500, GAP = 6, EDGE = 8;
  let tip = null, held = null, moved = [], shown = null, dismissed = null, timer = 0, warm = null, warmUntil = 0, x = 0;
  const titled = (el) => (el instanceof Element ? el.closest('[title]:not([title=""]), [data-tip]') : null);
  const flat = (s) => s.replace(/\s+/g, ' ').trim();
  // WebKit rounds scrollWidth up under page zoom, so a box only counts as cut off when it is 2 px too narrow.
  function cutOff(el) {
    const tip = flat(el.dataset.tip);
    return [el, ...el.querySelectorAll('*')].some((e) => {
      const text = flat(e.textContent);
      return (e === el || (text && tip.includes(text)))
        && e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX !== 'visible';
    });
  }
  const addsText = (el) => !flat(el.innerText).includes(flat(el.dataset.tip)) || cutOff(el);
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
  function hide(keepWarm = true) {
    clearTimeout(timer);
    if (!shown) return;
    if (keepWarm) { warm = shown; warmUntil = performance.now() + WARM_MS; }
    shown = null;
    tip.classList.remove('on');
  }
  function show(el) {
    hold(el);
    if (!el || !el.dataset.tip || el.dataset.tip === dismissed || !addsText(el)) return;
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
  function schedule(el, delay = el && el === warm && performance.now() < warmUntil ? 0 : SHOW_MS) {
    clearTimeout(timer);
    timer = setTimeout(() => show(hovered()), delay);
  }
  document.addEventListener('mouseover', (e) => {
    const el = titled(e.target);
    hold(el);
    if (el && el.dataset.tip !== dismissed) dismissed = null;
    if (el !== shown) { hide(); schedule(el); }
  }, true);
  document.addEventListener('mousemove', (e) => {
    x = e.clientX;
    if (!titled(e.target)) dismissed = null;
    if (!shown) schedule(titled(e.target));
  }, true);
  document.addEventListener('mouseout', (e) => { if (!e.relatedTarget) { hide(); hold(null); dismissed = null; } }, true);
  for (const type of ['mousedown', 'keydown']) {
    document.addEventListener(type, () => { const el = shown || held; dismissed = el ? el.dataset.tip : null; hide(); }, true);
  }
  document.addEventListener('scroll', () => { hide(false); schedule(null, SHOW_MS); }, true);
})();
