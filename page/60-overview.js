// The overview at the top of the main pane: the PR description, the renames and the conversation.

// GitHub sends a file of a private repository only to a signed-in browser, so the server reads such an image with gh.
const REPO_FILE = /^https:\/\/(github\.com\/[^/]+\/[^/]+\/(blob|raw)\/|raw\.githubusercontent\.com\/)/;
const imageSrc = (url) => REPO_FILE.test(url) ? `/image?src=${encodeURIComponent(url)}` : url;

// Enough markdown for a PR description: headings, bullets, paragraphs, images, links, bold, inline code.
function renderMarkdown(md, ctx) {
  // An image stands in as a placeholder until the end, so the link rules never write into its alt attribute. An image
  // from outside GitHub stays a link: GitHub's own rendering loads it through its proxy, which hides the reader.
  const inline = (t) => {
    const images = [];
    return esc(t.replace(/\uE000/g, ''))
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/!\[([^\]]*)\]\((https?:[^)\s]+)\)/g, (m, alt, src) => {
        images.push(REPO_FILE.test(unesc(src)) ? `<img src="${esc(imageSrc(unesc(src)))}" alt="${alt}">`
          : `<a href="${src}" target="_blank">${alt || src}</a>`);
        return `\uE000${images.length - 1}\uE000`;
      })
      .replace(/\[([^\]]+)\]\((https?:[^)\s\uE000]+)\)/g, '<a href="$2" target="_blank">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)\uE000]+)/g, '$1<a href="$2" target="_blank">$2</a>')
      .replace(/\uE000(\d+)\uE000/g, (m, i) => images[i] ?? '');
  };
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
      <td>${o ? `<a href="#" data-path="${esc(o.path)}" data-line="${o.line}" data-side="${o.side}" class="mono">${esc(o.path.split('/').pop())}:${o.line}</a> <span class="hint">${esc(o.text.slice(0, 60))}</span>` : '<span class="hint">not in this diff</span>'}</td></tr>`;
  }
  return h + `</table></div>`;
}

function overviewHtml() {
  const github = new URL(PR.url).origin;
  const commit = (sha) => `<a href="${github}/${PR.repo}/commit/${sha}" target="_blank">${sha.slice(0, 8)}</a>`;
  // gh names an app's bot app/<name>, and its GitHub page is apps/<name>.
  const author = PR.author ? ` · <a href="${github}/${esc(PR.author.replace(/^app\//, 'apps/'))}" target="_blank">${esc(PR.author)}</a>` : '';
  return `<h1>${esc(PR.title)}</h1>
    <div class="meta"><a href="${PR.url}" target="_blank">${esc(PR.repo)}#${PR.number}</a>${author} · ${plural(PR.commits, 'commit')} · <span class="mono">${commit(PR.base)}…${commit(PR.head)}</span></div>
    <div class="prbody">${bodyHtml({ body: PR.body || '', html: C.body_html })}</div>
    ${renamesHtml()}${conversationHtml()}`;
}
// Everything the overview's HTML depends on, so it is rebuilt only when one of them changes.
function overviewKey() {
  return JSON.stringify([C.version, C.loaded, C.error, state.busy]);
}
