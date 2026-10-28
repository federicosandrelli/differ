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
    const pill = (t) => `<div class="tab ${t === active ? 'on' : ''} ${t.warning ? 'warn' : ''}" data-key="${esc(t.key)}" title="${esc(t.title)}">${t.status && !t.warning ? `<span class="pr-icon st-${esc(t.status)}" aria-hidden="true"></span>` : ''}<span class="label">${t.warning ? 'Unsent text · close again to discard' : esc(t.title)}</span>${t.badge ? `<span class="count" title="${t.badge} unresolved">${t.badge}</span>` : ''}<button class="close" data-close="${esc(t.key)}" aria-label="Close tab">×</button></div>`;
    bar.innerHTML = `<div class="tab home ${active === home ? 'on' : ''}" data-key="home" title="Pull requests">${HOME_ICON}</div>` + tabs.map(pill).join('');
    document.title = active.title;
    drawUpdate();
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
    if (!tab) { tab = { key, url, title: '…', status: '', badge: 0, frame: null }; tabs.push(tab); }
    activate(tab);
  }
  function close(tab) {
    const i = tabs.indexOf(tab);
    if (i < 0) return;
    tabs.splice(i, 1);
    if (tab.frame) tab.frame.remove();
    if (active === tab) activate(tabs[i] || tabs[i - 1] || home); else { draw(); save(); }
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
    // An Escape left alone would make the macOS window leave full screen. Cancelling it also stops the open
    // dialog from closing on its own, so the shell closes it.
    if (e.key === 'Escape') { e.preventDefault(); settings.close(); return; }
    if (e.metaKey && !e.altKey && !e.ctrlKey && !e.shiftKey && e.key === ',') { e.preventDefault(); openSettings(); return; }
    if (e.metaKey && e.altKey && !e.ctrlKey && !e.shiftKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
      e.preventDefault();
      step(e.key === 'ArrowLeft' ? -1 : 1);
      return;
    }
    const dir = zoomDir(e);
    if (dir !== null && webview) { e.preventDefault(); zoomStep(dir); }
  });

  // The desktop app's Cmd+W menu item sends 'close-tab', and its "Settings…" menu item 'open-settings'.
  window.addEventListener('close-tab', () => closeAsked(active));
  window.addEventListener('open-settings', openSettings);

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
    if (m.type === 'settings') { openSettings(); return; }
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
      tab.key = key; draw(); save();
    }
  });

  // The settings: a modal in the middle of the window, opened by the gear in the bottom left, by Cmd+, (a page
  // forwards the chord while focus is inside it) and by the desktop app's "Settings…" menu item. The theme picker is
  // the shell's own, and so is the Update section, since only the shell knows the app's version; the rest of the
  // body comes from /settings on each open, so the editors, the cache and the commit are current.
  const settings = document.getElementById('settings');
  function openSettings() {
    // A change made while the body loads is newer than the list the body holds, so the fields keep that change.
    const before = followed.newest;
    const loading = settings.querySelector('.state .loading');
    if (loading) { loading.textContent = 'Loading…'; loading.classList.remove('err'); }
    followed.saving.then(() => fetch('/settings')).then(r => r.ok ? r.text() : Promise.reject()).then((body) => {
      settings.querySelector('.state').innerHTML = body;
      const saved = JSON.parse(settings.querySelector('.repos').dataset.repos);
      if (followed.newest === before) followed.saved = saved;
      followed.draw(followed.newest === before ? saved : followed.repos);
    }).catch(() => {
      if (loading) { loading.textContent = 'Could not load the settings.'; loading.classList.add('err'); }
    });
    if (!settings.open) settings.showModal();
    settings.querySelector('.body').scrollTop = 0;
  }
  // Every change goes into `repos` and then whole to the server, one request after the other. Only the newest
  // answer draws the fields, since an edit and a ✕ can fire together (the blur, then the click). A refused request
  // puts `repos` back to the list the server last saved, so the fields never show a list the server does not hold.
  const followed = {
    repos: [],
    saved: [],
    newest: 0,
    refused: 0,
    saving: Promise.resolve(),
    fields: () => [...settings.querySelectorAll('.repos input')],
    draw(repos) {
      this.repos = [...repos];
      const fields = this.fields();
      if (fields.length === repos.length) fields.forEach((f, i) => { if (f !== document.activeElement) f.value = repos[i]; });
      else settings.querySelector('.repos').innerHTML = repos.map(r => `<li><input value="${esc(r)}" aria-label="Followed repository"><button class="ghost" data-remove aria-label="Stop following" title="Stop following">✕</button></li>`).join('');
    },
    save(then) {
      const request = ++this.newest;
      const error = () => settings.querySelector('[data-error]');
      this.saving = this.saving.then(() => {
        const repos = this.repos.map(r => r.trim()).filter(Boolean);
        return fetch('/api/followed', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ repos }) })
          .then(r => r.json().then(d => r.ok ? d : Promise.reject(new Error(d.error || `HTTP ${r.status}`))))
          .then((d) => {
            this.saved = d.repos;
            if (request === this.newest) this.draw(d.repos);
            if (request > this.refused) error().hidden = true;
            if (then) then();
          })
          .catch((err) => {
            this.refused = request;
            this.repos = [...this.saved];
            if (request === this.newest) this.draw(this.saved);
            error().textContent = err.message;
            error().hidden = false;
          });
      });
    },
  };
  settings.addEventListener('submit', (e) => {
    const form = e.target.closest('.add');
    if (!form) return;
    e.preventDefault();
    const field = form.querySelector('input');
    const text = field.value;
    if (!text.trim()) return;
    followed.repos.push(text);
    // The reader may have typed the next name in the field while this one was on its way.
    followed.save(() => { if (field.value === text) { field.value = ''; field.focus(); } });
  });
  settings.addEventListener('change', (e) => {
    const field = e.target.closest('.repos input');
    if (field) { followed.repos[followed.fields().indexOf(field)] = field.value; followed.save(); }
  });
  // Enter in a field commits the edit, as leaving the field does.
  settings.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target.closest('.repos input')) e.target.blur(); });
  settings.addEventListener('click', (e) => {
    const remove = e.target.closest('[data-remove]');
    if (!remove) return;
    const row = remove.closest('li');
    followed.repos.splice([...row.parentElement.children].indexOf(row), 1);
    row.remove();
    followed.save();
  });
  document.getElementById('gear').addEventListener('click', openSettings);
  // A click on the dialog itself, outside its box, is a click on the backdrop.
  settings.addEventListener('click', (e) => { if (e.target === settings || e.target.closest('.close')) settings.close(); });
  // The page in front hears that it shows again, so the start page asks for its sections, which the followed
  // repositories may have changed.
  settings.addEventListener('close', () => {
    const frame = active.frame;
    if (frame && frame.contentWindow) { frame.contentWindow.postMessage({ type: 'shown' }, location.origin); frame.contentWindow.focus(); }
  });

  // The bar that offers a newer version in the desktop app, and the Update section of the settings, which also
  // checks on request. Update and Relaunch quits the app, and a page's unsent text would go with it, so the button
  // waits while a tab holds a draft.
  const invoke = APP && window.__TAURI__ && window.__TAURI__.core && window.__TAURI__.core.invoke;
  const updateBar = document.getElementById('update'), updateSection = settings.querySelector('.update-section');
  // Only the answer to the newest check counts, so an hourly check that ends late never undoes a forced one.
  let update = null, checks = 0, answered = 0, updating = false, updateError = '';
  const seenVersion = () => { try { return localStorage.getItem('dr:updateSeen'); } catch { return null; } };
  // What the reader can do about the newer version: a note on it and the button that installs it.
  function offer() {
    if (updating) return { note: 'Homebrew is installing it. differ will quit and reopen.', label: 'Updating…', disabled: true };
    if (!update.managed) return { note: 'This copy did not come from Homebrew.', label: 'Download' };
    if (tabs.some(t => t.draft)) return { note: 'A tab has unsent text. Send or discard it first.', label: 'Update and Relaunch', disabled: true };
    if (updateError) return { note: updateError, label: 'Update and Relaunch', error: true };
    return { note: 'Homebrew will install and relaunch differ.', label: 'Update and Relaunch' };
  }
  function drawUpdate() {
    drawUpdateSection();
    updateBar.hidden = !(update && update.newer && seenVersion() !== update.latest);
    if (updateBar.hidden) return;
    const o = offer();
    updateBar.querySelector('.title').textContent = `differ ${update.latest} is available`;
    const subEl = updateBar.querySelector('.sub'), act = updateBar.querySelector('.act');
    subEl.textContent = o.disabled || o.error ? o.note : `You have ${update.current}. ${o.note}`;
    subEl.classList.toggle('err', !!o.error);
    act.textContent = o.label;
    act.disabled = !!o.disabled;
    updateBar.querySelector('.later').disabled = updating;
  }
  function drawUpdateSection() {
    updateSection.hidden = !invoke;
    if (!invoke) return;
    const current = updateSection.querySelector('.current'), sub = updateSection.querySelector('.sub'), act = updateSection.querySelector('.act');
    current.textContent = update ? `differ ${update.current}${update.managed ? ' from Homebrew' : ''}` : 'differ';
    let text = 'This is the latest version.', label = 'Check for Updates', disabled = false, error = false;
    if (update && update.newer) {
      const o = offer();
      text = `${update.latest} is available. ${o.note}`;
      label = o.label; disabled = !!o.disabled; error = !!o.error;
    } else if (answered < checks) { text = 'Checking for updates…'; label = 'Checking…'; disabled = true; }
    else if (!update || update.failed) { text = 'Could not check for updates.'; error = true; }
    sub.textContent = text;
    sub.classList.toggle('err', error);
    act.textContent = label;
    act.disabled = disabled;
    act.classList.toggle('primary', !!(update && update.newer));
  }
  function checkUpdate(force = false) {
    if (!invoke) return;
    const request = ++checks;
    drawUpdate();
    invoke('update_check', { force })
      .then((status) => { if (request > answered) { answered = request; update = status; } })
      .catch(() => { answered = Math.max(answered, request); })
      .finally(drawUpdate);
  }
  function runUpdate() {
    if (!update.managed) { location.href = update.url; return; }
    updating = true; updateError = ''; drawUpdate();
    invoke('update_run').catch((err) => { updating = false; updateError = `Update failed: ${err}`; drawUpdate(); });
  }
  updateBar.addEventListener('click', (e) => {
    if (!update) return;
    if (e.target.closest('.later')) {
      try { localStorage.setItem('dr:updateSeen', update.latest); } catch {}
      drawUpdate();
      return;
    }
    if (e.target.closest('.act')) runUpdate();
  });
  updateSection.querySelector('.act').addEventListener('click', () => { if (update && update.newer) runUpdate(); else checkUpdate(true); });
  checkUpdate();
  setInterval(() => checkUpdate(), 60 * 60 * 1000);

  const savedZoom = (() => { try { return parseFloat(localStorage.getItem('dr:zoom')); } catch { return NaN; } })();
  if (savedZoom > 0 && savedZoom !== 1) setZoom(savedZoom);
  const saved = lsGet();
  tabs = (saved.tabs || []).filter(t => t && t.url).map(t => ({ key: keyOf(t.url), url: t.url, title: t.title || '…', status: t.status || '', badge: t.badge || 0, frame: null }));
  activate(byKey(saved.active) || home);
})();
