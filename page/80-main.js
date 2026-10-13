// The main pane: the overview, then one section per shown file.
//
// A section always has its header. Its body (the diff table) is cut into chunks of CHUNK rows, and a chunk
// holds its rows only while it is near the viewport; far away it is an empty box of the height it had, so
// nothing moves and the DOM stays small whatever the size of the pull request or of one file in it.
// A body's rows are computed from the state once per state key and kept, so a render after a state change
// costs only the files that both show and changed.
const main = document.getElementById('main');
const NEAR = 2000;   // px beyond the viewport within which chunks hold their rows
const FAR = 6000;    // px beyond the viewport past which chunks and bodies are emptied
const CHUNK = 100;   // rows per chunk
const ROW_PX = 20.6; // the height of a code row, to size a chunk before it is first filled
let overviewEl = null;
let sections = [];   // [{ f, el }] in page order
const bodies = new Map(); // path -> { key, outdated, rows, index, cols }: the body's HTML parts for the key it was computed from

// The old path from the last directory the two paths share, so only the changed part shows.
function renamedFrom(f) {
  if (!f.old_path || !f.new_path || f.old_path === f.new_path) return '';
  const o = f.old_path.split('/'), n = f.new_path.split('/');
  let i = 0;
  while (i < o.length - 1 && i < n.length - 1 && o[i] === n[i]) i++;
  const tail = o.slice(Math.max(0, i - 1)).join('/');
  return `<span class="dir" title="${esc(f.old_path)}">renamed from ${i > 1 ? '…/' : ''}${esc(tail)}</span>`;
}
function binarySize(f) {
  const { old, new: now } = f.binary;
  return old == null ? fileSize(now) : now == null ? fileSize(old) : `${fileSize(old)} → ${fileSize(now)}`;
}
function binaryHtml(f) {
  const what = f.status === 'A' ? 'added' : f.status === 'D' ? 'deleted' : 'changed';
  return `<div class="empty">Binary file ${what} · ${binarySize(f)}</div>`;
}
const countsHtml = (f) => f.binary ? `<span class="counts">${binarySize(f)}</span>`
  : `<span class="counts"><span class="a">+${f.stats.additions}</span> <span class="d">−${f.stats.deletions}</span></span>`;
function headerHtml(f, open) {
  const ck = changeKind(f);
  return `<div class="fhead"><div class="top" data-toggle="${esc(f.path)}">
      <span class="chev ${open ? 'open' : ''}" title="${open ? 'Collapse' : 'Expand'}"><svg viewBox="0 0 16 16" width="18" height="18" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
      <span class="badge ${ck}" title="${KIND_LABEL[ck]}">${ck === 'V' ? '→' : ck}</span>
      <span class="path" title="${esc(f.path)}"><b>${esc(f.base)}</b><span class="dir">${esc(f.dir)}</span>${renamedFrom(f)}</span>
      <span class="kind">${esc(f.kind)}</span>${countsHtml(f)}
      <button data-review="${esc(f.path)}" class="${isReviewed(f) ? 'on' : ''}">${isReviewed(f) ? '✓ Reviewed' : 'Mark reviewed'}</button>
    </div></div>`;
}
// Everything a file's body depends on besides the file itself.
function bodyKey(f) {
  const c = state.compose && state.compose.path === f.path ? state.compose : null;
  return JSON.stringify([
    viewKey(f), c && [c.side, c.startLine, c.line],
    C.version, C.pending && C.pending.id, state.busy, [...state.openThreads], [...state.replyOpen.keys()], state.showOutdated.has(f.path),
  ]);
}
function bodyParts(f) {
  const key = bodyKey(f);
  let parts = bodies.get(f.path);
  if (!parts || parts.key !== key) {
    const code = f.runs.length ? codeRows(f) : null;
    parts = { key, outdated: outdatedHtml(f), rows: code && code.html, index: code && code.index, cols: code ? code.cols : 0 };
    bodies.set(f.path, parts);
  }
  return parts;
}
// About how tall the body will be, from the rows the diff view shows: the changed lines with their context.
function estimateHeight(f) {
  const lines = contentLines(f).length, changed = f.stats.raw, runs = f.runs.length;
  const rows = Math.min(lines + changed, changed + runs * (2 * RADIUS + 2));
  return Math.round(rows * ROW_PX + 14);
}

const sectionEl = (f) => document.getElementById(`sec-${f.index}`);
const codeOf = (el) => el.querySelector(':scope > .code');
// An element's top and bottom in the main pane's scrolled content.
function span(el) {
  const r = el.getBoundingClientRect(), top = r.top - main.getBoundingClientRect().top + main.scrollTop;
  return [top, top + r.height];
}
const near = ([top, bottom]) => bottom >= main.scrollTop - NEAR && top <= main.scrollTop + main.clientHeight + NEAR;
const far = ([top, bottom]) => bottom < main.scrollTop - FAR || top > main.scrollTop + main.clientHeight + FAR;

// Replace an element's content and let the content size it, keeping what is on screen still when the
// element is above the viewport. Each textarea takes its draft from the state, because the kept HTML of a
// body can predate the latest typing.
function replaceContent(el, html) {
  const above = span(el)[1] <= main.scrollTop + 1, before = el.offsetHeight;
  el.style.height = '';
  el.innerHTML = html;
  for (const box of el.getElementsByTagName('textarea')) box.value = draftText(box);
  if (above) main.scrollTop += el.offsetHeight - before;
}
function fillChunk(parts, chunk) {
  if (chunk.dataset.filled) return;
  const start = +chunk.dataset.start;
  replaceContent(chunk, tableHtml(parts.rows.slice(start, start + CHUNK)));
  chunk.dataset.filled = '1';
}
function emptyChunk(chunk) {
  if (!chunk.dataset.filled || chunk.contains(document.activeElement)) return;
  chunk.style.height = `${chunk.offsetHeight}px`;
  chunk.innerHTML = '';
  delete chunk.dataset.filled;
}
// Give the body the chunks of its current rows, then fill the chunks near the viewport and empty the far ones.
function fillBody(f, el) {
  const code = codeOf(el);
  if (!code) return;
  const parts = bodyParts(f);
  if (code.dataset.key !== parts.key) {
    const chunks = [];
    for (let i = 0; parts.rows && i < parts.rows.length; i += CHUNK) {
      chunks.push(`<div class="chunk" data-start="${i}" style="height:${Math.round(Math.min(CHUNK, parts.rows.length - i) * ROW_PX)}px"></div>`);
    }
    const left = code.scrollLeft;
    code.style.setProperty('--cols', parts.cols);
    replaceContent(code, parts.outdated + (parts.rows ? chunks.join('') : f.binary ? binaryHtml(f) : '<div class="empty">No changes</div>'));
    code.scrollLeft = left;
    code.dataset.key = parts.key;
  }
  for (const chunk of code.getElementsByClassName('chunk')) {
    const s = span(chunk);
    if (near(s)) fillChunk(parts, chunk);
    else if (far(s)) emptyChunk(chunk);
  }
}
// Empty a body far from the viewport, keeping its height so nothing moves; the one being typed in stays.
function emptyBody(el) {
  const code = codeOf(el);
  if (!code || !code.dataset.key || el.contains(document.activeElement)) return;
  code.style.height = `${code.offsetHeight}px`;
  code.innerHTML = '';
  delete code.dataset.key;
}
// Fill the chunk holding the row with a key, so it can be scrolled to; the row element, or null when the
// body does not show it.
function revealRow(f, el, key) {
  fillBody(f, el);
  const code = codeOf(el), parts = bodyParts(f), i = parts.index ? parts.index.get(key) : undefined;
  if (!code || i === undefined) return null;
  const chunk = code.querySelector(`.chunk[data-start="${i - i % CHUNK}"]`);
  if (chunk) fillChunk(parts, chunk);
  return document.getElementById(rowId(f, key));
}

function patchOverview() {
  if (!overviewEl) {
    overviewEl = document.createElement('div');
    overviewEl.className = 'page'; overviewEl.id = 'overview';
    main.prepend(overviewEl);
  }
  const key = overviewKey();
  if (overviewEl.dataset.key === key) return;
  replaceContent(overviewEl, overviewHtml());
  overviewEl.dataset.key = key;
}
function updateSection(f, el) {
  const open = sectionOpen(f), key = `${open}|${isReviewed(f)}`;
  if (el.dataset.head === key) return;
  el.dataset.head = key;
  el.classList.toggle('closed', !open);
  const head = el.querySelector(':scope > .fhead');
  if (head) head.outerHTML = headerHtml(f, open); else el.insertAdjacentHTML('afterbegin', headerHtml(f, open));
  const code = codeOf(el);
  if (open && !code) {
    const body = document.createElement('div');
    body.className = 'code'; body.style.height = `${estimateHeight(f)}px`;
    el.append(body);
  } else if (!open && code) code.remove();
}
// One section per shown file, in order, reusing the sections already on the page.
function patchSections(files) {
  const existing = new Map(sections.map(s => [s.f.path, s.el]));
  sections = [];
  files.forEach((f, i) => {
    let el = existing.get(f.path);
    existing.delete(f.path);
    if (!el) {
      el = document.createElement('section');
      el.className = 'filesec'; el.id = `sec-${f.index}`; el.dataset.file = f.path;
    }
    updateSection(f, el);
    const at = main.children[i + 1];
    if (at !== el) main.insertBefore(el, at || null);
    sections.push({ f, el });
  });
  for (const el of existing.values()) el.remove();
}

// Bring the filled bodies in line with the state: the near ones (and the one being typed in) are rebuilt,
// the others dropped, to be rebuilt when they come near again.
function refreshBodies() {
  for (const { f, el } of sections) {
    const code = codeOf(el);
    if (!code || !code.dataset.key || code.dataset.key === bodyKey(f)) continue;
    if (near(span(el)) || el.contains(document.activeElement)) fillBody(f, el); else emptyBody(el);
  }
}
// Fill what is near the viewport, empty what is far, and mark the file under the top of the pane.
function syncViewport() {
  for (const { f, el } of sections) {
    const s = span(el);
    if (near(s)) fillBody(f, el);
    else if (far(s)) emptyBody(el);
  }
  // The last section starting within the sticky header's reach of the pane's top.
  const line = main.scrollTop + 60;
  let lo = 0, hi = sections.length - 1, found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (span(sections[mid].el)[0] <= line) { found = mid; lo = mid + 1; } else hi = mid - 1;
  }
  setActive(found < 0 ? 'overview' : sections[found].f.path);
}
let scrollPending = false;
main.addEventListener('scroll', () => {
  if (scrollPending) return;
  scrollPending = true;
  requestAnimationFrame(() => { scrollPending = false; syncViewport(); });
});
window.addEventListener('resize', syncViewport);

// What is under the top of the pane, below the sticky file header: a code row when there is one, and its
// section, with where each sits, so a render can put the same line back in the same place.
function viewportAnchor() {
  const mr = main.getBoundingClientRect();
  const hit = document.elementFromPoint(mr.left + mr.width / 2, mr.top + 80);
  const sec = hit && hit.closest('.filesec');
  if (!sec || !sec.dataset.file) return null;
  const row = hit.closest('tr[id]');
  return { f: BY_PATH[sec.dataset.file], sec, secTop: sec.getBoundingClientRect().top,
           rowId: row && row.id, rowTop: row && row.getBoundingClientRect().top };
}
function restoreAnchor(a) {
  if (!a || !a.sec.isConnected) return;
  const at = a.rowId && rowAt(a.rowId);
  const row = at && (document.getElementById(a.rowId) || revealRow(a.f, a.sec, at.key));
  if (row) main.scrollTop += row.getBoundingClientRect().top - a.rowTop;
  else main.scrollTop += a.sec.getBoundingClientRect().top - a.secTop;
}
// Bring the page in line with the state, keeping the line under the top of the pane where it is.
function render() {
  renderSide();
  const anchor = viewportAnchor();
  patchOverview();
  patchSections(visibleFiles());
  refreshBodies();
  syncViewport();
  restoreAnchor(anchor);
  syncViewport();
  restoreAnchor(anchor);
  restoreFocus();
}
function restoreFocus() {
  if (!state.focus) return;
  const el = document.querySelector(state.focus);
  if (!el) return;
  state.focus = null;
  el.focus({ preventScroll: true });
  if (el.setSelectionRange) el.setSelectionRange(el.value.length, el.value.length);
}

function scrollToFile(path) {
  if (path === 'overview') { main.scrollTop = 0; syncViewport(); return; }
  const f = BY_PATH[path], el = f && sectionEl(f);
  if (!el) return;
  main.scrollTop = span(el)[0] - 16;
  syncViewport();
  main.scrollTop = span(el)[0] - 16;
  setActive(path);
}
// Show a file, and a row of it when given the row's key, opening whatever folds hide it.
function go(path, key) {
  const f = BY_PATH[path];
  if (!f) { scrollToFile('overview'); return; }
  let dirty = false;
  if (!visibleFiles().includes(f)) {
    if (!kindVisible(f)) state.filter = 'all';
    if (noiseOnly(f)) state.showNoiseOnly = true;
    if (f.generated) state.showGenerated = true;
    if (state.q && !hits(f)) { state.q = ''; state.hitIndex = -1; }
    dirty = true;
  }
  if (!sectionOpen(f)) { state.openOverride.set(f.path, true); dirty = true; }
  if (key == null) { if (dirty) render(); scrollToFile(path); return; }
  reveal(f, key);
  render();
  const el = sectionEl(f);
  if (!el) return;
  const row = revealRow(f, el, key);
  if (!row) { scrollToFile(path); return; }
  row.scrollIntoView({ block: 'center' });
  const top = row.getBoundingClientRect().top;
  syncViewport();
  main.scrollTop += row.getBoundingClientRect().top - top;
  row.classList.add('target');
  setTimeout(() => row.classList.remove('target'), 2500);
}
