// The sidebar: the PR header, the controls, the search box and the file tree.

function countFiles(node) { let n = node.files.length; for (const c of node.dirs.values()) n += countFiles(c); return n; }
function allFolders(node, acc) { for (const d of node.dirs.values()) { acc.push(d.full); allFolders(d, acc); } return acc; }
// Background gradients paint the guide lines, so a row can span the full sidebar width.
function guides(depth) {
  if (!depth) return '';
  const img = [], pos = [], size = [];
  for (let k = 0; k < depth; k++) { img.push('linear-gradient(var(--line), var(--line))'); pos.push(`${17 + k * 19}px 0`); size.push('1px 100%'); }
  return `background-image:${img.join(',')};background-position:${pos.join(',')};background-size:${size.join(',')}`;
}
// Outline folder and speech-bubble icons drawn inline, so the page needs no icon font.
const FOLDER_CLOSED = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M5 4h4l3 3h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const BUBBLE = '<svg viewBox="0 0 24 24" width="11" height="11" aria-hidden="true"><path d="M20 15a2 2 0 0 1-2 2H8l-4 4V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const FOLDER_OPEN = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M5 19l2.757-7.351A1 1 0 0 1 8.693 11H21a1 1 0 0 1 .986 1.164l-.996 5.211A2 2 0 0 1 19.026 19H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h4l3 3h7a2 2 0 0 1 2 2v2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

function leafHtml(f, depth) {
  const k = changeKind(f);
  const oldDir = f.old_path ? f.old_path.slice(0, f.old_path.lastIndexOf('/') + 1) : '';
  const hitCount = state.q ? hits(f) : 0, threads = threadCount(f);
  return `<div class="row leaf ${state.view === f.path ? 'active' : ''} ${state.cursor === 'f:' + f.path ? 'cursor' : ''} ${isReviewed(f) ? 'reviewed' : ''}" style="--d:${depth};${guides(depth)}" data-path="${esc(f.path)}" title="${esc(f.path)}">
      <span class="ico"></span>
      <span class="pill st-${k} ${f.kind === 'test' ? 'test' : ''}" title="${KIND_LABEL[k]}">${k === 'R' ? `<s>${esc(f.old_path.split('/').pop())}</s>` : ''}<span class="nm">${esc(f.base)}</span></span>
      ${k === 'V' ? `<span class="from" title="${esc(f.old_path)}">← ${esc(oldDir)}</span>` : ''}
      ${hitCount ? `<span class="cnt hits">${hitCount}</span>` : ''}
      ${threads ? `<span class="cnt bubble" title="${plural(threads, 'unresolved thread')}">${BUBBLE}${threads}</span>` : ''}
      <span class="spacer"></span><span class="check" data-review="${esc(f.path)}" title="Mark reviewed">✓</span></div>`;
}
function renderTreeNode(node, depth, out) {
  for (const d of [...node.dirs.values()].sort(byLabel)) {
    const closed = state.closed.has(d.full);
    out.push(`<div class="row dir ${state.cursor === 'd:' + d.full ? 'cursor' : ''}" style="--d:${depth};${guides(depth)}" data-folder="${esc(d.full)}" title="${esc(d.full)}">
      <span class="ico folder">${closed ? FOLDER_CLOSED : FOLDER_OPEN}</span><span class="pill"><span class="nm">${esc(d.label)}/</span></span>${closed ? `<span class="cnt">${countFiles(d)}</span>` : ''}</div>`);
    if (!closed) renderTreeNode(d, depth + 1, out);
  }
  for (const f of [...node.files].sort(byBase)) out.push(leafHtml(f, depth));
}
function renderTree() {
  const files = visibleFiles();
  const root = buildTree(files);
  const counts = {}; for (const f of files) { const k = changeKind(f); counts[k] = (counts[k] || 0) + 1; }
  const out = [`<div class="tree"><div class="legend">${['A', 'M', 'D', 'R', 'V'].filter(k => counts[k]).map(k => `<span class="pill st-${k}">${KIND_LABEL[k]}</span><span class="n">${counts[k]}</span>`).join('')}</div>`];
  renderTreeNode(root, 0, out);
  out.push('</div>');
  return out.join('');
}
function hiddenNotice(n, what, toggle, showing) {
  if (!n) return '';
  const files = `${n} ${what.replace('file', n > 1 ? 'files' : 'file')}`;
  return `<div class="notice">${showing ? `Showing ${files}` : `${files} hidden`} · <a href="#" ${toggle}="${showing ? '0' : '1'}">${showing ? 'hide' : 'show'}</a></div>`;
}
function renderList() {
  const noiseOnlyCount = state.q ? 0 : FILES.filter(f => kindVisible(f) && noiseOnly(f)).length;
  const generatedCount = FILES.filter(f => kindVisible(f) && f.generated).length;
  return hiddenNotice(noiseOnlyCount, 'file with only noise', 'data-noiseonly', state.showNoiseOnly) +
    hiddenNotice(generatedCount, 'generated file', 'data-generated', state.showGenerated) + renderTree();
}
function updateSearchMeta() {
  const el = document.getElementById('searchmeta'); if (!el) return;
  if (!state.q) { el.textContent = ''; return; }
  const files = visibleFiles();
  const n = files.reduce((a, f) => a + hits(f), 0);
  el.textContent = `${plural(files.length, 'file')} · ${state.hitIndex >= 0 ? `${state.hitIndex + 1} / ${n}` : plural(n, 'line')}`;
}
function focusBox(sel) { const i = document.querySelector(sel); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }

const PR_ICON_PATHS = {
  open: '<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7M6 9v12"/>',
  draft: '<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M18 6V5M18 11v-1M6 9v12"/>',
  merged: '<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M6 21V9a9 9 0 0 0 9 9"/>',
  closed: '<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M6 9v12M21 3l-6 6M21 9l-6-6M18 11.5V15"/>',
};
function statusChip() {
  const st = PR.draft && PR.state === 'OPEN' ? 'draft' : (PR.state || '').toLowerCase();
  if (!PR_ICON_PATHS[st]) return '';
  const icon = `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${PR_ICON_PATHS[st]}</svg>`;
  return `<span class="status st-${st}">${icon}${st[0].toUpperCase() + st.slice(1)}</span>`;
}
function renderSide() {
  const reviewed = FILES.filter(isReviewed).length;
  const h = `<div class="head">${SERVED ? '<a class="back" href="/">← Pull requests</a>' : ''}<h1 data-go="overview">#${PR.number} ${esc(PR.title)}</h1>
    <div class="meta">${statusChip()}<span>${FILES.length} files · ${reviewed} reviewed · <a href="${PR.url}" target="_blank">GitHub</a>${API ? ` · <a href="#" data-review-panel class="${state.reviewPanel ? 'on' : ''}">Review${pendingCount() ? ` (${pendingCount()})` : ''}</a>` : ''}</span></div></div>
    ${commentNotices()}${state.reviewPanel ? reviewPanelHtml() : ''}
    <div class="controls"><span class="seg">
      <button data-filter="all" class="${state.filter === 'all' ? 'on' : ''}">All</button>
      <button data-filter="implementation" class="${state.filter === 'implementation' ? 'on' : ''}">Implementation</button>
      <button data-filter="test" class="${state.filter === 'test' ? 'on' : ''}">Tests</button></span>
      <span class="seg"><button data-whole="0" class="${!state.whole ? 'on' : ''}">Changes</button><button data-whole="1" class="${state.whole ? 'on' : ''}">Whole files</button></span>
      <span class="seg"><button data-tree="expand">Expand all</button><button data-tree="collapse">Collapse all</button></span>
      <span class="hint"><kbd>j</kbd>/<kbd>k</kbd> files · <kbd>↑</kbd><kbd>↓</kbd><kbd>←</kbd><kbd>→</kbd> tree · <kbd>w</kbd> whole · <kbd>c</kbd> collapse · <kbd>r</kbd> reviewed · <kbd>o</kbd> top · <kbd>/</kbd> search · <kbd>⏎</kbd> next hit</span></div>
    <div class="searchbar">
      <input type="search" class="search" placeholder="Search changed lines" value="${esc(state.q)}" spellcheck="false" title="Enter: next hit · Shift+Enter: previous">
      <span class="meta" id="searchmeta"></span></div>
    <div class="list" tabindex="0">${renderList()}</div><div class="grip"></div>`;
  const prev = document.querySelector('#side .list');
  const scroll = prev ? prev.scrollTop : 0;
  const ae = document.activeElement;
  const focused = ae && (ae.classList.contains('search') ? '.search' : ae.classList.contains('list') ? '.list' : null);
  document.getElementById('side').innerHTML = h;
  document.querySelector('#side .list').scrollTop = scroll;
  if (focused === '.list') document.querySelector('#side .list').focus({ preventScroll: true });
  else if (focused) focusBox(focused);
  revealActive();
  updateSearchMeta();
}
function revealActive() {
  const active = document.querySelector('#side .row.active');
  if (!active) return;
  const list = document.querySelector('#side .list'), a = active.getBoundingClientRect(), l = list.getBoundingClientRect();
  if (a.top < l.top || a.bottom > l.bottom) active.scrollIntoView({ block: 'center' });
}
// Mark the file under the top of the main pane in the tree and the URL, without rebuilding the sidebar.
function setActive(path) {
  if (state.view === path) return;
  state.view = path;
  if (!state.cursor || state.cursor.startsWith('f:')) state.cursor = path === 'overview' ? null : 'f:' + path;
  for (const el of document.querySelectorAll('#side .row.cursor')) el.classList.remove('cursor');
  const cur = state.cursor && document.querySelector(`#side .leaf[data-path="${CSS.escape(state.cursor.slice(2))}"]`);
  if (cur) cur.classList.add('cursor');
  for (const el of document.querySelectorAll('#side .row.active')) el.classList.remove('active');
  const row = path !== 'overview' && document.querySelector(`#side .leaf[data-path="${CSS.escape(path)}"]`);
  if (row) row.classList.add('active');
  revealActive();
  history.replaceState(null, '', path === 'overview' ? '#' : `#${encodeURIComponent(path)}`);
}

// ---------- keyboard navigation in the tree
function treeRows() { return [...document.querySelectorAll('#side .tree .row')]; }
function cursorKey(el) { return el.dataset.folder !== undefined ? 'd:' + el.dataset.folder : 'f:' + el.dataset.path; }
function setCursor(el) {
  state.cursor = cursorKey(el);
  for (const x of document.querySelectorAll('#side .row.cursor')) x.classList.remove('cursor');
  el.classList.add('cursor');
  const list = document.querySelector('#side .list'), a = el.getBoundingClientRect(), l = list.getBoundingClientRect();
  if (a.top < l.top || a.bottom > l.bottom) el.scrollIntoView({ block: 'nearest' });
  if (el.dataset.path) scrollToFile(el.dataset.path);
}
function toggleFolder(folder) {
  state.closed.has(folder) ? state.closed.delete(folder) : state.closed.add(folder);
  renderSide();
}
// Handle an arrow, Enter or space key in the focused tree; true when it was one.
function treeKey(e) {
  const rows = treeRows(); if (!rows.length) return false;
  let i = rows.findIndex(r => cursorKey(r) === state.cursor);
  if (i < 0) i = rows.findIndex(r => r.dataset.path === state.view);
  const cur = rows[i];
  if (e.key === 'ArrowDown') { setCursor(rows[Math.min(i + 1, rows.length - 1)]); return true; }
  if (e.key === 'ArrowUp') { setCursor(rows[Math.max(i - 1, 0)]); return true; }
  if (!cur) return false;
  const folder = cur.dataset.folder;
  if (e.key === 'ArrowRight') {
    if (folder !== undefined && state.closed.has(folder)) toggleFolder(folder);
    else if (folder !== undefined && rows[i + 1]) setCursor(rows[i + 1]);
    return true;
  }
  if (e.key === 'ArrowLeft') {
    if (folder !== undefined && !state.closed.has(folder)) { toggleFolder(folder); return true; }
    const depth = +cur.style.getPropertyValue('--d');
    for (let j = i - 1; j >= 0; j--) if (rows[j].dataset.folder !== undefined && +rows[j].style.getPropertyValue('--d') < depth) { setCursor(rows[j]); break; }
    return true;
  }
  if (e.key === 'Enter' || e.key === ' ') {
    if (folder !== undefined) toggleFolder(folder);
    else scrollToFile(cur.dataset.path);
    return true;
  }
  return false;
}
