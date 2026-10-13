// Where a row of a file is. A row key names a row on its own side: O<old line> for a removed line, L<new line>
// for the rest. The other modules pass keys around; only this one relates a key to lines, hunks and element ids.

const rowKeyOf = (r) => r.type === 'del' ? `O${r.oldNo}` : `L${r.newNo}`;

// The key of the row showing a line of the old (LEFT) or new (RIGHT) file. An unchanged old-side line shows as
// the new-side row it became; a deleted file shows its old lines only.
function rowKey(f, side, line) {
  if (f.content_side === 'old') return `O${line}`;
  if (side === 'RIGHT') return `L${line}`;
  for (const h of f.hunks) for (const [tag, o] of h.lines) if (tag === '-' && o === line) return `O${line}`;
  return `L${oldToNew(f, line)}`;
}

// The new-side line number of an unchanged old-side line: the hunks give the pairing inside them and the offset outside.
function oldToNew(f, o) {
  let delta = 0;
  for (const h of f.hunks) {
    if (o < h.old_start) return o + delta;
    let lastO = h.old_start - 1, lastN = h.new_start - 1;
    for (const [tag, ho, hn] of h.lines) {
      if (tag === ' ' && ho === o) return hn;
      if (ho != null) lastO = ho;
      if (hn != null) lastN = hn;
    }
    delta = lastN - lastO;
  }
  return o + delta;
}

// The element id of a row, and the file, key, side and line an id names.
const rowId = (f, key) => `r${f.index}-${key}`;
const ROW_ID = /^r(\d+)-([OL])(\d+)$/;
function rowAt(id) {
  const m = ROW_ID.exec(id || '');
  return m ? { file: FILES[+m[1]], key: m[2] + m[3], side: m[2] === 'O' ? 'LEFT' : 'RIGHT', line: +m[3] } : null;
}
