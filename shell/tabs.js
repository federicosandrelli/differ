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

  const lsGet = () => { try { return JSON.parse(localStorage.getItem('dr:tabs')) || {}; } catch { return {}; } };
  const save = () => { try { localStorage.setItem('dr:tabs', JSON.stringify({ active: active.key, tabs: tabs.map(t => ({ url: t.url, title: t.title, status: t.status, badge: t.badge })) })); } catch {} };

  function frameOf(tab) {
    if (!tab.frame) { tab.frame = document.createElement('iframe'); tab.frame.src = tab.url; panes.appendChild(tab.frame); }
    return tab.frame;
  }
  function draw() {
    const pill = (t) => `<div class="tab ${t === active ? 'on' : ''} ${t.warning ? 'warn' : ''}" data-key="${esc(t.key)}" title="${esc(t.title)}">${t.status && !t.warning ? `<span class="pr-icon st-${esc(t.status)}" aria-hidden="true"></span>` : ''}<span class="label">${t.warning ? 'Unsent text · close again to discard' : esc(t.title)}</span>${t.badge ? `<span class="badge" title="${t.badge} unresolved">${t.badge}</span>` : ''}<button class="close" data-close="${esc(t.key)}" aria-label="Close tab">×</button></div>`;
    bar.innerHTML = `<div class="tab home ${active === home ? 'on' : ''}" data-key="home" title="Pull requests">${HOME_ICON}</div>` + tabs.map(pill).join('');
    document.title = active.title;
  }
  function activate(tab) {
    active = tab;
    const frame = frameOf(tab);
    for (const f of panes.children) f.classList.toggle('on', f === frame);
    draw(); save(); drawUpdate();
    if (frame.contentWindow) { frame.contentWindow.postMessage({ type: 'shown' }, location.origin); frame.contentWindow.focus(); }
  }
  function open(url) {
    const key = keyOf(url);
    if (key === '' || key === '/home') return activate(home);
    let tab = tabs.find(t => t.key === key);
    if (!tab) { tab = { key, url, title: '…', status: '', badge: 0, frame: null }; tabs.push(tab); }
    activate(tab);
  }
  function close(tab) {
    const i = tabs.indexOf(tab);
    if (i < 0) return;
    tabs.splice(i, 1);
    if (tab.frame) tab.frame.remove();
    if (active === tab) activate(tabs[i] || tabs[i - 1] || home); else { draw(); save(); drawUpdate(); }
  }
  // A page with unsent text keeps its tab open on the first close and shows a warning in the tab instead. A second
  // close within a few seconds discards the text.
  function closeAsked(tab) {
    if (!tab || tab === home) return;
    if (tab.draft && !tab.warning) {
      tab.warning = setTimeout(() => { tab.warning = 0; draw(); }, 4000);
      draw();
      return;
    }
    clearTimeout(tab.warning);
    close(tab);
  }
  const byKey = (key) => key === 'home' ? home : tabs.find(t => t.key === key);
  // Cmd+Option+Left/Right steps through the home tab and the open tabs in strip order, wrapping at the ends.
  // Pages forward the chord when focus is inside them; this handler takes it when focus is on the strip.
  function step(dir) {
    const all = [home, ...tabs];
    activate(all[(all.indexOf(active) + dir + all.length) % all.length]);
  }
  // Cmd+Plus, Cmd+Minus and Cmd+0 zoom the pages in the desktop app, whose webview has no zoom of its own; a browser
  // zooms by itself. The zoom survives a relaunch. The tab strip zooms back out by the same factor, so it keeps its
  // size and its place around the traffic lights.
  const ZOOMS = [0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];
  const webview = APP && window.__TAURI__ && window.__TAURI__.webview;
  let zoom = 1;
  function setZoom(z) {
    if (!webview) return;
    zoom = z;
    bar.style.zoom = 1 / z;
    webview.getCurrentWebview().setZoom(z).catch(() => {});
    try { localStorage.setItem('dr:zoom', String(z)); } catch {}
  }
  function zoomStep(dir) {
    const next = dir === 0 ? 1 : dir > 0 ? ZOOMS.find(z => z > zoom + 1e-6) : ZOOMS.filter(z => z < zoom - 1e-6).pop();
    if (next) setZoom(next);
  }
  const zoomDir = (e) => !e.metaKey || e.altKey || e.ctrlKey ? null : e.key === '=' || e.key === '+' ? 1 : e.key === '-' || e.key === '_' ? -1 : e.key === '0' ? 0 : null;
  window.addEventListener('keydown', (e) => {
    // An Escape left alone would make the macOS window leave full screen.
    if (e.key === 'Escape') { e.preventDefault(); return; }
    if (e.metaKey && e.altKey && !e.ctrlKey && !e.shiftKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
      e.preventDefault();
      step(e.key === 'ArrowLeft' ? -1 : 1);
      return;
    }
    const dir = zoomDir(e);
    if (dir !== null && webview) { e.preventDefault(); zoomStep(dir); }
  });

  // The desktop app's Cmd+W menu item sends 'close-tab'.
  window.addEventListener('close-tab', () => closeAsked(active));

  bar.addEventListener('click', (e) => {
    const c = e.target.closest('[data-close]');
    if (c) { closeAsked(byKey(c.dataset.close)); return; }
    const t = e.target.closest('[data-key]');
    if (t) activate(byKey(t.dataset.key));
  });
  bar.addEventListener('auxclick', (e) => {
    const t = e.button === 1 && e.target.closest('.tab:not(.home)');
    if (t) closeAsked(byKey(t.dataset.key));
  });
  window.addEventListener('message', (e) => {
    if (e.origin !== location.origin || !e.data) return;
    const m = e.data;
    if (m.type === 'open') { open(m.url); return; }
    if (m.type === 'step') { step(m.dir === -1 ? -1 : 1); return; }
    if (m.type === 'zoom') { if ([-1, 0, 1].includes(m.dir)) zoomStep(m.dir); return; }
    if (m.type === 'external') { if (APP) location.href = m.url; else window.open(m.url, '_blank', 'noopener'); return; }
    const tab = tabs.find(t => t.frame && t.frame.contentWindow === e.source);
    if (!tab) return;
    if (m.type === 'badge') { tab.badge = m.n; draw(); save(); }
    if (m.type === 'status') { tab.status = m.status; draw(); save(); }
    if (m.type === 'draft') { tab.draft = !!m.on; drawUpdate(); }
    if (m.type === 'page') {
      tab.url = m.url; tab.title = m.title; tab.status = m.status || ''; tab.draft = false;
      const key = keyOf(m.url), twin = tabs.find(t => t !== tab && t.key === key);
      if (twin) { close(tab); activate(twin); return; }
      tab.key = key; draw(); save(); drawUpdate();
    }
  });

  // In the desktop app, src-tauri/src/update.rs reads the version the Homebrew cask names, once a day, and this
  // bar under the panes offers it. Later hides that version until the next one. Update and Relaunch quits the
  // app, lets Homebrew install the new version and reopens it; a page's unsent text would go with the app, so
  // the button waits while a tab holds a draft. A copy that did not come from Homebrew gets a link to the release.
  const invoke = APP && window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke;
  const updateBar = document.getElementById('update');
  let update = null, updating = false, updateError = '';
  const seenVersion = () => { try { return localStorage.getItem('dr:updateSeen'); } catch { return null; } };
  function drawUpdate() {
    const show = !!(update && update.newer && update.latest && seenVersion() !== update.latest);
    updateBar.hidden = !show;
    if (!show) return;
    const draft = tabs.some(t => t.draft);
    const sub = updateBar.querySelector('.sub'), act = updateBar.querySelector('.act');
    updateBar.querySelector('.title').textContent = `differ ${update.latest} is available`;
    sub.textContent = updateError ? updateError
      : updating ? 'Homebrew is installing it. differ will quit and reopen.'
      : !update.managed ? `You have ${update.current}. This copy did not come from Homebrew.`
      : draft ? 'A tab has unsent text. Send or discard it first.'
      : `You have ${update.current}. Homebrew installs it and reopens differ.`;
    sub.classList.toggle('err', !!updateError);
    act.textContent = !update.managed ? 'Download' : updating ? 'Updating…' : 'Update and Relaunch';
    act.disabled = updating || (update.managed && draft);
    updateBar.querySelector('.later').disabled = updating;
  }
  function checkUpdate() {
    if (!invoke) return;
    invoke('update_check', { force: false }).then((status) => { update = status; drawUpdate(); }).catch(() => {});
  }
  updateBar.addEventListener('click', (e) => {
    if (!update) return;
    if (e.target.closest('.later')) {
      try { localStorage.setItem('dr:updateSeen', update.latest); } catch {}
      drawUpdate();
      return;
    }
    if (!e.target.closest('.act')) return;
    if (!update.managed) { location.href = update.url; return; }
    updating = true; updateError = ''; drawUpdate();
    invoke('update_run').catch((err) => { updating = false; updateError = `Update failed: ${err}`; drawUpdate(); });
  });
  checkUpdate();
  setInterval(checkUpdate, 60 * 60 * 1000);

  const savedZoom = (() => { try { return parseFloat(localStorage.getItem('dr:zoom')); } catch { return NaN; } })();
  if (savedZoom > 0 && savedZoom !== 1) setZoom(savedZoom);
  const saved = lsGet();
  tabs = (saved.tabs || []).filter(t => t && t.url).map(t => ({ key: keyOf(t.url), url: t.url, title: t.title || '…', status: t.status || '', badge: t.badge || 0, frame: null }));
  activate(byKey(saved.active) || home);
})();
