// The page's data, state and small shared helpers. Every other module reads from here.
const D = JSON.parse(document.getElementById('data').textContent);
const PR = D.pr;
const FILES = D.files;
const BY_PATH = Object.fromEntries(FILES.map(f => [f.path, f]));
const NOISE_LABEL = { import: 'imports', rename: 'rename fallout', whitespace: 'whitespace', moved: 'moved code' };
const KIND_LABEL = { A: 'added', M: 'modified', D: 'deleted', R: 'renamed', V: 'moved' };
const RADIUS = 4;
// Comments live on GitHub and change independently of the page, so they load after it, from the server.
const SERVED = location.protocol.startsWith('http');
const API = SERVED ? `/api/${PR.repo}/${PR.number}` : null;

FILES.forEach((f, i) => {
  f.index = i;
  f.base = f.path.split('/').pop();
  f.dir = f.path.slice(0, f.path.length - f.base.length);
});

const state = {
  view: 'overview', filter: 'implementation', closed: new Set(), q: '', hit: null,
  showNoiseOnly: false, showGenerated: false, cursor: null, openOverride: new Map(),
  expanded: new Set(), unfolded: new Set(),
  compose: null, drafts: new Map(), replyOpen: new Map(), openThreads: new Set(), showOutdated: new Set(),
  reviewPanel: false, reviewText: '', convText: '', busy: false, focus: null,
  bug: { open: false, text: '', prompt: '', copied: null },
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

// What happened lately, for a bug report: the errors, and the clicks and keys outside text fields.
const LOG = { errors: [], actions: [] };
function logPush(list, entry) { list.push({ t: Date.now(), ...entry }); if (list.length > 25) list.shift(); }
function logTarget(el) {
  if (!el.closest || el.closest('#side .foot')) return '';
  const t = el.closest('[data-path],[data-folder],[data-fold],[data-toggle],[data-go],[data-filter],[data-review],button,a,td.n,.check,.thread.folded');
  if (!t) return '';
  const ds = Object.entries(t.dataset).slice(0, 2).map(([k, v]) => v ? `${k}=${v}` : k).join(' ');
  const row = el.closest('tr[id]'), text = (t.textContent || '').replace(/\s+/g, ' ').trim();
  return `${t.tagName.toLowerCase()}${ds ? ` [${ds}]` : ''}${row ? ` in #${row.id}` : ''}${text ? ` "${text.slice(0, 40)}${text.length > 40 ? '…' : ''}"` : ''}`;
}
const PAGE_SCRIPT = document.currentScript;
const clip = (s) => s.length > 2000 ? s.slice(0, 2000) + '…' : s;
const errorText = (x) => x instanceof Error ? `${x.name}: ${x.message}` : typeof x === 'string' ? x : (() => { try { return JSON.stringify(x); } catch { return String(x); } })();
const errorStack = (x) => x instanceof Error ? clip(x.stack || '') : '';
window.addEventListener('error', (e) => logPush(LOG.errors, { kind: 'error', text: clip(e.message), where: e.lineno ? `${e.filename}:${e.lineno}:${e.colno}` : '', stack: errorStack(e.error) }));
window.addEventListener('unhandledrejection', (e) => logPush(LOG.errors, { kind: 'unhandled rejection', text: clip(errorText(e.reason)), stack: errorStack(e.reason) }));
const consoleError = console.error.bind(console);
console.error = (...args) => {
  logPush(LOG.errors, { kind: 'console.error', text: clip(args.map(errorText).join(' ')), stack: errorStack(args.find(a => a instanceof Error)) });
  consoleError(...args);
};
document.addEventListener('click', (e) => {
  const what = logTarget(e.target);
  if (what) logPush(LOG.actions, { what: `click ${what}${e.shiftKey ? ' (shift)' : ''}` });
}, true);
document.addEventListener('keydown', (e) => {
  const field = e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT';
  if (['Control', 'Shift', 'Alt', 'Meta', 'AltGraph'].includes(e.key) || e.getModifierState('AltGraph')) return;
  if (field && !(e.metaKey || e.ctrlKey) && e.key !== 'Enter' && e.key !== 'Escape') return;
  if (!field && e.key.length !== 1 && !/^(Arrow|Enter|Escape|Tab|Backspace)/.test(e.key)) return;
  const mods = [e.metaKey && 'cmd', e.ctrlKey && 'ctrl', e.altKey && 'alt', e.shiftKey && 'shift'].filter(Boolean).join('+');
  logPush(LOG.actions, { what: `key ${mods ? mods + '+' : ''}${e.key}${field ? ` in ${e.target.tagName.toLowerCase()}` : ''}` });
}, true);
