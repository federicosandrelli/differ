// The comments the reader sends to the Claude Code session that works on the pull request instead of to GitHub, the
// threads they show as, and the sidebar footer's button that says whether the session listens for them and shows
// its terminal (page/53-terminal.js).

// CL holds the server's answer to /claude, with `descriptions`, the descriptions Claude gave the files by path,
// `session`, where the embedded session stands, and `error`, why the reader's last action on it failed, until the
// next one. `started` and `applied` count the loads and the reader's
// actions as LOADS does for the comments, so a load that answers after a later load or action changes nothing.
const CL = { loaded: false, listening: false, comments: [], descriptions: {}, session: null, error: '', timer: 0, started: 0, applied: 0 };
const CLAUDE_POLL_MS = 5000;
// The poll while the session starts or Claude works, so the dot and the terminal's header follow them.
const CLAUDE_BUSY_POLL_MS = 1000;
const CLAUDE_KEY = 'dr:claude';
// The words of the tag the reader's last message shows while it waits for Claude's reply.
const CLAUDE_TAGS = { waiting: 'waiting for Claude', sent: 'sent to Claude' };
// The statuses that end a pull request; the server then deletes its comments for Claude.
const CLAUDE_ENDED = ['merged', 'closed'];

const claudeOn = () => !!API && lsGet(CLAUDE_KEY) !== 'off';
// The key of the thread of the comment with that id, in the threads and in `state.replyOpen`.
const claudeKey = (id) => `claude:${id}`;

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
  const ms = CL.session && (CL.session.starting || CL.session.working) ? CLAUDE_BUSY_POLL_MS : CLAUDE_POLL_MS;
  CL.timer = setTimeout(() => { if (document.visibilityState === 'visible') loadClaude(); else pollClaude(); }, ms);
}
// Show the server's answer: the threads when the comments changed, the files when their descriptions changed or the
// session started or stopped taking quick prompts (the threads' Address with Claude buttons), else
// the footer when the session came or went, else the terminal's header when the session moved. A reply being written
// in a thread that is gone goes with it. The merge state loads again once Claude's work ends.
function applyClaude(d) {
  const threads = JSON.stringify(d.comments) !== JSON.stringify(CL.comments), footer = d.listening !== CL.listening;
  const descriptions = JSON.stringify(d.descriptions) !== JSON.stringify(CL.descriptions);
  const session = JSON.stringify(d.session) !== JSON.stringify(CL.session);
  const worked = CL.session?.working && !d.session?.working, ready = quickReady();
  const ids = new Set(d.comments.map(c => claudeKey(c.id)));
  for (const key of state.replyOpen.keys()) if (key.startsWith(claudeKey('')) && !ids.has(key)) state.replyOpen.delete(key);
  Object.assign(CL, d, { loaded: true });
  tellShellClaude();
  if (threads) threadsChanged();
  if (threads || descriptions || ready !== quickReady()) render(); else if (footer || session) renderSide();
  if (worked) watchMerge();
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
// A hidden window loads nothing, so a window that shows again loads at once, for the dot on the tab.
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') loadClaude(); });
window.addEventListener('storage', (e) => {
  if (e.key !== CLAUDE_KEY) return;
  loadClaude(); threadsChanged(); render(); tellShellClaude();
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
// The description Claude gave the file; empty when it has none, or while comments can't go to Claude.
const claudeDescription = (f) => claudeOn() && Object.hasOwn(CL.descriptions, f.path) ? CL.descriptions[f.path] : '';
const CLAUDE_CHEVRON = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
// The description under its title, whose chevron folds it to the title alone.
function claudeDescriptionHtml(f, body, open) {
  const chev = `<button class="chev ghost${open ? ' open' : ''}" data-claude-fold="${esc(f.path)}" aria-label="${open ? 'Hide' : 'Show'} Claude's description" aria-expanded="${open}">${CLAUDE_CHEVRON}</button>`;
  return `<div class="chead">${chev}${iconHtml('bot')}<b>Claude</b></div>${open ? `<div class="cbody">${renderMarkdown(body, { file: f })}</div>` : ''}`;
}
// The comments for Claude on files this diff leaves out, for the overview, under the path each one is on.
function claudeOrphansHtml() {
  const threads = THREADS.orphans.filter(t => t.claude);
  if (!threads.length) return '';
  return `<h2>Comments for Claude on files outside this diff</h2>${threads.map(t => `<p class="hint mono">${esc(t.path)}${t.line == null || threadHead(t) ? '' : `:${t.line}`}</p>${claudeThreadHtml(null, t)}`).join('')}`;
}
// Where the session stands, as the class of its dot: `ask` while Claude waits for the reader's answer to a question,
// `work` while Claude works, `on` while a session listens, `wait` while the session starts or runs without listening
// yet, and empty otherwise.
function claudeState() {
  const s = CL.session || {};
  return s.asking ? 'ask' : s.working ? 'work' : CL.listening ? 'on' : s.starting || s.running ? 'wait' : '';
}
const claudeDot = () => `<span class="dot ${claudeState()}"></span>`;
// Tell the shell where the session stands, for the dot on the page's tab.
function tellShellClaude() {
  if (SHELL) SHELL.claude(termAvailable() ? claudeState() : '', claudeStanding());
}
// What Claude does, for the bot's tooltip and the terminal's header: "working on" its task, or "working" without one.
const claudeWork = (s) => `working${s.task ? ` on ${s.task}` : ''}`;
// Where the session stands, in words.
function claudeStanding() {
  const s = CL.session || {}, waiting = CL.comments.filter(c => c.state === 'waiting').length;
  return s.asking ? 'Claude is waiting for your answer' : s.working ? `Claude is ${claudeWork(s)}` : CL.listening ? 'Claude is listening'
    : s.starting ? 'Claude is starting' : s.running ? 'Claude runs, but does not listen yet'
    : `${s.error || 'No Claude session'}${waiting ? ` · ${plural(waiting, 'comment')} waiting` : ''}`;
}
// The footer's buttons: the bot button, with the session's dot, shows and hides the terminal; the burger next to it
// opens the quick prompts; the arrow before them moves the terminal between the sidebar and its own column, and comes
// on the left so the bot keeps its place when it shows.
function claudeFootHtml() {
  if (!termAvailable()) return '';
  const dock = TERM.open ? `<button class="ghost" data-term-dock title="${TERM.dock === 'side' ? 'Move the terminal to its own column' : 'Move the terminal into this pane'}">${TERM.dock === 'side' ? ARROW_RIGHT : ARROW_LEFT}</button>` : '';
  const bot = `<button class="ghost session${TERM.open ? ' on' : ''}" data-term-toggle title="${esc(claudeStanding())} · ${TERM.open ? 'hide' : 'show'} the terminal">${iconHtml('bot')}${claudeDot()}</button>`;
  const more = `<button class="ghost claude-more" data-term-menu aria-label="Quick prompts" aria-haspopup="menu" aria-expanded="${TERM.menu}">${BURGER}</button>`;
  return `<div class="foot claude"><span class="claude-pick">${dock}<span class="seg">${bot}${more}</span>${quickMenuHtml()}</span>
    ${CL.error ? `<div class="hint err">${esc(CL.error)}</div>` : ''}</div>`;
}

// ---------- actions
const CLAUDE_ATTRS = '[data-claude-delete],[data-claude-follow],[data-claude-fold]';
async function claudeClick(t) {
  const d = t.dataset;
  CL.error = '';
  if (d.claudeFold !== undefined) {
    const folded = state.descriptionFolded;
    if (!folded.delete(d.claudeFold)) folded.add(d.claudeFold);
    renderKeepingHeader(BY_PATH[d.claudeFold]);
    return;
  }
  if (d.claudeFollow !== undefined) { await followUp(d.claudeFollow); return; }
  if (d.claudeDelete !== undefined) {
    try { await postClaude('claude_delete', { id: d.claudeDelete }); } catch (e) { CL.error = e.message; renderSide(); }
  }
}
