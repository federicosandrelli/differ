// Review threads from GitHub, placed on the lines they refer to, and the forms that write back.

// C.version counts the loads, so a file's rendered body knows when its threads changed.
const C = { loaded: false, loadedAt: 0, version: 0, error: '', viewer: '', head: '', pending: null, threads: [], reviews: [], comments: [] };
const THREADS = { byFile: new Map(), orphans: [] };

async function loadComments() {
  if (!API) return;
  try {
    const r = await fetch(`${API}/comments`);
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    Object.assign(C, d, { loaded: true, error: '', loadedAt: Date.now() });
  } catch (e) { C.error = e.message; }
  C.version++;
  indexThreads();
  render();
  if (SHELL) SHELL.tell({ type: 'badge', n: C.threads.filter(t => !t.resolved).length });
}
async function post(action, body) {
  state.busy = true; render();
  try {
    const r = await fetch(`${API}/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    state.busy = false;
    await loadComments();
    return true;
  } catch (e) { C.error = e.message; state.busy = false; render(); return false; }
}

function indexThreads() {
  THREADS.byFile = new Map(); THREADS.orphans = [];
  for (const t of C.threads) {
    const f = BY_PATH[t.path];
    if (!f) { THREADS.orphans.push(t); continue; }
    const entry = THREADS.byFile.get(t.path) || THREADS.byFile.set(t.path, { rows: new Map(), outdated: [] }).get(t.path);
    if (t.outdated || t.line == null) { entry.outdated.push(t); continue; }
    const key = rowKey(f, t.side, t.line);
    (entry.rows.get(key) || entry.rows.set(key, []).get(key)).push(t);
  }
}
function threadCount(f) {
  const e = THREADS.byFile.get(f.path);
  if (!e) return 0;
  let n = e.outdated.filter(t => !t.resolved).length;
  for (const ts of e.rows.values()) n += ts.filter(t => !t.resolved).length;
  return n;
}
function pendingCount() { return C.threads.reduce((a, t) => a + t.comments.filter(c => c.pending).length, 0); }
const excerpt = (s) => { const t = s.replace(/```[\s\S]*?```/g, '').replace(/\s+/g, ' ').trim(); return t.length > 90 ? t.slice(0, 90) + '…' : t; };
const lineRange = (a, b) => a && a !== b ? `${a}–${b}` : `${b}`;
const btn = (attrs, label, primary) => `<button ${attrs} class="${primary ? 'primary' : ''}" ${state.busy ? 'disabled' : ''}>${label}</button>`;

// ---------- HTML
// GitHub's own rendering of a body, when it has one, except for suggestions: the page draws those against the file.
function bodyHtml(c, ctx) {
  if (!c.html || /^\s*```suggestion/m.test(c.body)) return renderMarkdown(c.body, ctx);
  return c.html.replace(/<a\b(?![^>]*\btarget=)/g, '<a target="_blank"');
}
function commentHtml(c, ctx) {
  return `<div class="comment ${c.pending ? 'pending' : ''}"><div class="chead"><b>${esc(c.author)}</b><span class="hint">${ago(c.created)}</span>
    ${c.pending ? '<span class="pend">pending</span>' : ''}<span class="spacer"></span>
    ${c.pending ? `<a href="#" data-delete="${esc(c.id)}">delete</a>` : ''}<a href="${c.url}" target="_blank" title="Open on GitHub">↗</a></div>
    <div class="cbody">${bodyHtml(c, ctx)}</div></div>`;
}
function threadHtml(f, t) {
  const n = t.comments.length, first = t.comments[0];
  if (t.resolved && !state.openThreads.has(t.id)) {
    return `<div class="thread folded" data-thread-open="${esc(t.id)}">✓ Resolved${t.resolved_by ? ` by ${esc(t.resolved_by)}` : ''} · ${esc(first.author)}: <span class="hint">${esc(excerpt(first.body))}</span> · ${plural(n, 'comment')}</div>`;
  }
  const head = t.outdated ? `<div class="thead">Outdated · was on line${t.original_start_line && t.original_start_line !== t.original_line ? 's' : ''} ${lineRange(t.original_start_line, t.original_line)}${t.side === 'LEFT' ? ' of the old file' : ''}</div>`
    : t.start_line && t.start_line !== t.line ? `<div class="thead">Lines ${lineRange(t.start_line, t.line)}</div>` : '';
  const reply = state.replyOpen.has(t.id) ? replyHtml(t) : `<div class="tfoot">${btn(`data-reply="${esc(t.id)}"`, 'Reply')}
    ${btn(`data-resolve="${esc(t.id)}" data-resolved="${t.resolved ? 0 : 1}"`, t.resolved ? 'Unresolve' : 'Resolve')}
    ${t.resolved ? `<button data-thread-close="${esc(t.id)}">Hide</button>` : ''}</div>`;
  return `<div class="thread ${t.resolved ? 'is-resolved' : ''}" data-thread="${esc(t.id)}">${head}${t.comments.map(c => commentHtml(c, { file: f, thread: t })).join('')}${reply}</div>`;
}
function replyHtml(t) {
  return `<div class="form reply"><textarea data-reply-text="${esc(t.id)}" placeholder="Reply">${esc(state.replyOpen.get(t.id) || '')}</textarea>
    <div class="actions"><span class="spacer"></span>${btn(`data-reply-cancel="${esc(t.id)}"`, 'Cancel')}
    ${btn(`data-reply-send="${esc(t.id)}" data-mode="single"`, 'Add single comment')}${btn(`data-reply-send="${esc(t.id)}" data-mode="review"`, C.pending ? 'Add review comment' : 'Start a review', true)}</div></div>`;
}
function composeHtml(f, c) {
  const where = `line${c.startLine !== c.line ? 's' : ''} ${lineRange(c.startLine, c.line)}${c.side === 'LEFT' ? ' of the old file' : ''}`;
  return `<div class="form compose"><div class="hint">Comment on ${where} · shift-click another line number to extend</div>
    <textarea data-compose-text placeholder="Leave a comment">${esc(c.text)}</textarea>
    <div class="actions">${c.side === 'RIGHT' && f.content_side !== 'old' ? btn('data-suggest', 'Suggest a change') : ''}<span class="spacer"></span>${btn('data-compose-cancel', 'Cancel')}
    ${btn('data-compose-send="single"', 'Add single comment')}${btn('data-compose-send="review"', C.pending ? 'Add review comment' : 'Start a review', true)}</div></div>`;
}
// A suggestion shows as the lines it replaces, from the file, over the lines it proposes.
function suggestionHtml(code, ctx) {
  const t = ctx && ctx.thread, f = ctx && ctx.file;
  const rows = [];
  if (t && f && t.side === 'RIGHT' && !t.outdated && t.line != null && f.content_side !== 'old') {
    const src = contentLines(f);
    for (let n = t.start_line || t.line; n <= t.line; n++) if (src[n - 1] !== undefined) rows.push(`<tr class="del"><td class="n">${n}</td><td class="m">−</td><td class="c">${hlOne(src[n - 1], f.lang)}</td></tr>`);
  }
  for (const l of code) rows.push(`<tr class="add"><td class="n"></td><td class="m">+</td><td class="c">${hlOne(l, f && f.lang)}</td></tr>`);
  return `<div class="suggestion"><div class="shead">Suggested change</div><table class="diff"><colgroup><col style="width:38px"><col style="width:16px"><col></colgroup>${rows.join('')}</table></div>`;
}
function suggestionTemplate(f, c) {
  const src = contentLines(f);
  const lines = [];
  for (let n = c.startLine; n <= c.line; n++) if (src[n - 1] !== undefined) lines.push(src[n - 1]);
  return '```suggestion\n' + lines.join('\n') + '\n```\n';
}
function outdatedHtml(f) {
  const e = THREADS.byFile.get(f.path);
  if (!e || !e.outdated.length) return '';
  const open = state.showOutdated.has(f.path);
  return `<div class="outdated"><a href="#" data-outdated="${esc(f.path)}">${open ? '▾' : '▸'} ${plural(e.outdated.length, 'outdated thread')}</a>${open ? e.outdated.map(t => threadHtml(f, t)).join('') : ''}</div>`;
}
function commentNotices() {
  if (!API) return '';
  if (C.error) return `<div class="notice err">Comments: ${esc(C.error)} · <a href="#" data-comments-reload>retry</a></div>`;
  if (!C.loaded) return '<div class="notice">Loading comments…</div>';
  const stale = C.head && !PR.head.startsWith(C.head) && !C.head.startsWith(PR.head);
  return stale ? `<div class="notice err">The pull request has new commits · <a href="?refresh=1">rebuild the page</a></div>` : '';
}
function reviewPanelHtml() {
  const n = pendingCount();
  return `<div class="form review"><textarea data-review-text placeholder="${n ? 'Review summary (optional)' : 'Review summary'}">${esc(state.reviewText)}</textarea>
    <div class="actions">${btn('data-submit="APPROVE"', 'Approve')}${btn('data-submit="REQUEST_CHANGES"', 'Request changes')}${btn('data-submit="COMMENT"', 'Comment', true)}
    <span class="spacer"></span>${C.pending ? btn('data-discard', 'Discard') : ''}</div>
    <div class="hint">${n ? `${plural(n, 'pending comment')} go out with the review.` : C.pending ? 'A review is pending without comments.' : 'No pending comments.'}</div></div>`;
}
const REVIEW_VERB = { APPROVED: 'approved', CHANGES_REQUESTED: 'requested changes', COMMENTED: 'commented', DISMISSED: 'review dismissed' };
function conversationHtml() {
  if (!API) return '';
  const items = [...C.comments.map(c => ({ ...c, verb: 'commented' })),
    ...C.reviews.filter(r => r.body || r.state !== 'COMMENTED').map(r => ({ ...r, verb: REVIEW_VERB[r.state] || r.state.toLowerCase() }))]
    .sort((a, b) => a.created.localeCompare(b.created));
  let h = `<h2>Conversation</h2>`;
  if (!C.loaded) h += '<p class="hint">Loading…</p>';
  else if (!items.length) h += '<p class="hint">No comments yet.</p>';
  for (const it of items) {
    h += `<div class="comment ${it.state === 'APPROVED' ? 'approved' : it.state === 'CHANGES_REQUESTED' ? 'changes' : ''}"><div class="chead"><b>${esc(it.author)}</b> ${esc(it.verb)}<span class="hint">${ago(it.created)}</span><span class="spacer"></span><a href="${it.url}" target="_blank" title="Open on GitHub">↗</a></div>
      ${it.body ? `<div class="cbody">${bodyHtml(it)}</div>` : ''}</div>`;
  }
  h += `<div class="form conv"><textarea data-conv-text placeholder="Leave a comment on the pull request">${esc(state.convText)}</textarea>
    <div class="actions"><span class="spacer"></span>${btn('data-conv-send', 'Comment', true)}</div></div>`;
  const threads = C.threads;
  if (threads.length) {
    const open = threads.filter(t => !t.resolved).length;
    h += `<h2>Review threads</h2><p class="hint">${open} unresolved · ${threads.length - open} resolved</p><div class="scrollx"><table class="plain threads">`;
    for (const t of [...threads].sort((a, b) => a.resolved - b.resolved || a.path.localeCompare(b.path) || (a.line || 0) - (b.line || 0))) {
      const f = BY_PATH[t.path], c = t.comments[0];
      const where = `${esc(t.path.split('/').pop())}:${lineRange(t.start_line, t.line ?? t.original_line)}`;
      h += `<tr class="${t.resolved ? 'resolved' : ''}"><td>${f ? `<a href="#" data-thread-go="${esc(t.id)}" class="mono">${where}</a>` : `<span class="mono" title="not in this diff">${where}</span>`}</td>
        <td class="hint">${t.resolved ? 'resolved' : t.outdated ? 'outdated' : c.pending ? 'pending' : ''}</td><td>${esc(c.author)}</td><td class="wrap">${esc(excerpt(c.body))}${t.comments.length > 1 ? ` <span class="hint">+${t.comments.length - 1}</span>` : ''}</td></tr>`;
    }
    h += '</table></div>';
  }
  return h;
}

// ---------- actions
function goThread(t) {
  const f = BY_PATH[t.path];
  if (!f) return;
  if (t.outdated || t.line == null) { state.showOutdated.add(f.path); go(t.path); return; }
  go(t.path, rowKey(f, t.side, t.line));
}
function openCompose(path, side, line, extend) {
  const c = state.compose;
  if (extend && c && c.path === path && c.side === side) { c.startLine = Math.min(c.startLine, c.line, line); c.line = Math.max(c.startLine, c.line, line); }
  else state.compose = { path, side, line, startLine: line, text: c && c.path === path ? c.text : '' };
  state.focus = '[data-compose-text]';
  render();
}
function textareaKey(e) {
  const form = e.target.closest('.form');
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { const b = form && form.querySelector('button.primary'); if (b && !b.disabled) b.click(); e.preventDefault(); }
  else if (e.key === 'Escape') { const b = form && form.querySelector('[data-compose-cancel],[data-reply-cancel]'); if (b) b.click(); else e.target.blur(); }
}
const COMMENT_ATTRS = '[data-compose-cancel],[data-suggest],[data-compose-send],[data-reply],[data-reply-cancel],[data-reply-send],[data-resolve],[data-delete],[data-thread-open],[data-thread-close],[data-thread-go],[data-outdated],[data-review-panel],[data-submit],[data-discard],[data-conv-send],[data-comments-reload]';
// Handle a click on a comment control; true when the element was one.
async function commentClick(t, e) {
  const d = t.dataset;
  if (d.composeCancel !== undefined) { state.compose = null; render(); return true; }
  if (d.suggest !== undefined) {
    const c = state.compose, f = BY_PATH[c.path];
    c.text = (c.text && !c.text.endsWith('\n') ? c.text + '\n' : c.text) + suggestionTemplate(f, c);
    state.focus = '[data-compose-text]'; render(); return true;
  }
  if (d.composeSend !== undefined) {
    const c = state.compose;
    if (!c.text.trim()) { state.focus = '[data-compose-text]'; render(); return true; }
    const body = { path: c.path, body: c.text, line: c.line, side: c.side, mode: d.composeSend };
    if (c.startLine !== c.line) { body.start_line = c.startLine; body.start_side = c.side; }
    if (await post('comment', body)) { state.compose = null; render(); }
    return true;
  }
  if (d.reply !== undefined) { state.replyOpen.set(d.reply, ''); state.focus = `[data-reply-text="${CSS.escape(d.reply)}"]`; render(); return true; }
  if (d.replyCancel !== undefined) { state.replyOpen.delete(d.replyCancel); render(); return true; }
  if (d.replySend !== undefined) {
    const text = state.replyOpen.get(d.replySend) || '';
    if (!text.trim()) return true;
    if (await post('reply', { thread: d.replySend, body: text, mode: d.mode })) { state.replyOpen.delete(d.replySend); render(); }
    return true;
  }
  if (d.resolve !== undefined) { await post('resolve', { thread: d.resolve, resolved: d.resolved === '1' }); return true; }
  if (d.delete !== undefined) { e.preventDefault(); await post('delete', { comment: d.delete }); return true; }
  if (d.threadOpen !== undefined) { state.openThreads.add(d.threadOpen); render(); return true; }
  if (d.threadClose !== undefined) { state.openThreads.delete(d.threadClose); render(); return true; }
  if (d.threadGo !== undefined) { e.preventDefault(); const th = C.threads.find(x => x.id === d.threadGo); if (th) goThread(th); return true; }
  if (d.outdated !== undefined) { e.preventDefault(); state.showOutdated.has(d.outdated) ? state.showOutdated.delete(d.outdated) : state.showOutdated.add(d.outdated); render(); return true; }
  if (d.reviewPanel !== undefined) { e.preventDefault(); state.reviewPanel = !state.reviewPanel; if (state.reviewPanel) state.focus = '[data-review-text]'; renderSide(); restoreFocus(); return true; }
  if (d.submit !== undefined) {
    if (!pendingCount() && !C.pending && !state.reviewText.trim() && d.submit === 'COMMENT') { state.focus = '[data-review-text]'; renderSide(); restoreFocus(); return true; }
    if (await post('submit', { event: d.submit, body: state.reviewText })) { state.reviewText = ''; state.reviewPanel = false; renderSide(); }
    return true;
  }
  if (d.discard !== undefined) {
    const n = pendingCount();
    if (confirm(n ? `Discard the pending review and its ${plural(n, 'comment')}?` : 'Discard the pending review?')) await post('discard', {});
    return true;
  }
  if (d.convSend !== undefined) {
    if (!state.convText.trim()) { state.focus = '[data-conv-text]'; render(); return true; }
    if (await post('conversation', { body: state.convText })) { state.convText = ''; render(); }
    return true;
  }
  if (d.commentsReload !== undefined) { e.preventDefault(); C.error = ''; render(); loadComments(); return true; }
  return false;
}
