// Event handling and start-up.

// Render after a file's section opens or closes. When the file's header is on screen, the header stays where it
// is: a collapse removes the rows under the viewport, so keeping them as the anchor would carry the pane deep into
// the next files.
function renderKeepingHeader(f) {
  const head = (el) => el && el.querySelector(':scope > .fhead');
  const pane = main.getBoundingClientRect(), old = head(sectionEl(f));
  const top = old && old.getBoundingClientRect().top;
  const onScreen = top != null && top >= pane.top - 1 && top < pane.bottom;
  render();
  const now = onScreen && head(sectionEl(f));
  if (!now) return;
  const keep = () => { main.scrollTop += now.getBoundingClientRect().top - top; };
  keep(); syncViewport(); keep(); syncViewport(); keep();
}
// Change the file filter, keeping the reader on the file at the top of the pane, or on the nearest shown file before
// it when the filter hides it. Filter changes in a row keep returning to the file from before the first one; any
// other click, key, wheel or touch ends the run.
let filterReturn = null;
function setFilter(filter) {
  if (filter === state.filter) return;
  const back = filterReturn || state.view;
  state.filter = filter;
  render();
  if (state.view !== back) scrollToFile(shownAtOrBefore(back));
  filterReturn = back;
}
for (const type of ['pointerdown', 'keydown', 'wheel', 'touchmove']) {
  document.addEventListener(type, (e) => { if (!(e.target.closest && e.target.closest('[data-filter]'))) filterReturn = null; }, true);
}
function toggleReviewed(f) { setReviewed(f, !isReviewed(f)); state.openOverride.delete(f.path); renderKeepingHeader(f); }
function toggleSection(f) { state.openOverride.set(f.path, !sectionOpen(f)); renderKeepingHeader(f); }

document.addEventListener('click', (e) => {
  const bug = e.target.closest(BUG_ATTRS);
  if (bug) { e.preventDefault(); bugClick(bug); return; }
  if (e.target.closest('[data-docs-open]')) { openDocs(); return; }
  if (e.target.closest('#docs')) return;
  if (e.target.closest('textarea, .form, .thread .cbody')) { const b = e.target.closest(COMMENT_ATTRS); if (b) commentClick(b, e); return; }
  // The number cell that shows a code row's line is its comment button.
  const cell = API && e.target.closest('table.diff td.n.key');
  const at = cell && rowAt(cell.parentElement.id);
  if (at) { e.preventDefault(); openCompose(at.file.path, at.side, at.line, e.shiftKey); return; }
  const t = e.target.closest('[data-go],[data-path],[data-toggle],[data-filter],[data-noiseonly],[data-generated],[data-folder],[data-review],[data-fold],' + COMMENT_ATTRS);
  if (!t) return;
  if (t.matches(COMMENT_ATTRS)) { e.stopPropagation(); commentClick(t, e); return; }
  const d = t.dataset;
  if (d.review !== undefined) { e.preventDefault(); e.stopPropagation(); toggleReviewed(BY_PATH[d.review]); return; }
  if (d.toggle !== undefined && !e.target.closest('button, a')) { toggleSection(BY_PATH[d.toggle]); return; }
  if (d.go) { scrollToFile('overview'); return; }
  if (d.path) {
    e.preventDefault();
    if (t.closest('#side .tree')) { state.cursor = 'f:' + d.path; document.querySelector('#side .list').focus({ preventScroll: true }); }
    const f = BY_PATH[d.path];
    const key = f && d.line ? rowKey(f, d.side === 'old' ? 'LEFT' : 'RIGHT', +d.line) : null;
    if (!key && t.closest('#side') && goFirstHit(d.path)) return;
    go(d.path, key);
    return;
  }
  if (d.filter) { e.preventDefault(); setFilter(d.filter); return; }
  if (d.generated !== undefined) { e.preventDefault(); state.showGenerated = d.generated === '1'; lsSet('dr:generated', state.showGenerated ? '1' : '0'); render(); return; }
  if (d.noiseonly !== undefined) { e.preventDefault(); state.showNoiseOnly = d.noiseonly === '1'; lsSet('dr:noise-only', state.showNoiseOnly ? '1' : '0'); render(); return; }
  if (d.folder !== undefined) { state.cursor = 'd:' + d.folder; document.querySelector('#side .list').focus({ preventScroll: true }); toggleFolder(d.folder); return; }
  if (d.fold) { toggleFold(d.fold); render(); return; }
});

document.addEventListener('input', (e) => {
  const box = e.target, d = box.dataset;
  if (d.composeText !== undefined && state.compose) { state.compose.text = box.value; return; }
  if (d.replyText !== undefined) { state.replyOpen.set(d.replyText, box.value); return; }
  if (d.reviewText !== undefined) { state.reviewText = box.value; const b = document.querySelector('[data-submit="REQUEST_CHANGES"]'); if (b) updateRequestChanges(b); return; }
  if (d.convText !== undefined) { state.convText = box.value; return; }
  if (d.bugText !== undefined) { state.bug.text = box.value; return; }
  if (!box.classList.contains('search')) return;
  state.q = box.value.trim(); state.hit = null;
  render();
});
document.addEventListener('input', () => { if (SHELL) SHELL.draft(hasDraft()); });

// Cmd+F searches for the first line of the selected text, as an editor's find does.
function openSearch() {
  const text = String(window.getSelection()).split('\n').map(l => l.trim()).find(Boolean);
  const box = document.querySelector('#side .search');
  if (text && box) { box.value = text; box.dispatchEvent(new Event('input', { bubbles: true })); }
  focusBox('#side .search');
}
function closeSearch() {
  const box = document.querySelector('#side .search');
  if (box && box.value) { box.value = ''; box.dispatchEvent(new Event('input', { bubbles: true })); }
}

// The hit after (or before) the one Enter last went to. When that one is gone, as when its section closed, the
// first hit of its file or of the files after it (the last hit of its file or of the files before it).
function nextHit(all, dir) {
  const at = hitPosition(all);
  if (at >= 0) return all[(at + dir + all.length) % all.length];
  const order = new Map(visibleFiles().map((f, i) => [f.path, i]));
  const from = state.hit && order.has(state.hit.path) ? order.get(state.hit.path) : -1;
  if (dir > 0) return all.find(h => order.get(h.path) >= from) || all[0];
  return [...all].reverse().find(h => from < 0 || order.get(h.path) <= from) || all[all.length - 1];
}
function stepHit(dir) {
  const all = allHits();
  if (all.length) goHit(nextHit(all, dir));
}
// Go to a search hit and make it the one Enter goes on from.
function goHit(hit) {
  state.hit = hit;
  go(hit.path, hit.key, true);
  popHit();
  updateSearchMeta();
}
// While searching, a file shown from the tree opens at its first hit; false when the file has none.
function goFirstHit(path) {
  const key = state.q ? firstHitKey(BY_PATH[path]) : null;
  if (key) goHit({ path, key });
  return !!key;
}

document.addEventListener('keydown', (e) => {
  if (e.target.closest && e.target.closest('#docs')) return;
  if (e.metaKey && !e.altKey && !e.ctrlKey && !e.shiftKey && e.key === 'f') { e.preventDefault(); openSearch(); return; }
  if (e.target.tagName === 'TEXTAREA') { textareaKey(e); return; }
  if (e.key === 'Escape') { closeSearch(); if (document.activeElement) document.activeElement.blur(); return; }
  if (e.target.classList && e.target.classList.contains('list') && !e.metaKey && !e.ctrlKey && !e.altKey) {
    if (treeKey(e)) { e.preventDefault(); return; }
  }
  if (e.target.classList && e.target.classList.contains('search')) {
    if (e.key === 'Enter') { e.preventDefault(); stepHit(e.shiftKey ? -1 : 1); focusBox('.search'); }
    return;
  }
  if (e.target.tagName === 'INPUT' || e.metaKey || e.ctrlKey) return;
  if (e.key === 'o') scrollToFile('overview');
});

// #path or #path:line, the line being one of the file as shown
function hashTarget() {
  const h = decodeURIComponent(location.hash.slice(1));
  const m = h.match(/^(.*?)(?::(\d+))?$/);
  const f = h && BY_PATH[m[1]];
  return f ? { path: m[1], key: m[2] ? rowKey(f, 'RIGHT', +m[2]) : null } : null;
}
window.addEventListener('hashchange', () => {
  const t = hashTarget();
  if (!t) { if (!location.hash.slice(1) && state.view !== 'overview') scrollToFile('overview'); return; }
  if (state.view !== t.path || t.key) go(t.path, t.key);
});

(function sideResize() {
  const w = lsGet('dr:side-width'); if (w) document.documentElement.style.setProperty('--side', w + 'px');
  let dragging = null;
  document.addEventListener('mousedown', (e) => { const g = e.target.closest('#side .grip'); if (!g) return; dragging = g; g.classList.add('dragging'); e.preventDefault(); });
  document.addEventListener('mousemove', (e) => { if (!dragging) return; const w = Math.max(260, Math.min(e.clientX, window.innerWidth * 0.7)); document.documentElement.style.setProperty('--side', w + 'px'); });
  document.addEventListener('mouseup', () => { if (!dragging) return; dragging.classList.remove('dragging'); dragging = null; lsSet('dr:side-width', parseInt(getComputedStyle(document.getElementById('side')).width)); });
})();

// Pressing a row's comment button and dragging to another row of the same file and side opens a comment on the
// lines in between. The rows light up as the pointer moves; a release on the starting row leaves the click to open
// a one-line comment.
(function commentDrag() {
  let drag = null;
  const rowUnder = (el) => { const tr = el.closest && el.closest('table.diff tr[id]'); return tr && rowAt(tr.id); };
  document.addEventListener('mousedown', (e) => {
    if (!API || e.button !== 0 || e.shiftKey) return;
    const cell = e.target.closest('table.diff td.n.key');
    const at = cell && rowAt(cell.parentElement.id);
    if (!at) return;
    e.preventDefault();
    drag = { start: at, end: at };
    document.body.classList.add('comment-drag');
  });
  // A release outside the window never reaches the page, so a move without the button down ends the drag.
  document.addEventListener('mousemove', (e) => {
    if (!drag) return;
    if (!(e.buttons & 1)) { finish(); return; }
    const at = rowUnder(e.target);
    if (!at || at.file !== drag.start.file || at.side !== drag.start.side || at.line === drag.end.line) return;
    drag.end = at;
    const lo = Math.min(drag.start.line, at.line), hi = Math.max(drag.start.line, at.line);
    const trs = [...sectionEl(at.file).querySelectorAll('table.diff tr[id]')];
    const sel = trs.map(tr => { const r = rowAt(tr.id); return !!r && r.side === at.side && r.line >= lo && r.line <= hi; });
    const between = markBetween(trs, sel);
    trs.forEach((tr, i) => { tr.classList.toggle('sel', sel[i]); tr.classList.toggle('between', between[i]); });
  });
  document.addEventListener('mouseup', () => { if (drag) finish(); });
  function finish() {
    const { start, end } = drag;
    drag = null;
    document.body.classList.remove('comment-drag');
    if (start.line === end.line) return;
    openCompose(start.file.path, start.side, start.line, false);
    openCompose(start.file.path, start.side, end.line, true);
  }
})();

(function init() {
  state.showNoiseOnly = lsGet('dr:noise-only') === '1';
  state.showGenerated = lsGet('dr:generated') === '1';
  if (!FILES.some(kindVisible)) state.filter = 'all';
  document.body.classList.toggle('commentable', !!API);
  render();
  // A hash without a line puts the reader back on its file and leaves a reviewed file closed.
  const t = hashTarget();
  if (t && t.key == null) { if (unhide(BY_PATH[t.path])) render(); scrollToFile(t.path); }
  else if (t) go(t.path, t.key);
  loadComments();
  // A reload rebuilds the forms, so it waits while a comment box has focus.
  const refresh = () => {
    const typing = document.activeElement && document.activeElement.tagName === 'TEXTAREA';
    if (C.loaded && !state.busy && !typing && Date.now() - C.loadedAt > 30000) loadComments();
  };
  window.addEventListener('focus', refresh);
  document.addEventListener('tab-shown', refresh);
})();
