// The comments the reader sends to the Claude Code session that works on the pull request instead of to GitHub, the
// threads they show as, and the sidebar footer's button that says whether a session listens for them.

// CL holds the server's answer to /claude, and `error` why the reader's last action on it failed, until the next one.
// `started` and `applied` count the loads and the reader's actions as LOADS does for the comments, so a load that
// answers after a later load or action changes nothing. `copied` is when the reader last copied the connect prompt,
// and `menu` whether the footer's menu is open.
const CL = { loaded: false, listening: false, comments: [], error: '', timer: 0, started: 0, applied: 0, copied: 0, menu: false };
const CLAUDE_POLL_MS = 5000;
const CLAUDE_KEY = 'dr:claude';
// The words of the tag the reader's last message shows while it waits for Claude's reply.
const CLAUDE_TAGS = { waiting: 'waiting for Claude', sent: 'sent to Claude' };
// The statuses that end a pull request; the server then deletes its comments for Claude.
const CLAUDE_ENDED = ['merged', 'closed'];

const claudeOn = () => !!API && lsGet(CLAUDE_KEY) !== 'off';
// The key of the thread of the comment with that id, in the threads and in `state.replyOpen`.
const claudeKey = (id) => `claude:${id}`;
const connectPrompt = () => `Listen for differ comments on ${PR.repo}#${PR.number} at ws://127.0.0.1:${location.port}${API}/listen`;

async function loadClaude() {
  clearTimeout(CL.timer); CL.timer = 0;
  if (!claudeOn()) return;
  const load = ++CL.started;
  let d = null;
  try { d = await api(`claude?head=${PR.head}`); } catch { /* the next load tries again */ }
  if (d) showClaude(load, d);
  pollClaude();
}
// Load the comments for Claude again in CLAUDE_POLL_MS, or later while the page is hidden, in place of the load
// planned before.
function pollClaude() {
  clearTimeout(CL.timer);
  CL.timer = setTimeout(() => { if (document.visibilityState === 'visible') loadClaude(); else pollClaude(); }, CLAUDE_POLL_MS);
}
// Show the server's answer: the threads when the comments changed, else the footer when the session came or went.
// A reply being written in a thread that is gone goes with it.
function applyClaude(d) {
  const threads = JSON.stringify(d.comments) !== JSON.stringify(CL.comments), footer = d.listening !== CL.listening;
  const ids = new Set(d.comments.map(c => claudeKey(c.id)));
  for (const key of state.replyOpen.keys()) if (key.startsWith(claudeKey('')) && !ids.has(key)) state.replyOpen.delete(key);
  Object.assign(CL, d, { loaded: true });
  if (threads) { threadsChanged(); render(); } else if (footer) renderSide();
}
// Show the server's answer to the load or action with that number, unless the answer to a later one shows already.
function showClaude(load, d) {
  if (load < CL.applied) return;
  CL.applied = load;
  applyClaude(d);
}
// Send an action on the comments for Claude, with the page's head commit, and show the server's answer. Throws when
// the server refuses the action.
async function postClaude(action, body) {
  const load = ++CL.started;
  showClaude(load, await api(action, { ...body, head: PR.head }));
}
// The setting changed in the settings of the shell, which share this page's storage.
window.addEventListener('storage', (e) => {
  if (e.key !== CLAUDE_KEY) return;
  loadClaude(); threadsChanged(); render();
});

// The lines of code a comment box sits on, as the file shows them.
function claudeCode(f, c) {
  if (c.side === 'RIGHT' || f.content_side === 'old') return contentLines(f).slice(c.startLine - 1, c.line);
  return f.hunks.flatMap(h => h.lines.filter(([tag, o]) => tag === '-' && o >= c.startLine && o <= c.line).map(l => l[3]));
}
async function sendToClaude(c) {
  const body = { path: c.path, line: c.line, start_line: c.startLine !== c.line ? c.startLine : null, side: c.side,
    commit: PR.head, code: claudeCode(BY_PATH[c.path], c), body: c.text };
  state.compose = null; CL.error = '';
  await sendClaude('claude', body, () => { c.claude = true; restoreCompose(c); });
}
// Send the reader's reply in the thread of the comment with that id. When it fails, its text goes back to its box.
async function followUp(id) {
  const key = claudeKey(id), text = state.replyOpen.get(key) || '';
  if (!text.trim()) return;
  state.replyOpen.delete(key);
  await sendClaude('claude_follow_up', { id, body: text }, () => restoreReply(key, text));
}
// Send the reader's text to Claude and show the server's answer. When the server refuses it, undo puts the text back.
async function sendClaude(action, body, undo) {
  state.busy = true; render();
  try {
    await postClaude(action, body);
  } catch (e) {
    CL.error = `Send to Claude: ${e.message}`;
    undo();
  } finally { state.busy = false; }
  render();
}

// ---------- HTML
// The comments for Claude as threads, shaped like GitHub's: one on the lines the server placed it on in the head
// commit the page shows, else outdated.
function claudeThreads() {
  if (!claudeOn()) return [];
  return CL.comments.map(c => ({ id: claudeKey(c.id), claude: c, path: c.path, resolved: false, outdated: c.placed.line == null,
    line: c.placed.line, start_line: c.placed.start_line, side: c.side, original_line: c.line, original_start_line: c.start_line, comments: [] }));
}
function claudeThreadHtml(f, t) {
  const c = t.claude, ctx = { file: f, thread: t }, last = c.messages.map(m => m.author).lastIndexOf('reader');
  const tag = CLAUDE_TAGS[c.state] ? `<span class="tag claude">${CLAUDE_TAGS[c.state]}</span>` : '';
  const messages = c.messages.map((m, i) => {
    const who = m.author === 'claude' ? `${iconHtml('bot')}<b>Claude</b>` : `<b>${esc(C.viewer || 'you')}</b>`;
    const del = i === 0 ? `<span class="spacer"></span><a href="#" data-claude-delete="${esc(c.id)}">delete</a>` : '';
    return `<div class="comment"><div class="chead">${who}<span class="hint">${ago(m.at)}</span>
      ${i === last ? tag : ''}${del}</div><div class="cbody">${renderMarkdown(m.body, ctx)}</div></div>`;
  }).join('');
  const reply = state.replyOpen.has(t.id) ? replyHtml(t, btn(`data-claude-follow="${esc(c.id)}"`, 'Send to Claude', true))
    : `<div class="tfoot">${btn(`data-reply="${esc(t.id)}"`, 'Reply')}</div>`;
  return `<div class="thread card" data-thread="${esc(t.id)}">${threadHead(t)}${messages}${reply}</div>`;
}
// The comments for Claude on files this diff leaves out, for the overview, under the path each one is on.
function claudeOrphansHtml() {
  const threads = THREADS.orphans.filter(t => t.claude);
  if (!threads.length) return '';
  return `<h2>Comments for Claude on files outside this diff</h2>${threads.map(t => `<p class="hint mono">${esc(t.path)}${t.line == null || threadHead(t) ? '' : `:${t.line}`}</p>${claudeThreadHtml(null, t)}`).join('')}`;
}
function claudeFootHtml() {
  if (!claudeOn()) return '';
  const waiting = CL.comments.filter(c => c.state === 'waiting').length;
  const title = CL.listening ? 'Claude is listening' : `No Claude session${waiting ? ` · ${plural(waiting, 'comment')} waiting` : ''}`;
  const note = CL.listening
    ? 'A Claude Code session listens on this pull request. Comments you send to Claude go straight to it.'
    : `No Claude Code session listens on this pull request. Paste the connect prompt into the session that works on it.${waiting ? ` ${plural(waiting, 'comment')} will go to it when it connects.` : ''}`;
  const copy = CL.listening ? '' : `<button data-claude-connect>${Date.now() - CL.copied < 2000 ? 'Copied' : 'Copy connect prompt'}</button>`;
  const menu = CL.menu ? `<div class="menu"><div class="hint note">${note}</div>${copy}</div>` : '';
  return `<div class="foot claude"><span class="claude-pick"><button class="ghost session" data-claude-menu title="${title}">${iconHtml('bot')}<span class="dot${CL.listening ? ' on' : ''}"></span></button>${menu}</span>
    ${CL.error ? `<div class="hint err">${esc(CL.error)}</div>` : ''}</div>`;
}

// ---------- actions
// Copy the text through the Clipboard API, else through a selected textarea, which some embedded browsers allow when
// they refuse the API. False when both fail.
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* try the textarea */ }
  const box = document.createElement('textarea'), prev = document.activeElement;
  box.value = text; box.readOnly = true; box.style.position = 'fixed'; box.style.opacity = '0';
  document.body.appendChild(box); box.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch { /* ok stays false */ }
  box.remove();
  if (prev && prev.focus) prev.focus({ preventScroll: true });
  return ok;
}
const CLAUDE_ATTRS = '[data-claude-delete],[data-claude-connect],[data-claude-menu],[data-claude-follow]';
async function claudeClick(t) {
  const d = t.dataset;
  CL.error = '';
  if (d.claudeMenu !== undefined) { CL.menu = !CL.menu; renderSide(); return; }
  if (d.claudeConnect !== undefined) {
    if (!await copyText(connectPrompt())) { CL.error = `The clipboard refused the prompt. Copy it from here: ${connectPrompt()}`; renderSide(); return; }
    CL.copied = Date.now(); renderSide();
    setTimeout(renderSide, 2000);
    return;
  }
  if (d.claudeFollow !== undefined) { await followUp(d.claudeFollow); return; }
  if (d.claudeDelete !== undefined) {
    try { await postClaude('claude_delete', { id: d.claudeDelete }); } catch (e) { CL.error = e.message; renderSide(); }
  }
}
