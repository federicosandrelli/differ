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
function toggleReviewed(f) { setReviewed(f, !isReviewed(f)); state.openOverride.delete(f.path); renderKeepingHeader(f); }
function toggleSection(f) { state.openOverride.set(f.path, !sectionOpen(f)); renderKeepingHeader(f); }

document.addEventListener('click', (e) => {
  const bug = e.target.closest(BUG_ATTRS);
  if (bug) { e.preventDefault(); bugClick(bug); return; }
  if (e.target.closest('textarea, .form, .thread .cbody')) { const b = e.target.closest(COMMENT_ATTRS); if (b) commentClick(b, e); return; }
  // The number cell of a code row is its comment button.
  const cell = API && e.target.closest('table.diff td.n:first-child');
  const at = cell && rowAt(cell.parentElement.id);
  if (at) { e.preventDefault(); openCompose(at.file.path, at.side, at.line, e.shiftKey); return; }
  const t = e.target.closest('[data-go],[data-path],[data-toggle],[data-filter],[data-tree],[data-noiseonly],[data-generated],[data-folder],[data-show],[data-review],[data-fold],' + COMMENT_ATTRS);
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
    let key = f && d.line ? rowKey(f, d.side === 'old' ? 'LEFT' : 'RIGHT', +d.line) : null;
    if (!key && state.q && t.closest('#side')) key = firstHitKey(f);
    go(d.path, key);
    return;
  }
  if (d.filter) { state.filter = d.filter; render(); return; }
  if (d.generated !== undefined) { e.preventDefault(); state.showGenerated = d.generated === '1'; lsSet('dr:generated', state.showGenerated ? '1' : '0'); render(); return; }
  if (d.noiseonly !== undefined) { e.preventDefault(); state.showNoiseOnly = d.noiseonly === '1'; lsSet('dr:noise-only', state.showNoiseOnly ? '1' : '0'); render(); return; }
  if (d.tree) { state.closed = new Set(d.tree === 'collapse' ? allFolders(buildTree(visibleFiles()), []) : []); renderSide(); return; }
  if (d.folder !== undefined) { state.cursor = 'd:' + d.folder; document.querySelector('#side .list').focus({ preventScroll: true }); toggleFolder(d.folder); return; }
  if (d.show) { state.show[d.show] = !state.show[d.show]; render(); return; }
  if (d.fold) { toggleFold(d.fold); render(); return; }
});

document.addEventListener('input', (e) => {
  const box = e.target, d = box.dataset;
  if (d.composeText !== undefined && state.compose) { state.compose.text = box.value; return; }
  if (d.replyText !== undefined) { state.replyOpen.set(d.replyText, box.value); return; }
  if (d.reviewText !== undefined) {
    state.reviewText = box.value;
    const b = document.querySelector('[data-submit="REQUEST_CHANGES"]');
    if (b) b.disabled = state.busy || !box.value.trim();
    return;
  }
  if (d.convText !== undefined) { state.convText = box.value; return; }
  if (d.bugText !== undefined) { state.bug.text = box.value; return; }
  if (!box.classList.contains('search')) return;
  state.q = box.value.trim(); state.hitIndex = -1;
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

function stepHit(dir) {
  const all = allHits();
  if (!all.length) return;
  state.hitIndex = ((state.hitIndex ?? -1) + dir + all.length) % all.length;
  const hit = all[state.hitIndex];
  go(hit.path, hit.key);
  focusBox('.search');
  updateSearchMeta();
}

document.addEventListener('keydown', (e) => {
  if (e.metaKey && !e.altKey && !e.ctrlKey && !e.shiftKey && e.key === 'f') { e.preventDefault(); openSearch(); return; }
  if (e.target.tagName === 'TEXTAREA') { textareaKey(e); return; }
  if (e.target.classList && e.target.classList.contains('list') && !e.metaKey && !e.ctrlKey && !e.altKey) {
    if (treeKey(e)) { e.preventDefault(); return; }
  }
  if (e.target.classList && e.target.classList.contains('search')) {
    if (e.key === 'Escape') { e.target.value = ''; e.target.dispatchEvent(new Event('input', { bubbles: true })); e.target.blur(); }
    if (e.key === 'Enter') { e.preventDefault(); stepHit(e.shiftKey ? -1 : 1); }
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

(function init() {
  state.showNoiseOnly = lsGet('dr:noise-only') === '1';
  state.showGenerated = lsGet('dr:generated') === '1';
  if (!FILES.some(kindVisible)) state.filter = 'all';
  document.body.classList.toggle('commentable', !!API);
  render();
  const t = hashTarget();
  if (t) go(t.path, t.key);
  loadComments();
  // A reload rebuilds the forms, so it waits while a comment box has focus.
  const refresh = () => {
    const typing = document.activeElement && document.activeElement.tagName === 'TEXTAREA';
    if (C.loaded && !state.busy && !typing && Date.now() - C.loadedAt > 30000) loadComments();
  };
  window.addEventListener('focus', refresh);
  document.addEventListener('tab-shown', refresh);
})();
