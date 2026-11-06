// The terminal that shows the pull request's Claude Code session. The server runs the session in a pseudoterminal and
// relays it over a WebSocket; the page draws it with xterm.js, loaded from /vendor when the terminal first opens. The
// terminal has two docks: under the file tree in the sidebar, with a grip that sets its height, or a column of its own
// between the sidebar and the code, with a grip that sets its width. The bot button in the sidebar footer shows and
// hides it, and the arrow next to the button moves it between the docks.

// TERM holds the dock and the sizes, saved together as dr:pane for every pull request; whether the terminal shows,
// saved per pull request, so a terminal left open comes back with its own page only; the element the terminal lives
// in, which moves between the docks and survives the sidebar's re-renders; the xterm.js terminal and its fit addon
// once the library has loaded; the relay WebSocket; and `lib`, the promise that loads the library once.
const TERM = { open: false, dock: 'side', height: 320, width: 520, el: null, term: null, fit: null, ws: null, lib: null };
const PANE_KEY = 'dr:pane';
const TERM_OPEN_KEY = `dr:term:${PR.repo}#${PR.number}`;
// The most bytes one frame carries to the relay, which reads a frame of at most 64 KB.
const TERM_FRAME_MAX = 16384;
const TERM_FILES = { css: '/vendor/xterm.css', js: ['/vendor/xterm.js', '/vendor/xterm-addon-fit.js'] };
const TERM_MIN_HEIGHT = 160, TERM_MIN_WIDTH = 320;
const TERM_ATTRS = '[data-term-toggle],[data-term-dock],[data-term-close],[data-term-start]';
const ARROW_RIGHT = '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2.5 7h8M7.5 3.5L11 7l-3.5 3.5"/><path d="M12.5 2.5v9" stroke-opacity=".5"/></svg>';
const ARROW_LEFT = '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M11.5 7h-8M6.5 3.5L3 7l3.5 3.5"/><path d="M1.5 2.5v9" stroke-opacity=".5"/></svg>';
const CHEVRON_LEFT = '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 3L5 7l4 4"/></svg>';
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
const termSession = () => CL.session || { starting: false, running: false, error: '', branch: '', head: '' };
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
  let text, action = '';
  if (s.error) { text = 'could not start'; action = '<button data-term-start>Retry</button>'; }
  else if (s.starting) text = 'starting…';
  else if (s.running && CL.listening) text = `listening on ${esc(PR.repo)}#${PR.number}`;
  else if (s.running) text = 'running, not listening yet';
  else { text = 'not running'; action = '<button data-term-start>Start</button>'; }
  const where = s.head ? `<span class="hint mono" title="${esc(s.branch)} at ${esc(s.head)}">${esc(s.head.slice(0, 7))}</span>` : '';
  const hide = `<button class="ghost chev-hide" data-term-close title="Hide the terminal">${TERM.dock === 'side' ? CHEVRON_DOWN : CHEVRON_LEFT}</button>`;
  return `<span class="dot${CL.listening ? ' on' : ''}"></span><b>Claude</b><span class="state">${text}</span>${action}<span class="spacer"></span>${where}${hide}`;
}
// Draw the header, and under it the note that says why the session did not start, while it did not.
function renderTermHead() {
  if (!TERM.el || !TERM.el.isConnected) return;
  TERM.el.querySelector('.thead').innerHTML = termHeadHtml();
  const error = termSession().error;
  TERM.el.querySelector('.tnote').innerHTML = error ? `<div class="err">${esc(error)}</div>` : '';
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
// Show the terminal, start the session unless it runs, and connect.
async function openTerminal() {
  TERM.open = true; savePane();
  renderSide();
  await createTerm();
  mountTerminal();
  connectTerm();
  await startSession();
}
// Ask the server to start the session, with the terminal's size; the header shows how it goes.
async function startSession() {
  CL.error = '';
  const size = TERM.term ? { cols: TERM.term.cols, rows: TERM.term.rows } : { cols: 80, rows: 24 };
  try {
    const d = await api('claude_open', size);
    CL.session = d; renderTermHead(); renderSide();
  } catch (e) { CL.error = `Claude: ${e.message}`; renderSide(); }
  loadClaude();
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
// Put a mention of the comment box's lines into the session's prompt, and bring the terminal up with the cursor in it.
async function mentionInTerminal(c) {
  const lines = c.startLine !== c.line ? `#L${c.startLine}-L${c.line}` : `#L${c.line}`;
  const text = `@${c.path}${lines}${c.side === 'LEFT' ? ' (old)' : ''} `;
  if (!TERM.open) await openTerminal();
  try { await api('claude_paste', { text }); } catch (e) { CL.error = `Claude: ${e.message}`; renderSide(); return; }
  if (TERM.term) TERM.term.focus();
}
async function termClick(t) {
  const d = t.dataset;
  if (d.termToggle !== undefined) { TERM.open ? hideTerminal() : await openTerminal(); return; }
  if (d.termClose !== undefined) { hideTerminal(); return; }
  if (d.termDock !== undefined) { toggleTermDock(); return; }
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
// scripts after this one have run.
setTimeout(() => { if (termAvailable() && TERM.open) openTerminal(); }, 0);
