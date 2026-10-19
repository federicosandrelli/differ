// The sidebar: the PR header, the controls, the search box and the file tree.

function countFiles(node) { let n = node.files.length; for (const c of node.dirs.values()) n += countFiles(c); return n; }
const noHit = (f) => state.q && !found(f);
const allNoHit = (node) => node.files.every(noHit) && [...node.dirs.values()].every(allNoHit);
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
  const files = FILES.filter(fileInTree);
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
  if (!FILES.some(kindVisible)) return `<div class="notice">No ${state.filter} files · <a href="#" data-filter="all">show all</a></div>`;
  const noiseOnlyCount = FILES.filter(f => kindVisible(f) && f.noise_only).length;
  const generatedCount = FILES.filter(f => kindVisible(f) && f.generated).length;
  return hiddenNotice(noiseOnlyCount, 'file with only noise', 'data-noiseonly', state.showNoiseOnly) +
    hiddenNotice(generatedCount, 'generated file', 'data-generated', state.showGenerated) + renderTree();
}
function updateSearchMeta() {
  const el = document.getElementById('searchmeta'); if (!el) return;
  if (!state.q) { el.textContent = ''; return; }
  const all = allHits(), at = hitPosition(all);
  const where = at >= 0 ? ` · ${at + 1} / ${all.length}` : state.fileSearch ? '' : ` · ${plural(all.length, 'line')}`;
  el.textContent = plural(visibleFiles().length, 'file') + where;
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
function editorPicker() {
  if (!EDITOR.installed || EDITOR.installed.length < 2) return '';
  const chosen = chosenEditor();
  return `<span class="editors">Editor <span class="seg">${EDITOR.installed.map(e => `<button data-editor="${esc(e.id)}" class="${e === chosen ? 'on' : ''}">${esc(e.name)}</button>`).join('')}</span></span>`;
}
function renderSide() {
  // The box keeps the spaces the reader typed around the query, which the search ignores.
  const box = document.querySelector('#side .search');
  const typed = box && box.value.trim() === state.q ? box.value : state.q;
  const reviewed = FILES.filter(isReviewed).length;
  const filterButton = (filter, label) => {
    const files = FILES.filter(f => ofKind(f, filter));
    const sum = (key) => fmt(files.reduce((n, f) => n + f.stats[key], 0));
    const split = filter === 'all' ? '' : `<span class="split"><span class="a">+${sum('additions')}</span><span class="d">−${sum('deletions')}</span></span>`;
    return `<button data-filter="${filter}" class="${state.filter === filter ? 'on' : ''}">${label}<span class="count" title="${plural(files.length, 'file')}">${fmt(files.length)}</span>${split}</button>`;
  };
  const h = `<div class="head">${SERVED ? '<a class="back" href="/">← Pull requests</a>' : ''}<h1 data-go="overview">#${PR.number} ${esc(PR.title)}</h1>
    <div class="meta">${statusChip()}<span>${plural(FILES.length, 'file')} · ${reviewed} reviewed · <a href="${PR.url}" target="_blank">GitHub</a></span>${API ? reviewButton() : ''}</div></div>
    ${commentNotices()}${EDITOR.error ? `<div class="notice err">Open in editor: ${esc(EDITOR.error)} · <a href="#" data-editor-dismiss>dismiss</a></div>` : ''}${state.reviewPanel ? reviewPanelHtml() : ''}
    <div class="controls"><span class="seg">
      ${filterButton('all', 'All')}${filterButton('implementation', 'Implementation')}${filterButton('test', 'Tests')}</span>${editorPicker()}</div>
    <div class="searchbar">
      <input type="search" class="search" placeholder="⌘F to search or ⇧⌘F to search files" value="${esc(typed)}" spellcheck="false" title="Enter: next ${state.fileSearch ? 'file' : 'hit'} · Shift+Enter: previous">
      ${state.fileSearch ? '<span class="mode">Files</span>' : ''}<span class="meta" id="searchmeta"></span></div>
    <div class="list" tabindex="0">${renderList()}</div>${bugFootHtml()}<div class="grip"></div>`;
  const prev = document.querySelector('#side .list');
  const scroll = prev ? prev.scrollTop : 0;
  const ae = document.activeElement;
  const focused = ae && (ae.classList.contains('search') ? '.search' : ae.classList.contains('list') ? '.list' : ae.dataset.bugText !== undefined ? '[data-bug-text]' : null);
  const selection = focused && focused !== '.list' ? [ae.selectionStart, ae.selectionEnd, ae.selectionDirection] : null;
  document.getElementById('side').innerHTML = h;
  if (SHELL) SHELL.draft(hasDraft());
  document.querySelector('#side .list').scrollTop = scroll;
  if (focused === '.list') document.querySelector('#side .list').focus({ preventScroll: true });
  else if (focused) { const el = document.querySelector(`#side ${focused}`); if (el) { el.focus(); el.setSelectionRange(...selection); } }
  revealActive();
  updateSearchMeta();
  fitFilterCounts();
}
// The file type buttons drop their file counts when the buttons don't fit on one line, then their line counts.
function fitFilterCounts() {
  const seg = document.querySelector('#side .seg');
  if (!seg) return;
  seg.classList.remove('no-files', 'no-lines');
  for (const cls of ['no-files', 'no-lines']) if (seg.scrollWidth > seg.clientWidth) seg.classList.add(cls);
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
  const f = el.dataset.path && BY_PATH[el.dataset.path];
  if (f) passFile(f);
}
// Show a file the reader steps onto. While searching, it opens at its first hit; a closed section stays closed.
function passFile(f) { if (!((state.fileSearch || sectionOpen(f)) && goFirstHit(f.path))) scrollToFile(f.path); }
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
