// The comments the reader sends to the Claude Code session that works on the pull request instead of to GitHub, the
// threads they show as, and the sidebar footer that says whether a session listens for them.

// CL holds the server's answer to /claude. `started` and `applied` count the loads as LOADS does for the comments, and
// `copied` is when the reader last copied the connect prompt.
const CL = { loaded: false, listening: false, comments: [], error: '', timer: 0, started: 0, applied: 0, copied: 0 };
const CLAUDE_POLL_MS = 5000;
const CLAUDE_KEY = 'dr:claude';
// The words of the tag a comment shows while it waits for Claude's reply.
const CLAUDE_TAGS = { waiting: 'waiting for Claude', sent: 'sent to Claude' };

const claudeOn = () => !!API && lsGet(CLAUDE_KEY) !== 'off';
const connectPrompt = () => `Listen for differ comments on ${PR.repo}#${PR.number} at ws://127.0.0.1:${location.port}${API}/listen`;

async function loadClaude() {
  clearTimeout(CL.timer); CL.timer = 0;
  if (!claudeOn()) return;
  const load = ++CL.started;
  let d = null, error = '';
  try { d = await api('claude'); } catch (e) { error = e.message; }
  if (load < CL.applied) return;
  CL.applied = load;
  if (d) applyClaude(d);
  else if (CL.error !== error) { CL.error = error; renderSide(); }
  pollClaude();
}
// Load the comments for Claude again in CLAUDE_POLL_MS, or later while the page is hidden.
function pollClaude() {
  CL.timer = setTimeout(() => { if (document.visibilityState === 'visible') loadClaude(); else pollClaude(); }, CLAUDE_POLL_MS);
}
// Show the server's answer: the threads when the comments changed, else the footer when the session came or went.
function applyClaude(d) {
  const threads = JSON.stringify(d.comments) !== JSON.stringify(CL.comments), footer = d.listening !== CL.listening || !!CL.error;
  Object.assign(CL, d, { loaded: true, error: '' });
  if (threads) { threadsChanged(); render(); } else if (footer) renderSide();
}
// The setting changed in the settings of the shell, which share this page's storage.
window.addEventListener('storage', (e) => {
  if (e.key !== CLAUDE_KEY) return;
  if (claudeOn()) loadClaude(); else clearTimeout(CL.timer);
  threadsChanged(); render();
});

// The lines of code a comment box sits on, as the file shows them.
function claudeCode(f, c) {
  if (c.side === 'RIGHT' || f.content_side === 'old') return contentLines(f).slice(c.startLine - 1, c.line);
  return f.hunks.flatMap(h => h.lines.filter(([tag, o]) => tag === '-' && o >= c.startLine && o <= c.line).map(l => l[3]));
}
async function sendToClaude(c) {
  const body = { path: c.path, line: c.line, start_line: c.startLine !== c.line ? c.startLine : null, side: c.side,
    commit: PR.head, code: claudeCode(BY_PATH[c.path], c), body: c.text };
  state.compose = null; state.busy = true; CL.error = ''; render();
  try {
    applyClaude(await api('claude', body));
  } catch (e) {
    CL.error = e.message;
    restoreCompose(c);
  } finally { state.busy = false; }
  render();
}

// ---------- HTML
// The comments for Claude as threads, shaped like GitHub's: one on a line of the head commit the page shows, else
// outdated.
function claudeThreads() {
  if (!claudeOn()) return [];
  return CL.comments.map(c => {
    const here = c.commit === PR.head;
    return { id: `claude:${c.id}`, claude: c, path: c.path, resolved: false, outdated: !here, line: here ? c.line : null,
      start_line: c.start_line, side: c.side, original_line: c.line, original_start_line: c.start_line, comments: [] };
  });
}
function claudeThreadHtml(f, t) {
  const c = t.claude, ctx = { file: f, thread: t };
  const tag = CLAUDE_TAGS[c.state] ? `<span class="tag claude">${CLAUDE_TAGS[c.state]}</span>` : '';
  const reply = c.reply ? `<div class="comment"><div class="chead">${iconHtml('claude')}<b>Claude</b><span class="hint">${ago(c.answered)}</span></div>
    <div class="cbody">${renderMarkdown(c.reply, ctx)}</div></div>` : '';
  return `<div class="thread card" data-thread="${esc(t.id)}">${threadHead(t)}<div class="comment"><div class="chead"><b>${esc(C.viewer || 'you')}</b><span class="hint">${ago(c.created)}</span>
    ${tag}<span class="spacer"></span><a href="#" data-claude-delete="${esc(c.id)}">delete</a></div>
    <div class="cbody">${renderMarkdown(c.body, ctx)}</div></div>${reply}</div>`;
}
function claudeFootHtml() {
  if (!claudeOn()) return '';
  const waiting = CL.comments.filter(c => c.state === 'waiting').length;
  const words = CL.listening ? 'Claude is listening' : `No Claude session${waiting ? ` · ${plural(waiting, 'comment')} waiting` : ''}`;
  const copy = CL.listening ? '' : `<button class="ghost" data-claude-connect title="Paste it into the Claude Code session that works on this pull request">${Date.now() - CL.copied < 2000 ? 'Copied' : 'Copy connect prompt'}</button>`;
  return `<div class="foot claude ${CL.listening ? 'on' : ''}"><span class="state">${iconHtml('claude', CL.listening ? 'accent' : '')}${words}</span>${copy}
    ${CL.error ? `<div class="hint err">${esc(CL.error)}</div>` : ''}</div>`;
}

// ---------- actions
const CLAUDE_ATTRS = '[data-claude-delete],[data-claude-connect]';
async function claudeClick(t) {
  const d = t.dataset;
  if (d.claudeConnect !== undefined) {
    try { await navigator.clipboard.writeText(connectPrompt()); } catch (e) { CL.error = `Could not copy the prompt: ${e.message}`; renderSide(); return; }
    CL.copied = Date.now(); renderSide();
    setTimeout(renderSide, 2000);
    return;
  }
  if (d.claudeDelete !== undefined) {
    try { applyClaude(await api('claude_delete', { id: d.claudeDelete })); } catch (e) { CL.error = e.message; renderSide(); }
  }
}
