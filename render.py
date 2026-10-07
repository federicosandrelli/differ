#!/usr/bin/env python3
"""Render a classified diff plus PR metadata as one HTML page.

Usage: render.py <classified.json> <annotations.json> <out.html>
"""

import json
import sys
from pathlib import Path

# Greys, git colours and the font stack follow the Claude desktop app's design tokens (its "cds" scale,
# text-git-added / text-git-removed, and the 20% mix it uses for diff backgrounds). The app has light and dark
# only; sepia keeps the same structure on warm greys.
def mix(color: str, pct: int) -> str:
    return f"color-mix(in srgb, {color} {pct}%, transparent)"


def palette(*, bg, bg2, bg3, fg, fg2, fg3, line, accent, accent_bg, sel, sel_fg, add, dele, mod, ren, mark, mark_fg, syntax):
    return dict(
        bg=bg, bg2=bg2, bg3=bg3, fg=fg, fg2=fg2, fg3=fg3, line=line, accent=accent, accent_bg=accent_bg, sel=sel, sel_fg=sel_fg,
        add_bg=mix(add, 16), add_fg=add, add_num=mix(add, 28), add_word=mix(add, 38),
        del_bg=mix(dele, 16), del_fg=dele, del_num=mix(dele, 28), del_word=mix(dele, 38),
        mod_bg=mix(mod, 22), mod_fg=mod, ren_bg=mix(ren, 22), ren_fg=ren, mark=mark, mark_fg=mark_fg, **syntax,
    )


PALETTES = {
    "light": palette(
        bg="#ffffff", bg2="#f9f9f7", bg3="#f0efec", fg="#20201f", fg2="#6d6b67", fg3="#898781", line="#e1e0d9",
        accent="#2f6feb", accent_bg="#e8f0fe", sel="#cddcfb", sel_fg="#2f6feb",
        add="#1e9e3c", dele="#cd2054", mod="#9a5b00", ren="#6d3fc7", mark="#ffe066", mark_fg="#20201f",
        syntax=dict(kw="#c2410c", str_="#3f7d2a", cmt="#898781", num="#9a6a10", fn="#20201f", cls="#8a5a00", builtin="#c2410c"),
    ),
    "sepia": palette(
        bg="#f6efdc", bg2="#efe6cf", bg3="#e5d9bd", fg="#3b3328", fg2="#6b5f4e", fg3="#9a8c74", line="#dacdb0",
        accent="#3a5a9a", accent_bg="#dfe4ec", sel="#cfd9ec", sel_fg="#3a5a9a",
        add="#2f7d2a", dele="#b8324d", mod="#8a6410", ren="#6a4a8a", mark="#f2d25b", mark_fg="#3b3328",
        syntax=dict(kw="#b2451a", str_="#4f7a2a", cmt="#9a8c74", num="#9a6a10", fn="#3b3328", cls="#8a5a00", builtin="#b2451a"),
    ),
    "dark": palette(
        bg="#1a1a19", bg2="#151515", bg3="#20201f", fg="#d2d1c7", fg2="#a5a49a", fg3="#898781", line="#2c2c2a",
        accent="#6ea0ff", accent_bg="#1a2742", sel="#2a4474", sel_fg="#6ea0ff",
        add="#32d74b", dele="#ff2c56", mod="#f0b45a", ren="#c4a6ff", mark="#ffe066", mark_fg="#1c1f26",
        syntax=dict(kw="#f29b7a", str_="#9fcf8a", cmt="#898781", num="#e5c07b", fn="#d2d1c7", cls="#e5c07b", builtin="#f29b7a"),
    ),
}


def vars_block(p: dict) -> str:
    return "; ".join(f"--{k.rstrip('_').replace('_', '-')}: {v}" for k, v in p.items()) + ";"


def theme_css() -> str:
    light, sepia, dark = (vars_block(PALETTES[k]) for k in ("light", "sepia", "dark"))
    return f"""
:root {{ {light} }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme]) {{ {dark} }} }}
:root[data-theme="light"] {{ {light} }}
:root[data-theme="sepia"] {{ {sepia} }}
:root[data-theme="dark"] {{ {dark} }}
"""


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<style>
__THEMES__
:root { --mono: "SF Mono", ui-monospace, Menlo, Consolas, monospace; --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif; --dim: 0.45; }
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; }
body { background: var(--bg); color: var(--fg); font: 14px/1.45 var(--sans); display: flex; }
a { color: var(--accent); text-decoration: none; } a:hover { text-decoration: underline; }
button { font: inherit; color: inherit; background: none; border: 1px solid var(--line); border-radius: 6px; padding: 3px 9px; cursor: pointer; }
button:hover { background: var(--bg3); } button.on { background: var(--accent-bg); border-color: var(--accent); color: var(--accent); }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
.seg button { border: 0; border-radius: 0; padding: 3px 10px; } .seg button + button { border-left: 1px solid var(--line); }
.seg button.on { background: var(--accent-bg); color: var(--accent); }
kbd { font: 11px var(--mono); background: var(--bg3); border: 1px solid var(--line); border-radius: 4px; padding: 0 4px; }
.hint { color: var(--fg3); font-size: 12px; }
mark { background: var(--mark); color: var(--mark-fg); border-radius: 2px; padding: 0 1px; }

#side { width: var(--side, 420px); min-width: 260px; max-width: 70vw; flex: none; border-right: 1px solid var(--line); background: var(--bg2); display: flex; flex-direction: column; height: 100vh; position: relative; }
#side .grip { position: absolute; top: 0; right: -3px; width: 6px; height: 100%; cursor: col-resize; z-index: 6; }
#side .grip:hover, #side .grip.dragging { background: var(--accent); opacity: .4; }
#side .head { padding: 14px 16px 10px; border-bottom: 1px solid var(--line); }
#side .head .back { display: inline-block; font-size: 12px; margin-bottom: 6px; }
#side .head h1 { font-size: 14px; margin: 0 0 4px; line-height: 1.3; cursor: pointer; }
#side .head h1:hover { color: var(--accent); }
#side .head .meta { color: var(--fg2); font-size: 12px; display: flex; justify-content: space-between; align-items: center; gap: 8px; }
#side .head .themes { display: inline-flex; gap: 2px; }
#side .head .themes button { padding: 1px 6px; font-size: 11px; }
#side .controls { padding: 8px 16px; border-bottom: 1px solid var(--line); display: flex; gap: 8px; align-items: center; font-size: 12px; color: var(--fg2); flex-wrap: wrap; }
#side .searchbar { padding: 8px 12px; border-bottom: 1px solid var(--line); display: grid; grid-template-columns: 1fr; gap: 4px; }
#side .searchbar input { font: 12.5px var(--sans); color: var(--fg); background: var(--bg); border: 1px solid var(--line); border-radius: 6px; padding: 5px 9px; outline: none; min-width: 0; width: 100%; }
#side .searchbar input:focus { border-color: var(--accent); }
#side .searchbar .meta { grid-column: 1 / -1; font-size: 11.5px; color: var(--fg3); min-height: 1em; }
#side .notice { padding: 8px 16px 2px; font-size: 11.5px; color: var(--fg3); }
#side .list { overflow-y: auto; flex: 1; padding-bottom: 24px; }

.tree { padding: 6px 0 24px; font-size: 12.5px; }
.tree .legend { display: flex; gap: 8px 10px; flex-wrap: wrap; padding: 4px 16px 10px; font-size: 11.5px; color: var(--fg2); align-items: center; }
.tree .legend .pill { font-size: 11.5px; } .tree .legend .n { margin-left: -4px; font-family: var(--mono); color: var(--fg3); }
.tree .row { display: flex; align-items: center; gap: 6px; padding: 2px 10px 2px calc(10px + var(--d) * 19px); white-space: nowrap; cursor: pointer; min-width: 0; background-repeat: no-repeat; }
.tree .row:hover { background-color: var(--bg3); }
.tree .row.active { background-color: var(--sel); }
.tree .row.active .pill { font-weight: 600; outline: 1.5px solid var(--sel-fg); }
#side .list:focus { outline: none; }
#side .list:focus .row.cursor { box-shadow: inset 0 0 0 1.5px var(--sel-fg); }
#side .list:focus .row.cursor.active { box-shadow: none; }
.tree .ico { width: 16px; flex: none; text-align: center; color: var(--fg3); font-size: 11px; }
.tree .ico.folder { display: inline-flex; align-items: center; justify-content: center; color: var(--fg2); }
.tree .row.dir:hover .ico.folder { color: var(--accent); }
.tree .pill { font-family: var(--mono); font-size: 12px; padding: 1px 7px; border-radius: 4px; background: var(--bg3); color: var(--fg); display: inline-flex; min-width: 0; flex: 0 1 auto; }
.tree .pill .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.tree .pill s { opacity: .6; margin-right: 6px; flex: 0 1 auto; min-width: 2ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tree .pill.st-A { background: var(--add-num); color: var(--add-fg); } .tree .pill.st-M { background: var(--mod-bg); color: var(--mod-fg); }
.tree .pill.st-D { background: var(--del-num); color: var(--del-fg); text-decoration: line-through; }
.tree .pill.st-R, .tree .pill.st-V { background: var(--ren-bg); color: var(--ren-fg); }
.tree .pill.test { font-style: italic; }
.tree .cnt { font-family: var(--mono); font-size: 11.5px; color: var(--fg2); }
.tree .cnt.hits { background: var(--accent-bg); color: var(--accent); border-radius: 999px; padding: 0 6px; font-size: 10.5px; }
.tree .from { font-family: var(--mono); font-size: 11.5px; color: var(--fg3); overflow: hidden; text-overflow: ellipsis; min-width: 3ch; flex: 0 2 auto; }
.tree .spacer { flex: 1; }
.tree .leaf.reviewed .pill { opacity: .5; }
.check { width: 15px; height: 15px; border: 1.5px solid var(--fg3); border-radius: 4px; flex: none; display: inline-flex; align-items: center; justify-content: center; font-size: 10px; color: transparent; opacity: .35; }
.row:hover .check { opacity: 1; }
.check:hover { border-color: var(--accent); color: var(--accent); }
.leaf.reviewed .check, .check.on { background: var(--accent); border-color: var(--accent); color: #fff; opacity: 1; }

#main { flex: 1; min-width: 0; height: 100vh; overflow-y: auto; }
.page { max-width: 1100px; margin: 0 auto; padding: 28px 36px 40px; }
.page h1 { font-size: 22px; margin: 0 0 6px; }
.page .meta { color: var(--fg2); margin-bottom: 18px; }
.page p { max-width: 78ch; }
.page h2 { font-size: 15px; margin: 28px 0 10px; }
.prbody { max-width: 78ch; } .prbody h3, .prbody h4 { font-size: 14px; margin: 16px 0 6px; } .prbody ul { padding-left: 20px; } .prbody li { margin: 4px 0; }
.prbody code { font: 12.5px var(--mono); background: var(--bg3); padding: 0 4px; border-radius: 3px; }
.stats { display: flex; gap: 12px; flex-wrap: wrap; margin: 16px 0; }
.stat { background: var(--bg2); border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px; min-width: 130px; }
.stat b { display: block; font-size: 20px; font-weight: 600; } .stat span { color: var(--fg2); font-size: 12px; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { font-size: 12px; border: 1px solid var(--line); border-radius: 999px; padding: 2px 10px; cursor: pointer; color: var(--fg2); }
.chip.on { background: var(--accent-bg); color: var(--accent); border-color: var(--accent); }
.chip b { font-weight: 600; }
table.plain { border-collapse: collapse; width: 100%; font-size: 13px; }
table.plain th, table.plain td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
table.plain th { color: var(--fg3); font-weight: 500; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; }
.mono { font-family: var(--mono); font-size: 12.5px; }
.badge { font: 10px/16px var(--mono); padding: 0 5px; border-radius: 4px; background: var(--bg3); color: var(--fg2); flex: none; min-width: 16px; text-align: center; }
.badge.A { background: var(--add-num); color: var(--add-fg); } .badge.D { background: var(--del-num); color: var(--del-fg); }
.badge.M { background: var(--mod-bg); color: var(--mod-fg); } .badge.R, .badge.V { background: var(--ren-bg); color: var(--ren-fg); }
.kind { font-size: 10px; text-transform: uppercase; letter-spacing: .04em; color: var(--fg3); }
.counts { font: 11px var(--mono); color: var(--fg2); white-space: nowrap; }
.counts .a { color: var(--add-fg); } .counts .d { color: var(--del-fg); }

.filesec { border-top: 6px solid var(--bg3); }
.fhead { position: sticky; top: 0; background: var(--bg); border-bottom: 1px solid var(--line); z-index: 5; }
.fhead .top { display: flex; align-items: center; gap: 10px; padding: 10px 20px 8px; cursor: pointer; }
.fhead .top .chev { width: 22px; height: 22px; flex: none; color: var(--fg2); display: inline-flex; align-items: center; justify-content: center; border-radius: 5px; transition: transform .12s ease; }
.fhead .top .chev.open { transform: rotate(90deg); }
.fhead .top:hover .chev { background: var(--bg3); }
.fhead .top:hover .chev { color: var(--accent); }
.filesec.closed .fhead { border-bottom: 0; }
.filesec.closed .fhead .top { padding: 8px 20px; }
.filesec.closed .path b { font-size: 13px; color: var(--fg2); }
.fhead .path { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.fhead .path .dir { font: 11.5px var(--mono); color: var(--fg3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fhead .path b { font: 600 16px var(--mono); color: var(--fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.code { padding: 8px 0 24px; }
.empty { color: var(--fg3); padding: 24px; text-align: center; }

table.diff { border-collapse: collapse; width: 100%; font: 12.5px/1.65 var(--mono); table-layout: fixed; color: var(--fg); }
table.diff td { padding: 0 6px; vertical-align: top; white-space: pre; transition: box-shadow .8s ease; }
table.diff td.n { text-align: right; color: var(--fg3); user-select: none; font-size: 11px; padding: 0 4px 0 2px; }
table.diff td.m { text-align: center; color: var(--fg3); user-select: none; padding: 0; }
table.diff tr.add td { background: var(--add-bg); } table.diff tr.add td.n { background: var(--add-num); color: var(--add-fg); } table.diff tr.add td.m { color: var(--add-fg); }
table.diff tr.del td { background: var(--del-bg); } table.diff tr.del td.n { background: var(--del-num); color: var(--del-fg); } table.diff tr.del td.m { color: var(--del-fg); }
table.diff tr.add td:first-child { box-shadow: inset 3px 0 0 var(--add-fg); }
table.diff tr.del td:first-child { box-shadow: inset 3px 0 0 var(--del-fg); }
table.diff tr.ctx td.c { color: var(--fg2); }
table.diff tr.add .ic { background: var(--add-word); border-radius: 2px; }
table.diff tr.del .ic { background: var(--del-word); border-radius: 2px; }
table.diff tr.dim td.c { opacity: var(--dim); }
table.diff tr.target td { box-shadow: inset 0 0 0 100px color-mix(in srgb, var(--accent) 22%, transparent); }
table.diff tr.target td:first-child { box-shadow: inset 3px 0 0 var(--accent), inset 0 0 0 100px color-mix(in srgb, var(--accent) 22%, transparent) !important; }
table.diff td.c { white-space: pre-wrap; word-break: break-all; }
table.diff td.c .tag { float: right; font: 10px var(--sans); color: var(--fg3); background: var(--bg3); border-radius: 3px; padding: 0 5px; margin-left: 8px; opacity: 1; }
tr.fold td { background: var(--bg2); color: var(--fg3); font: 11.5px var(--sans); padding: 3px 12px; cursor: pointer; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
tr.fold td:hover { color: var(--accent); }
tr.collapsed td { background: var(--bg2); color: var(--fg2); font: 12px var(--sans); padding: 4px 12px; cursor: pointer; border-top: 1px dashed var(--line); border-bottom: 1px dashed var(--line); }
tr.collapsed td:hover { color: var(--accent); }
tr.collapsed td .cls { display: inline-block; min-width: 110px; font-weight: 600; color: var(--fg3); }
tr.collapsed td code { font-family: var(--mono); font-size: 11.5px; }
tr.runhead td { background: var(--bg2); color: var(--fg3); font: 11px var(--sans); padding: 2px 12px; cursor: pointer; border-top: 1px dashed var(--line); }

.hljs-keyword, .hljs-built_in.hljs-keyword { color: var(--kw); } .hljs-string { color: var(--str); } .hljs-comment { color: var(--cmt); font-style: italic; }
.hljs-number, .hljs-literal { color: var(--num); } .hljs-title.function_ { color: var(--fn); } .hljs-title.class_ { color: var(--cls); } .hljs-built_in { color: var(--builtin); }
.hljs-params { color: inherit; } .hljs-meta { color: var(--num); }
</style>
</head>
<body>
<div id="side"></div>
<div id="main"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
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
  showImportOnly: false, showGenerated: false, theme: 'auto', cursor: null, openOverride: new Map(),
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
function importOnly(f) {
  const classes = Object.keys(f.stats.by_class);
  return !f.generated && f.stats.substantive === 0 && classes.length > 0 && classes.every(c => c === 'import');
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
    kindVisible(f) && (!state.q || hits(f) > 0) && (state.showImportOnly || state.q || !importOnly(f)) &&
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
let hitRuns = new Set();
function buildRows(f) {
  const runOfOld = new Map(), runOfNew = new Map(), dimOld = new Set(), dimNew = new Set();
  for (const r of f.runs) {
    r.removed.forEach(n => runOfOld.set(n, r)); r.added.forEach(n => runOfNew.set(n, r));
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
  hitRuns = new Set();
  const q = state.q.toLowerCase();
  if (!q) return rows;
  for (const r of rows) if (r.text.toLowerCase().includes(q)) { r.hit = true; r.html = markHtml(r.html, state.q); if (r.run) hitRuns.add(r.run); }
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
function isCollapsed(f, r) {
  if (!NOISE.includes(r.class)) return false;
  if (hitRuns.has(r)) return false;
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
    if (!(b.run && isCollapsed(f, b.run))) { merged.push(b); i++; continue; }
    const cls = b.run.class; const group = [b.run]; let j = i + 1; let lastRun = i;
    while (j < blocks.length) {
      const c = blocks[j];
      if (c.run && isCollapsed(f, c.run) && c.run.class === cls) { group.push(c.run); lastRun = j; j++; continue; }
      const nextSame = j + 1 < blocks.length && blocks[j + 1].run && isCollapsed(f, blocks[j + 1].run) && blocks[j + 1].run.class === cls;
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
      const visible = (nb) => nb && !nb.group && !(nb.run && isCollapsed(f, nb.run)) && !(nb.imports && !importsOpen(f, nb));
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
    if (r && isCollapsed(f, r)) { out.push(`<tr class="collapsed" data-run="${esc(runKey(f, r))}"><td colspan="4">▸ ${runSummary(r)}</td></tr>`); return; }
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
  // Folders with one child folder and no files fold into one row, like compact folders in an editor.
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
// Guide lines are painted as background gradients so a row can span the full sidebar width.
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
  const importOnlyCount = state.q ? 0 : FILES.filter(f => kindVisible(f) && importOnly(f)).length;
  const generatedCount = FILES.filter(f => kindVisible(f) && f.generated).length;
  return hiddenNotice(importOnlyCount, 'file with import-only changes', 'data-importonly', state.showImportOnly) +
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
// The old path from the last directory the two paths share, so only the part that changed is spelled out.
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
    h += `<h2>Renames</h2><table class="plain"><tr><th>From</th><th>To</th><th>Blocks</th><th>Files</th><th>Origin</th></tr>`;
    for (const r of D.renames) {
      const o = r.origin;
      h += `<tr><td class="mono">${esc(r.from)}</td><td class="mono">${esc(r.to)}</td><td>${r.runs}</td><td>${r.files.length}</td>
        <td>${o ? `<a href="#" data-path="${esc(o.path)}" data-line="${o.line}" class="mono">${esc(o.path.split('/').pop())}:${o.line}</a> <span class="hint">${esc(o.text.slice(0, 60))}</span>` : '<span class="hint">not in this diff</span>'}</td></tr>`;
    }
    h += `</table>`;
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
  // Keep the place inside the current file, so a change to which files show does not land somewhere else.
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
  main.scrollTop = sec.offsetTop;
  setActive(path);
}
function go(path, line) {
  const f = BY_PATH[path];
  if (!f) { scrollToFile('overview'); return; }
  let dirty = false;
  if (!visibleFiles().includes(f)) {
    if (!kindVisible(f)) state.filter = 'all';
    if (importOnly(f)) state.showImportOnly = true;
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
}

// ---------- events
document.addEventListener('click', (e) => {
  const t = e.target.closest('[data-go],[data-path],[data-toggle],[data-filter],[data-tree],[data-importonly],[data-generated],[data-folder],[data-show],[data-whole],[data-review],[data-fold],[data-runs],[data-run],[data-theme]');
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
  if (t.dataset.importonly !== undefined) { e.preventDefault(); state.showImportOnly = t.dataset.importonly === '1'; lsSet('dr:import-only', state.showImportOnly ? '1' : '0'); render(); return; }
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
  state.showImportOnly = lsGet('dr:import-only') === '1';
  state.showGenerated = lsGet('dr:generated') === '1';
  state.theme = THEMES.includes(lsGet('dr:theme')) ? lsGet('dr:theme') : 'auto';
  applyTheme();
  const h = decodeURIComponent(location.hash.slice(1));
  render();
  if (h) { const m = h.match(/^(.*?)(?::(\d+))?$/); if (BY_PATH[m[1]]) go(m[1], m[2] ? +m[2] : null); }
})();
</script>
</body>
</html>
"""


def main() -> None:
    classified = json.loads(Path(sys.argv[1]).read_text())
    annotations = json.loads(Path(sys.argv[2]).read_text())
    data = {**classified, "annotations": annotations}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = f"#{annotations['pr']['number']} {annotations['pr']['title']}"
    html = TEMPLATE.replace("__TITLE__", title).replace("__THEMES__", theme_css()).replace("__DATA__", payload)
    Path(sys.argv[3]).write_text(html)
    print(f"wrote {sys.argv[3]} ({len(html) // 1024} KB)")


if __name__ == "__main__":
    main()
