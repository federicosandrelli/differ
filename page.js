const D = JSON.parse(document.getElementById('data').textContent);
const PR = D.annotations.pr;
const FILES = D.files;
const BY_PATH = Object.fromEntries(FILES.map(f => [f.path, f]));
const NOISE = ['import', 'rename', 'whitespace', 'moved'];
const NOISE_LABEL = { import: 'imports', rename: 'rename fallout', whitespace: 'whitespace', moved: 'moved code' };
const KIND_LABEL = { A: 'added', M: 'modified', D: 'deleted', R: 'renamed', V: 'moved' };
const THEMES = ['auto', 'light', 'sepia', 'dark'];
const RADIUS = 4;

FILES.forEach((f, i) => {
  f.index = i;
  f.base = f.path.split('/').pop();
  f.dir = f.path.slice(0, f.path.length - f.base.length);
});

const state = {
  view: 'overview', filter: 'implementation', whole: false, closed: new Set(), q: '', hitIndex: -1,
  showNoiseOnly: false, showGenerated: false, theme: 'auto', cursor: null, openOverride: new Map(),
  show: Object.fromEntries(NOISE.map(k => [k, false])),
  expanded: new Set(), unfolded: new Set(), target: null,
};
const LS = (k) => `dr:${PR.head.slice(0, 12)}:${k}`;
const lsGet = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
const lsSet = (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch {} };
const isReviewed = (f) => lsGet(LS(f.path)) === '1';
const setReviewed = (f, v) => lsSet(LS(f.path), v ? '1' : null);

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const fmt = (n) => n.toLocaleString('en-GB');

// A renamed file kept its directory; a moved one left it. Git reports both as R.
function changeKind(f) {
  if (f.status !== 'R') return f.status;
  const od = f.old_path.slice(0, f.old_path.lastIndexOf('/')), nd = f.new_path.slice(0, f.new_path.lastIndexOf('/'));
  return od === nd ? 'R' : 'V';
}

// ---------- which files show
// All the file's change blocks fold: imports, rename fallout, whitespace or moved code.
function noiseOnly(f) {
  return !f.generated && f.stats.substantive === 0 && f.stats.raw > 0;
}
function kindVisible(f) { return state.filter === 'all' || (state.filter === 'test' ? f.kind === 'test' : f.kind !== 'test'); }
const hitCache = new Map();
function hits(f) {
  const q = state.q.toLowerCase();
  if (!q) return 0;
  const key = f.path + '\u0000' + q;
  if (hitCache.has(key)) return hitCache.get(key);
  let n = 0;
  for (const h of f.hunks) for (const [tag, , , text] of h.lines) if (tag !== ' ' && text.toLowerCase().includes(q)) n++;
  hitCache.set(key, n);
  return n;
}
// The order the tree shows: in every folder, subfolders first, then files.
function treeOrder(node, out) {
  for (const d of [...node.dirs.values()].sort((x, y) => x.label.localeCompare(y.label))) treeOrder(d, out);
  for (const f of [...node.files].sort((x, y) => x.base.localeCompare(y.base))) out.push(f);
  return out;
}
function visibleFiles() {
  const shown = FILES.filter(f =>
    kindVisible(f) && (!state.q || hits(f) > 0) && (state.showNoiseOnly || state.q || !noiseOnly(f)) &&
    (state.showGenerated || !f.generated));
  return treeOrder(buildTree(shown), []);
}

// ---------- search hits, for Enter to step through
function allHits() {
  const q = state.q.toLowerCase();
  if (!q) return [];
  const out = [];
  for (const f of visibleFiles()) {
    for (const h of f.hunks) for (const [tag, o, n, text] of h.lines) {
      if (tag === ' ' || !text.toLowerCase().includes(q)) continue;
      if (f.content_side === 'old') out.push({ path: f.path, line: o });
      else out.push({ path: f.path, line: n != null ? n : { oldNo: o, anchor: anchorAfter(h, o) } });
    }
  }
  return out;
}
function anchorAfter(h, oldNo) {
  for (const [, o, n] of h.lines) if (o != null && o >= oldNo && n != null) return n;
  for (let i = h.lines.length - 1; i >= 0; i--) if (h.lines[i][2] != null) return h.lines[i][2];
  return h.new_start;
}
function stepHit(dir) {
  const all = allHits();
  if (!all.length) return;
  state.hitIndex = ((state.hitIndex ?? -1) + dir + all.length) % all.length;
  const hit = all[state.hitIndex];
  go(hit.path, hit.line);
  focusBox('.search');
  updateSearchMeta();
}
function markHtml(html, q) {
  if (!q) return html;
  const needle = esc(q).toLowerCase();
  return html.split(/(<[^>]+>)/).map(part => {
    if (part.startsWith('<')) return part;
    let out = '', i = 0, lower = part.toLowerCase();
    for (;;) { const j = lower.indexOf(needle, i); if (j < 0) break; out += part.slice(i, j) + '<mark>' + part.slice(j, j + needle.length) + '</mark>'; i = j + needle.length; }
    return out + part.slice(i);
  }).join('');
}

// ---------- syntax highlighting, whole file at once so docstrings keep their state across lines
const hlCache = new Map();
function highlightLines(f) {
  if (hlCache.has(f.path)) return hlCache.get(f.path);
  let lines;
  if (window.hljs && f.lang === 'python') lines = splitHighlighted(hljs.highlight(f.content, { language: 'python', ignoreIllegals: true }).value);
  else lines = f.content.split('\n').map(esc);
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  hlCache.set(f.path, lines);
  return lines;
}
function splitHighlighted(html) {
  const out = []; const stack = []; let cur = '';
  const re = /<span class="[^"]*">|<\/span>|\n/g; let last = 0; let m;
  while ((m = re.exec(html))) {
    cur += html.slice(last, m.index); last = re.lastIndex;
    if (m[0] === '\n') { out.push(cur + '</span>'.repeat(stack.length)); cur = stack.join(''); }
    else if (m[0] === '</span>') { stack.pop(); cur += m[0]; }
    else { stack.push(m[0]); cur += m[0]; }
  }
  cur += html.slice(last); out.push(cur + '</span>'.repeat(stack.length));
  return out;
}
function hlOne(text, lang) {
  if (window.hljs && lang === 'python') return hljs.highlight(text, { language: 'python', ignoreIllegals: true }).value;
  return esc(text);
}

// ---------- rows: the whole new file with removed lines interleaved where they were
function buildRows(f) {
  const runOfOld = new Map(), runOfNew = new Map(), dimOld = new Set(), dimNew = new Set();
  for (const r of f.runs) {
    r.removed.forEach(n => runOfOld.set(n, r)); r.added.forEach(n => runOfNew.set(n, r));
    if (r.class === 'rename' && r.origin) continue;
    (r.pair_rename || []).forEach((flag, i) => { if (flag) { dimOld.add(r.removed[i]); dimNew.add(r.added[i]); } });
  }
  const hl = highlightLines(f);
  const rows = [];
  const raw = f.content.split('\n'); if (raw.length && raw[raw.length - 1] === '') raw.pop();
  if (f.content_side === 'old') {
    raw.forEach((t, i) => rows.push({ type: 'del', oldNo: i + 1, html: hl[i], text: t, run: runOfOld.get(i + 1) }));
    return flagHits(rows);
  }
  const removedBefore = new Map();
  for (const h of f.hunks) {
    const L = h.lines;
    for (let i = 0; i < L.length; i++) {
      const [tag, o, , t] = L[i]; if (tag !== '-') continue;
      let anchor = null;
      for (let j = i + 1; j < L.length; j++) if (L[j][2] != null) { anchor = L[j][2]; break; }
      if (anchor == null) anchor = h.new_start + L.filter(x => x[2] != null).length;
      (removedBefore.get(anchor) || removedBefore.set(anchor, []).get(anchor)).push({ type: 'del', oldNo: o, html: hlOne(t, f.lang), text: t, run: runOfOld.get(o), dim: dimOld.has(o) });
    }
  }
  for (let i = 1; i <= raw.length + 1; i++) {
    for (const d of removedBefore.get(i) || []) rows.push(d);
    if (i <= raw.length) rows.push({ type: runOfNew.has(i) ? 'add' : 'ctx', newNo: i, html: hl[i - 1], text: raw[i - 1], run: runOfNew.get(i), dim: dimNew.has(i) });
  }
  return flagHits(rows);
}
function flagHits(rows) {
  const q = state.q.toLowerCase();
  if (!q) return rows;
  for (const r of rows) if (r.text.toLowerCase().includes(q)) { r.hit = true; r.html = markHtml(r.html, state.q); }
  return rows;
}
function blocksFor(rows) {
  const blocks = [];
  for (const r of rows) {
    const last = blocks[blocks.length - 1];
    if (r.type === 'ctx' && !r.hit) { if (last && last.ctx) last.rows.push(r); else blocks.push({ ctx: true, rows: [r] }); }
    else if (r.run && last && last.run === r.run) last.rows.push(r);
    else blocks.push({ run: r.run || null, rows: [r] });
  }
  return blocks;
}

function runSummary(r) {
  const n = r.removed.length + r.added.length;
  if (r.class === 'import') return `<span class="cls">imports</span> ${n} import line${n > 1 ? 's' : ''} changed`;
  if (r.class === 'rename') return `<span class="cls">rename fallout</span> <code>${esc(r.rename ? r.rename[0] : '')}</code> → <code>${esc(r.rename ? r.rename[1] : '')}</code> · ${n} line${n > 1 ? 's' : ''}`;
  if (r.class === 'whitespace') return `<span class="cls">whitespace</span> ${n} whitespace-only line${n > 1 ? 's' : ''}`;
  if (r.class === 'moved') return `<span class="cls">moved</span> ${n} lines moved ${r.removed.length ? 'to' : 'from'} <code>${esc(r.moved_to.path.split('/').pop())}:${r.moved_to.line}</code>`;
  return `${n} lines`;
}
const runKey = (f, r) => `${f.path}#${r.index}`;
// A block of folded noise stays open while a search hit, a rename origin, or the reader's choice needs it.
function isCollapsed(f, b) {
  const r = b.run;
  if (!r || !NOISE.includes(r.class)) return false;
  if (b.rows.some(row => row.hit)) return false;
  if (r.class === 'rename' && r.origin) return false;
  if (state.show[r.class]) return false;
  return !state.expanded.has(runKey(f, r));
}
const RENAME_RUNS = Object.fromEntries(D.renames.map(r => [r.from + '\u0000' + r.to, r.runs]));
function showOriginHead(r) { return r.class === 'rename' && r.origin && r.rename && (RENAME_RUNS[r.rename.join('\u0000')] || 0) >= 2; }

// Several collapsed blocks of one class with only short context between them read as one thing (an import
// section), so they fold into one row that expands together.
function mergeCollapsed(f, blocks) {
  const importSet = new Set(f.import_lines || []);
  const merged = [];
  let i = 0;
  while (i < blocks.length) {
    const b = blocks[i];
    if (!isCollapsed(f, b)) { merged.push(b); i++; continue; }
    const cls = b.run.class; const group = [b.run]; let j = i + 1; let lastRun = i;
    while (j < blocks.length) {
      const c = blocks[j];
      if (isCollapsed(f, c) && c.run.class === cls) { group.push(c.run); lastRun = j; j++; continue; }
      const nextSame = j + 1 < blocks.length && isCollapsed(f, blocks[j + 1]) && blocks[j + 1].run.class === cls;
      if (c.ctx && nextSame && (c.rows.length <= RADIUS * 2 + 2 || (cls === 'import' && c.rows.every(r => importSet.has(r.newNo) || !r.html.trim())))) { j++; continue; }
      break;
    }
    merged.push(group.length > 1 ? { group, cls } : b);
    i = lastRun + 1;
  }
  return merged;
}

// Word-level differences between a removed line and its added counterpart, as plain-text ranges.
function wordDiff(a, b) {
  const tok = (t) => t.match(/\w+|\s+|[^\w\s]/g) || [];
  const A = tok(a), B = tok(b);
  const n = A.length, m = B.length;
  const L = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const words = Math.max(A.filter(t => /\w/.test(t)).length, B.filter(t => /\w/.test(t)).length);
  if (!words) return null;
  const ra = [], rb = [];
  let i = 0, j = 0, pa = 0, pb = 0, commonWords = 0;
  const push = (arr, s, e) => { const last = arr[arr.length - 1]; if (last && last[1] === s) last[1] = e; else arr.push([s, e]); };
  while (i < n && j < m) {
    if (A[i] === B[j]) { if (/\w/.test(A[i])) commonWords++; pa += A[i].length; pb += B[j].length; i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) { push(ra, pa, pa + A[i].length); pa += A[i].length; i++; }
    else { push(rb, pb, pb + B[j].length); pb += B[j].length; j++; }
  }
  while (i < n) { push(ra, pa, pa + A[i].length); pa += A[i].length; i++; }
  while (j < m) { push(rb, pb, pb + B[j].length); pb += B[j].length; j++; }
  if (commonWords < Math.ceil(words * 0.3)) return null;
  // Join changed words separated only by spaces, so a rewritten phrase reads as one highlight.
  const join = (arr, text) => arr.filter(([s, e]) => e > s).reduce((out, r) => {
    const last = out[out.length - 1];
    if (last && /^\s*$/.test(text.slice(last[1], r[0]))) last[1] = r[1]; else out.push([r[0], r[1]]);
    return out;
  }, []).map(([s, e]) => {
    while (s < e && /\s/.test(text[s])) s++;
    while (e > s && /\s/.test(text[e - 1])) e--;
    return [s, e];
  }).filter(([s, e]) => e > s);
  return { a: join(ra, a), b: join(rb, b) };
}
// Wrap plain-text ranges of a highlighted line in <span class="ic">, walking only its text nodes.
function wrapRanges(html, ranges) {
  if (!ranges || !ranges.length) return html;
  const ENT = /&(?:amp|lt|gt|quot|#x27|#39);|./gs;
  let pos = 0, ri = 0;
  return html.split(/(<[^>]+>)/).map(part => {
    if (part.startsWith('<') && part.endsWith('>') && part.length > 1 && !part.startsWith('&')) return part;
    let out = '', open = false;
    for (const m of part.matchAll(ENT)) {
      while (ri < ranges.length && pos >= ranges[ri][1]) ri++;
      const inside = ri < ranges.length && pos >= ranges[ri][0] && pos < ranges[ri][1];
      if (inside && !open) { out += '<span class="ic">'; open = true; }
      if (!inside && open) { out += '</span>'; open = false; }
      out += m[0]; pos++;
    }
    if (open) out += '</span>';
    return out;
  }).join('');
}
function markPairs(rows) {
  const n = rows.length / 2;
  for (let i = 0; i < n; i++) {
    const d = rows[i], a = rows[i + n];
    if (!d || !a || d.type !== 'del' || a.type !== 'add') return;
    const diff = wordDiff(d.text, a.text);
    if (!diff) continue;
    d.html = wrapRanges(d.html, diff.a); a.html = wrapRanges(a.html, diff.b);
  }
}
const rowId = (f, r) => r.type === 'del' ? `r${f.index}-O${r.oldNo}` : `r${f.index}-L${r.newNo}`;
function renderRows(f, rows, out) {
  for (const r of rows) {
    const id = rowId(f, r);
    const cls = [r.type, r.dim ? 'dim' : '', state.target === id ? 'target' : ''].join(' ');
    const tag = r.dim ? '<span class="tag">rename</span>' : '';
    out.push(`<tr class="${cls}" id="${id}"><td class="n">${r.type === 'add' ? '' : (r.oldNo ?? '')}</td><td class="n">${r.type === 'del' ? '' : (r.newNo ?? '')}</td><td class="m">${r.type === 'add' ? '+' : r.type === 'del' ? '−' : ''}</td><td class="c">${tag}${r.html}</td></tr>`);
  }
}
// Every import section folds to one row, changed or not: the file's import lines, with the blank lines between them.
function importSegments(f, rows) {
  const newSet = new Set(f.import_lines || []), oldSet = new Set(f.old_import_lines || []);
  const isImp = (r) => r.type === 'del' ? oldSet.has(r.oldNo) : newSet.has(r.newNo);
  const isBlank = (r) => !r.text.trim();
  const segs = [];
  let i = 0;
  while (i < rows.length) {
    const seg = [];
    if (isImp(rows[i])) {
      let j = i;
      while (j < rows.length && (isImp(rows[j]) || isBlank(rows[j]))) seg.push(rows[j++]);
      while (seg.length && isBlank(seg[seg.length - 1])) { seg.pop(); j--; }
      segs.push({ imports: true, rows: seg }); i = j;
    } else {
      let j = i;
      while (j < rows.length && !isImp(rows[j])) seg.push(rows[j++]);
      segs.push({ imports: false, rows: seg }); i = j;
    }
  }
  return segs;
}
const importsKey = (f, b) => `${f.path}#imports:${b.rows[0].newNo ?? b.rows[0].oldNo}`;
const importsOpen = (f, b) => state.show.import || state.expanded.has(importsKey(f, b)) || b.rows.some(r => r.hit);
function renderCode(f) {
  const rows = buildRows(f);
  let blocks = [];
  for (const seg of importSegments(f, rows)) blocks = blocks.concat(seg.imports ? [seg] : blocksFor(seg.rows));
  blocks = mergeCollapsed(f, blocks);
  const symStarts = new Map((f.symbols || []).map(s => [s.start, s]));
  const out = [];
  blocks.forEach((b, bi) => {
    if (b.imports) {
      const key = importsKey(f, b), n = b.rows.length;
      const adds = b.rows.filter(r => r.type === 'add').length, dels = b.rows.filter(r => r.type === 'del').length;
      const delta = adds || dels ? `<span class="counts"><span class="a">+${adds}</span> <span class="d">−${dels}</span></span>` : 'unchanged';
      if (!importsOpen(f, b)) { out.push(`<tr class="collapsed" data-run="${esc(key)}"><td colspan="4">▸ <span class="cls">imports</span> ${n} line${n > 1 ? 's' : ''} · ${delta}</td></tr>`); return; }
      out.push(`<tr class="runhead" data-run="${esc(key)}"><td colspan="4">▾ <span class="cls">imports</span> ${n} line${n > 1 ? 's' : ''} · ${delta}</td></tr>`);
      renderRows(f, b.rows, out);
      return;
    }
    if (b.group) {
      const n = b.group.reduce((a, r) => a + r.removed.length + r.added.length, 0);
      const keys = b.group.map(r => runKey(f, r)).join('|');
      out.push(`<tr class="collapsed" data-runs="${esc(keys)}"><td colspan="4">▸ <span class="cls">${esc(NOISE_LABEL[b.cls])}</span> ${n} lines in ${b.group.length} places${b.cls === 'rename' ? ` · <code>${esc(b.group[0].rename[0])}</code> → <code>${esc(b.group[0].rename[1])}</code>` : ''}</td></tr>`);
      return;
    }
    if (b.ctx) {
      const rs = b.rows;
      const visible = (nb) => nb && !nb.group && !isCollapsed(f, nb) && !(nb.imports && !importsOpen(f, nb));
      const leftVisible = bi > 0 && visible(blocks[bi - 1]);
      const rightVisible = bi < blocks.length - 1 && visible(blocks[bi + 1]);
      if (state.whole || (rs.length <= RADIUS * 2 + 2 && (leftVisible || rightVisible))) { renderRows(f, rs, out); return; }
      const head = leftVisible ? rs.slice(0, RADIUS) : [];
      const tail = rightVisible ? rs.slice(rs.length - RADIUS) : [];
      const mid = rs.slice(head.length, rs.length - tail.length);
      if (!mid.length) { renderRows(f, rs, out); return; }
      renderRows(f, head, out);
      const key = `${f.path}:${mid[0].newNo}`;
      if (state.unfolded.has(key)) renderRows(f, mid, out);
      else {
        const syms = mid.map(r => symStarts.get(r.newNo)).filter(Boolean);
        const hint = syms.length ? ` · ${syms.slice(0, 4).map(s => esc(s.short)).join(', ')}${syms.length > 4 ? ', …' : ''}` : '';
        out.push(`<tr class="fold" data-fold="${esc(key)}"><td colspan="4">⋯ ${mid.length} unchanged lines${hint}</td></tr>`);
      }
      renderRows(f, tail, out);
      return;
    }
    const r = b.run;
    if (isCollapsed(f, b)) { out.push(`<tr class="collapsed" data-run="${esc(runKey(f, r))}"><td colspan="4">▸ ${runSummary(r)}</td></tr>`); return; }
    if (r && NOISE.includes(r.class) && !(r.class === 'rename' && r.origin)) out.push(`<tr class="runhead" data-run="${esc(runKey(f, r))}"><td colspan="4">▾ ${runSummary(r)}</td></tr>`);
    if (r && showOriginHead(r)) out.push(`<tr class="runhead"><td colspan="4">rename origin · <code>${esc(r.rename[0])}</code> → <code>${esc(r.rename[1])}</code></td></tr>`);
    if (r && r.removed.length === r.added.length && b.rows.length === r.removed.length * 2) markPairs(b.rows);
    renderRows(f, b.rows, out);
  });
  return `<table class="diff"><colgroup><col style="width:38px"><col style="width:38px"><col style="width:16px"><col></colgroup>${out.join('')}</table>`;
}

// ---------- sidebar
function buildTree(files) {
  const root = { name: '', dirs: new Map(), files: [] };
  for (const f of files) {
    const parts = f.path.split('/'); let node = root;
    for (const part of parts.slice(0, -1)) {
      if (!node.dirs.has(part)) node.dirs.set(part, { name: part, dirs: new Map(), files: [] });
      node = node.dirs.get(part);
    }
    node.files.push(f);
  }
  // A folder holding only one subfolder folds into one row with it, like compact folders in an editor.
  const compact = (node, path) => {
    for (const [name, child] of [...node.dirs]) {
      let merged = child, label = name, full = path ? `${path}/${name}` : name;
      while (merged.dirs.size === 1 && merged.files.length === 0) {
        const [[n, c]] = [...merged.dirs]; label += '/' + n; full += '/' + n; merged = c;
      }
      node.dirs.delete(name); node.dirs.set(label, merged); merged.label = label; merged.full = full;
      compact(merged, full);
    }
  };
  compact(root, '');
  return root;
}
function countFiles(node) { let n = node.files.length; for (const c of node.dirs.values()) n += countFiles(c); return n; }
function allFolders(node, acc) { for (const d of node.dirs.values()) { acc.push(d.full); allFolders(d, acc); } return acc; }
// Background gradients paint the guide lines, so a row can span the full sidebar width.
function guides(depth) {
  if (!depth) return '';
  const img = [], pos = [], size = [];
  for (let k = 0; k < depth; k++) { img.push('linear-gradient(var(--line), var(--line))'); pos.push(`${17 + k * 19}px 0`); size.push('1px 100%'); }
  return `background-image:${img.join(',')};background-position:${pos.join(',')};background-size:${size.join(',')}`;
}
// Outline folder icons drawn inline, closed and open, so the page needs no icon font.
const FOLDER_CLOSED = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M5 4h4l3 3h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const FOLDER_OPEN = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M5 19l2.757-7.351A1 1 0 0 1 8.693 11H21a1 1 0 0 1 .986 1.164l-.996 5.211A2 2 0 0 1 19.026 19H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h4l3 3h7a2 2 0 0 1 2 2v2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
function renderTreeNode(node, depth, out) {
  const dirs = [...node.dirs.values()].sort((x, y) => x.label.localeCompare(y.label));
  for (const d of dirs) {
    const closed = state.closed.has(d.full);
    out.push(`<div class="row dir ${state.cursor === 'd:' + d.full ? 'cursor' : ''}" style="--d:${depth};${guides(depth)}" data-folder="${esc(d.full)}" title="${esc(d.full)}">
      <span class="ico folder">${closed ? FOLDER_CLOSED : FOLDER_OPEN}</span><span class="pill"><span class="nm">${esc(d.label)}/</span></span>${closed ? `<span class="cnt">${countFiles(d)}</span>` : ''}</div>`);
    if (!closed) renderTreeNode(d, depth + 1, out);
  }
  for (const f of [...node.files].sort((x, y) => x.base.localeCompare(y.base))) {
    const k = changeKind(f);
    const oldDir = f.old_path ? f.old_path.slice(0, f.old_path.lastIndexOf('/') + 1) : '';
    out.push(`<div class="row leaf ${state.view === f.path ? 'active' : ''} ${state.cursor === 'f:' + f.path ? 'cursor' : ''} ${isReviewed(f) ? 'reviewed' : ''}" style="--d:${depth};${guides(depth)}" data-path="${esc(f.path)}" title="${esc(f.path)}">
      <span class="ico"></span>
      <span class="pill st-${k} ${f.kind === 'test' ? 'test' : ''}" title="${KIND_LABEL[k]}">${k === 'R' ? `<s>${esc(f.old_path.split('/').pop())}</s>` : ''}<span class="nm">${esc(f.base)}</span></span>
      ${k === 'V' ? `<span class="from" title="${esc(f.old_path)}">← ${esc(oldDir)}</span>` : ''}
      ${state.q && hits(f) ? `<span class="cnt hits">${hits(f)}</span>` : ''}
      <span class="spacer"></span><span class="check" data-review="${esc(f.path)}" title="Mark reviewed">✓</span></div>`);
  }
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
  el.textContent = `${files.length} file${files.length === 1 ? '' : 's'} · ${state.hitIndex >= 0 ? `${state.hitIndex + 1} / ${n}` : `${n} line${n === 1 ? '' : 's'}`}`;
}
function focusBox(sel) { const i = document.querySelector(sel); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }
function renderSide() {
  const reviewed = FILES.filter(isReviewed).length;
  const h = `<div class="head">${location.protocol.startsWith('http') ? '<a class="back" href="/">← Pull requests</a>' : ''}<h1 data-go="overview">#${PR.number} ${esc(PR.title)}</h1>
    <div class="meta"><span>${FILES.length} files · ${reviewed} reviewed · <a href="${PR.url}" target="_blank">GitHub</a></span>
      <span class="themes">${THEMES.map(t => `<button data-theme="${t}" class="${state.theme === t ? 'on' : ''}">${t}</button>`).join('')}</span></div></div>
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

// ---------- main: the overview and then every visible file, one long page
// Enough markdown for a PR description: headings, bullets, paragraphs, links, bold, inline code.
function renderMarkdown(md) {
  const inline = (t) => esc(t)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank">$2</a>');
  const out = []; let para = [], list = null;
  const flush = () => {
    if (para.length) { out.push(`<p>${inline(para.join(' '))}</p>`); para = []; }
    if (list) { out.push(`<ul>${list.map(i => `<li>${inline(i)}</li>`).join('')}</ul>`); list = null; }
  };
  for (const raw of md.split('\n')) {
    const line = raw.trimEnd();
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    const b = line.match(/^\s*[-*]\s+(.*)$/);
    if (h) { flush(); const lvl = Math.min(h[1].length + 1, 4); out.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`); }
    else if (b) { if (para.length) flush(); (list = list || []).push(b[1]); }
    else if (!line.trim()) flush();
    else { if (list) flush(); para.push(line.trim()); }
  }
  flush();
  return out.join('');
}
// The old path from the last directory the two paths share, so only the changed part shows.
function renamedFrom(f) {
  if (!f.old_path || !f.new_path || f.old_path === f.new_path) return '';
  const o = f.old_path.split('/'), n = f.new_path.split('/');
  let i = 0;
  while (i < o.length - 1 && i < n.length - 1 && o[i] === n[i]) i++;
  const tail = o.slice(Math.max(0, i - 1)).join('/');
  return `<span class="dir" title="${esc(f.old_path)}">renamed from ${i > 1 ? '…/' : ''}${esc(tail)}</span>`;
}
function fileCounts(f) { return `<span class="a">+${f.stats.additions}</span> <span class="d">−${f.stats.deletions}</span>`; }
function renderOverview() {
  const t = D.totals, raw = Object.values(t).reduce((a, b) => a + b, 0);
  const sub = FILES.filter(f => !f.generated).reduce((a, f) => a + f.stats.substantive, 0);
  const impl = FILES.filter(f => f.kind === 'implementation'), tests = FILES.filter(f => f.kind === 'test');
  const reviewed = FILES.filter(isReviewed).length;
  let h = `<div class="page" id="overview"><h1>${esc(PR.title)}</h1>
    <div class="meta"><a href="${PR.url}" target="_blank">${esc(PR.repo)}#${PR.number}</a> · ${esc(PR.author)} · ${PR.commits} commits · <span class="mono">${PR.base.slice(0, 8)}…${PR.head.slice(0, 8)}</span></div>
    <div class="prbody">${renderMarkdown(PR.body || '')}</div>
    <div class="stats">
      <div class="stat"><b>${fmt(sub)}</b><span>lines to read</span></div>
      <div class="stat"><b>${fmt(raw - sub)}</b><span>lines of noise hidden (${raw ? Math.round(100 * (raw - sub) / raw) : 0}% of ${fmt(raw)})</span></div>
      <div class="stat"><b>${impl.length} / ${tests.length}</b><span>implementation / test files</span></div>
      <div class="stat"><b>${reviewed} / ${FILES.length}</b><span>files reviewed</span></div>
    </div>
    <div class="chips">${NOISE.filter(k => t[k]).map(k => `<span class="chip ${state.show[k] ? 'on' : ''}" data-show="${k}">${state.show[k] ? 'showing' : 'hiding'} ${NOISE_LABEL[k]} <b>${fmt(t[k])}</b></span>`).join('')}</div>
    <p class="hint" style="margin-top:6px">Import sections are folded to one row in every file, changed or not. Other lines are collapsed only when the classifier can prove what they are: the removed line becomes the added line exactly by applying a rename that recurs elsewhere in the PR.</p>`;
  if (D.renames.length) {
    h += `<h2>Renames</h2><div class="scrollx"><table class="plain"><tr><th>From</th><th>To</th><th>Blocks</th><th>Files</th><th>Origin</th></tr>`;
    for (const r of D.renames) {
      const o = r.origin;
      h += `<tr><td class="mono">${esc(r.from)}</td><td class="mono">${esc(r.to)}</td><td>${r.runs}</td><td>${r.files.length}</td>
        <td>${o ? `<a href="#" data-path="${esc(o.path)}" data-line="${o.line}" class="mono">${esc(o.path.split('/').pop())}:${o.line}</a> <span class="hint">${esc(o.text.slice(0, 60))}</span>` : '<span class="hint">not in this diff</span>'}</td></tr>`;
    }
    h += `</table></div>`;
  }
  return h + `</div>`;
}
const sectionOpen = (f) => state.openOverride.has(f.path) ? state.openOverride.get(f.path) : !isReviewed(f);
function renderFile(f) {
  const ck = changeKind(f), open = sectionOpen(f);
  return `<section class="filesec ${open ? '' : 'closed'}" id="sec-${f.index}" data-path="${esc(f.path)}"><div class="fhead"><div class="top" data-toggle="${esc(f.path)}">
      <span class="chev ${open ? 'open' : ''}" title="${open ? 'Collapse' : 'Expand'}"><svg viewBox="0 0 16 16" width="18" height="18" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
      <span class="badge ${ck}" title="${KIND_LABEL[ck]}">${ck === 'V' ? '→' : ck}</span>
      <span class="path" title="${esc(f.path)}"><b>${esc(f.base)}</b><span class="dir">${esc(f.dir)}</span>${renamedFrom(f)}</span>
      <span class="kind">${esc(f.kind)}</span><span class="counts">${fileCounts(f)}</span>
      <button data-review="${esc(f.path)}" class="${isReviewed(f) ? 'on' : ''}">${isReviewed(f) ? '✓ Reviewed' : 'Mark reviewed'}</button>
    </div></div>
    ${open ? `<div class="code">${f.runs.length ? renderCode(f) : '<div class="empty">No changes</div>'}</div>` : ''}</section>`;
}

function render() {
  renderSide();
  const main = document.getElementById('main');
  const scroll = main.scrollTop;
  // Keep the place inside the current file, so the view stays on the same line when the file list changes.
  const curFile = BY_PATH[state.view];
  const curSec = curFile && document.getElementById(`sec-${curFile.index}`);
  const within = curSec ? scroll - curSec.offsetTop : null;
  main.innerHTML = renderOverview() + visibleFiles().map(renderFile).join('');
  if (state.target) {
    const el = document.getElementById(state.target);
    if (el) { el.scrollIntoView({ block: 'center' }); setTimeout(() => el.classList.remove('target'), 2500); }
    else main.scrollTop = scroll;
    state.target = null;
  } else {
    const sec = curFile && document.getElementById(`sec-${curFile.index}`);
    main.scrollTop = sec && within != null ? sec.offsetTop + Math.max(0, within) : scroll;
  }
  trackScroll();
}

// The sidebar follows the file under the top of the main pane.
let scrollPending = false;
function trackScroll() {
  const main = document.getElementById('main');
  const top = main.getBoundingClientRect().top + 60;
  let current = 'overview';
  for (const sec of main.querySelectorAll('.filesec')) { if (sec.getBoundingClientRect().top <= top) current = sec.dataset.path; else break; }
  setActive(current);
}
document.getElementById('main').addEventListener('scroll', () => {
  if (scrollPending) return;
  scrollPending = true;
  requestAnimationFrame(() => { scrollPending = false; trackScroll(); });
});

function scrollToFile(path, line) {
  const main = document.getElementById('main');
  if (path === 'overview') { main.scrollTop = 0; setActive('overview'); return; }
  const f = BY_PATH[path]; if (!f) return;
  const sec = document.getElementById(`sec-${f.index}`); if (!sec) return;
  main.scrollTop = sec.offsetTop - 16;
  setActive(path);
}
function go(path, line) {
  const f = BY_PATH[path];
  if (!f) { scrollToFile('overview'); return; }
  let dirty = false;
  if (!visibleFiles().includes(f)) {
    if (!kindVisible(f)) state.filter = 'all';
    if (noiseOnly(f)) state.showNoiseOnly = true;
    if (f.generated) state.showGenerated = true;
    dirty = true;
  }
  if (!sectionOpen(f)) { state.openOverride.set(f.path, true); dirty = true; }
  if (dirty) render();
  if (line == null) { scrollToFile(path); return; }
  const explicit = typeof line === 'object' ? line : null;
  const anchor = explicit ? explicit.anchor : line;
  state.target = explicit ? `r${f.index}-O${explicit.oldNo}` : (f.content_side === 'old' ? `r${f.index}-O${line}` : `r${f.index}-L${line}`);
  const blocks = blocksFor(buildRows(f));
  for (const b of blocks) if (b.ctx && b.rows.some(r => r.newNo === anchor)) { const mid = b.rows.slice(RADIUS, b.rows.length - RADIUS); if (mid.length) state.unfolded.add(`${f.path}:${mid[0].newNo}`); }
  render();
  setActive(path);
}
function applyTheme() {
  if (state.theme === 'auto') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = state.theme;
  syncWindowTheme(state.theme);
}
// In the desktop app the native window follows the page, so the title bar and traffic lights match.
function syncWindowTheme(theme) {
  const win = window.__TAURI__ && window.__TAURI__.window;
  if (!win) return;
  win.getCurrentWindow().setTheme(theme === 'auto' ? null : theme === 'dark' ? 'dark' : 'light').catch(() => {});
}

// ---------- events
document.addEventListener('click', (e) => {
  const t = e.target.closest('[data-go],[data-path],[data-toggle],[data-filter],[data-tree],[data-noiseonly],[data-generated],[data-folder],[data-show],[data-whole],[data-review],[data-fold],[data-runs],[data-run],[data-theme]');
  if (!t) return;
  if (t.dataset.review !== undefined) { e.preventDefault(); e.stopPropagation(); const f = BY_PATH[t.dataset.review]; setReviewed(f, !isReviewed(f)); state.openOverride.delete(f.path); render(); return; }
  if (t.dataset.toggle !== undefined && !e.target.closest('button, a')) { const f = BY_PATH[t.dataset.toggle]; state.openOverride.set(f.path, !sectionOpen(f)); render(); return; }
  if (t.dataset.go) { scrollToFile('overview'); return; }
  if (t.dataset.path) {
    e.preventDefault();
    if (t.closest('#side .tree')) { state.cursor = 'f:' + t.dataset.path; document.querySelector('#side .list').focus({ preventScroll: true }); }
    let line = t.dataset.line ? +t.dataset.line : null;
    if (!line && state.q && t.closest('#side')) line = firstHitLine(BY_PATH[t.dataset.path]);
    go(t.dataset.path, line);
    return;
  }
  if (t.dataset.filter) { state.filter = t.dataset.filter; render(); return; }
  if (t.dataset.theme) { state.theme = t.dataset.theme; lsSet('dr:theme', state.theme); applyTheme(); renderSide(); return; }
  if (t.dataset.generated !== undefined) { e.preventDefault(); state.showGenerated = t.dataset.generated === '1'; lsSet('dr:generated', state.showGenerated ? '1' : '0'); render(); return; }
  if (t.dataset.noiseonly !== undefined) { e.preventDefault(); state.showNoiseOnly = t.dataset.noiseonly === '1'; lsSet('dr:noise-only', state.showNoiseOnly ? '1' : '0'); render(); return; }
  if (t.dataset.tree) { state.closed = new Set(t.dataset.tree === 'collapse' ? allFolders(buildTree(visibleFiles()), []) : []); renderSide(); return; }
  if (t.dataset.folder !== undefined) { const k = t.dataset.folder; state.cursor = 'd:' + k; state.closed.has(k) ? state.closed.delete(k) : state.closed.add(k); document.querySelector('#side .list').focus({ preventScroll: true }); renderSide(); return; }
  if (t.dataset.show) { state.show[t.dataset.show] = !state.show[t.dataset.show]; render(); return; }
  if (t.dataset.whole !== undefined) { state.whole = t.dataset.whole === '1'; render(); return; }
  if (t.dataset.fold) { state.unfolded.add(t.dataset.fold); render(); return; }
  if (t.dataset.runs) { for (const k of t.dataset.runs.split('|')) state.expanded.add(k); render(); return; }
  if (t.dataset.run) { const k = t.dataset.run; state.expanded.has(k) ? state.expanded.delete(k) : state.expanded.add(k); render(); return; }
});
function firstHitLine(f) {
  const q = state.q.toLowerCase();
  if (!f || !q) return null;
  for (const h of f.hunks) for (const [tag, o, n, text] of h.lines) {
    if (tag !== ' ' && text.toLowerCase().includes(q)) {
      if (f.content_side === 'old') return o;
      return n != null ? n : { oldNo: o, anchor: anchorAfter(h, o) };
    }
  }
  return null;
}
document.addEventListener('input', (e) => {
  const box = e.target;
  if (!box.classList.contains('search')) return;
  state.q = box.value.trim(); state.hitIndex = -1;
  render();
});
function treeRows() { return [...document.querySelectorAll('#side .tree .row')]; }
function rowKey(el) { return el.dataset.folder !== undefined ? 'd:' + el.dataset.folder : 'f:' + el.dataset.path; }
function setCursor(el) {
  state.cursor = rowKey(el);
  for (const x of document.querySelectorAll('#side .row.cursor')) x.classList.remove('cursor');
  el.classList.add('cursor');
  const list = document.querySelector('#side .list'), a = el.getBoundingClientRect(), l = list.getBoundingClientRect();
  if (a.top < l.top || a.bottom > l.bottom) el.scrollIntoView({ block: 'nearest' });
  if (el.dataset.path) scrollToFile(el.dataset.path);
}
function treeKey(e) {
  const rows = treeRows(); if (!rows.length) return false;
  let i = rows.findIndex(r => rowKey(r) === state.cursor);
  if (i < 0) i = rows.findIndex(r => r.dataset.path === state.view);
  const cur = rows[i];
  if (e.key === 'ArrowDown') { setCursor(rows[Math.min(i + 1, rows.length - 1)]); return true; }
  if (e.key === 'ArrowUp') { setCursor(rows[Math.max(i - 1, 0)]); return true; }
  if (!cur) return false;
  const folder = cur.dataset.folder;
  if (e.key === 'ArrowRight') {
    if (folder !== undefined && state.closed.has(folder)) { state.closed.delete(folder); renderSide(); }
    else if (folder !== undefined && rows[i + 1]) setCursor(rows[i + 1]);
    return true;
  }
  if (e.key === 'ArrowLeft') {
    if (folder !== undefined && !state.closed.has(folder)) { state.closed.add(folder); renderSide(); return true; }
    const depth = +cur.style.getPropertyValue('--d');
    for (let j = i - 1; j >= 0; j--) if (rows[j].dataset.folder !== undefined && +rows[j].style.getPropertyValue('--d') < depth) { setCursor(rows[j]); break; }
    return true;
  }
  if (e.key === 'Enter' || e.key === ' ') {
    if (folder !== undefined) { state.closed.has(folder) ? state.closed.delete(folder) : state.closed.add(folder); renderSide(); }
    else scrollToFile(cur.dataset.path);
    return true;
  }
  return false;
}
document.addEventListener('keydown', (e) => {
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
  if (e.key === 'j') { const n = files[i + 1] || files[0]; if (n) scrollToFile(n.path); }
  else if (e.key === 'k') { if (i > 0) scrollToFile(files[i - 1].path); else scrollToFile('overview'); }
  else if (e.key === 'o') scrollToFile('overview');
  else if (e.key === 'w') { state.whole = !state.whole; render(); }
  else if (e.key === 'r' && BY_PATH[state.view]) { const f = BY_PATH[state.view]; setReviewed(f, !isReviewed(f)); state.openOverride.delete(f.path); render(); }
  else if (e.key === 'c' && BY_PATH[state.view]) { const f = BY_PATH[state.view]; state.openOverride.set(f.path, !sectionOpen(f)); render(); }
});
window.addEventListener('hashchange', () => {
  const h = decodeURIComponent(location.hash.slice(1));
  const m = h.match(/^(.*?)(?::(\d+))?$/);
  if (!h) { if (state.view !== 'overview') scrollToFile('overview'); return; }
  if (BY_PATH[m[1]] && (state.view !== m[1] || m[2])) go(m[1], m[2] ? +m[2] : null);
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
  const h = decodeURIComponent(location.hash.slice(1));
  render();
  if (h) { const m = h.match(/^(.*?)(?::(\d+))?$/); if (BY_PATH[m[1]]) go(m[1], m[2] ? +m[2] : null); }
})();
