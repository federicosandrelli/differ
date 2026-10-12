// Which files the page shows, in which order, and where the search hits are.

// All the file's change blocks fold: imports, rename fallout, whitespace or moved code.
function noiseOnly(f) {
  return !f.generated && f.stats.substantive === 0 && f.stats.raw > 0;
}
function kindVisible(f) { return state.filter === 'all' || (state.filter === 'test' ? f.kind === 'test' : f.kind !== 'test'); }
function fileShown(f) {
  return kindVisible(f) && (!state.q || hits(f) > 0) && (state.showNoiseOnly || state.q || !noiseOnly(f)) &&
    (state.showGenerated || !f.generated);
}

// The changed hunk lines holding the query, as [hunk, line] pairs, in file order.
function* hitLines(f) {
  const q = state.q.toLowerCase();
  if (!q) return;
  for (const h of f.hunks) for (const l of h.lines) if (l[0] !== ' ' && l[3].toLowerCase().includes(q)) yield [h, l];
}
// Changed lines of the file holding the query; counted once per query.
const hitCache = { q: '', counts: new Map() };
function hits(f) {
  const q = state.q.toLowerCase();
  if (!q) return 0;
  if (hitCache.q !== q) { hitCache.q = q; hitCache.counts.clear(); }
  let n = hitCache.counts.get(f.path);
  if (n === undefined) {
    n = 0;
    for (const _ of hitLines(f)) n++;
    hitCache.counts.set(f.path, n);
  }
  return n;
}

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
const byLabel = (x, y) => x.label.localeCompare(y.label);
const byBase = (x, y) => x.base.localeCompare(y.base);
// The order the tree shows: in every folder, subfolders first, then files.
function treeOrder(node, out) {
  for (const d of [...node.dirs.values()].sort(byLabel)) treeOrder(d, out);
  for (const f of [...node.files].sort(byBase)) out.push(f);
  return out;
}

// The shown files in tree order; recomputed only when the state that selects them changes.
const visibleCache = { key: null, files: [] };
function visibleFiles() {
  const key = [state.filter, state.q, state.showNoiseOnly, state.showGenerated].join('\u0000');
  if (visibleCache.key !== key) {
    visibleCache.key = key;
    visibleCache.files = treeOrder(buildTree(FILES.filter(fileShown)), []);
  }
  return visibleCache.files;
}

// ---------- search hits, for Enter to step through: every hit as { path, key } of its row
function allHits() {
  const out = [];
  for (const f of visibleFiles()) for (const [, [, o, n]] of hitLines(f)) out.push({ path: f.path, key: hunkKey(f, o, n) });
  return out;
}
function firstHitKey(f) {
  if (!f) return null;
  for (const [, [, o, n]] of hitLines(f)) return hunkKey(f, o, n);
  return null;
}
// Wrap every occurrence of the query in <mark>, in the text of an HTML fragment only.
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
