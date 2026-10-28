// Review threads from GitHub, placed on the lines they refer to, and the forms that write back.

// C.version counts the changes to the threads the page shows, so a file's rendered body knows when they changed.
const C = { loaded: false, loadedAt: 0, version: 0, error: '', viewer: '', head: '', status: '', pending: null, verdict: null, threads: [], reviews: [], comments: [] };
const THREADS = { byFile: new Map(), orphans: [] };
// The number of comment loads started, and the number of the load the page shows. A load that answers after a later
// one changes nothing.
const LOADS = { started: 0, applied: 0 };
// The comments the viewer sent that the page shows before GitHub gives them back, in the order sent. Each entry holds
// the comment and either `thread`, the new thread it starts, or `replyTo`, the id of the thread it answers. Once GitHub
// takes the comment, the comment holds GitHub's id when GitHub's answer has it, and `takenAtLoad` the number of loads
// started by then. A load drops the entry once the comment arrived.
let sendingComments = [];
let localThreads = 0;

async function api(action, body) {
  const init = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const d = await (await fetch(`${API}/${action}`, init)).json();
  if (d.error) throw new Error(d.error);
  return d;
}
async function loadComments() {
  if (!API) return;
  const load = ++LOADS.started;
  try {
    const d = await api('comments');
    if (load < LOADS.applied) return;
    Object.assign(C, d, { loaded: true, error: '', loadedAt: Date.now() });
    const ids = new Set(C.threads.flatMap(t => t.comments.map(c => c.id)));
    sendingComments = sendingComments.filter(s => !arrived(s, ids, load));
  } catch (e) {
    if (load < LOADS.applied) return;
    C.error = e.message;
  }
  LOADS.applied = load;
  threadsChanged();
  render();
  if (SHELL) { SHELL.badge(C.threads.filter(t => !t.resolved).length); if (C.status) SHELL.status(C.status); }
}
// Whether the page holds text the user typed that GitHub has yet to take. Closing the page's tab would lose it.
function hasDraft() {
  const b = state.bug;
  return !!((state.compose && state.compose.text.trim()) || state.drafts.size || [...state.replyOpen.values()].some(t => t.trim())
    || state.reviewText.trim() || state.convText.trim() || (b.open && !b.prompt && b.text.trim())
    || sendingComments.some(s => s.comment.sending));
}
// Send an action to GitHub. Returns GitHub's answer, or null when GitHub refused the action, with the reason in C.error.
async function send(action, body) {
  state.busy = true; render();
  try {
    return await api(action, body);
  } catch (e) {
    C.error = e.message;
    return null;
  } finally { state.busy = false; }
}
// Send an action to GitHub, then reload the comments; false when GitHub refused it.
async function post(action, body) {
  if (!await send(action, body)) { render(); return false; }
  await loadComments();
  return true;
}
// Send a comment to GitHub, showing it at once in entry's thread with a sending tag until GitHub takes it, then reload
// the comments. The comment stays until a load ends the wait for GitHub's copy (arrived). When GitHub refuses it, it
// leaves and undo runs.
async function postComment(action, body, entry, undo) {
  sendingComments.push(entry); threadsChanged();
  const d = await send(action, body);
  if (!d) {
    sendingComments = sendingComments.filter(s => s !== entry);
    threadsChanged(); undo(); render();
    return;
  }
  Object.assign(entry.comment, { id: d.comment || '', sending: false });
  entry.takenAtLoad = LOADS.started;
  threadsChanged(); render();
  await loadComments();
}
// Whether a load with this number, showing comments with these ids, ends the wait for GitHub's copy of a sending
// comment. A load that shows the comment's id ends it. Otherwise the first load started after GitHub took the comment
// ends it, or the second when GitHub answered with an id, since a read just after a write can miss the comment.
function arrived(s, ids, load) {
  if (s.takenAtLoad === undefined) return false;
  return ids.has(s.comment.id) || load > s.takenAtLoad + (s.comment.id ? 1 : 0);
}
// A comment the viewer is sending, shaped like GitHub's. It is pending when GitHub will put it in a pending review: a
// reply joins the pending review only when there is one, while a new thread starts one.
const sendingComment = (body, mode, reply) => ({ id: '', author: C.viewer || 'you', body, html: '', created: new Date().toISOString(), url: '',
  pending: mode === 'review' && (!reply || !!C.pending), reply, sending: true });
// The thread a comment box starts, shaped like GitHub's, with the comment in it. It is `local`: GitHub has no copy yet.
const localThread = (c, comment) => ({ id: `local:${++localThreads}`, path: c.path, resolved: false, outdated: false, resolved_by: '', local: true,
  line: c.line, start_line: c.startLine !== c.line ? c.startLine : null, side: c.side, start_side: c.startLine !== c.line ? c.side : null,
  original_line: c.line, original_start_line: c.startLine !== c.line ? c.startLine : null, comments: [comment] });
function threadsChanged() { C.version++; indexThreads(); }
// The comments being sent, without their text, and the loads.
const sendingSummary = () => sendingComments.map(s => ({ thread: s.thread ? s.thread.id : null, replyTo: s.replyTo ?? null, taken: !s.comment.sending, takenAtLoad: s.takenAtLoad ?? null }));
const loadsSummary = () => `load ${LOADS.applied} of ${LOADS.started}`;

// GitHub's threads with the comments being sent: replies at the end of their threads, then the new threads.
function shownThreads() {
  const threads = C.threads.map(t => {
    const replies = sendingComments.filter(s => s.replyTo === t.id).map(s => s.comment);
    return replies.length ? { ...t, comments: [...t.comments, ...replies] } : t;
  });
  return [...threads, ...sendingComments.filter(s => s.thread).map(s => s.thread)];
}
function indexThreads() {
  THREADS.byFile = new Map(); THREADS.orphans = [];
  for (const t of shownThreads()) {
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
const hasReviewSummary = () => !!state.reviewText.trim();
// The viewer's verdict on the head commit, APPROVED or CHANGES_REQUESTED, or '' when they gave none on it. A verdict
// on an older commit lets the viewer review the new commits.
const headVerdict = () => C.verdict && C.verdict.commit === C.head ? C.verdict.state : '';
// The verdict each review event gives, and why the event waits when the viewer gave that verdict on the head already.
const REPEAT = { APPROVE: ['APPROVED', 'You approved this commit already'], REQUEST_CHANGES: ['CHANGES_REQUESTED', 'You requested changes on this commit already'] };
// Why a review event can't go out now, or '' when it can. Request changes also waits for a review summary. The
// button's title says why only while it waits. While the pointer rests on the button, shell/tooltip.js holds its
// title in data-tip and puts it back when the pointer leaves; an empty title is no title.
function submitTip(event) {
  const r = REPEAT[event];
  if (r && headVerdict() === r[0]) return r[1];
  return event === 'REQUEST_CHANGES' && !hasReviewSummary() ? 'Requesting changes needs a review summary' : '';
}
const submitBtn = (event, label) => btn(`data-submit="${event}" title="${submitTip(event)}"`, label, false, !!submitTip(event));
function updateRequestChanges(b) {
  b.disabled = state.busy || !!submitTip('REQUEST_CHANGES');
  if (b.dataset.tip !== undefined) b.dataset.tip = submitTip('REQUEST_CHANGES'); else b.title = submitTip('REQUEST_CHANGES');
}
function pendingCount() { return C.threads.reduce((a, t) => a + t.comments.filter(c => c.pending).length, 0); }
const excerpt = (s) => { const t = s.replace(/```[\s\S]*?```/g, '').replace(/\s+/g, ' ').trim(); return t.length > 90 ? t.slice(0, 90) + '…' : t; };
const lineRange = (a, b) => a && a !== b ? `${a}–${b}` : `${b}`;
const btn = (attrs, label, primary, off) => `<button ${attrs} class="${primary ? 'primary' : ''}" ${state.busy || off ? 'disabled' : ''}>${label}</button>`;

// ---------- HTML
// GitHub's own rendering of a body, when it has one, except for suggestions: the page draws those against the file.
function bodyHtml(c, ctx) {
  if (!c.html || /^\s*```suggestion/m.test(c.body)) return renderMarkdown(c.body, ctx);
  return c.html.replace(/<a\b(?![^>]*\btarget=)/g, '<a target="_blank"')
    .replace(/(<img\b[^>]*?\ssrc=")([^"]*)"/g, (m, head, src) => `${head}${esc(imageSrc(unesc(src)))}"`);
}
function commentHtml(c, ctx) {
  const links = !c.url ? '' : `${c.pending ? `<a href="#" data-delete="${esc(c.id)}">delete</a>` : ''}<a href="${c.url}" target="_blank" title="Open on GitHub">↗</a>`;
  return `<div class="comment ${c.pending ? 'pending' : ''}"><div class="chead"><b>${esc(c.author)}</b><span class="hint">${ago(c.created)}</span>
    ${c.pending ? '<span class="tag pending">pending</span>' : ''}${c.sending ? '<span class="tag sending">sending</span>' : ''}<span class="spacer"></span>${links}</div>
    <div class="cbody">${bodyHtml(c, ctx)}</div></div>`;
}
function threadHtml(f, t) {
  const n = t.comments.length, first = t.comments[0];
  if (t.resolved && !state.openThreads.has(t.id)) {
    return `<div class="thread card folded" data-thread-open="${esc(t.id)}">✓ Resolved${t.resolved_by ? ` by ${esc(t.resolved_by)}` : ''} · ${esc(first.author)}: <span class="hint">${esc(excerpt(first.body))}</span> · ${plural(n, 'comment')}</div>`;
  }
  const head = t.outdated ? `<div class="thead">Outdated · was on line${t.original_start_line && t.original_start_line !== t.original_line ? 's' : ''} ${lineRange(t.original_start_line, t.original_line)}${t.side === 'LEFT' ? ' of the old file' : ''}</div>`
    : t.start_line && t.start_line !== t.line ? `<div class="thead">Lines ${lineRange(t.start_line, t.line)}</div>` : '';
  const reply = t.local ? '' : state.replyOpen.has(t.id) ? replyHtml(t) : `<div class="tfoot">${btn(`data-reply="${esc(t.id)}"`, 'Reply')}
    ${btn(`data-resolve="${esc(t.id)}" data-resolved="${t.resolved ? 0 : 1}"`, t.resolved ? 'Unresolve' : 'Resolve')}
    ${t.resolved ? `<button data-thread-close="${esc(t.id)}">Hide</button>` : ''}</div>`;
  return `<div class="thread card ${t.resolved ? 'is-resolved' : ''}" data-thread="${esc(t.id)}">${head}${t.comments.map(c => commentHtml(c, { file: f, thread: t })).join('')}${reply}</div>`;
}
function replyHtml(t) {
  return `<div class="form reply"><textarea data-reply-text="${esc(t.id)}" placeholder="Reply">${esc(state.replyOpen.get(t.id) || '')}</textarea>
    <div class="actions"><span class="spacer"></span>${btn(`data-reply-cancel="${esc(t.id)}"`, 'Cancel')}
    ${btn(`data-reply-send="${esc(t.id)}" data-mode="single"`, 'Add single comment')}${btn(`data-reply-send="${esc(t.id)}" data-mode="review"`, C.pending ? 'Add review comment' : 'Start a review', true)}</div></div>`;
}
function composeHtml(f, c) {
  const where = `line${c.startLine !== c.line ? 's' : ''} ${lineRange(c.startLine, c.line)}${c.side === 'LEFT' ? ' of the old file' : ''}`;
  return `<div class="form card compose"><div class="hint">Comment on ${where} · shift-click another line number to extend</div>
    <textarea data-compose-text="${esc(draftKey(c.path, c.side, c.line))}" placeholder="Leave a comment">${esc(c.text)}</textarea>
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
  const refused = [...state.drafts].filter(([, d]) => d.refused).map(([key, d]) =>
    `<div class="notice">GitHub refused your comment on line ${d.line} of ${esc(d.path.split('/').pop())}. It is a draft on its line · <a href="#" data-draft-open="${esc(key)}">open it</a></div>`).join('');
  return commentsStatus() + refused;
}
function commentsStatus() {
  if (C.error) return `<div class="notice err">Comments: ${esc(C.error)} · <a href="#" data-comments-reload>retry</a></div>`;
  if (!C.loaded) return '<div class="notice">Loading comments…</div>';
  const stale = C.head && !PR.head.startsWith(C.head) && !C.head.startsWith(PR.head);
  return stale ? `<div class="notice err">The pull request has new commits · <a href="?refresh=1">rebuild the page</a></div>` : '';
}
function reviewPanelHtml() {
  const n = pendingCount();
  return `<div class="form card review"><textarea data-review-text placeholder="Review summary">${esc(state.reviewText)}</textarea>
    <div class="actions">${submitBtn('APPROVE', 'Approve')}${submitBtn('REQUEST_CHANGES', 'Request changes')}${btn('data-submit="COMMENT"', 'Comment')}
    <span class="spacer"></span>${C.pending ? btn('data-discard', 'Discard') : ''}</div>
    <div class="hint">${n ? `${plural(n, 'pending comment')} ${n === 1 ? 'goes' : 'go'} out with the review.` : C.pending ? 'A review is pending without comments.' : 'No pending comments.'}</div></div>`;
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
    h += `<div class="comment card ${it.state === 'APPROVED' ? 'approved' : it.state === 'CHANGES_REQUESTED' ? 'changes' : ''}"><div class="chead"><b>${esc(it.author)}</b> ${esc(it.verb)}<span class="hint">${ago(it.created)}</span><span class="spacer"></span><a href="${it.url}" target="_blank" title="Open on GitHub">↗</a></div>
      ${it.body ? `<div class="cbody">${bodyHtml(it)}</div>` : ''}</div>`;
  }
  h += `<div class="form card conv"><textarea data-conv-text placeholder="Leave a comment on the pull request">${esc(state.convText)}</textarea>
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
// A replaced comment box with text stays as a draft, kept under the last line of its range.
const draftKey = (path, side, line) => `${side}:${line}:${path}`;
function takeDraft(path, side, line) {
  const key = draftKey(path, side, line), d = state.drafts.get(key);
  state.drafts.delete(key);
  if (d) delete d.refused;
  return d;
}
function openCompose(path, side, line, extend) {
  const c = state.compose;
  if (extend && c && c.path === path && c.side === side) {
    c.startLine = Math.min(c.startLine, c.line, line); c.line = Math.max(c.startLine, c.line, line);
    const d = takeDraft(path, side, c.line);
    if (d) c.text = joinDrafts(d.text, c.text);
  } else {
    if (c && c.text.trim()) state.drafts.set(draftKey(c.path, c.side, c.line), c);
    state.compose = takeDraft(path, side, line) || { path, side, line, startLine: line, text: '' };
  }
  state.focus = '[data-compose-text]';
  render();
}
const joinDrafts = (first, then) => then.trim() ? first.trimEnd() + '\n\n' + then : first;
// Open a comment box again with its text, joined by the text of a box or draft written on its line since. While a box
// with text is open on another line, that box stays and the comment becomes a draft on its own line, marked `refused`.
// The box takes focus unless another textarea has it.
function restoreCompose(c) {
  const open = state.compose, key = draftKey(c.path, c.side, c.line), draft = takeDraft(c.path, c.side, c.line);
  if (draft) c.text = joinDrafts(c.text, draft.text);
  const sameLine = open && draftKey(open.path, open.side, open.line) === key;
  if (open && !sameLine && open.text.trim()) {
    c.refused = true;
    state.drafts.set(key, c);
    return;
  }
  if (sameLine) c.text = joinDrafts(c.text, open.text);
  state.compose = c;
  if (!typingElsewhere('[data-compose-text]')) state.focus = '[data-compose-text]';
}
// Whether a textarea that selector misses has focus.
function typingElsewhere(selector) {
  const el = document.activeElement;
  return !!el && el.tagName === 'TEXTAREA' && !el.matches(selector);
}
// The draft the state holds for a form's textarea.
function draftText(box) {
  const d = box.dataset;
  if (d.composeText !== undefined) return state.compose ? state.compose.text : '';
  if (d.replyText !== undefined) return state.replyOpen.get(d.replyText) || '';
  if (d.reviewText !== undefined) return state.reviewText;
  if (d.convText !== undefined) return state.convText;
  return box.value;
}
// The heights the reader dragged textareas to, by the textarea's data attributes.
const boxHeights = new Map();
const boxKey = (box) => JSON.stringify({ ...box.dataset });
// The textarea in el that has focus, by its data attributes, with its selection; null when none has.
function focusedBox(el) {
  const box = document.activeElement;
  if (!box || box.tagName !== 'TEXTAREA' || !el.contains(box)) return null;
  return { key: boxKey(box), selection: [box.selectionStart, box.selectionEnd, box.selectionDirection] };
}
// Give focus back to the textarea in el with the data attributes of kept, with its selection, unless some other element
// holds focus.
function refocusBox(el, kept) {
  if (!kept || (document.activeElement && document.activeElement !== document.body)) return;
  const box = [...el.getElementsByTagName('textarea')].find(b => boxKey(b) === kept.key);
  if (!box) return;
  box.focus({ preventScroll: true });
  box.setSelectionRange(...kept.selection);
}
function keepBoxHeights(el) {
  for (const box of el.getElementsByTagName('textarea')) if (box.style.height) boxHeights.set(boxKey(box), box.style.height);
}
// Give each textarea its draft from the state and the height the reader dragged it to. The HTML it came from can
// predate the latest typing.
function fillBoxes(el) {
  for (const box of el.getElementsByTagName('textarea')) {
    box.value = draftText(box);
    box.style.height = boxHeights.get(boxKey(box)) || '';
  }
}
function textareaKey(e) {
  const form = e.target.closest('.form');
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { const b = form && form.querySelector('button.primary, button[data-submit="COMMENT"]'); if (b && !b.disabled) b.click(); e.preventDefault(); }
  else if (e.key === 'Escape') { const b = form && form.querySelector('[data-bug-cancel]'); if (b) b.click(); else e.target.blur(); }
}
const COMMENT_ATTRS = '[data-compose-cancel],[data-suggest],[data-compose-send],[data-reply],[data-reply-cancel],[data-reply-send],[data-resolve],[data-delete],[data-thread-open],[data-thread-close],[data-thread-go],[data-outdated],[data-review-panel],[data-submit],[data-discard],[data-conv-send],[data-comments-reload],[data-draft-open]';
// Handle a click on a comment control; true when the element was one.
async function commentClick(t, e) {
  const d = t.dataset;
  if (d.composeCancel !== undefined) { state.compose = null; render(); return true; }
  if (d.suggest !== undefined) {
    const c = state.compose, f = BY_PATH[c.path];
    c.text = (c.text && !c.text.endsWith('\n') ? c.text + '\n' : c.text) + suggestionTemplate(f, c);
    const box = document.querySelector('[data-compose-text]');
    if (box) box.value = c.text;
    state.focus = '[data-compose-text]'; render(); return true;
  }
  if (d.composeSend !== undefined) {
    const c = state.compose;
    if (!c.text.trim()) { state.focus = '[data-compose-text]'; render(); return true; }
    const body = { path: c.path, body: c.text, line: c.line, side: c.side, mode: d.composeSend };
    if (c.startLine !== c.line) { body.start_line = c.startLine; body.start_side = c.side; }
    const comment = sendingComment(c.text, d.composeSend, false);
    state.compose = null;
    await postComment('comment', body, { thread: localThread(c, comment), comment }, () => restoreCompose(c));
    return true;
  }
  if (d.reply !== undefined) { state.replyOpen.set(d.reply, ''); state.focus = `[data-reply-text="${CSS.escape(d.reply)}"]`; render(); return true; }
  if (d.replyCancel !== undefined) { state.replyOpen.delete(d.replyCancel); render(); return true; }
  if (d.replySend !== undefined) {
    const text = state.replyOpen.get(d.replySend) || '';
    if (!text.trim()) return true;
    const id = d.replySend;
    state.replyOpen.delete(id);
    await postComment('reply', { thread: id, body: text, mode: d.mode }, { replyTo: id, comment: sendingComment(text, d.mode, true) }, () => {
      const box = `[data-reply-text="${CSS.escape(id)}"]`;
      state.replyOpen.set(id, joinDrafts(text, state.replyOpen.get(id) || ''));
      if (!typingElsewhere(box)) state.focus = box;
    });
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
    if (!hasReviewSummary() && (d.submit === 'REQUEST_CHANGES' || (d.submit === 'COMMENT' && !pendingCount() && !C.pending))) { state.focus = '[data-review-text]'; renderSide(); restoreFocus(); return true; }
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
  if (d.draftOpen !== undefined) {
    e.preventDefault();
    const draft = state.drafts.get(d.draftOpen), f = draft && BY_PATH[draft.path];
    if (f) { openCompose(draft.path, draft.side, draft.line, false); go(draft.path, rowKey(f, draft.side, draft.line)); }
    return true;
  }
  if (d.commentsReload !== undefined) { e.preventDefault(); C.error = ''; render(); loadComments(); return true; }
  return false;
}
