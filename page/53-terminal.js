// The terminal that shows the pull request's Claude Code session. The server runs the session in a pseudoterminal and
// relays it over a WebSocket; the page draws it with xterm.js, loaded from /vendor when the terminal first opens. The
// terminal has two docks: under the file tree in the sidebar, with a grip that sets its height, or a column of its own
// between the sidebar and the code, with a grip that sets its width. The bot button in the sidebar footer shows and
// hides it, the arrow next to the button moves it between the docks, and the burger opens the quick prompts, which
// send a prompt to the session.

// TERM holds the dock and the sizes, saved together as dr:pane for every pull request; whether the terminal shows,
// saved per pull request, so a terminal left open comes back with its own page only; the element the terminal lives
// in, which moves between the docks and survives the sidebar's re-renders; the xterm.js terminal and its fit addon
// once the library has loaded; the relay WebSocket; `lib`, the promise that loads the library once; whether the
// menu of quick prompts is open; whether a quick prompt is on its way to the session; and `setup`, whether this page
// has seen a start that needs Claude Code installed or updated, so the setup steps stay until the session listens;
// and `tried`, whether the reader clicked Install or Update, after which the steps show why it failed.
const TERM = { open: false, dock: 'side', height: 320, width: 520, el: null, term: null, fit: null, ws: null, lib: null, menu: false, pasting: false, setup: false, tried: false };
const PANE_KEY = 'dr:pane';
const TERM_OPEN_KEY = `dr:term:${PR.repo}#${PR.number}`;
// Model in the settings: the model a session starts with, or nothing for the server's default.
const CLAUDE_MODEL_KEY = 'dr:claudeModel';
// The most bytes one frame carries to the relay, which reads a frame of at most 64 KB.
const TERM_FRAME_MAX = 16384;
const TERM_FILES = { css: '/vendor/xterm.css', js: ['/vendor/xterm.js', '/vendor/xterm-addon-fit.js'] };
const TERM_MIN_HEIGHT = 160, TERM_MIN_WIDTH = 320;
const TERM_ATTRS = '[data-term-toggle],[data-term-dock],[data-term-close],[data-term-start],[data-term-menu],[data-term-prompt],[data-term-install],[data-term-copy]';
// The quick prompt that has Claude rebase the pull request's branch onto the latest `base` and push it.
const rebasePrompt = (base) => ({ label: `Rebase onto ${base}`, text: `Rebase this pull request's branch onto the latest \`${base}\`: run \`git fetch origin ${base}\`, then \`git rebase origin/${base}\`. If the worktree has uncommitted changes, stop and tell me. Resolve each conflict so that both the changes of \`${base}\` and the changes of this pull request stay. When a conflict has no clear resolution, run \`git rebase --abort\` and tell me why. Never merge \`${base}\` into the branch. Then push with \`git push --force-with-lease --force-if-includes\`.` });
// The prompts the burger's menu sends to the session, each as if the reader typed it and pressed Enter. The rebase
// names the base branch of the merge state once it loads, since GitHub may have retargeted the pull request.
const quickPrompts = () => [
  { label: 'Describe file changes', text: "Describe every file of this pull request's diff: follow the differ-file-descriptions skill, call describe_file for each file that needs a description, and comment on the pull request's salient lines with start_thread." },
  rebasePrompt(M.loaded ? M.base : PR.base_ref),
];
const ARROW_RIGHT = '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2.5 7h8M7.5 3.5L11 7l-3.5 3.5"/><path d="M12.5 2.5v9" stroke-opacity=".5"/></svg>';
const ARROW_LEFT = '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M11.5 7h-8M6.5 3.5L3 7l3.5 3.5"/><path d="M1.5 2.5v9" stroke-opacity=".5"/></svg>';
const CHEVRON_LEFT = '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 3L5 7l4 4"/></svg>';
const BURGER = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M3 4.5h10M3 8h10M3 11.5h10"/></svg>';
const CHEVRON_DOWN = '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 5l4 4 4-4"/></svg>';

(function loadPane() {
  try {
    const saved = JSON.parse(lsGet(PANE_KEY) || '{}');
    if (saved.dock === 'column') TERM.dock = 'column';
    if (saved.height >= TERM_MIN_HEIGHT) TERM.height = saved.height;
    if (saved.width >= TERM_MIN_WIDTH) TERM.width = saved.width;
  } catch { /* the defaults stand */ }
  TERM.open = lsGet(TERM_OPEN_KEY) === 'open';
})();
function savePane() {
  lsSet(PANE_KEY, JSON.stringify({ dock: TERM.dock, height: TERM.height, width: TERM.width }));
  lsSet(TERM_OPEN_KEY, TERM.open ? 'open' : null);
}
const termSession = () => CL.session || { starting: false, running: false, error: '', branch: '', head: '', setup: '', command: '', installing: false };
// Whether the reader can show the terminal: the page is served, the setting is on and the pull request is still
// open, since the server ends the session of a merged or closed one as soon as it sees the status.
const termAvailable = () => claudeOn() && !CLAUDE_ENDED.includes(prStatus());

// ---------- the element and its docks
// The terminal's element, made once: a header and the screen xterm.js draws in.
function termElement() {
  if (!TERM.el) {
    TERM.el = document.createElement('div');
    TERM.el.className = 'term';
    TERM.el.innerHTML = '<div class="thead"></div><div class="tnote"></div><div class="screen"></div>';
  }
  return TERM.el;
}
// The sidebar's place for the terminal: a grip above it, then the slot the element goes in after each render.
function termSlotHtml() {
  if (!termAvailable() || !TERM.open || TERM.dock !== 'side') return '';
  return `<div class="termgrip" data-term-grip></div><div id="termslot" style="height: ${TERM.height}px"></div>`;
}
// The column the terminal takes between the sidebar and the code, made when first needed.
function termColumn() {
  let col = document.getElementById('termcol');
  if (!col) {
    col = document.createElement('div');
    col.id = 'termcol';
    col.innerHTML = '<div class="grip" data-term-colgrip></div>';
    document.body.insertBefore(col, document.getElementById('main'));
  }
  col.style.setProperty('--term', TERM.width + 'px');
  return col;
}
// Put the terminal's element where its dock says, or take it out of the page when it is hidden, and draw its header.
function mountTerminal() {
  const el = termElement(), col = document.getElementById('termcol');
  if (!termAvailable() || !TERM.open) {
    el.remove();
    if (col) col.remove();
    return;
  }
  if (TERM.dock === 'side') {
    if (col) col.remove();
    const slot = document.getElementById('termslot');
    if (slot && el.parentElement !== slot) slot.appendChild(el);
  } else {
    const target = termColumn();
    if (el.parentElement !== target) target.appendChild(el);
  }
  renderTermHead();
  fitTerm();
}
function termHeadHtml() {
  const s = termSession();
  let text, action = '', tone = '';
  if (s.installing) { text = `${s.setup === 'old' ? 'updating' : 'installing'} Claude Code…`; tone = ' tone-accent'; }
  else if (s.setup && s.error) text = s.setup === 'old' ? 'Claude Code is too old' : 'Claude Code is not installed';
  else if (s.error) { text = 'could not start'; action = '<button data-term-start>Retry</button>'; }
  else if (s.asking) { text = 'waiting for your answer'; tone = ' tone-warn'; }
  else if (s.working) { text = esc(claudeWork(s)); tone = ' tone-accent'; }
  else if (s.starting) text = 'starting…';
  else if (s.running && CL.listening) text = `listening on ${esc(PR.repo)}#${PR.number}`;
  else if (s.running) text = 'running, not listening yet';
  else { text = 'not running'; action = '<button data-term-start>Start</button>'; }
  const where = s.head ? `<span class="hint mono" title="${esc(s.branch)} at ${esc(s.head)}">${esc(s.head.slice(0, 7))}</span>` : '';
  const hide = `<button class="ghost chev-hide" data-term-close title="Hide the terminal">${TERM.dock === 'side' ? CHEVRON_DOWN : CHEVRON_LEFT}</button>`;
  return `${claudeDot()}<b>Claude</b><span class="state${tone}">${text}</span>${action}<span class="spacer"></span>${where}${hide}`;
}
// Whether the session can take a quick prompt. A session that listens has passed the dialogs Claude Code may show at a
// start, where the prompt's Enter would pick an answer, as it would in a question Claude asks.
const quickReady = () => termSession().running && CL.listening && !termSession().asking;
// Why the session can't take a quick prompt now, or '' when it can.
function quickBlock() {
  if (!claudeOn()) return 'Claude is off. Turn it on in the settings.';
  if (termSession().asking) return 'Claude is waiting for your answer.';
  return quickReady() ? '' : 'Claude is not listening yet.';
}
// The menu of quick prompts, while it is open.
function quickMenuHtml() {
  if (!TERM.menu) return '';
  const off = quickReady() ? '' : ' disabled';
  return `<div class="menu" role="menu">${quickPrompts().map((p, i) => `<button role="menuitem" data-term-prompt="${i}"${off}>${esc(p.label)}</button>`).join('')}</div>`;
}
// The steps that get a session going when Claude Code is missing or too old: install or update it, here in the
// terminal with one click or by hand with the command, sign in, and connect, which differ does itself. They show
// from the start that found Claude Code missing until the session listens.
function setupHtml() {
  const s = termSession(), old = s.setup === 'old', command = s.command;
  const installed = !s.setup && !s.installing, listening = CL.listening;
  // The error of a start that found Claude Code missing says what the header says already; an install that failed,
  // or the versions of one too old, say more.
  const note = TERM.tried && s.error ? `<span class="err">${esc(s.error)}</span>` : old && s.error ? `<span class="hint">${esc(s.error)}</span>` : '';
  let first;
  if (installed) first = '';
  else if (s.installing) first = `<span class="hint">${old ? 'Updating' : 'Installing'} below. Answer it there if it asks anything.</span>`;
  else {
    first = `${note}
      <span class="row"><button class="primary" data-term-install>${old ? 'Update' : 'Install'}</button><button data-term-start>Check again</button></span>
      <span class="hint">Or run it yourself, then Check again:</span>
      <span class="cmd"><code>${esc(command)}</code><button class="ghost copy" data-term-copy="${esc(command)}" title="Copy the command">${COPY}</button></span>`;
  }
  // Each step is done, the one to do now, or one for later, which shows dimmed.
  const steps = [
    { done: installed, title: `${old ? 'Update' : 'Install'} Claude Code`, body: first },
    { done: listening, title: 'Sign in', body: '<span class="hint">Claude Code asks for it the first time, below.</span>' },
    { done: listening, title: 'Connect to differ', body: '<span class="hint">differ does it for this pull request. Nothing to set up.</span>' },
  ];
  const now = steps.findIndex((step) => !step.done);
  return `<ol class="setup">${steps.map((step, i) => `<li${i > now && now >= 0 ? ' class="later"' : ''}>${step.done ? `<span class="tone-ok">${TICK}</span>` : `<span class="count">${i + 1}</span>`}<div><b>${step.title}</b>${step.body}</div></li>`).join('')}</ol>`;
}
// Draw the header, and under it the setup steps while Claude Code needs them, or the note that says why the session
// did not start, while it did not.
function renderTermHead() {
  if (!TERM.el || !TERM.el.isConnected) return;
  TERM.el.querySelector('.thead').innerHTML = termHeadHtml();
  const s = termSession();
  // The steps go once the session listens, or once a start past them fails for another reason, which the note and
  // the header's Retry then show.
  if (s.setup || s.installing) TERM.setup = true;
  else if (CL.listening || s.error) TERM.setup = false;
  // The steps take the whole height only while they wait for the reader, when nothing has run in the screen; from the
  // install on, the screen shows the install, then Claude Code's sign-in.
  TERM.el.classList.toggle('setting-up', TERM.setup && !!s.setup && !s.installing);
  TERM.el.querySelector('.tnote').innerHTML = TERM.setup ? setupHtml() : s.error ? `<div class="err">${esc(s.error)}</div>` : '';
}

// ---------- xterm.js
// Load xterm.js and its fit addon from the server once; the page inlines everything else, but a terminal is big and
// rare.
function loadXterm() {
  if (TERM.lib) return TERM.lib;
  const one = (tag, attrs) => new Promise((ok, fail) => {
    const el = document.createElement(tag);
    Object.assign(el, attrs);
    el.onload = ok; el.onerror = () => fail(new Error(`${attrs.src || attrs.href} did not load`));
    document.head.appendChild(el);
  });
  TERM.lib = (async () => {
    await one('link', { rel: 'stylesheet', href: TERM_FILES.css });
    for (const src of TERM_FILES.js) await one('script', { src });
  })();
  return TERM.lib;
}
// The theme xterm.js draws with, from the page's colour tokens, so it follows the theme the reader picked.
function termTheme() {
  const css = getComputedStyle(document.documentElement), token = (name) => css.getPropertyValue(name).trim();
  return {
    background: token('--bg-canvas'), foreground: token('--text'), cursor: token('--text'), cursorAccent: token('--bg-canvas'),
    selectionBackground: token('--row-selected-bg'), black: token('--bg-inset'), brightBlack: token('--text-muted'),
    red: token('--del-fg'), brightRed: token('--del-fg'), green: token('--add-fg'), brightGreen: token('--add-fg'),
    yellow: token('--mod-fg'), brightYellow: token('--mod-fg'), blue: token('--accent'), brightBlue: token('--accent'),
    magenta: token('--ren-fg'), brightMagenta: token('--ren-fg'), cyan: token('--accent'), brightCyan: token('--accent'),
    white: token('--text-secondary'), brightWhite: token('--text'),
  };
}
const termFont = () => getComputedStyle(document.documentElement).getPropertyValue('--mono').trim();
// Make the xterm.js terminal in the screen, once the library is there, and keep it the size of the screen.
async function createTerm() {
  if (TERM.term) return;
  await loadXterm();
  if (TERM.term) return;
  const term = new window.Terminal({ theme: termTheme(), fontFamily: termFont(), fontSize: 12.5, lineHeight: 1.15, cursorBlink: true, scrollback: 5000 });
  const fit = new window.FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(termElement().querySelector('.screen'));
  term.onData((data) => sendInput(new TextEncoder().encode(data)));
  term.onBinary((data) => sendInput(Uint8Array.from(data, (c) => c.charCodeAt(0))));
  term.onResize(({ cols, rows }) => { if (TERM.ws && TERM.ws.readyState === WebSocket.OPEN) TERM.ws.send(JSON.stringify({ cols, rows })); });
  TERM.term = term; TERM.fit = fit;
  new ResizeObserver(() => fitTerm()).observe(termElement().querySelector('.screen'));
  fitTerm();
}
// Send what the reader typed or pasted to the session, in frames the relay takes.
function sendInput(bytes) {
  if (!TERM.ws || TERM.ws.readyState !== WebSocket.OPEN) return;
  for (let at = 0; at < bytes.length; at += TERM_FRAME_MAX) TERM.ws.send(bytes.subarray(at, at + TERM_FRAME_MAX));
}
function fitTerm() {
  if (!TERM.fit || !TERM.el || !TERM.el.isConnected || !TERM.el.querySelector('.screen').offsetHeight) return;
  try { TERM.fit.fit(); } catch { /* a screen without a size yet */ }
}
// Connect the terminal to the session's relay, which replays what the session printed, so the screen starts clean.
// A connection that drops comes back while the terminal shows.
function connectTerm() {
  if (TERM.ws && TERM.ws.readyState <= WebSocket.OPEN) return;
  const ws = new WebSocket(`ws://${location.host}${API}/terminal`);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => { if (TERM.term) { TERM.term.reset(); ws.send(JSON.stringify({ cols: TERM.term.cols, rows: TERM.term.rows })); } };
  ws.onmessage = (e) => { if (TERM.term && e.data instanceof ArrayBuffer) TERM.term.write(new Uint8Array(e.data)); };
  ws.onclose = () => { TERM.ws = null; if (TERM.open) setTimeout(() => { if (TERM.open) connectTerm(); }, 1000); };
  TERM.ws = ws;
}

// ---------- actions
// Show the terminal and connect, and, unless `start` is false, start the session unless it runs.
async function openTerminal(start = true) {
  TERM.open = true; savePane();
  renderSide();
  await createTerm();
  mountTerminal();
  connectTerm();
  if (start) await startSession();
}
// Ask the server to start the session, with the terminal's size and the model; the header shows how it goes.
async function startSession() {
  CL.error = ''; TERM.tried = false;
  const size = TERM.term ? { cols: TERM.term.cols, rows: TERM.term.rows } : { cols: 80, rows: 24 };
  const model = lsGet(CLAUDE_MODEL_KEY);
  try {
    const d = await api('claude_open', model ? { ...size, model } : size), ready = quickReady();
    CL.session = d; renderTermHead();
    if (ready !== quickReady()) render(); else renderSide();
  } catch (e) { CL.error = `Claude: ${e.message}`; renderSide(); }
  loadClaude();
}
// Install Claude Code, or update it, in the session's terminal; the server starts the session once it succeeds.
async function installClaude() {
  TERM.tried = true;
  const size = TERM.term ? { cols: TERM.term.cols, rows: TERM.term.rows } : { cols: 80, rows: 24 };
  try {
    CL.session = await api('claude_install', size); renderTermHead(); renderSide();
    if (TERM.term) TERM.term.focus();
  } catch (e) { CL.error = `Claude: ${e.message}`; renderSide(); }
  loadClaude();
}
// Copy a setup command, and show a tick on its button for a moment, as the link's copy button does.
function copyCommand(button) {
  navigator.clipboard.writeText(button.dataset.termCopy).then(() => {
    button.innerHTML = TICK; button.classList.add('copied');
    setTimeout(() => { button.innerHTML = COPY; button.classList.remove('copied'); }, 1500);
  });
}
// Hide the terminal. The session goes on, and its output waits in the server until the terminal shows again.
function hideTerminal() {
  TERM.open = false; savePane();
  renderSide();
}
function toggleTermDock() {
  TERM.dock = TERM.dock === 'side' ? 'column' : 'side'; savePane();
  renderSide();
  if (TERM.term) setTimeout(() => { fitTerm(); TERM.term.focus(); }, 0);
}
// Send a quick prompt to the session, and bring the terminal up with the cursor in it, to show the answer. A prompt
// sent while the one before is still on its way is dropped, so a double click sends it once.
async function sendQuickPrompt(prompt) {
  TERM.menu = false;
  if (!quickReady() || TERM.pasting) { renderSide(); return; }
  TERM.pasting = true;
  try {
    if (TERM.open) renderSide(); else await openTerminal(false);
    if (TERM.term) TERM.term.focus();
    await api('claude_paste', { text: prompt.text, submit: true });
  } catch (e) { CL.error = `Claude: ${e.message}`; renderSide(); } finally { TERM.pasting = false; }
}
// Paste the prompt a differ:// link brought into the session's prompt, unsent, once the session takes quick prompts,
// and bring the terminal up with the cursor in it, for the reader to read it and send it.
async function pasteHeldPrompt() {
  if (!termSession().held || !quickReady() || TERM.pasting) return;
  TERM.pasting = true;
  try {
    if (TERM.open) renderSide(); else await openTerminal(false);
    if (TERM.term) TERM.term.focus();
    await api('claude_paste_held', {});
  } catch (e) { CL.error = `Claude: ${e.message}`; renderSide(); } finally { TERM.pasting = false; }
}
async function termClick(t) {
  const d = t.dataset;
  // A click on a button leaves the focus where it was in WebKit, so the terminal would keep the Escape that closes
  // the menu, and send it to Claude.
  if (d.termMenu !== undefined) { TERM.menu = !TERM.menu; if (TERM.menu) document.activeElement.blur(); renderSide(); return; }
  if (d.termPrompt !== undefined) { await sendQuickPrompt(quickPrompts()[d.termPrompt]); return; }
  if (d.termToggle !== undefined) { TERM.open ? hideTerminal() : await openTerminal(); return; }
  if (d.termClose !== undefined) { hideTerminal(); return; }
  if (d.termDock !== undefined) { toggleTermDock(); return; }
  if (d.termInstall !== undefined) { await installClaude(); return; }
  if (d.termCopy !== undefined) { copyCommand(t); return; }
  if (d.termStart !== undefined) { await startSession(); }
}
// The grips: the sidebar's sets the terminal's height, the column's its width. A release outside the window never
// reaches the page, so a move without the button down ends the drag.
(function termGrips() {
  let drag = null;
  document.addEventListener('mousedown', (e) => {
    const g = e.target.closest('[data-term-grip],[data-term-colgrip]');
    if (!g) return;
    drag = g; g.classList.add('dragging'); e.preventDefault();
  });
  document.addEventListener('mousemove', (e) => {
    if (!drag) return;
    if (!(e.buttons & 1)) { endDrag(); return; }
    if (drag.dataset.termGrip !== undefined) {
      const side = document.getElementById('side'), foot = side.querySelector('.foot');
      const bottom = foot ? foot.getBoundingClientRect().top : side.getBoundingClientRect().bottom;
      TERM.height = Math.max(TERM_MIN_HEIGHT, Math.min(bottom - e.clientY, side.offsetHeight * 0.8));
      const slot = document.getElementById('termslot');
      if (slot) slot.style.height = TERM.height + 'px';
    } else {
      const col = document.getElementById('termcol');
      TERM.width = Math.max(TERM_MIN_WIDTH, Math.min(e.clientX - col.getBoundingClientRect().left, window.innerWidth * 0.6));
      col.style.setProperty('--term', TERM.width + 'px');
    }
    fitTerm();
  });
  function endDrag() { if (!drag) return; drag.classList.remove('dragging'); drag = null; savePane(); fitTerm(); }
  document.addEventListener('mouseup', endDrag);
})();
// The theme changed in the shell's settings, which share this page's storage.
window.addEventListener('storage', (e) => { if (e.key === 'dr:theme' && TERM.term) TERM.term.options.theme = termTheme(); });
// A terminal the reader left open on this pull request comes back with the page, once the page has drawn and the
// scripts after this one have run. It starts nothing: the shell's Auto start does.
setTimeout(() => { if (termAvailable() && TERM.open) openTerminal(false); }, 0);
