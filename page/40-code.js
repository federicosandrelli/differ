// The diff table of one file: draws the blocks the file view decided.

// A row's code as HTML: its line of the highlighted file, or a removed line highlighted on its own, with the
// matches of the search marked.
function rowHtml(f, r) {
  const html = r.type === 'del' && f.content_side === 'new' ? hlOne(r.text, f.lang) : highlightLines(f)[(r.newNo ?? r.oldNo) - 1];
  const q = rowQuery();
  return q && caseFold(r.text).includes(caseFold(q)) ? markHtml(html, q) : html;
}

// Append the row's HTML to out, with its threads and the comment being written on it, noting in index where the
// row's key landed.
function renderRow(f, r, out, index) {
  const key = rowKeyOf(r);
  const cls = [r.type, r.dim ? 'dim' : '', r.sel ? 'sel' : '', r.between ? 'between' : ''].join(' ');
  const tag = r.dim ? '<span class="chip">rename</span>' : '';
  index.set(key, out.length);
  const old = r.type === 'del';
  out.push(`<tr class="${cls}" id="${rowId(f, key)}"><td class="n${old ? ' key' : ''}">${r.type === 'add' ? '' : (r.oldNo ?? '')}</td><td class="n${old ? '' : ' key'}">${old ? '' : (r.newNo ?? '')}</td><td class="m">${r.type === 'add' ? '+' : r.type === 'del' ? '−' : ''}</td><td class="c">${tag}${r.html}</td></tr>`);
  if (r.threads) for (const t of r.threads) out.push(`<tr class="thread"><td colspan="4">${threadHtml(f, t)}</td></tr>`);
  if (r.compose) out.push(`<tr class="compose"><td colspan="4">${composeHtml(f, state.compose)}</td></tr>`);
}

// A row with a chip in the code column, indented like the first folded line.
function inlineFoldRow(b) {
  const title = `${b.open ? 'Fold' : 'Show'} ${plural(b.mid.length, 'unchanged line')}`;
  const indent = b.mid[0].text.match(/^\s*/)[0];
  return `<tr class="inline-fold-row"><td class="n"></td><td class="n"></td><td class="m"></td><td class="c">${esc(indent)}<span class="inline-fold chip action${b.open ? ' open' : ''}" data-fold="${esc(b.fold)}" title="${title}">⋯</span></td></tr>`;
}

function runSummary(r) {
  const n = r.removed.length + r.added.length;
  if (r.class === 'import') return `<span class="cls">imports</span> ${plural(n, 'import line')} changed`;
  if (r.class === 'rename') return `<span class="cls">rename fallout</span> <code>${esc(r.rename ? r.rename[0] : '')}</code> → <code>${esc(r.rename ? r.rename[1] : '')}</code> · ${plural(n, 'line')}`;
  if (r.class === 'whitespace') return `<span class="cls">whitespace</span> ${plural(n, 'whitespace-only line')}`;
  if (r.class === 'moved') return `<span class="cls">moved</span> ${n} lines moved ${r.removed.length ? 'to' : 'from'} <code>${esc(r.moved_to.path.split('/').pop())}:${r.moved_to.line}</code>`;
  return `${n} lines`;
}

// The row that opens or closes a block's fold, or only names it while the fold is locked.
function foldRowHtml(b, symStarts) {
  if (b.inline) return inlineFoldRow(b);
  if ((b.joined || b.ctx) && !b.open) return `<tr class="fold" data-fold="${esc(b.fold)}"><td colspan="4"><span class="lbl">⋯ ${hiddenLabel(b.joined ? b.rows : b.mid, symStarts)}</span></td></tr>`;
  const label = b.joined ? hiddenLabel(b.rows, symStarts) : b.ctx ? hiddenLabel(b.mid, symStarts) : b.imports ? importsLabel(b) : b.group ? groupLabel(b) : runSummary(b.run);
  if (b.locked) return `<tr class="runhead locked"><td colspan="4"><span class="lbl">${label}</span></td></tr>`;
  return (b.open ? openRow : foldRow)(b.fold, label);
}
const foldRow = (id, label) => `<tr class="collapsed" data-fold="${esc(id)}"><td colspan="4"><span class="lbl">▸ ${label}</span></td></tr>`;
const openRow = (id, label) => `<tr class="runhead" data-fold="${esc(id)}"><td colspan="4"><span class="lbl">▾ ${label}</span></td></tr>`;

function importsLabel(b) {
  const adds = b.rows.filter(r => r.type === 'add').length, dels = b.rows.filter(r => r.type === 'del').length;
  const delta = adds || dels ? `<span class="counts"><span class="a">+${adds}</span> <span class="d">−${dels}</span></span>` : 'unchanged';
  return `<span class="cls">imports</span> ${plural(b.rows.length, 'line')} · ${delta}`;
}
function groupLabel(b) {
  const n = b.group.reduce((a, r) => a + r.removed.length + r.added.length, 0);
  const named = b.cls === 'rename' && b.group.find(r => r.rename);
  const rename = named ? ` · <code>${esc(named.rename[0])}</code> → <code>${esc(named.rename[1])}</code>` : '';
  return `<span class="cls">${esc(NOISE_LABEL[b.cls])}</span> ${n} lines in ${b.group.length} places${rename}`;
}
// How many of these rows a fold hides, unchanged and changed, and the classes and functions that start on them.
function hiddenLabel(rows, symStarts) {
  const count = (type) => rows.filter(r => r.type === type).length;
  const same = count('ctx'), adds = count('add'), dels = count('del');
  const syms = rows.map(r => symStarts.get(r.newNo)).filter(Boolean);
  const hint = syms.length ? ` · ${syms.slice(0, 4).map(s => esc(s.name)).join(', ')}${syms.length > 4 ? ', …' : ''}` : '';
  if (!adds && !dels) return `${plural(same, 'unchanged line')}${hint}`;
  const changed = `<span class="counts"><span class="a">+${adds}</span> <span class="d">−${dels}</span></span> changed ${adds + dels === 1 ? 'line' : 'lines'}`;
  return `${same ? `${same} unchanged, ` : ''}${changed}${hint}`;
}

// Mark the word changes of each whole run among these rows that pairs every removed line with an added one.
function markRuns(rows) {
  for (let i = 0; i < rows.length;) {
    const r = rows[i].run;
    let j = i + 1;
    while (j < rows.length && rows[j].run === r) j++;
    if (r && r.removed.length === r.added.length && j - i === r.removed.length * 2) markPairs(rows.slice(i, j));
    i = j;
  }
}

// The number of columns a line takes with tabs expanded to the browser's default tab stops of 8.
function lineColumns(text) {
  let n = 0;
  for (const ch of text) n = ch === '\t' ? n + 8 - n % 8 : n + 1;
  return n;
}

// The rows of a file's diff table, one HTML string each so the table can be shown in pieces, the index of
// each row's key in them, and the columns of the longest drawn line.
function codeRows(f) {
  const symStarts = new Map(f.symbols.filter(s => s.kind !== 'call').map(s => [s.start, s]));
  const html = [], index = new Map();
  let cols = 0;
  for (const b of fileBlocks(f)) {
    for (const row of rowsDrawn(b)) row.html = rowHtml(f, row);
    if (b.open && (b.run || b.group || b.joined)) markRuns(b.rows);
    for (const item of drawn(b)) {
      if (item === FOLD_ROW) { html.push(foldRowHtml(b, symStarts)); continue; }
      renderRow(f, item, html, index);
      cols = Math.max(cols, lineColumns(item.text));
    }
  }
  return { html, index, cols };
}
const tableHtml = (rows) => `<table class="diff"><colgroup><col style="width:38px"><col style="width:38px"><col style="width:16px"><col></colgroup>${rows.join('')}</table>`;
