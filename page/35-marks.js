// Marks inside a line's highlighted HTML: the matches of the search, and the words that differ between a
// removed line and the added line that replaces it.

// Wrap every occurrence of the query in one <mark>. The query is found in the plain text of the HTML fragment,
// so a match can span several highlighter tokens but never starts or ends inside an entity such as &gt;.
function markHtml(html, q) {
  if (!q) return html;
  const needle = caseFold(q);
  const text = caseFold(html.split(/(<[^>]+>)/).filter((_, i) => i % 2 === 0).map(unesc).join(''));
  const ranges = [];
  for (let j = text.indexOf(needle); j >= 0; j = text.indexOf(needle, j + needle.length)) ranges.push([j, j + needle.length]);
  return wrapRanges(html, ranges, '<mark>', '</mark>', true);
}

const WORD_DIFF_MAX = 1000;
// Changed ranges of the two lines, as plain-text offsets, or null when the lines share too little or either is
// longer than WORD_DIFF_MAX characters: the comparison's table grows with the product of the two lengths.
function wordDiff(a, b) {
  if (a.length > WORD_DIFF_MAX || b.length > WORD_DIFF_MAX) return null;
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
// Wrap plain-text ranges of a highlighted line in the open and close tags, walking only its text nodes. A
// range that crosses a tag is closed before the tag and reopened after it, so the HTML stays well formed. A whole
// range is one element instead: the tags open where it starts or ends are closed before that point and reopened
// after it.
function wrapRanges(html, ranges, open = '<span class="ic">', close = '</span>', whole = false) {
  if (!ranges || !ranges.length) return html;
  const ENT = /&(?:amp|lt|gt|quot|#x27|#39);|./gs;
  const tags = [];
  const around = (tag) => whole ? tags.map(t => `</${/^<(\w+)/.exec(t)[1]}>`).reverse().join('') + tag + tags.join('') : tag;
  let pos = 0, ri = 0, wrapped = false, out = '';
  for (const part of html.split(/(<[^>]+>)/)) {
    if (part.startsWith('<') && part.endsWith('>') && part.length > 1) {
      if (wrapped && !whole) { out += close; wrapped = false; }
      if (whole) { if (part.startsWith('</')) tags.pop(); else tags.push(part); }
      out += part;
      continue;
    }
    for (const m of part.matchAll(ENT)) {
      while (ri < ranges.length && pos >= ranges[ri][1]) ri++;
      const inside = ri < ranges.length && pos >= ranges[ri][0] && pos < ranges[ri][1];
      if (inside !== wrapped) { out += around(inside ? open : close); wrapped = inside; }
      out += m[0]; pos++;
    }
  }
  return wrapped ? out + around(close) : out;
}
// Mark the word changes in a run's rows, which hold its removed lines followed by its added ones.
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
