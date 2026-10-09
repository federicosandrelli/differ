// Event handling and start-up.

function toggleReviewed(f) { setReviewed(f, !isReviewed(f)); state.openOverride.delete(f.path); render(); }
function toggleSection(f) { state.openOverride.set(f.path, !sectionOpen(f)); render(); }

document.addEventListener('click', (e) => {
  if (e.target.closest('textarea, .form, .thread .cbody')) { const b = e.target.closest(COMMENT_ATTRS); if (b) commentClick(b, e); return; }
  // The number cell of a code row is its comment button.
  const cell = API && e.target.closest('table.diff td.n:first-child');
  const at = cell && rowAt(cell.parentElement.id);
  if (at) { e.preventDefault(); openCompose(at.file.path, at.side, at.line, e.shiftKey); return; }
  const t = e.target.closest('[data-go],[data-path],[data-toggle],[data-filter],[data-tree],[data-noiseonly],[data-generated],[data-folder],[data-show],[data-whole],[data-review],[data-fold],[data-runs],[data-run],[data-theme],' + COMMENT_ATTRS);
  if (!t) return;
  if (t.matches(COMMENT_ATTRS)) { e.stopPropagation(); commentClick(t, e); return; }
  const d = t.dataset;
  if (d.review !== undefined) { e.preventDefault(); e.stopPropagation(); toggleReviewed(BY_PATH[d.review]); return; }
  if (d.toggle !== undefined && !e.target.closest('button, a')) { toggleSection(BY_PATH[d.toggle]); return; }
  if (d.go) { scrollToFile('overview'); return; }
  if (d.path) {
    e.preventDefault();
    if (t.closest('#side .tree')) { state.cursor = 'f:' + d.path; document.querySelector('#side .list').focus({ preventScroll: true }); }
    let line = d.line ? +d.line : null;
    if (!line && state.q && t.closest('#side')) line = firstHitLine(BY_PATH[d.path]);
    go(d.path, line);
    return;
  }
  if (d.filter) { state.filter = d.filter; render(); return; }
  if (d.theme) { state.theme = d.theme; lsSet('dr:theme', state.theme); applyTheme(); renderSide(); return; }
  if (d.generated !== undefined) { e.preventDefault(); state.showGenerated = d.generated === '1'; lsSet('dr:generated', state.showGenerated ? '1' : '0'); render(); return; }
  if (d.noiseonly !== undefined) { e.preventDefault(); state.showNoiseOnly = d.noiseonly === '1'; lsSet('dr:noise-only', state.showNoiseOnly ? '1' : '0'); render(); return; }
  if (d.tree) { state.closed = new Set(d.tree === 'collapse' ? allFolders(buildTree(visibleFiles()), []) : []); renderSide(); return; }
  if (d.folder !== undefined) { state.cursor = 'd:' + d.folder; document.querySelector('#side .list').focus({ preventScroll: true }); toggleFolder(d.folder); return; }
  if (d.show) { state.show[d.show] = !state.show[d.show]; render(); return; }
  if (d.whole !== undefined) { state.whole = d.whole === '1'; render(); return; }
  if (d.fold) { state.unfolded.add(d.fold); render(); return; }
  if (d.runs) { for (const k of d.runs.split('|')) state.expanded.add(k); render(); return; }
  if (d.run) { state.expanded.has(d.run) ? state.expanded.delete(d.run) : state.expanded.add(d.run); render(); return; }
});

document.addEventListener('input', (e) => {
  const box = e.target, d = box.dataset;
  if (d.composeText !== undefined && state.compose) { state.compose.text = box.value; return; }
  if (d.replyText !== undefined) { state.replyOpen.set(d.replyText, box.value); return; }
  if (d.reviewText !== undefined) { state.reviewText = box.value; return; }
  if (d.convText !== undefined) { state.convText = box.value; return; }
  if (!box.classList.contains('search')) return;
  state.q = box.value.trim(); state.hitIndex = -1;
  render();
});

function stepHit(dir) {
  const all = allHits();
  if (!all.length) return;
  state.hitIndex = ((state.hitIndex ?? -1) + dir + all.length) % all.length;
  const hit = all[state.hitIndex];
  go(hit.path, hit.line);
  focusBox('.search');
  updateSearchMeta();
}

document.addEventListener('keydown', (e) => {
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
  if (e.key === '/') { e.preventDefault(); focusBox('#side .search'); return; }
  const files = visibleFiles(); const i = files.findIndex(f => f.path === state.view);
  const current = BY_PATH[state.view];
  if (e.key === 'j') { const n = files[i + 1] || files[0]; if (n) scrollToFile(n.path); }
  else if (e.key === 'k') { if (i > 0) scrollToFile(files[i - 1].path); else scrollToFile('overview'); }
  else if (e.key === 'o') scrollToFile('overview');
  else if (e.key === 'w') { state.whole = !state.whole; render(); }
  else if (e.key === 'r' && current) toggleReviewed(current);
  else if (e.key === 'c' && current) toggleSection(current);
});

// #path or #path:line
function hashTarget() {
  const h = decodeURIComponent(location.hash.slice(1));
  const m = h.match(/^(.*?)(?::(\d+))?$/);
  return h && BY_PATH[m[1]] ? { path: m[1], line: m[2] ? +m[2] : null } : null;
}
window.addEventListener('hashchange', () => {
  const t = hashTarget();
  if (!t) { if (!location.hash.slice(1) && state.view !== 'overview') scrollToFile('overview'); return; }
  if (state.view !== t.path || t.line) go(t.path, t.line);
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
  state.theme = THEMES.includes(lsGet('dr:theme')) ? lsGet('dr:theme') : 'dark';
  applyTheme();
  document.body.classList.toggle('commentable', !!API);
  render();
  const t = hashTarget();
  if (t) go(t.path, t.line);
  loadComments();
  window.addEventListener('focus', () => { if (C.loaded && !state.busy && Date.now() - C.loadedAt > 30000) loadComments(); });
})();
