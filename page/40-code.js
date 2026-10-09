// The diff table of one file.

const rowId = (f, r) => r.type === 'del' ? `r${f.index}-O${r.oldNo}` : `r${f.index}-L${r.newNo}`;
const ROW_ID = /^r(\d+)-([OL])(\d+)$/;
// The file, side and line a row id names, for the comment button on its number cell.
function rowAt(id) {
  const m = ROW_ID.exec(id || '');
  return m ? { file: FILES[+m[1]], side: m[2] === 'O' ? 'LEFT' : 'RIGHT', line: +m[3] } : null;
}

function renderRows(f, rows, out) {
  for (const r of rows) {
    const cls = [r.type, r.dim ? 'dim' : '', r.sel ? 'sel' : ''].join(' ');
    const tag = r.dim ? '<span class="tag">rename</span>' : '';
    out.push(`<tr class="${cls}" id="${rowId(f, r)}"><td class="n">${r.type === 'add' ? '' : (r.oldNo ?? '')}</td><td class="n">${r.type === 'del' ? '' : (r.newNo ?? '')}</td><td class="m">${r.type === 'add' ? '+' : r.type === 'del' ? '−' : ''}</td><td class="c">${tag}${r.html}</td></tr>`);
    if (r.threads) for (const t of r.threads) out.push(`<tr class="thread"><td colspan="4">${threadHtml(f, t)}</td></tr>`);
    if (r.compose) out.push(`<tr class="compose"><td colspan="4">${composeHtml(f, state.compose)}</td></tr>`);
  }
}

function runSummary(r) {
  const n = r.removed.length + r.added.length;
  if (r.class === 'import') return `<span class="cls">imports</span> ${plural(n, 'import line')} changed`;
  if (r.class === 'rename') return `<span class="cls">rename fallout</span> <code>${esc(r.rename ? r.rename[0] : '')}</code> → <code>${esc(r.rename ? r.rename[1] : '')}</code> · ${plural(n, 'line')}`;
  if (r.class === 'whitespace') return `<span class="cls">whitespace</span> ${plural(n, 'whitespace-only line')}`;
  if (r.class === 'moved') return `<span class="cls">moved</span> ${n} lines moved ${r.removed.length ? 'to' : 'from'} <code>${esc(r.moved_to.path.split('/').pop())}:${r.moved_to.line}</code>`;
  return `${n} lines`;
}
const RENAME_RUNS = Object.fromEntries(D.renames.map(r => [r.from + '\u0000' + r.to, r.runs]));
function showOriginHead(r) { return r.class === 'rename' && r.origin && r.rename && (RENAME_RUNS[r.rename.join('\u0000')] || 0) >= 2; }

const foldRow = (key, label) => `<tr class="collapsed" data-run="${esc(key)}"><td colspan="4">▸ ${label}</td></tr>`;
const openRow = (key, label) => `<tr class="runhead" data-run="${esc(key)}"><td colspan="4">▾ ${label}</td></tr>`;

function importsLabel(b) {
  const adds = b.rows.filter(r => r.type === 'add').length, dels = b.rows.filter(r => r.type === 'del').length;
  const delta = adds || dels ? `<span class="counts"><span class="a">+${adds}</span> <span class="d">−${dels}</span></span>` : 'unchanged';
  return `<span class="cls">imports</span> ${plural(b.rows.length, 'line')} · ${delta}`;
}

// A context block shows RADIUS lines next to each visible neighbour and folds the middle, unless the
// reader opened it or asked for whole files.
function renderContext(f, blocks, bi, symStarts, out) {
  const rs = blocks[bi].rows;
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
}

// The rows of a file's diff table, one HTML string each, so the table can be shown in pieces.
function codeRows(f) {
  const blocks = fileBlocks(f);
  const symStarts = new Map((f.symbols || []).map(s => [s.start, s]));
  const out = [];
  blocks.forEach((b, bi) => {
    if (b.imports) {
      const key = importsKey(f, b);
      if (!importsOpen(f, b)) { out.push(foldRow(key, importsLabel(b))); return; }
      out.push(openRow(key, importsLabel(b)));
      renderRows(f, b.rows, out);
      return;
    }
    if (b.group) {
      const n = b.group.reduce((a, r) => a + r.removed.length + r.added.length, 0);
      const keys = b.group.map(r => runKey(f, r)).join('|');
      const rename = b.cls === 'rename' ? ` · <code>${esc(b.group[0].rename[0])}</code> → <code>${esc(b.group[0].rename[1])}</code>` : '';
      out.push(`<tr class="collapsed" data-runs="${esc(keys)}"><td colspan="4">▸ <span class="cls">${esc(NOISE_LABEL[b.cls])}</span> ${n} lines in ${b.group.length} places${rename}</td></tr>`);
      return;
    }
    if (b.ctx) { renderContext(f, blocks, bi, symStarts, out); return; }
    const r = b.run;
    if (isCollapsed(f, b)) { out.push(foldRow(runKey(f, r), runSummary(r))); return; }
    if (r && NOISE.includes(r.class) && !(r.class === 'rename' && r.origin)) out.push(openRow(runKey(f, r), runSummary(r)));
    if (r && showOriginHead(r)) out.push(`<tr class="runhead"><td colspan="4">rename origin · <code>${esc(r.rename[0])}</code> → <code>${esc(r.rename[1])}</code></td></tr>`);
    if (r && r.removed.length === r.added.length && b.rows.length === r.removed.length * 2) markPairs(b.rows);
    renderRows(f, b.rows, out);
  });
  return out;
}
const tableHtml = (rows) => `<table class="diff"><colgroup><col style="width:38px"><col style="width:38px"><col style="width:16px"><col></colgroup>${rows.join('')}</table>`;
