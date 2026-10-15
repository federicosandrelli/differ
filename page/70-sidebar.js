// The sidebar: the PR header, the controls, the search box and the file tree.

function countFiles(node) { let n = node.files.length; for (const c of node.dirs.values()) n += countFiles(c); return n; }
const noHit = (f) => state.q && !viewHits(f).length;
const allNoHit = (node) => node.files.every(noHit) && [...node.dirs.values()].every(allNoHit);
function allFolders(node, acc) { for (const d of node.dirs.values()) { acc.push(d.full); allFolders(d, acc); } return acc; }
// Background gradients paint the guide lines, so a row can span the full sidebar width.
function guides(depth) {
  if (!depth) return '';
  const img = [], pos = [], size = [];
  for (let k = 0; k < depth; k++) { img.push('linear-gradient(var(--border), var(--border))'); pos.push(`${17 + k * 19}px 0`); size.push('1px 100%'); }
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
  return `<div class="row leaf ${state.view === f.path ? 'active' : ''} ${state.cursor === 'f:' + f.path ? 'cursor' : ''} ${isReviewed(f) ? 'reviewed' : ''} ${noHit(f) ? 'nohit' : ''}" style="--d:${depth};${guides(depth)}" data-path="${esc(f.path)}" title="${esc(f.base)}">
      <span class="ico"></span>
      <span class="pill st-${k} ${f.kind === 'test' ? 'test' : ''}">${k === 'R' ? `<s title="${esc(f.old_path.split('/').pop())}">${esc(f.old_path.split('/').pop())}</s>` : ''}<span class="nm">${esc(f.base)}</span></span>
      ${k === 'V' ? `<span class="from" title="${esc(oldDir)}">← ${esc(oldDir)}</span>` : ''}
      ${hitCount ? `<span class="cnt hits">${hitCount}</span>` : ''}
      ${threads ? `<span class="cnt bubble">${BUBBLE}${threads}</span>` : ''}
      <span class="spacer"></span><span class="check" data-review="${esc(f.path)}">✓</span></div>`;
}
function renderTreeNode(node, depth, out) {
  for (const d of [...node.dirs.values()].sort(byLabel)) {
    const closed = state.closed.has(d.full);
    out.push(`<div class="row dir ${state.cursor === 'd:' + d.full ? 'cursor' : ''} ${allNoHit(d) ? 'nohit' : ''}" style="--d:${depth};${guides(depth)}" data-folder="${esc(d.full)}" title="${esc(d.label)}/">
      <span class="ico folder">${closed ? FOLDER_CLOSED : FOLDER_OPEN}</span><span class="pill"><span class="nm">${esc(d.label)}/</span></span>${closed ? `<span class="cnt">${countFiles(d)}</span>` : ''}</div>`);
    if (!closed) renderTreeNode(d, depth + 1, out);
  }
  for (const f of [...node.files].sort(byBase)) out.push(leafHtml(f, depth));
}
function renderTree() {
  const files = FILES.filter(fileListed);
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
  const noiseOnlyCount = FILES.filter(f => kindVisible(f) && noiseOnly(f)).length;
  const generatedCount = FILES.filter(f => kindVisible(f) && f.generated).length;
  return hiddenNotice(noiseOnlyCount, 'file with only noise', 'data-noiseonly', state.showNoiseOnly) +
    hiddenNotice(generatedCount, 'generated file', 'data-generated', state.showGenerated) + renderTree();
}
function updateSearchMeta() {
  const el = document.getElementById('searchmeta'); if (!el) return;
  if (!state.q) { el.textContent = ''; return; }
  const all = allHits(), at = hitPosition(all);
  el.textContent = `${plural(visibleFiles().length, 'file')} · ${at >= 0 ? `${at + 1} / ${all.length}` : plural(all.length, 'line')}`;
}
function focusBox(sel) { const i = document.querySelector(sel); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }

// The status GitHub reports now, once the comments load; until then the one the page was built with.
function statusChip() {
  const status = C.status || PR_STATUS;
  if (!status) return '';
  return `<span class="status st-${status}"><span class="pr-icon st-${status}" aria-hidden="true"></span>${status[0].toUpperCase() + status.slice(1)}</span>`;
}
// The button names the viewer's verdict on the head commit, and stays primary while the viewer has a review to give.
const VERDICT = { APPROVED: ['Approved', 'approved'], CHANGES_REQUESTED: ['Changes requested', 'changes'] };
function reviewButton() {
  const n = pendingCount(), [label, cls] = VERDICT[headVerdict()] || ['Review', ''];
  const look = state.reviewPanel ? 'on' : cls && !n ? cls : 'primary';
  return `<button data-review-panel class="review ${look}">${label}${n ? `<span class="count">${n}</span>` : ''}</button>`;
}
function renderSide() {
  const reviewed = FILES.filter(isReviewed).length;
  const h = `<div class="head">${SERVED ? '<a class="back" href="/">← Pull requests</a>' : ''}<h1 data-go="overview">#${PR.number} ${esc(PR.title)}</h1>
    <div class="meta">${statusChip()}<span>${FILES.length} files · ${reviewed} reviewed · <a href="${PR.url}" target="_blank">GitHub</a></span>${API ? reviewButton() : ''}</div></div>
    ${commentNotices()}${state.reviewPanel ? reviewPanelHtml() : ''}
    <div class="controls"><span class="seg">
      <button data-filter="all" class="${state.filter === 'all' ? 'on' : ''}">All</button>
      <button data-filter="implementation" class="${state.filter === 'implementation' ? 'on' : ''}">Implementation</button>
      <button data-filter="test" class="${state.filter === 'test' ? 'on' : ''}">Tests</button></span>
      <span class="seg"><button data-tree="expand">Expand all</button><button data-tree="collapse">Collapse all</button></span>
      <span class="hint"><kbd>↑</kbd><kbd>↓</kbd><kbd>←</kbd><kbd>→</kbd> tree · <kbd>o</kbd> top</span></div>
    <div class="searchbar">
      <input type="search" class="search" placeholder="Search" value="${esc(state.q)}" spellcheck="false" title="Enter: next hit · Shift+Enter: previous">
      <span class="meta" id="searchmeta"></span></div>
    <div class="list" tabindex="0">${renderList()}</div>${bugFootHtml()}<div class="grip"></div>`;
  const prev = document.querySelector('#side .list');
  const scroll = prev ? prev.scrollTop : 0;
  const ae = document.activeElement;
  const focused = ae && (ae.classList.contains('search') ? '.search' : ae.classList.contains('list') ? '.list' : ae.dataset.bugText !== undefined ? '[data-bug-text]' : null);
  document.getElementById('side').innerHTML = h;
  if (SHELL) SHELL.draft(hasDraft());
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
  // While searching, a file opens at its first hit; a closed section stays closed as the cursor passes it.
  const f = el.dataset.path && BY_PATH[el.dataset.path];
  if (f && !(sectionOpen(f) && goFirstHit(f.path))) scrollToFile(f.path);
}
function toggleFolder(folder) {
  state.closed.has(folder) ? state.closed.delete(folder) : state.closed.add(folder);
  renderSide();
}
// Handle an arrow, Enter or space key in the focused tree; true when it was one. While searching, Enter steps
// through the hits from the current one, as it does in the search box, unless the cursor is on a folder or on a
// file without a hit on screen. The cursor is on no row when it followed the hit into a closed folder.
function treeKey(e) {
  const rows = treeRows(); if (!rows.length) return false;
  let i = rows.findIndex(r => cursorKey(r) === state.cursor);
  if (i < 0) i = rows.findIndex(r => r.dataset.path === state.view);
  const cur = rows[i];
  if (e.key === 'ArrowDown') { if (i < rows.length - 1) setCursor(rows[i + 1]); return true; }
  if (e.key === 'ArrowUp') { if (i !== 0) setCursor(rows[i < 0 ? 0 : i - 1]); return true; }
  const f = cur && BY_PATH[cur.dataset.path];
  if (e.key === 'Enter' && state.hit && (!cur || (f && hits(f)))) { stepHit(e.shiftKey ? -1 : 1); return true; }
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
    else if (!goFirstHit(cur.dataset.path)) go(cur.dataset.path);
    return true;
  }
  return false;
}
