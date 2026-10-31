// The page's data, state and small shared helpers. Every other module reads from here.
const D = JSON.parse(document.getElementById('data').textContent);
const PR = D.pr;
const FILES = D.files;
const BY_PATH = Object.fromEntries(FILES.map(f => [f.path, f]));
const NOISE_LABEL = { import: 'imports', rename: 'rename fallout', whitespace: 'whitespace', moved: 'moved code' };
const KIND_LABEL = { A: 'added', M: 'modified', D: 'deleted', R: 'renamed', V: 'moved' };
const RADIUS = 4;
const CHIP_LINES = 2;
// Comments live on GitHub and change independently of the page, so they load after it, from the server.
const SERVED = location.protocol.startsWith('http');
const API = SERVED ? `/api/${PR.repo}/${PR.number}` : null;

FILES.forEach((f, i) => {
  f.index = i;
  f.base = f.path.split('/').pop();
  f.dir = f.path.slice(0, f.path.length - f.base.length);
});

const state = {
  view: 'overview', filter: 'implementation', closed: new Set(), q: '', fileSearch: false, hit: null,
  showNoiseOnly: false, showGenerated: false, cursor: null, openOverride: new Map(),
  expanded: new Set(), unfolded: new Set(),
  compose: null, drafts: new Map(), replyOpen: new Map(), openThreads: new Set(), showOutdated: new Set(),
  reviewPanel: false, reviewText: '', convText: '', busy: false, focus: null,
  // The merge panel, its method menu, the reader's choice to show or hide its checks (null: the default), and the
  // commit message the reader typed for each merge method.
  mergePanel: false, mergeMenu: false, checksOpen: null, mergeText: {},
};

const LS = (k) => `dr:${PR.head.slice(0, 12)}:${k}`;
const lsGet = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
const lsSet = (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch {} };
const isReviewed = (f) => lsGet(LS(f.path)) === '1';
const setReviewed = (f, v) => lsSet(LS(f.path), v ? '1' : null);
const sectionOpen = (f) => state.openOverride.has(f.path) ? state.openOverride.get(f.path) : !isReviewed(f);

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
// highlight.js escapes like esc and also turns ' into &#x27;.
const unesc = (s) => s.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#(?:x27|39);/g, "'").replace(/&amp;/g, '&');
// Lowercase without changing the length. toLowerCase turns İ alone into two characters: i and a combining dot.
const caseFold = (s) => s.replace(/İ/g, 'i').toLowerCase();
const fmt = (n) => n.toLocaleString('en-GB');
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
function fileSize(bytes) {
  if (bytes < 1024) return plural(bytes, 'byte');
  const kb = (bytes / 1024).toFixed(1);
  return kb < 1024 ? `${kb} KB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}
function ago(iso) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  for (const [u, n] of [['d', 86400], ['h', 3600], ['m', 60]]) if (s >= n) return `${Math.floor(s / n)}${u} ago`;
  return 'just now';
}

// A renamed file kept its directory; a moved one left it. Git reports both as R.
function changeKind(f) {
  if (f.status !== 'R') return f.status;
  const od = f.old_path.slice(0, f.old_path.lastIndexOf('/')), nd = f.new_path.slice(0, f.new_path.lastIndexOf('/'));
  return od === nd ? 'R' : 'V';
}

// The file's content as lines, split once.
function contentLines(f) {
  if (!f._lines) {
    const raw = f.content.split('\n');
    if (raw.length && raw[raw.length - 1] === '') raw.pop();
    f._lines = raw;
  }
  return f._lines;
}
