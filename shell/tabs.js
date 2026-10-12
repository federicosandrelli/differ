// The tabbed shell: a tab strip (the title bar strip in the desktop app) and one iframe per tab. The home tab
// holds the start page; a link to a page opens a tab for it, or brings the tab that has it to the front. The
// tabs survive a relaunch through localStorage. A hidden frame keeps its layout, so a page keeps its scroll
// position across switches. Pages talk to the shell through frame.js; the messages are known only to the two.
(function () {
  const APP = document.documentElement.classList.contains('app');
  const HOME_ICON = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path d="M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>';
  const bar = document.getElementById('tabs'), panes = document.getElementById('panes');
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  // A tab is keyed by its page's path; a page still being resolved (/open?pr=...) by its query until it reports.
  const keyOf = (url) => { const u = new URL(url, location.origin); const path = u.pathname.replace(/\/+$/, '').toLowerCase(); return path === '/open' ? path + u.search.toLowerCase() : path; };
  const home = { key: 'home', url: '/home', title: 'Pull requests', badge: 0, frame: null };
  let tabs = [], active = home;

  const lsGet = () => { try { return JSON.parse(localStorage.getItem('dr:tabs')) || {}; } catch (e) { return {}; } };
  const save = () => { try { localStorage.setItem('dr:tabs', JSON.stringify({ active: active.key, tabs: tabs.map(t => ({ url: t.url, title: t.title, badge: t.badge })) })); } catch (e) {} };

  function frameOf(tab) {
    if (!tab.frame) { tab.frame = document.createElement('iframe'); tab.frame.src = tab.url; panes.appendChild(tab.frame); }
    return tab.frame;
  }
  function draw() {
    const pill = (t) => `<div class="tab ${t === active ? 'on' : ''}" data-key="${esc(t.key)}" title="${esc(t.title)}"><span class="label">${esc(t.title)}</span>${t.badge ? `<span class="badge" title="${t.badge} unresolved">${t.badge}</span>` : ''}<button class="close" data-close="${esc(t.key)}" title="Close tab">×</button></div>`;
    bar.innerHTML = `<div class="tab home ${active === home ? 'on' : ''}" data-key="home" title="Pull requests">${HOME_ICON}</div>` + tabs.map(pill).join('');
    document.title = active.title;
  }
  function activate(tab) {
    active = tab;
    const frame = frameOf(tab);
    for (const f of panes.children) f.classList.toggle('on', f === frame);
    draw(); save();
    if (frame.contentWindow) { frame.contentWindow.postMessage({ type: 'shown' }, location.origin); frame.contentWindow.focus(); }
  }
  function open(url) {
    const key = keyOf(url);
    if (key === '' || key === '/home') return activate(home);
    let tab = tabs.find(t => t.key === key);
    if (!tab) { tab = { key, url, title: '…', badge: 0, frame: null }; tabs.push(tab); }
    activate(tab);
  }
  function close(tab) {
    const i = tabs.indexOf(tab);
    if (i < 0) return;
    tabs.splice(i, 1);
    if (tab.frame) tab.frame.remove();
    if (active === tab) activate(tabs[i] || tabs[i - 1] || home); else { draw(); save(); }
  }
  const byKey = (key) => key === 'home' ? home : tabs.find(t => t.key === key);
  // Cmd+Option+Left/Right steps through the home tab and the open tabs in strip order, wrapping at the ends.
  // Pages forward the chord when focus is inside them; this handler takes it when focus is on the strip.
  function step(dir) {
    const all = [home, ...tabs];
    activate(all[(all.indexOf(active) + dir + all.length) % all.length]);
  }
  window.addEventListener('keydown', (e) => {
    if (!e.metaKey || !e.altKey || e.ctrlKey || e.shiftKey || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return;
    e.preventDefault();
    step(e.key === 'ArrowLeft' ? -1 : 1);
  });

  bar.addEventListener('click', (e) => {
    const c = e.target.closest('[data-close]');
    if (c) { close(byKey(c.dataset.close)); return; }
    const t = e.target.closest('[data-key]');
    if (t) activate(byKey(t.dataset.key));
  });
  bar.addEventListener('auxclick', (e) => {
    const t = e.button === 1 && e.target.closest('.tab:not(.home)');
    if (t) close(byKey(t.dataset.key));
  });
  window.addEventListener('message', (e) => {
    if (e.origin !== location.origin || !e.data) return;
    const m = e.data;
    if (m.type === 'open') { open(m.url); return; }
    if (m.type === 'step') { step(m.dir === -1 ? -1 : 1); return; }
    if (m.type === 'external') { if (APP) location.href = m.url; else window.open(m.url, '_blank', 'noopener'); return; }
    const tab = tabs.find(t => t.frame && t.frame.contentWindow === e.source);
    if (!tab) return;
    if (m.type === 'badge') { tab.badge = m.n; draw(); save(); }
    if (m.type === 'page') {
      tab.url = m.url; tab.title = m.title;
      const key = keyOf(m.url), twin = tabs.find(t => t !== tab && t.key === key);
      if (twin) { close(tab); activate(twin); return; }
      tab.key = key; draw(); save();
    }
  });

  const saved = lsGet();
  tabs = (saved.tabs || []).filter(t => t && t.url).map(t => ({ key: keyOf(t.url), url: t.url, title: t.title || '…', badge: t.badge || 0, frame: null }));
  activate(byKey(saved.active) || home);
})();
