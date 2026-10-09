// The overview at the top of the main pane: the PR description, the noise totals, the renames and the conversation.

// Enough markdown for a PR description: headings, bullets, paragraphs, links, bold, inline code.
function renderMarkdown(md, ctx) {
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
  const lines = md.split('\n');
  for (let li = 0; li < lines.length; li++) {
    const line = lines[li].trimEnd();
    const fence = line.match(/^\s*```\s*(\S*)\s*$/);
    if (fence) {
      flush();
      const code = [];
      for (li++; li < lines.length && !/^\s*```\s*$/.test(lines[li]); li++) code.push(lines[li]);
      out.push(fence[1] === 'suggestion' ? suggestionHtml(code, ctx) : `<pre><code>${esc(code.join('\n'))}</code></pre>`);
      continue;
    }
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

function renamesHtml() {
  if (!D.renames.length) return '';
  let h = `<h2>Renames</h2><div class="scrollx"><table class="plain"><tr><th>From</th><th>To</th><th>Blocks</th><th>Files</th><th>Origin</th></tr>`;
  for (const r of D.renames) {
    const o = r.origin;
    h += `<tr><td class="mono">${esc(r.from)}</td><td class="mono">${esc(r.to)}</td><td>${r.runs}</td><td>${r.files.length}</td>
      <td>${o ? `<a href="#" data-path="${esc(o.path)}" data-line="${o.line}" class="mono">${esc(o.path.split('/').pop())}:${o.line}</a> <span class="hint">${esc(o.text.slice(0, 60))}</span>` : '<span class="hint">not in this diff</span>'}</td></tr>`;
  }
  return h + `</table></div>`;
}

function overviewHtml() {
  const t = D.totals, raw = Object.values(t).reduce((a, b) => a + b, 0);
  const sub = FILES.filter(f => !f.generated).reduce((a, f) => a + f.stats.substantive, 0);
  const impl = FILES.filter(f => f.kind === 'implementation'), tests = FILES.filter(f => f.kind === 'test');
  const reviewed = FILES.filter(isReviewed).length;
  return `<h1>${esc(PR.title)}</h1>
    <div class="meta"><a href="${PR.url}" target="_blank">${esc(PR.repo)}#${PR.number}</a> · ${esc(PR.author)} · ${PR.commits} commits · <span class="mono">${PR.base.slice(0, 8)}…${PR.head.slice(0, 8)}</span></div>
    <div class="prbody">${renderMarkdown(PR.body || '')}</div>
    <div class="stats">
      <div class="stat"><b>${fmt(sub)}</b><span>lines to read</span></div>
      <div class="stat"><b>${fmt(raw - sub)}</b><span>lines of noise hidden (${raw ? Math.round(100 * (raw - sub) / raw) : 0}% of ${fmt(raw)})</span></div>
      <div class="stat"><b>${impl.length} / ${tests.length}</b><span>implementation / test files</span></div>
      <div class="stat"><b>${reviewed} / ${FILES.length}</b><span>files reviewed</span></div>
    </div>
    <div class="chips">${NOISE.filter(k => t[k]).map(k => `<span class="chip ${state.show[k] ? 'on' : ''}" data-show="${k}">${state.show[k] ? 'showing' : 'hiding'} ${NOISE_LABEL[k]} <b>${fmt(t[k])}</b></span>`).join('')}</div>
    <p class="hint" style="margin-top:6px">Import sections are folded to one row in every file, changed or not. Other lines are collapsed only when the classifier can prove what they are: the removed line becomes the added line exactly by applying a rename that recurs elsewhere in the PR.</p>
    ${renamesHtml()}${conversationHtml()}`;
}
// Everything the overview's HTML depends on, so it is rebuilt only when one of them changes.
function overviewKey() {
  return JSON.stringify([C.version, C.loaded, C.error, state.show, FILES.filter(isReviewed).length, state.busy]);
}
