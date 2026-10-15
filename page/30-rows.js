// The view of one file: the whole new file with the removed lines interleaved where they were, grouped into
// blocks of context and change runs, with every fold decided. fileBlocks gives the diff table what to draw,
// reveal opens whatever hides a row, and toggleFold answers a click on a fold row; the fold keys stay in here.

// Plain rows skip the syntax highlighting and have no HTML: they decide the folds but are never drawn.
function buildRows(f, plain) {
  const runOfOld = new Map(), runOfNew = new Map(), dimOld = new Set(), dimNew = new Set();
  for (const r of f.runs) {
    r.removed.forEach(n => runOfOld.set(n, r)); r.added.forEach(n => runOfNew.set(n, r));
    if (r.class === 'rename' && r.origin) continue;
    r.pair_rename.forEach((flag, i) => { if (flag) { dimOld.add(r.removed[i]); dimNew.add(r.added[i]); } });
  }
  const hl = plain ? [] : highlightLines(f);
  const raw = contentLines(f);
  const rows = [];
  if (f.content_side === 'old') {
    raw.forEach((t, i) => rows.push({ type: 'del', oldNo: i + 1, html: hl && hl[i], text: t, run: runOfOld.get(i + 1) }));
    return plain ? attachThreads(f, rows) : markHits(attachThreads(f, rows));
  }
  const removedBefore = new Map();
  for (const h of f.hunks) {
    const L = h.lines;
    for (let i = 0; i < L.length; i++) {
      const [tag, o, , t] = L[i]; if (tag !== '-') continue;
      let anchor = null;
      for (let j = i + 1; j < L.length; j++) if (L[j][2] != null) { anchor = L[j][2]; break; }
      if (anchor == null) anchor = h.new_start + L.filter(x => x[2] != null).length;
      (removedBefore.get(anchor) || removedBefore.set(anchor, []).get(anchor)).push({ type: 'del', oldNo: o, html: plain ? undefined : hlOne(t, f.lang), text: t, run: runOfOld.get(o), dim: dimOld.has(o) });
    }
  }
  for (let i = 1; i <= raw.length + 1; i++) {
    for (const d of removedBefore.get(i) || []) rows.push(d);
    if (i <= raw.length) rows.push({ type: runOfNew.has(i) ? 'add' : 'ctx', newNo: i, html: hl && hl[i - 1], text: raw[i - 1], run: runOfNew.get(i), dim: dimNew.has(i) });
  }
  return plain ? attachThreads(f, rows) : markHits(attachThreads(f, rows));
}
function attachThreads(f, rows) {
  const entry = THREADS.byFile.get(f.path), c = state.compose && state.compose.path === f.path ? state.compose : null;
  if (!entry && !c) return rows;
  for (const r of rows) {
    if (entry) { const ts = entry.rows.get(rowKeyOf(r)); if (ts) r.threads = ts; }
    if (!c) continue;
    const n = c.side === 'RIGHT' ? (r.type !== 'del' ? r.newNo : null) : (r.type === 'del' ? r.oldNo : null);
    if (n != null && n >= c.startLine && n <= c.line) { r.sel = true; if (n === c.line) r.compose = true; }
  }
  if (c) markBetween(rows, rows.map(r => !!r.sel)).forEach((b, i) => { rows[i].between = b; });
  return rows;
}
// Which rows lie between the first and last selected rows without being selected themselves: the other side's
// lines inside a range.
function markBetween(rows, sel) {
  const first = sel.indexOf(true), last = sel.lastIndexOf(true);
  return rows.map((_, i) => !sel[i] && i > first && i < last);
}
// A row that must stay in view whatever folds around it: a thread, or the comment being written.
const pinned = (r) => r.threads || r.compose;
function markHits(rows) {
  const q = caseFold(state.q);
  if (!q) return rows;
  for (const r of rows) if (caseFold(r.text).includes(q) && r.html !== undefined) r.html = markHtml(r.html, state.q);
  return rows;
}
function blocksFor(rows) {
  const blocks = [];
  for (const r of rows) {
    const last = blocks[blocks.length - 1];
    if (r.type === 'ctx' && !pinned(r)) { if (last && last.ctx) last.rows.push(r); else blocks.push({ ctx: true, rows: [r] }); }
    else if (r.run && last && last.run === r.run) last.rows.push(r);
    else blocks.push({ run: r.run || null, rows: [r] });
  }
  return blocks;
}

// ---------- folds
// A fold id names a fold row on the page; what opening it does is kept here, so the page only passes ids back.
const FOLDS = new Map();
function fold(id, open, toggle) { FOLDS.set(id, { open, toggle: toggle || open }); return id; }
function toggleFold(id) { const x = FOLDS.get(id); if (x) x.toggle(); }
const toggleIn = (set, key) => () => { set.has(key) ? set.delete(key) : set.add(key); };

const runKey = (f, r) => `${f.path}#${r.index}`;
// A noise run folds; a rename run that holds the rename's origin does not.
const isNoise = (r) => !!r && NOISE.includes(r.class) && !(r.class === 'rename' && r.origin);
// A block of folded noise stays open while a thread, the comment being written, or the reader's choice needs it.
function isCollapsed(f, b) {
  const r = b.run;
  if (!isNoise(r)) return false;
  if (b.rows.some(pinned)) return false;
  if (state.show[r.class]) return false;
  return !state.expanded.has(runKey(f, r));
}

// Several collapsed blocks of one class with only short context between them read as one thing (an import
// section), so they fold into one row that expands together.
function mergeCollapsed(f, blocks) {
  const importSet = new Set(f.import_lines);
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
      if (c.ctx && nextSame && (c.rows.length <= RADIUS * 2 + 2 || (cls === 'import' && c.rows.every(r => importSet.has(r.newNo) || !r.text.trim())))) { j++; continue; }
      break;
    }
    merged.push(group.length > 1 ? { group, cls, rows: blocks.slice(i, lastRun + 1).flatMap(x => x.rows) } : b);
    i = lastRun + 1;
  }
  return merged;
}

// Every import section folds to one row, changed or not: the file's import lines, with the blank lines between them.
function importSegments(f, rows) {
  const newSet = new Set(f.import_lines), oldSet = new Set(f.old_import_lines);
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
const importsOpen = (f, b) => state.show.import || state.expanded.has(importsKey(f, b)) || b.rows.some(pinned);

// The first body line of the innermost class, function or module-level call around each change run that adds
// lines and is not noise, by the line the symbol starts on.
function enclosingBodies(f) {
  const bodies = new Map();
  for (const r of f.runs) {
    if (isNoise(r) || !r.added.length) continue;
    const first = r.added[0];
    const around = f.symbols.filter(s => s.start < first && first <= s.end);
    if (!around.length) continue;
    const inner = around.reduce((a, s) => s.start > a.start ? s : a);
    bodies.set(inner.start, inner.body);
  }
  return bodies;
}

// Context rows that show headLength rows at the start and tailLength rows at the end and fold the middle, unless
// the reader opened it or the middle is shorter than minFold. A toggle fold also closes again.
function contextPart(f, rs, headLength, tailLength, register, { minFold = 1, toggle = false } = {}) {
  const head = rs.slice(0, headLength), tail = rs.slice(Math.max(headLength, rs.length - tailLength));
  const mid = rs.slice(head.length, rs.length - tail.length);
  if (mid.length < minFold) return { ctx: true, rows: rs, head: rs, mid: [], tail: [], fold: null, open: true };
  const key = `${f.path}:${mid[0].newNo}`;
  const fold = register(key, () => state.unfolded.add(key), toggle ? toggleIn(state.unfolded, key) : undefined);
  return { ctx: true, rows: rs, head, mid, tail, fold, open: state.unfolded.has(key) };
}

// A context block shows RADIUS lines next to each visible neighbour and folds the middle. When the block holds the
// start of the class, function or call around the next change, the last such start in the block, it splits in
// two: the first part shows the lines of that start up to its body, and the second folds the body lines into an
// inline fold, short of the RADIUS lines next to each visible neighbour.
function contextBlock(f, blocks, bi, register, bodies) {
  const rs = blocks[bi].rows;
  const visible = (nb) => nb && !nb.group && !isCollapsed(f, nb) && !(nb.imports && !importsOpen(f, nb));
  const leftVisible = bi > 0 && visible(blocks[bi - 1]);
  const rightVisible = bi < blocks.length - 1 && visible(blocks[bi + 1]);
  if (rs.length <= RADIUS * 2 + 2 && (leftVisible || rightVisible)) return [contextPart(f, rs, rs.length, 0, register)];
  const head = leftVisible ? RADIUS : 0, tail = rightVisible ? RADIUS : 0;
  const k = rs.findLastIndex(r => bodies.has(r.newNo));
  if (k < 0) return [contextPart(f, rs, head, tail, register)];
  const body = bodies.get(rs[k].newNo);
  let end = rs.findIndex((r, i) => i > k && r.newNo >= body);
  if (end < 0) end = rs.length;
  const rest = rs.slice(end);
  const above = contextPart(f, rs.slice(0, end), head, Math.max(end - k, tail - rest.length), register, { minFold: 3 });
  if (!rest.length) return [above];
  const inner = contextPart(f, rest, Math.max(0, head - end), tail, register, { minFold: 3, toggle: true });
  return [above, inner.fold ? { ...inner, inline: true } : inner];
}

// The file as the blocks the diff table draws, every fold decided. A block has its rows, the id of the fold
// that hides some of them (fold, null when nothing folds) and whether that fold is open:
//   { imports, rows }                 an import section, shown when open
//   { group, cls, rows }              noise runs folded together, always closed
//   { ctx, rows, head, mid, tail }    context: head and tail show, mid when open; an inline one keeps its fold
//                                     row when open
//   { run, rows }                     a change run; a noise run folds, shown when open
// Blocks of plain rows leave their folds out of FOLDS, whose entries would keep the rows alive.
function fileBlocks(f, plain) {
  const rows = buildRows(f, plain), register = plain ? (id) => id : fold, bodies = enclosingBodies(f);
  let blocks = [];
  for (const seg of importSegments(f, rows)) blocks = blocks.concat(seg.imports ? [seg] : blocksFor(seg.rows));
  blocks = mergeCollapsed(f, blocks);
  return blocks.flatMap((b, bi) => {
    if (b.imports) {
      const k = importsKey(f, b);
      return { ...b, fold: register(k, () => state.expanded.add(k), toggleIn(state.expanded, k)), open: importsOpen(f, b) };
    }
    if (b.group) {
      const keys = b.group.map(r => runKey(f, r));
      return { ...b, fold: register(`${f.path}#runs:${b.group.map(r => r.index).join(',')}`, () => keys.forEach(k => state.expanded.add(k))), open: false };
    }
    if (b.ctx) return contextBlock(f, blocks, bi, register, bodies);
    if (!isNoise(b.run)) return { ...b, fold: null, open: true };
    const k = runKey(f, b.run);
    return { ...b, fold: register(k, () => state.expanded.add(k), toggleIn(state.expanded, k)), open: !isCollapsed(f, b) };
  });
}
const hiddenRows = (b) => b.open ? [] : b.ctx ? b.mid : b.rows;
// The rows of the file the diff table draws when the file's section is open, in order, as plain rows.
function shownRows(f) {
  if (!f.runs.length) return [];
  return fileBlocks(f, true).flatMap(b => b.open ? b.rows : b.ctx ? [...b.head, ...b.tail] : []);
}

// Open whatever hides the row with this key, so the next render shows it; false when the file has no such row.
// Opening one fold can uncover another (a grouped run's context folds on its own), so it looks again.
function reveal(f, key) {
  const has = (rows) => rows.some(r => rowKeyOf(r) === key);
  for (let pass = 0; pass < 4; pass++) {
    const b = fileBlocks(f).find(b => has(b.rows));
    if (!b) return false;
    if (!has(hiddenRows(b))) return true;
    FOLDS.get(b.fold).open();
  }
  return true;
}

// The state the file's folds depend on, for a cache key.
function foldKey(f) {
  const runs = f.path + '#', folds = f.path + ':', c = state.compose && state.compose.path === f.path ? state.compose : null;
  const threads = THREADS.byFile.get(f.path);
  return JSON.stringify([state.show, [...state.expanded].filter(k => k.startsWith(runs)), [...state.unfolded].filter(k => k.startsWith(folds)),
    threads ? [...threads.rows.keys()] : [], c && [c.side, c.startLine, c.line]]);
}
// The state the file's blocks depend on, for a cache key: the folds, and the search marks in the rows.
const viewKey = (f) => [state.q, foldKey(f)];

// ---------- word-level differences between a removed line and its added counterpart
const WORD_DIFF_MAX = 1000;
// Changed ranges of the two lines, as plain-text offsets, or null when the lines share too little or either is
// longer than WORD_DIFF_MAX characters: the comparison's table grows with the product of the two lengths.
function wordDiff(a, b) {
  if (a.length > WORD_DIFF_MAX || b.length > WORD_DIFF_MAX) return null;
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
// Wrap plain-text ranges of a highlighted line in the open and close tags, walking only its text nodes. A
// range that crosses a tag is closed before the tag and reopened after it, so the HTML stays well formed. A whole
// range is one element instead: the tags open where it starts or ends are closed before that point and reopened
// after it.
function wrapRanges(html, ranges, open = '<span class="ic">', close = '</span>', whole = false) {
  if (!ranges || !ranges.length) return html;
  const ENT = /&(?:amp|lt|gt|quot|#x27|#39);|./gs;
  const tags = [];
  const around = (tag) => whole ? tags.map(t => `</${/^<(\w+)/.exec(t)[1]}>`).reverse().join('') + tag + tags.join('') : tag;
  let pos = 0, ri = 0, wrapped = false, out = '';
  for (const part of html.split(/(<[^>]+>)/)) {
    if (part.startsWith('<') && part.endsWith('>') && part.length > 1) {
      if (wrapped && !whole) { out += close; wrapped = false; }
      if (whole) { if (part.startsWith('</')) tags.pop(); else tags.push(part); }
      out += part;
      continue;
    }
    for (const m of part.matchAll(ENT)) {
      while (ri < ranges.length && pos >= ranges[ri][1]) ri++;
      const inside = ri < ranges.length && pos >= ranges[ri][0] && pos < ranges[ri][1];
      if (inside !== wrapped) { out += around(inside ? open : close); wrapped = inside; }
      out += m[0]; pos++;
    }
  }
  return wrapped ? out + around(close) : out;
}
// Mark the word changes in a run's rows, which hold its removed lines followed by its added ones.
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
