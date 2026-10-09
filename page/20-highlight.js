// Syntax highlighting through highlight.js, when it loaded and knows the language. A file is highlighted
// whole, so docstrings and other multi-line tokens keep their state across lines, and only when a section
// of it is first shown.
const canHighlight = (lang) => !!(window.hljs && lang && hljs.getLanguage(lang));
const hlCache = new Map();
function highlightLines(f) {
  let lines = hlCache.get(f.path);
  if (lines) return lines;
  if (canHighlight(f.lang)) lines = splitHighlighted(hljs.highlight(f.content, { language: f.lang, ignoreIllegals: true }).value);
  else lines = f.content.split('\n').map(esc);
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  hlCache.set(f.path, lines);
  return lines;
}
// Split highlighted HTML into lines, closing the open spans at each line end and reopening them on the next.
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
  if (canHighlight(lang)) return hljs.highlight(text, { language: lang, ignoreIllegals: true }).value;
  return esc(text);
}
