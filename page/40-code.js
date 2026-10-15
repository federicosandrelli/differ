// The diff table of one file: draws the blocks the file view decided.

// Append the rows' HTML to out, noting in index where each row's key landed.
function renderRows(f, rows, out, index) {
  for (const r of rows) {
    const key = rowKeyOf(r);
    const cls = [r.type, r.dim ? 'dim' : '', r.sel ? 'sel' : '', r.between ? 'between' : ''].join(' ');
    const tag = r.dim ? '<span class="tag">rename</span>' : '';
    index.set(key, out.length);
    out.push(`<tr class="${cls}" id="${rowId(f, key)}"><td class="n">${r.type === 'add' ? '' : (r.oldNo ?? '')}</td><td class="n">${r.type === 'del' ? '' : (r.newNo ?? '')}</td><td class="m">${r.type === 'add' ? '+' : r.type === 'del' ? '−' : ''}</td><td class="c">${tag}${r.html}</td></tr>`);
    if (r.threads) for (const t of r.threads) out.push(`<tr class="thread"><td colspan="4">${threadHtml(f, t)}</td></tr>`);
    if (r.compose) out.push(`<tr class="compose"><td colspan="4">${composeHtml(f, state.compose)}</td></tr>`);
  }
}

// A row with a chip in the code column, indented like the first folded line.
function inlineFoldRow(b) {
  const title = `${b.open ? 'Fold' : 'Show'} ${plural(b.mid.length, 'unchanged line')}`;
  const indent = b.mid[0].text.match(/^\s*/)[0];
  return `<tr class="inline-fold-row"><td class="n"></td><td class="n"></td><td class="m"></td><td class="c">${esc(indent)}<span class="inline-fold${b.open ? ' open' : ''}" data-fold="${esc(b.fold)}" title="${title}">⋯</span></td></tr>`;
}

function runSummary(r) {
  const n = r.removed.length + r.added.length;
  if (r.class === 'import') return `<span class="cls">imports</span> ${plural(n, 'import line')} changed`;
  if (r.class === 'rename') return `<span class="cls">rename fallout</span> <code>${esc(r.rename ? r.rename[0] : '')}</code> → <code>${esc(r.rename ? r.rename[1] : '')}</code> · ${plural(n, 'line')}`;
  if (r.class === 'whitespace') return `<span class="cls">whitespace</span> ${plural(n, 'whitespace-only line')}`;
  if (r.class === 'moved') return `<span class="cls">moved</span> ${n} lines moved ${r.removed.length ? 'to' : 'from'} <code>${esc(r.moved_to.path.split('/').pop())}:${r.moved_to.line}</code>`;
  return `${n} lines`;
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
function contextLabel(b, symStarts) {
  const syms = b.mid.map(r => symStarts.get(r.newNo)).filter(Boolean);
  const hint = syms.length ? ` · ${syms.slice(0, 4).map(s => esc(s.name)).join(', ')}${syms.length > 4 ? ', …' : ''}` : '';
  return `⋯ ${b.mid.length} unchanged lines${hint}`;
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
  const draw = (rows) => {
    renderRows(f, rows, html, index);
    for (const r of rows) cols = Math.max(cols, lineColumns(r.text));
  };
  for (const b of fileBlocks(f)) {
    if (b.imports) {
      html.push((b.open ? openRow : foldRow)(b.fold, importsLabel(b)));
      if (b.open) draw(b.rows);
    } else if (b.group) {
      html.push(foldRow(b.fold, groupLabel(b)));
    } else if (b.ctx) {
      draw(b.head);
      if (b.inline && b.mid.length) html.push(inlineFoldRow(b));
      if (b.open) draw(b.mid);
      else if (b.mid.length && !b.inline) html.push(`<tr class="fold" data-fold="${esc(b.fold)}"><td colspan="4"><span class="lbl">${contextLabel(b, symStarts)}</span></td></tr>`);
      draw(b.tail);
    } else {
      const r = b.run;
      if (b.fold) html.push((b.open ? openRow : foldRow)(b.fold, runSummary(r)));
      if (!b.open) continue;
      if (r && r.removed.length === r.added.length && b.rows.length === r.removed.length * 2) markPairs(b.rows);
      draw(b.rows);
    }
  }
  return { html, index, cols };
}
// How many rows codeRows draws for a block, leaving out threads: the shown rows and the fold row (an open context
// block has none, unless it is inline).
function drawnRows(b) {
  const shown = b.rows.length - hiddenRows(b).length;
  return shown + (b.fold && (b.inline || !(b.ctx && b.open)) ? 1 : 0);
}
const tableHtml = (rows) => `<table class="diff"><colgroup><col style="width:38px"><col style="width:38px"><col style="width:16px"><col></colgroup>${rows.join('')}</table>`;
