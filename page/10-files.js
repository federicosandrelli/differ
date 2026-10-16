// Which files the page shows, in which order, and where the search hits are.

// Whether the file type button `filter` lists f.
function ofKind(f, filter) { return filter === 'all' || (filter === 'test' ? f.kind === 'test' : f.kind !== 'test'); }
function kindVisible(f) { return ofKind(f, state.filter); }
// The files the tree lists: the search leaves them all in place.
function fileListed(f) {
  return kindVisible(f) && (state.showNoiseOnly || !f.noise_only) && (state.showGenerated || !f.generated);
}
// While searching, the page shows the files with a hit in their view. A closed section counts its view too, so
// closing a section keeps its file on the page.
function fileShown(f) { return fileListed(f) && (!state.q || viewHits(f).length > 0); }

// The keys of the rows the file's view shows that hold the query, in file order, whether its section is open
// or not. The rows are kept per fold state and searched once per query.
const hitCache = new Map();
function viewHits(f) {
  const q = caseFold(state.q);
  if (!q) return [];
  const key = foldKey(f);
  let c = hitCache.get(f.path);
  if (!c || c.key !== key) {
    c = { key, rows: shownRows(f).map(r => [rowKeyOf(r), caseFold(r.text)]), q: null, hits: [] };
    hitCache.set(f.path, c);
  }
  if (c.q !== q) { c.q = q; c.hits = c.rows.filter(([, t]) => t.includes(q)).map(([k]) => k); }
  return c.hits;
}
// The number of rows of the file the reader can see that hold the query: 0 while its section is closed.
const hits = (f) => sectionOpen(f) ? viewHits(f).length : 0;

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
  const folds = state.q ? FILES.map(foldKey).join('\u0000') : '';
  const key = [state.filter, state.q, state.showNoiseOnly, state.showGenerated, folds].join('\u0000');
  if (visibleCache.key !== key) {
    visibleCache.key = key;
    visibleCache.files = treeOrder(buildTree(FILES.filter(fileShown)), []);
  }
  return visibleCache.files;
}
// The file when it shows, or else the nearest shown file before it in tree order; 'overview' when there is none.
function shownAtOrBefore(path) {
  const all = treeOrder(buildTree(FILES), []), shown = new Set(visibleFiles());
  for (let i = all.indexOf(BY_PATH[path]); i >= 0; i--) if (shown.has(all[i])) return all[i].path;
  return 'overview';
}

// ---------- search hits, for Enter to step through: every hit the reader can see as { path, key } of its row
function allHits() {
  const out = [];
  for (const f of visibleFiles()) if (sectionOpen(f)) for (const key of viewHits(f)) out.push({ path: f.path, key });
  return out;
}
// The position of the hit Enter last went to, or -1 when it is no longer among the hits.
const hitPosition = (all) => state.hit ? all.findIndex(h => h.path === state.hit.path && h.key === state.hit.key) : -1;
const firstHitKey = (f) => f && viewHits(f)[0] || null;
