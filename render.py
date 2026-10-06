#!/usr/bin/env python3
"""Render a classified diff plus annotations as one HTML page.

Usage: render.py <classified.json> <annotations.json> <out.html>
"""

import json
import sys
from pathlib import Path

TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<style>
:root {
  --bg: #ffffff; --bg2: #f6f7f9; --bg3: #eceef2; --fg: #1c1f26; --fg2: #5b6270; --fg3: #8a91a0;
  --line: #e3e6eb; --accent: #2f6feb; --accent-bg: #e8f0fe;
  --add-bg: #e6f7ec; --add-fg: #166534; --add-num: #c6ebd3; --del-bg: #fdecec; --del-fg: #991b1b; --del-num: #f7cfcf;
  --note-bg: #fff8e1; --note-line: #f2d98a; --dim: 0.45;
  --mod-bg: #fff1d6; --mod-fg: #9a5b00; --ren-bg: #efe6ff; --ren-fg: #6d3fc7;
  --code: #1c1f26; --kw: #a626a4; --str: #50a14f; --cmt: #9ca3af; --num: #986801; --fn: #4078f2; --cls: #c18401; --builtin: #0184bc;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace; --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0f1115; --bg2: #161920; --bg3: #1f232c; --fg: #e6e8ee; --fg2: #a3aab8; --fg3: #6b7280;
  --line: #262b36; --accent: #6ea0ff; --accent-bg: #1a2742;
  --add-bg: #10291b; --add-fg: #7ee2a4; --add-num: #184d2c; --del-bg: #351a1d; --del-fg: #ff9b9b; --del-num: #5a2328;
  --note-bg: #2b2616; --note-line: #5e4d1d;
  --mod-bg: #3a2c10; --mod-fg: #f0b45a; --ren-bg: #2a1f45; --ren-fg: #c4a6ff;
  --code: #e6e8ee; --kw: #c678dd; --str: #98c379; --cmt: #6b7280; --num: #d19a66; --fn: #61afef; --cls: #e5c07b; --builtin: #56b6c2;
}}
:root[data-theme="dark"] {
  --bg: #0f1115; --bg2: #161920; --bg3: #1f232c; --fg: #e6e8ee; --fg2: #a3aab8; --fg3: #6b7280;
  --line: #262b36; --accent: #6ea0ff; --accent-bg: #1a2742;
  --add-bg: #10291b; --add-fg: #7ee2a4; --add-num: #184d2c; --del-bg: #351a1d; --del-fg: #ff9b9b; --del-num: #5a2328;
  --note-bg: #2b2616; --note-line: #5e4d1d;
  --mod-bg: #3a2c10; --mod-fg: #f0b45a; --ren-bg: #2a1f45; --ren-fg: #c4a6ff;
  --code: #e6e8ee; --kw: #c678dd; --str: #98c379; --cmt: #6b7280; --num: #d19a66; --fn: #61afef; --cls: #e5c07b; --builtin: #56b6c2;
}
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

#side { width: var(--side, 420px); min-width: 260px; max-width: 70vw; flex: none; border-right: 1px solid var(--line); background: var(--bg2); display: flex; flex-direction: column; height: 100vh; position: relative; }
#side .grip { position: absolute; top: 0; right: -3px; width: 6px; height: 100%; cursor: col-resize; z-index: 6; }
#side .grip:hover, #side .grip.dragging { background: var(--accent); opacity: .4; }
#side .head { padding: 14px 16px 10px; border-bottom: 1px solid var(--line); }
#side .head h1 { font-size: 14px; margin: 0 0 4px; line-height: 1.3; cursor: pointer; }
#side .head h1:hover { color: var(--accent); }
#side .head .meta { color: var(--fg2); font-size: 12px; }
#side .controls { padding: 8px 16px; border-bottom: 1px solid var(--line); display: flex; gap: 8px; align-items: center; font-size: 12px; color: var(--fg2); flex-wrap: wrap; }
#side .list { overflow-y: auto; flex: 1; padding-bottom: 24px; }
.group { padding: 10px 0 4px; }
.group h2 { font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: var(--fg3); margin: 0; padding: 4px 16px; display: flex; justify-content: space-between; }
.group h2 .n { font-weight: 400; }
.file { padding: 7px 12px 7px 16px; cursor: pointer; border-left: 3px solid transparent; }
.file:hover { background: var(--bg3); }
.file.active { background: var(--accent-bg); border-left-color: var(--accent); }
.file.reviewed { opacity: .55; }
.file .row1 { display: flex; align-items: center; gap: 6px; }
.file .name { font: 12.5px var(--mono); flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.file .dir { color: var(--fg3); font-size: 11px; }
.file .name b { font-weight: 600; }
.file .counts { font: 11px var(--mono); color: var(--fg2); white-space: nowrap; }
.file .counts .a { color: var(--add-fg); } .file .counts .d { color: var(--del-fg); }
.file .sum { font-size: 12px; color: var(--fg2); margin-top: 2px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.file .bar { height: 3px; background: var(--bg3); border-radius: 2px; margin-top: 5px; display: flex; overflow: hidden; }
.file .bar i { display: block; height: 100%; } .bar .s { background: var(--accent); } .bar .noise { background: var(--fg3); opacity: .5; }
.badge { font: 10px/16px var(--mono); padding: 0 5px; border-radius: 4px; background: var(--bg3); color: var(--fg2); flex: none; min-width: 16px; text-align: center; }
.badge.A { background: var(--add-num); color: var(--add-fg); } .badge.D { background: var(--del-num); color: var(--del-fg); }
.badge.M { background: var(--mod-bg); color: var(--mod-fg); } .badge.R, .badge.V { background: var(--ren-bg); color: var(--ren-fg); }
.st-A { color: var(--add-fg); } .st-D { color: var(--del-fg); } .st-M { color: var(--mod-fg); } .st-R, .st-V { color: var(--ren-fg); }

.tree { padding: 6px 8px 24px 10px; font-size: 12.5px; }
.tree .legend { display: flex; gap: 8px 10px; flex-wrap: wrap; padding: 4px 6px 10px; font-size: 11.5px; color: var(--fg2); align-items: center; }
.tree .legend .pill { font-size: 11.5px; } .tree .legend .n { margin-left: -4px; font-family: var(--mono); color: var(--fg3); }
.tree .row { display: flex; align-items: center; gap: 6px; padding: 2px 4px; white-space: nowrap; cursor: pointer; border-radius: 4px; min-width: 0; }
.tree .row:hover { background: var(--bg3); }
.tree .row.active { background: var(--accent-bg); }
.tree .chev { width: 14px; flex: none; font-size: 9px; color: var(--fg3); text-align: center; }
.tree .ico { width: 14px; flex: none; text-align: center; color: var(--fg3); font-size: 11px; }
.tree .pill { font-family: var(--mono); font-size: 12px; padding: 1px 7px; border-radius: 4px; background: var(--bg3); color: var(--fg); display: inline-flex; min-width: 0; flex: 0 1 auto; }
.tree .pill .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.tree .pill s { opacity: .6; margin-right: 6px; flex: 0 1 auto; min-width: 2ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tree .pill.st-A { background: var(--add-num); color: var(--add-fg); } .tree .pill.st-M { background: var(--mod-bg); color: var(--mod-fg); }
.tree .pill.st-D { background: var(--del-num); color: var(--del-fg); text-decoration: line-through; }
.tree .pill.st-R, .tree .pill.st-V { background: var(--ren-bg); color: var(--ren-fg); }
.tree .pill.test { font-style: italic; }
.tree .cnt { font-family: var(--mono); font-size: 11.5px; color: var(--fg2); }
.tree .from { font-family: var(--mono); font-size: 11.5px; color: var(--fg3); overflow: hidden; text-overflow: ellipsis; min-width: 3ch; flex: 0 2 auto; }
.tree .spacer { flex: 1; }
.tree .kids { margin-left: 11px; padding-left: 8px; border-left: 1px solid var(--line); }
.tree .leaf.reviewed .pill { opacity: .5; }
.kind { font-size: 10px; text-transform: uppercase; letter-spacing: .04em; color: var(--fg3); }
.check { width: 15px; height: 15px; border: 1.5px solid var(--fg3); border-radius: 4px; flex: none; display: inline-flex; align-items: center; justify-content: center; font-size: 10px; color: transparent; opacity: .35; }
.file:hover .check, .row:hover .check { opacity: 1; }
.check:hover { border-color: var(--accent); color: var(--accent); }
.file.reviewed .check, .leaf.reviewed .check, .check.on { background: var(--accent); border-color: var(--accent); color: #fff; opacity: 1; }

#main { flex: 1; min-width: 0; height: 100vh; overflow-y: auto; }
.page { max-width: 1100px; margin: 0 auto; padding: 28px 36px 80px; }
.page h1 { font-size: 22px; margin: 0 0 6px; }
.page .meta { color: var(--fg2); margin-bottom: 18px; }
.page p { max-width: 78ch; }
.page h2 { font-size: 15px; margin: 28px 0 10px; }
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
.card { border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; margin: 10px 0; background: var(--bg2); }
.card h3 { margin: 0 0 4px; font-size: 14px; } .card .desc { color: var(--fg2); font-size: 13px; margin-bottom: 8px; }
.card ul { margin: 0; padding: 0; list-style: none; }
.card li { display: grid; grid-template-columns: 1fr auto; gap: 2px 12px; padding: 5px 0; border-top: 1px solid var(--line); }
.card li .sum { grid-column: 1 / -1; color: var(--fg2); font-size: 12.5px; }
.card li a { word-break: break-all; }
.focus li { margin: 6px 0; }

.fhead { position: sticky; top: 0; background: var(--bg); border-bottom: 1px solid var(--line); z-index: 5; }
.fhead .top { display: flex; align-items: center; gap: 10px; padding: 10px 20px 6px; }
.fhead .path { font: 13px var(--mono); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fhead .path .dir { color: var(--fg3); } .fhead .path b { font-weight: 600; }
.fhead .tools { display: flex; align-items: center; gap: 8px; padding: 0 20px 8px; flex-wrap: wrap; font-size: 12px; color: var(--fg2); }
.fdesc { padding: 12px 20px 4px; }
.fdesc p { margin: 0 0 8px; max-width: 90ch; }
.fdesc .links { font-size: 12.5px; color: var(--fg2); } .fdesc .links a { font-family: var(--mono); font-size: 12px; }
.body { display: flex; align-items: flex-start; }
.code { flex: 1; min-width: 0; padding: 8px 0 60px; }
.outline { width: 250px; flex: none; position: sticky; top: 92px; max-height: calc(100vh - 100px); overflow-y: auto; padding: 10px 12px; font-size: 12px; border-left: 1px solid var(--line); }
.outline h4 { margin: 0 0 6px; font-size: 11px; text-transform: uppercase; color: var(--fg3); letter-spacing: .04em; }
.outline div { padding: 2px 0 2px calc(var(--d) * 12px); color: var(--fg2); cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-family: var(--mono); font-size: 11.5px; }
.outline div:hover { color: var(--accent); }
.outline div.changed { color: var(--fg); font-weight: 600; }
.outline div.changed::before { content: "●"; color: var(--accent); font-size: 8px; margin-right: 5px; vertical-align: 1px; }

table.diff { border-collapse: collapse; width: 100%; font: 12.5px/1.5 var(--mono); table-layout: fixed; }
table.diff td { padding: 0 6px; vertical-align: top; white-space: pre; }
table.diff td.n { text-align: right; color: var(--fg3); user-select: none; font-size: 11px; padding-right: 8px; }
table.diff td.m { text-align: center; color: var(--fg3); user-select: none; padding: 0; }
table.diff tr.add td { background: var(--add-bg); } table.diff tr.add td.n { background: var(--add-num); } table.diff tr.add td.m { color: var(--add-fg); }
table.diff tr.del td { background: var(--del-bg); } table.diff tr.del td.n { background: var(--del-num); } table.diff tr.del td.m { color: var(--del-fg); }
table.diff tr.ctx td.c { color: var(--fg2); }
table.diff tr.dim td.c { opacity: var(--dim); }
table.diff tr.target td { outline: 2px solid var(--accent); outline-offset: -2px; }
table.diff td.c { white-space: pre-wrap; word-break: break-all; }
table.diff td.c .tag { float: right; font: 10px var(--sans); color: var(--fg3); background: var(--bg3); border-radius: 3px; padding: 0 5px; margin-left: 8px; opacity: 1; }
tr.fold td { background: var(--bg2); color: var(--fg3); font: 11.5px var(--sans); padding: 3px 12px; cursor: pointer; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
tr.fold td:hover { color: var(--accent); }
tr.collapsed td { background: var(--bg2); color: var(--fg2); font: 12px var(--sans); padding: 4px 12px; cursor: pointer; border-top: 1px dashed var(--line); border-bottom: 1px dashed var(--line); }
tr.collapsed td:hover { color: var(--accent); }
tr.collapsed td .cls { display: inline-block; min-width: 110px; font-weight: 600; color: var(--fg3); }
tr.collapsed td code { font-family: var(--mono); font-size: 11.5px; }
tr.runhead td { background: var(--bg2); color: var(--fg3); font: 11px var(--sans); padding: 2px 12px; cursor: pointer; border-top: 1px dashed var(--line); }
tr.note td { padding: 6px 12px 10px 70px; white-space: normal; font: 13px/1.45 var(--sans); }
tr.note .callout { background: var(--note-bg); border: 1px solid var(--note-line); border-left-width: 3px; border-radius: 6px; padding: 8px 12px; max-width: 90ch; }
tr.note .callout .who { font-size: 11px; color: var(--fg2); text-transform: uppercase; letter-spacing: .04em; margin-bottom: 2px; }
tr.symhead td { background: var(--bg2); color: var(--fg3); font: 11px var(--mono); padding: 2px 12px; border-top: 1px solid var(--line); }

.hljs-keyword, .hljs-built_in.hljs-keyword { color: var(--kw); } .hljs-string { color: var(--str); } .hljs-comment { color: var(--cmt); font-style: italic; }
.hljs-number, .hljs-literal { color: var(--num); } .hljs-title.function_ { color: var(--fn); } .hljs-title.class_ { color: var(--cls); } .hljs-built_in { color: var(--builtin); }
.hljs-params { color: inherit; } .hljs-meta { color: var(--num); }
.empty { color: var(--fg3); padding: 40px; text-align: center; }
.hint { color: var(--fg3); font-size: 12px; }
</style>
</head>
<body>
<div id="side"></div>
<div id="main"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const ANN = D.annotations, PR = ANN.pr;
const FILES = D.files;
const BY_PATH = Object.fromEntries(FILES.map(f => [f.path, f]));
const NOISE = ['import', 'rename', 'docstring', 'whitespace', 'moved'];
const NOISE_LABEL = { import: 'imports', rename: 'rename fallout', docstring: 'docstrings & comments', whitespace: 'whitespace', moved: 'moved code' };
const RADIUS = 4;

for (const f of FILES) {
  f.ann = ANN.files[f.path] || {};
  f.kind = f.ann.kind || f.kind;
  f.base = f.path.split('/').pop();
  f.dir = f.path.slice(0, f.path.length - f.base.length);
  f.testedBy = [];
}
for (const f of FILES) for (const t of (f.ann.tests || [])) if (BY_PATH[t]) BY_PATH[t].testedBy.push(f.path);
const GROUPS = ANN.groups.map(g => ({ ...g, files: g.files.map(p => BY_PATH[p]).filter(Boolean) }));
const grouped = new Set(GROUPS.flatMap(g => g.files.map(f => f.path)));
const rest = FILES.filter(f => !grouped.has(f.path));
if (rest.length) GROUPS.push({ title: 'Other', description: '', files: rest });
const ORDER = GROUPS.flatMap(g => g.files);

const state = {
  view: 'overview', filter: 'all', whole: false, side: 'tree', closed: new Set(),
  show: Object.fromEntries(NOISE.map(k => [k, false])),
  expanded: new Set(), unfolded: new Set(), target: null,
};
const LS = (k) => `dr:${PR.head.slice(0, 12)}:${k}`;
const isReviewed = (f) => { try { return localStorage.getItem(LS(f.path)) === '1'; } catch { return false; } };
const setReviewed = (f, v) => { try { v ? localStorage.setItem(LS(f.path), '1') : localStorage.removeItem(LS(f.path)); } catch {} };

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const fmt = (n) => n.toLocaleString('en-GB');

// ---------- syntax highlighting, whole file at once so docstrings keep their state across lines
const hlCache = new Map();
function highlightLines(f) {
  if (hlCache.has(f.path)) return hlCache.get(f.path);
  const text = f.content;
  let lines;
  if (window.hljs && f.lang === 'python') {
    const html = hljs.highlight(text, { language: 'python', ignoreIllegals: true }).value;
    lines = splitHighlighted(html);
  } else {
    lines = text.split('\n').map(esc);
  }
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
    (r.pair_rename || []).forEach((flag, i) => { if (flag) { dimOld.add(r.removed[i]); dimNew.add(r.added[i]); } });
  }
  const notes = new Map(); for (const n of (f.ann.notes || [])) (notes.get(n.line) || notes.set(n.line, []).get(n.line)).push(n);
  const hl = highlightLines(f);
  const rows = [];
  const raw = f.content.split('\n'); if (raw.length && raw[raw.length - 1] === '') raw.pop();
  if (f.content_side === 'old') {
    raw.forEach((t, i) => rows.push({ type: 'del', oldNo: i + 1, html: hl[i], run: runOfOld.get(i + 1), notes: notes.get(i + 1) }));
    return rows;
  }
  const removedBefore = new Map();
  for (const h of f.hunks) {
    const L = h.lines;
    for (let i = 0; i < L.length; i++) {
      const [tag, o, , t] = L[i]; if (tag !== '-') continue;
      let anchor = null;
      for (let j = i + 1; j < L.length; j++) if (L[j][2] != null) { anchor = L[j][2]; break; }
      if (anchor == null) anchor = h.new_start + L.filter(x => x[2] != null).length;
      (removedBefore.get(anchor) || removedBefore.set(anchor, []).get(anchor)).push({ type: 'del', oldNo: o, html: hlOne(t, f.lang), run: runOfOld.get(o), dim: dimOld.has(o) });
    }
  }
  for (let i = 1; i <= raw.length + 1; i++) {
    for (const d of removedBefore.get(i) || []) rows.push(d);
    if (i <= raw.length) rows.push({ type: runOfNew.has(i) ? 'add' : 'ctx', newNo: i, html: hl[i - 1], run: runOfNew.get(i), dim: dimNew.has(i), notes: notes.get(i) });
  }
  return rows;
}

function blocksFor(rows) {
  const blocks = [];
  for (const r of rows) {
    const last = blocks[blocks.length - 1];
    if (r.type === 'ctx' && !r.notes) { if (last && last.ctx) last.rows.push(r); else blocks.push({ ctx: true, rows: [r] }); }
    else if (r.run && last && last.run === r.run) last.rows.push(r);
    else blocks.push({ run: r.run || null, rows: [r] });
  }
  return blocks;
}

function runSummary(r, f) {
  const n = r.removed.length + r.added.length;
  if (r.class === 'import') return `<span class="cls">imports</span> ${n} import line${n > 1 ? 's' : ''} changed`;
  if (r.class === 'rename') return `<span class="cls">rename fallout</span> <code>${esc(r.rename ? r.rename[0] : '')}</code> → <code>${esc(r.rename ? r.rename[1] : '')}</code> · ${n} line${n > 1 ? 's' : ''}`;
  if (r.class === 'docstring') return `<span class="cls">docstring</span> ${n} docstring or comment line${n > 1 ? 's' : ''}`;
  if (r.class === 'whitespace') return `<span class="cls">whitespace</span> ${n} whitespace-only line${n > 1 ? 's' : ''}`;
  if (r.class === 'moved') return `<span class="cls">moved</span> ${n} lines moved ${r.removed.length ? 'to' : 'from'} <code>${esc(r.moved_to.path.split('/').pop())}:${r.moved_to.line}</code>`;
  return `${n} lines`;
}
const runKey = (f, r) => `${f.path}#${r.index}`;
function isCollapsed(f, r) {
  if (!NOISE.includes(r.class)) return false;
  if (r.class === 'rename' && r.origin) return false;
  if (state.show[r.class]) return false;
  return !state.expanded.has(runKey(f, r));
}

function renderRows(f, rows, out) {
  for (const r of rows) {
    const no = r.type === 'del' ? r.oldNo : r.newNo;
    const id = r.type === 'del' ? `O${r.oldNo}` : `L${r.newNo}`;
    const cls = [r.type, r.dim ? 'dim' : '', state.target === id ? 'target' : ''].join(' ');
    const tag = r.dim ? '<span class="tag">rename</span>' : '';
    out.push(`<tr class="${cls}" id="${id}"><td class="n">${r.type === 'add' ? '' : (r.oldNo ?? '')}</td><td class="n">${r.type === 'del' ? '' : (r.newNo ?? '')}</td><td class="m">${r.type === 'add' ? '+' : r.type === 'del' ? '−' : ''}</td><td class="c">${tag}${r.html}</td></tr>`);
    for (const n of r.notes || []) out.push(`<tr class="note"><td colspan="4"><div class="callout"><div class="who">Reviewer note · line ${no}</div>${esc(n.text)}</div></td></tr>`);
  }
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

function renderCode(f) {
  const rows = buildRows(f);
  const blocks = mergeCollapsed(f, blocksFor(rows));
  const symStarts = new Map((f.symbols || []).map(s => [s.start, s]));
  const out = [];
  blocks.forEach((b, bi) => {
    if (b.group) {
      const n = b.group.reduce((a, r) => a + r.removed.length + r.added.length, 0);
      const keys = b.group.map(r => runKey(f, r)).join('|');
      const label = b.cls === 'rename' ? `rename fallout <code>${esc(b.group[0].rename[0])}</code> → <code>${esc(b.group[0].rename[1])}</code>` : NOISE_LABEL[b.cls];
      out.push(`<tr class="collapsed" data-runs="${esc(keys)}"><td colspan="4">▸ <span class="cls">${esc(NOISE_LABEL[b.cls])}</span> ${n} lines in ${b.group.length} places${b.cls === 'rename' ? ` · <code>${esc(b.group[0].rename[0])}</code> → <code>${esc(b.group[0].rename[1])}</code>` : ''}</td></tr>`);
      return;
    }
    if (b.ctx) {
      const rs = b.rows;
      const visible = (nb) => nb && !nb.group && !(nb.run && isCollapsed(f, nb.run));
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
    if (r && isCollapsed(f, r)) {
      out.push(`<tr class="collapsed" data-run="${esc(runKey(f, r))}"><td colspan="4">▸ ${runSummary(r, f)}</td></tr>`);
      return;
    }
    if (r && NOISE.includes(r.class) && !(r.class === 'rename' && r.origin)) {
      out.push(`<tr class="runhead" data-run="${esc(runKey(f, r))}"><td colspan="4">▾ ${runSummary(r, f)}</td></tr>`);
    }
    if (r && showOriginHead(r)) {
      out.push(`<tr class="runhead"><td colspan="4">rename origin · <code>${esc(r.rename[0])}</code> → <code>${esc(r.rename[1])}</code></td></tr>`);
    }
    renderRows(f, b.rows, out);
  });
  return `<table class="diff"><colgroup><col style="width:52px"><col style="width:52px"><col style="width:18px"><col></colgroup>${out.join('')}</table>`;
}

// ---------- views
function fileCounts(f) {
  const s = f.stats;
  return `<span class="a">+${s.additions}</span> <span class="d">−${s.deletions}</span>`;
}
function visibleFiles() {
  const base = state.side === 'tree' ? [...FILES].sort((x, y) => x.path.localeCompare(y.path)) : ORDER;
  return base.filter(f => state.filter === 'all' || (state.filter === 'test' ? f.kind === 'test' : f.kind !== 'test'));
}

// A renamed file kept its directory; a moved one left it. Git reports both as R.
function changeKind(f) {
  if (f.status !== 'R') return f.status;
  const od = f.old_path.slice(0, f.old_path.lastIndexOf('/')), nd = f.new_path.slice(0, f.new_path.lastIndexOf('/'));
  return od === nd ? 'R' : 'V';
}
const KIND_LABEL = { A: 'added', M: 'modified', D: 'deleted', R: 'renamed', V: 'moved' };

function buildTree(files) {
  const root = { name: '', dirs: new Map(), files: [], depth: 0 };
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
function aggregate(node) {
  const agg = { a: 0, d: 0, n: 0, kinds: new Set() };
  for (const f of node.files) { agg.a += f.stats.additions; agg.d += f.stats.deletions; agg.n++; agg.kinds.add(changeKind(f)); }
  for (const c of node.dirs.values()) { const x = aggregate(c); agg.a += x.a; agg.d += x.d; agg.n += x.n; x.kinds.forEach(k => agg.kinds.add(k)); }
  node.agg = agg; return agg;
}
function renderTreeNode(node, out) {
  const dirs = [...node.dirs.values()].sort((x, y) => x.label.localeCompare(y.label));
  for (const d of dirs) {
    const closed = state.closed.has(d.full);
    const only = d.agg.kinds.size === 1 ? [...d.agg.kinds][0] : null;
    const cls = only === 'A' || only === 'D' ? 'st-' + only : '';
    out.push(`<div class="node"><div class="row dir ${closed ? 'closed' : ''}" data-folder="${esc(d.full)}" title="${esc(d.full)}">
      <span class="chev">${closed ? '▶' : '▼'}</span><span class="ico">▤</span><span class="pill ${cls}"><span class="nm">${esc(d.label)}/</span></span>${closed ? `<span class="cnt">${d.agg.n}</span>` : ''}</div>`);
    if (!closed) { out.push('<div class="kids">'); renderTreeNode(d, out); out.push('</div>'); }
    out.push('</div>');
  }
  const files = [...node.files].sort((x, y) => x.base.localeCompare(y.base));
  for (const f of files) {
    const k = changeKind(f);
    const oldDir = f.old_path ? f.old_path.slice(0, f.old_path.lastIndexOf('/') + 1) : '';
    out.push(`<div class="row leaf ${state.view === f.path ? 'active' : ''} ${isReviewed(f) ? 'reviewed' : ''}" data-path="${esc(f.path)}" title="${esc(f.ann.summary || f.path)}">
      <span class="chev"></span><span class="ico">·</span>
      <span class="pill st-${k} ${f.kind === 'test' ? 'test' : ''}" title="${KIND_LABEL[k]}">${k === 'R' ? `<s>${esc(f.old_path.split('/').pop())}</s>` : ''}<span class="nm">${esc(f.base)}</span></span>
      ${k === 'V' ? `<span class="from" title="${esc(f.old_path)}">← ${esc(oldDir)}</span>` : ''}
      <span class="spacer"></span><span class="check" data-review="${esc(f.path)}" title="Mark reviewed">✓</span></div>`);
  }
}
function allFolders(node, acc) { for (const d of node.dirs.values()) { acc.push(d.full); allFolders(d, acc); } return acc; }
function renderTree() {
  const root = buildTree(visibleFiles()); aggregate(root);
  const counts = {}; for (const f of visibleFiles()) { const k = changeKind(f); counts[k] = (counts[k] || 0) + 1; }
  const out = [`<div class="tree"><div class="legend">${['A', 'M', 'D', 'R', 'V'].filter(k => counts[k]).map(k => `<span class="pill st-${k}">${KIND_LABEL[k]}</span><span class="n">${counts[k]}</span>`).join('')}</div>`];
  renderTreeNode(root, out);
  out.push('</div>');
  return out.join('');
}

function renderSide() {
  const reviewed = FILES.filter(isReviewed).length;
  let h = `<div class="head"><h1 data-go="overview">#${PR.number} ${esc(PR.title)}</h1>
    <div class="meta">${FILES.length} files · ${reviewed} reviewed · <a href="${PR.url}" target="_blank">GitHub</a></div></div>
    <div class="controls"><span class="seg">
      <button data-filter="all" class="${state.filter === 'all' ? 'on' : ''}">All</button>
      <button data-filter="implementation" class="${state.filter === 'implementation' ? 'on' : ''}">Implementation</button>
      <button data-filter="test" class="${state.filter === 'test' ? 'on' : ''}">Tests</button></span>
      <span class="seg"><button data-side="tree" class="${state.side === 'tree' ? 'on' : ''}">Tree</button><button data-side="groups" class="${state.side === 'groups' ? 'on' : ''}">Reading order</button></span>
      ${state.side === 'tree' ? `<span class="seg"><button data-tree="expand">Expand all</button><button data-tree="collapse">Collapse all</button></span>` : ''}
      <span class="hint"><kbd>j</kbd>/<kbd>k</kbd> files · <kbd>w</kbd> whole file · <kbd>o</kbd> overview</span></div>
    <div class="list">`;
  if (state.side === 'tree') {
    h += renderTree() + `</div>`;
    document.getElementById('side').innerHTML = h + '<div class="grip"></div>';
    return;
  }
  for (const g of GROUPS) {
    const files = g.files.filter(f => state.filter === 'all' || (state.filter === 'test' ? f.kind === 'test' : f.kind !== 'test'));
    if (!files.length) continue;
    const sub = files.reduce((a, f) => a + f.stats.substantive, 0), raw = files.reduce((a, f) => a + f.stats.raw, 0);
    h += `<div class="group"><h2><span>${esc(g.title)}</span><span class="n">${fmt(sub)}${sub !== raw ? ` <span style="opacity:.6">/ ${fmt(raw)}</span>` : ''}</span></h2>`;
    for (const f of files) {
      const s = f.stats, pct = s.raw ? Math.round(100 * s.substantive / s.raw) : 0;
      const k = changeKind(f);
      h += `<div class="file ${state.view === f.path ? 'active' : ''} ${isReviewed(f) ? 'reviewed' : ''}" data-path="${esc(f.path)}">
        <div class="row1"><span class="badge ${k}" title="${KIND_LABEL[k]}">${k === 'V' ? '→' : k}</span><span class="name" title="${esc(f.path)}"><b>${esc(f.base)}</b><span class="dir"> ${esc(shortDir(f.dir))}</span></span>
          <span class="counts">${fileCounts(f)}</span><span class="check" data-review="${esc(f.path)}" title="Mark reviewed">✓</span></div>
        <div class="sum">${esc(f.ann.summary || '')}</div>
        <div class="bar" title="${s.substantive} substantive of ${s.raw} changed lines"><i class="s" style="width:${pct}%"></i><i class="noise" style="width:${100 - pct}%"></i></div></div>`;
    }
    h += `</div>`;
  }
  h += `</div>`;
  document.getElementById('side').innerHTML = h + '<div class="grip"></div>';
}
(function sideResize() {
  try { const w = localStorage.getItem('dr:side-width'); if (w) document.documentElement.style.setProperty('--side', w + 'px'); } catch {}
  let dragging = null;
  document.addEventListener('mousedown', (e) => { const g = e.target.closest('#side .grip'); if (!g) return; dragging = g; g.classList.add('dragging'); e.preventDefault(); });
  document.addEventListener('mousemove', (e) => { if (!dragging) return; const w = Math.max(260, Math.min(e.clientX, window.innerWidth * 0.7)); document.documentElement.style.setProperty('--side', w + 'px'); });
  document.addEventListener('mouseup', () => { if (!dragging) return; dragging.classList.remove('dragging'); dragging = null; try { localStorage.setItem('dr:side-width', parseInt(getComputedStyle(document.getElementById('side')).width)); } catch {} });
})();
function shortDir(dir) {
  const parts = dir.split('/').filter(Boolean);
  if (parts.length <= 2) return dir;
  return '…/' + parts.slice(-2).join('/') + '/';
}

function renderOverview() {
  const t = D.totals, raw = Object.values(t).reduce((a, b) => a + b, 0), sub = t.substantive || 0;
  const impl = FILES.filter(f => f.kind === 'implementation'), tests = FILES.filter(f => f.kind === 'test');
  const reviewed = FILES.filter(isReviewed).length;
  let h = `<div class="page"><h1>${esc(PR.title)}</h1>
    <div class="meta"><a href="${PR.url}" target="_blank">${esc(PR.repo)}#${PR.number}</a> · ${esc(PR.author)} · ${PR.commits} commits · <span class="mono">${PR.base.slice(0, 8)}…${PR.head.slice(0, 8)}</span></div>
    <p>${esc(ANN.summary)}</p>
    <div class="stats">
      <div class="stat"><b>${fmt(sub)}</b><span>lines to read</span></div>
      <div class="stat"><b>${fmt(raw - sub)}</b><span>lines of noise hidden (${raw ? Math.round(100 * (raw - sub) / raw) : 0}% of ${fmt(raw)})</span></div>
      <div class="stat"><b>${impl.length} / ${tests.length}</b><span>implementation / test files</span></div>
      <div class="stat"><b>${reviewed} / ${FILES.length}</b><span>files reviewed</span></div>
    </div>
    <div class="chips">${NOISE.filter(k => t[k]).map(k => `<span class="chip ${state.show[k] ? 'on' : ''}" data-show="${k}">${state.show[k] ? 'showing' : 'hiding'} ${NOISE_LABEL[k]} <b>${fmt(t[k])}</b></span>`).join('')}</div>
    <p class="hint" style="margin-top:6px">Hidden lines are collapsed to one row inside each file, never removed. A row is only collapsed when the classifier can prove what it is: every changed line is inside an import statement, or the removed line becomes the added line exactly by applying a rename that recurs elsewhere in the PR.</p>`;
  if (ANN.review_focus && ANN.review_focus.length) {
    h += `<h2>Where to look</h2><ul class="focus">${ANN.review_focus.map(x => `<li>${esc(x)}</li>`).join('')}</ul>`;
  }
  if (D.renames.length) {
    h += `<h2>Renames</h2><table class="plain"><tr><th>From</th><th>To</th><th>Blocks</th><th>Files</th><th>Origin</th></tr>`;
    for (const r of D.renames) {
      const o = r.origin;
      h += `<tr><td class="mono">${esc(r.from)}</td><td class="mono">${esc(r.to)}</td><td>${r.runs}</td><td>${r.files.length}</td>
        <td>${o ? `<a href="#" data-path="${esc(o.path)}" data-line="${o.line}" class="mono">${esc(o.path.split('/').pop())}:${o.line}</a> <span class="hint">${esc(o.text.slice(0, 60))}</span>` : '<span class="hint">not in this diff</span>'}</td></tr>`;
    }
    h += `</table>`;
  }
  h += `<h2>Reading order</h2>`;
  GROUPS.forEach((g, i) => {
    h += `<div class="card"><h3>${i + 1}. ${esc(g.title)}</h3><div class="desc">${esc(g.description || '')}</div><ul>`;
    for (const f of g.files) {
      h += `<li><a href="#" data-path="${esc(f.path)}" class="mono"><span class="badge ${f.status}">${f.status}</span> ${esc(f.path)}</a><span class="counts mono">${fileCounts(f)}${f.stats.raw !== f.stats.substantive ? ` <span class="hint">${f.stats.substantive} to read</span>` : ''}</span><span class="sum">${esc(f.ann.summary || '')}</span></li>`;
    }
    h += `</ul></div>`;
  });
  h += `</div>`;
  return h;
}

function renderFile(f) {
  const s = f.stats, idx = ORDER.indexOf(f);
  const noiseHere = NOISE.filter(k => s.by_class[k]);
  const ck = changeKind(f);
  let h = `<div class="fhead"><div class="top">
      <span class="badge ${ck}" title="${KIND_LABEL[ck]}">${ck === 'V' ? '→' : ck}</span>
      <span class="path" title="${esc(f.path)}"><span class="dir">${esc(f.dir)}</span><b>${esc(f.base)}</b></span>
      <span class="kind">${esc(f.kind)}</span><span class="counts mono">${fileCounts(f)}</span>
      <button data-review="${esc(f.path)}" class="${isReviewed(f) ? 'on' : ''}">${isReviewed(f) ? '✓ Reviewed' : 'Mark reviewed'}</button>
      <button data-nav="-1" ${idx <= 0 ? 'disabled' : ''} title="previous file (k)">↑</button><button data-nav="1" ${idx >= ORDER.length - 1 ? 'disabled' : ''} title="next file (j)">↓</button>
    </div><div class="tools">
      <span class="seg"><button data-whole="0" class="${!state.whole ? 'on' : ''}">Changes</button><button data-whole="1" class="${state.whole ? 'on' : ''}">Whole file</button></span>
      ${f.old_path && f.new_path && f.old_path !== f.new_path ? `<span class="hint">renamed from <span class="mono">${esc(f.old_path)}</span>${f.similarity ? ` (${f.similarity}% similar)` : ''}</span>` : ''}
      ${noiseHere.length ? `<span class="chips">${noiseHere.map(k => `<span class="chip ${state.show[k] ? 'on' : ''}" data-show="${k}">${NOISE_LABEL[k]} <b>${s.by_class[k]}</b></span>`).join('')}</span>` : ''}
      ${s.raw !== s.substantive ? `<span class="hint">${s.substantive} of ${s.raw} changed lines to read</span>` : ''}
    </div></div>`;
  h += `<div class="fdesc">${f.ann.summary ? `<p>${esc(f.ann.summary)}</p>` : ''}`;
  const links = [];
  if (f.ann.tests && f.ann.tests.length) links.push(`Tested by ${f.ann.tests.map(p => BY_PATH[p] ? `<a href="#" data-path="${esc(p)}">${esc(p.split('/').pop())}</a>` : esc(p)).join(', ')}`);
  if (f.testedBy.length) links.push(`Tests for ${f.testedBy.map(p => `<a href="#" data-path="${esc(p)}">${esc(p.split('/').pop())}</a>`).join(', ')}`);
  if (links.length) h += `<div class="links">${links.join(' · ')}</div>`;
  h += `</div>`;
  h += `<div class="body"><div class="code">${f.runs.length ? renderCode(f) : '<div class="empty">No changes</div>'}</div>`;
  if (f.symbols && f.symbols.length) {
    h += `<div class="outline"><h4>Outline</h4>${f.symbols.map(sym => `<div style="--d:${sym.depth}" class="${sym.changed ? 'changed' : ''}" data-line="${sym.start}" title="${esc(sym.name)}">${sym.kind === 'class' ? '◆ ' : ''}${esc(sym.short)}</div>`).join('')}</div>`;
  }
  h += `</div>`;
  return h;
}

function render() {
  renderSide();
  const main = document.getElementById('main');
  const f = BY_PATH[state.view];
  main.innerHTML = f ? renderFile(f) : renderOverview();
  if (state.target) {
    const el = document.getElementById(state.target);
    if (el) el.scrollIntoView({ block: 'center' });
    state.target = null;
  } else if (!f) main.scrollTop = 0;
}

function go(path, line) {
  const f = BY_PATH[path];
  if (!f) { state.view = 'overview'; location.hash = ''; render(); return; }
  state.view = path;
  if (line) {
    const id = f.content_side === 'old' ? `O${line}` : `L${line}`;
    state.target = id;
    // unfold around the target line
    const rows = buildRows(f); const blocks = blocksFor(rows);
    for (const b of blocks) if (b.ctx && b.rows.some(r => r.newNo === line)) { const mid = b.rows.slice(RADIUS, b.rows.length - RADIUS); if (mid.length) state.unfolded.add(`${f.path}:${mid[0].newNo}`); }
  } else {
    state.target = null;
  }
  history.replaceState(null, '', `#${encodeURIComponent(path)}${line ? ':' + line : ''}`);
  render();
  if (!line) document.getElementById('main').scrollTop = 0;
}

document.addEventListener('click', (e) => {
  const t = e.target.closest('[data-go],[data-path],[data-filter],[data-side],[data-tree],[data-folder],[data-show],[data-whole],[data-review],[data-nav],[data-fold],[data-runs],[data-run],[data-line]');
  if (!t) return;
  if (t.dataset.review !== undefined) { e.preventDefault(); e.stopPropagation(); const f = BY_PATH[t.dataset.review]; setReviewed(f, !isReviewed(f)); render(); return; }
  if (t.dataset.go) { go('overview'); return; }
  if (t.dataset.path) { e.preventDefault(); go(t.dataset.path, t.dataset.line ? +t.dataset.line : null); return; }
  if (t.dataset.filter) { state.filter = t.dataset.filter; render(); return; }
  if (t.dataset.side) { state.side = t.dataset.side; try { localStorage.setItem('dr:side', state.side); } catch {} render(); return; }
  if (t.dataset.tree) { state.closed = new Set(t.dataset.tree === 'collapse' ? allFolders(buildTree(visibleFiles()), []) : []); renderSide(); return; }
  if (t.dataset.folder !== undefined) { const k = t.dataset.folder; state.closed.has(k) ? state.closed.delete(k) : state.closed.add(k); renderSide(); return; }
  if (t.dataset.show) { state.show[t.dataset.show] = !state.show[t.dataset.show]; render(); return; }
  if (t.dataset.whole !== undefined) { state.whole = t.dataset.whole === '1'; render(); return; }
  if (t.dataset.nav) { const files = visibleFiles(); const i = files.indexOf(BY_PATH[state.view]); const n = files[i + (+t.dataset.nav)]; if (n) go(n.path); return; }
  if (t.dataset.fold) { state.unfolded.add(t.dataset.fold); render(); return; }
  if (t.dataset.runs) { for (const k of t.dataset.runs.split('|')) state.expanded.add(k); render(); return; }
  if (t.dataset.run) { const k = t.dataset.run; state.expanded.has(k) ? state.expanded.delete(k) : state.expanded.add(k); render(); return; }
  if (t.dataset.line && t.closest('.outline')) { go(state.view, +t.dataset.line); return; }
});
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.metaKey || e.ctrlKey) return;
  const files = visibleFiles(); const i = files.indexOf(BY_PATH[state.view]);
  if (e.key === 'j') { const n = files[i + 1] || files[0]; if (n) go(n.path); }
  else if (e.key === 'k') { const n = i > 0 ? files[i - 1] : null; if (n) go(n.path); else go('overview'); }
  else if (e.key === 'o') go('overview');
  else if (e.key === 'w') { state.whole = !state.whole; render(); }
  else if (e.key === 'r' && BY_PATH[state.view]) { const f = BY_PATH[state.view]; setReviewed(f, !isReviewed(f)); render(); }
});
window.addEventListener('hashchange', () => {
  const h = decodeURIComponent(location.hash.slice(1));
  const m = h.match(/^(.*?)(?::(\d+))?$/);
  if (!h) { if (state.view !== 'overview') go('overview'); return; }
  if (BY_PATH[m[1]] && (state.view !== m[1] || m[2])) go(m[1], m[2] ? +m[2] : null);
});
(function init() {
  try { state.side = localStorage.getItem('dr:side') || 'tree'; } catch {}
  const h = decodeURIComponent(location.hash.slice(1));
  if (h) { const m = h.match(/^(.*?)(?::(\d+))?$/); if (BY_PATH[m[1]]) { go(m[1], m[2] ? +m[2] : null); return; } }
  render();
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
    html = TEMPLATE.replace("__TITLE__", title).replace("__DATA__", payload)
    Path(sys.argv[3]).write_text(html)
    print(f"wrote {sys.argv[3]} ({len(html) // 1024} KB)")


if __name__ == "__main__":
    main()
