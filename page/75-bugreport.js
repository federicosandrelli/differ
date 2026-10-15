// The bug report: a description typed in the sidebar's footer, turned into a prompt for Claude Code that carries the
// page's state, the recent actions and errors from LOG, and pointers into this repository, then copied to the clipboard.

const BUG_ATTRS = '[data-bug-open],[data-bug-cancel],[data-bug-generate],[data-bug-copy]';

const BUG_ICON = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="m8 2 1.88 1.88M14.12 3.88 16 2M9 7.13v-1a3 3 0 1 1 6 0v1M12 20c-3.3 0-6-2.7-6-6v-3a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v3c0 3.3-2.7 6-6 6M12 20v-9M6.53 9C4.6 8.8 3 7.1 3 5M6 13H2M3 21c0-2.1 1.7-3.9 3.8-4M20.97 5c0 2.1-1.6 3.8-3.5 4M22 13h-4M17.2 17c2.1.1 3.8 1.9 3.8 4"/></svg>';

function bugFootHtml() {
  const b = state.bug;
  if (!b.open) return `<div class="foot">${docsButtonHtml()}<button data-bug-open class="bug">${BUG_ICON}Report a bug</button></div>`;
  const note = b.copied === true ? 'Copied to the clipboard · paste it into a Claude Code session opened in the differ repository'
    : b.copied === false ? 'The clipboard refused the text · select it above and copy it' : '';
  return `<div class="foot"><div class="form bug">
    <div class="hint">What went wrong, and what did you expect? The prompt adds the page's state for Claude Code.</div>
    <textarea data-bug-text placeholder="Describe the issue">${esc(b.text)}</textarea>
    ${b.prompt ? `<textarea class="out" data-bug-out readonly spellcheck="false">${esc(b.prompt)}</textarea><div class="hint ${b.copied === false ? 'err' : ''}">${note}</div>` : ''}
    <div class="actions"><span class="spacer"></span><button data-bug-cancel>Close</button>${b.prompt ? '<button data-bug-copy>Copy again</button>' : ''}
    <button data-bug-generate class="primary">${b.prompt ? 'Regenerate' : 'Generate prompt'}</button></div></div></div>`;
}

// ---------- the prompt
const cap = (xs, n = 20) => xs.length > n ? [...xs.slice(0, n), `… ${xs.length - n} more`] : xs;
const yesNo = (v) => v ? 'yes' : 'no';
const short = (sha) => (sha || '').slice(0, 10);
const relative = (t, now) => { const s = Math.round((now - t) / 1000); return s < 60 ? `${s}s ago` : `${Math.floor(s / 60)}m${s % 60}s ago`; };

function fileLine(f) {
  const k = changeKind(f), runs = {};
  for (const r of f.runs) runs[r.class] = (runs[r.class] || 0) + 1;
  const runText = Object.entries(runs).map(([c, n]) => `${n} ${c}`).join(', ') || 'none';
  return [`\`${f.path}\``, KIND_LABEL[k] + (f.old_path && f.old_path !== f.path ? ` from \`${f.old_path}\`` : ''), f.kind,
    f.lang ? `language ${f.lang}` : 'no highlighting', f.binary ? `binary ${binarySize(f)}` : `+${f.stats.additions} −${f.stats.deletions}`, `runs: ${runText}`,
    plural(f.hunks.length, 'hunk'), `content side ${f.content_side}`, f.generated ? 'generated' : '', f.noise_only ? 'noise only' : '',
    `section ${sectionOpen(f) ? 'open' : 'collapsed'}`, isReviewed(f) ? 'reviewed' : 'not reviewed'].filter(Boolean).join(' · ');
}
function bugState() {
  const c = state.compose;
  return {
    view: state.view, cursor: state.cursor, filter: state.filter, q: state.q, hit: state.hit,
    showNoiseOnly: state.showNoiseOnly, showGenerated: state.showGenerated,
    closedFolders: cap([...state.closed]), openOverride: Object.fromEntries(state.openOverride),
    expanded: cap([...state.expanded]), unfolded: cap([...state.unfolded]),
    compose: c && { ...c, text: `${c.text.length} chars` }, drafts: [...state.drafts.values()].map(d => ({ ...d, text: `${d.text.length} chars` })),
    replyOpen: [...state.replyOpen.keys()], openThreads: cap([...state.openThreads]), showOutdated: [...state.showOutdated],
    reviewPanel: state.reviewPanel, reviewText: `${state.reviewText.length} chars`, convText: `${state.convText.length} chars`,
    busy: state.busy, focus: state.focus, reviewedFiles: FILES.filter(isReviewed).length,
    visibleFiles: visibleFiles().length, hiddenNoiseOnly: FILES.filter(f => kindVisible(f) && f.noise_only && !state.showNoiseOnly).length,
    hiddenGenerated: FILES.filter(f => kindVisible(f) && f.generated && !state.showGenerated).length,
  };
}
function commentsLine() {
  if (!API) return 'Not available: the page was opened from disk, so it has no comments API.';
  if (!C.loaded) return `Not loaded yet${C.error ? ` · error: ${C.error}` : ''}.`;
  const unresolved = C.threads.filter(t => !t.resolved).length, outdated = C.threads.filter(t => t.outdated).length;
  const stale = C.head && !PR.head.startsWith(C.head) && !C.head.startsWith(PR.head);
  return [`loaded ${relative(C.loadedAt, Date.now())} (load ${C.version})`, `viewer ${C.viewer || 'unknown'}`,
    `pending review ${C.pending ? `yes (${plural(pendingCount(), 'pending comment')})` : 'no'}`,
    `${plural(C.threads.length, 'thread')} (${unresolved} unresolved, ${outdated} outdated, ${THREADS.orphans.length} on files outside the diff)`,
    `${plural(C.reviews.length, 'review')}`, `${plural(C.comments.length, 'conversation comment')}`,
    stale ? `GitHub's head ${short(C.head)} differs from the page's ${short(PR.head)}` : '', C.error ? `error: ${C.error}` : ''].filter(Boolean).join(' · ');
}
function errorsText() {
  if (!LOG.errors.length) return 'None recorded since the page loaded.';
  const now = Date.now();
  return LOG.errors.map(e => {
    const stack = (e.stack || '').split('\n').map(l => l.trim()).filter(l => l && l !== e.text).slice(0, 6).map(l => '    ' + mapPageLines(l));
    return `- ${relative(e.t, now)} ${e.kind}: ${mapPageLines(e.text)}${e.where ? ` at ${mapPageLines(e.where)}` : ''}${stack.length ? '\n' + stack.join('\n') : ''}`;
  }).join('\n');
}
function actionsText() {
  if (!LOG.actions.length) return 'None recorded since the page loaded.';
  const now = Date.now();
  return LOG.actions.map(a => `- ${relative(a.t, now)} ${a.what}`).join('\n');
}
function bugPrompt(text) {
  const f = BY_PATH[state.view];
  const anchor = viewportAnchor(), at = anchor && anchor.rowId && rowAt(anchor.rowId);
  const side = document.getElementById('side');
  const app = D.app;
  const where = [
    `- Page: ${location.href}${SHELL ? ' · inside the tabbed shell' : ' · top-level document'}${window.__TAURI__ ? ' · desktop app' : ''}`,
    `- Pull request: ${PR.repo}#${PR.number} (${PR.url}) · ${PR.state}${PR.draft ? ', draft' : ''} · head ${short(PR.head)} (${PR.head_ref}) · base ${short(PR.base)} (${PR.base_ref}) · ${plural(FILES.length, 'file')} · ${plural(PR.commits, 'commit')}`,
    f ? `- Showing: ${fileLine(f)}` : '- Showing: the overview',
    at ? `- Row under the top of the main pane: ${at.key} (${at.side === 'LEFT' ? 'old' : 'new'} line ${at.line}) of \`${at.file.path}\`, element #${anchor.rowId}` : '',
    `- Main pane scrolled to ${Math.round(main.scrollTop)}px · ${plural(sections.length, 'section')} · ${bodies.size} ${bodies.size === 1 ? 'body' : 'bodies'} computed · ${plural(document.querySelectorAll('#main .chunk[data-filled]').length, 'chunk')} filled`,
  ].filter(Boolean).join('\n');
  const env = [
    `- differ commit ${app.commit || 'unknown'}${app.dirty ? ' with uncommitted changes' : ''} · page built ${app.built || 'unknown'} · highlight.js ${yesNo(window.hljs)} · served ${yesNo(SERVED)}`,
    `- Window ${innerWidth}×${innerHeight} · devicePixelRatio ${devicePixelRatio} · sidebar ${side ? side.offsetWidth : '?'}px wide · theme ${Theme.get()}`,
    `- ${navigator.userAgent}`,
    window.differServerLog ? `- Server log: ${window.differServerLog} (the desktop app appends the server's stderr there; its last lines may hold the server side of this failure)` : '',
    `- Reported ${new Date().toISOString()}`,
  ].filter(Boolean).join('\n');
  return `Bug report from differ, the pull request viewer in this repository. Reproduce it, find the cause and fix it.

## What happened

${text.trim() || '(no description given)'}

## Where

${where}

## Page state

\`\`\`json
${JSON.stringify(bugState(), null, 1)}
\`\`\`

## Comments

${commentsLine()}

## Recent actions, oldest first

${actionsText()}

## Errors

${errorsText()}

## Environment

${env}

## How the page works

- README.md and CONTEXT.md describe the app and its terms. The page is one HTML file: render.py inlines page/page.css, shell/theme.js, page/vendor/highlight.min.js, shell/frame.js and page/*.js in name order; build.py caches it under ~/.cache/diff-review/pages/ and rebuilds it when a source file is newer than the page, or when the head commit moved (checked in the background on a visit, or at once with ?refresh=1).
- Run \`python3 server.py 8765\` and open http://localhost:8765${location.pathname} to see this pull request. server.py and comments.py answer the comments API under /api/.
- A "page line" above is a line of the built HTML; the file:line before it is where that line comes from.
`;
}

// ---------- page lines back to source files
// The page's script is the source files concatenated with a "// ---- path" line before each, the file's path in
// the repository. With the document's text, a line of the page maps to a file and a line of it. The map stays
// empty when the text cannot be fetched.
let pageMap = null;
async function loadPageMap() {
  if (pageMap !== null) return;
  pageMap = false;
  try {
    const script = PAGE_SCRIPT;
    const src = await (await fetch(SERVED ? location.pathname : location.href)).text();
    const at = script ? src.indexOf(script.textContent) : -1;
    if (at < 0) return;
    const marks = [];
    script.textContent.split('\n').forEach((l, i) => { const m = /^\/\/ ---- (\S+)$/.exec(l); if (m) marks.push([i + 1, m[1]]); });
    pageMap = { first: src.slice(0, at).split('\n').length, marks };
  } catch {}
}
function sourceOf(line) {
  if (!pageMap) return '';
  const s = line - pageMap.first + 1;
  let hit = null;
  for (const [start, name] of pageMap.marks) { if (start <= s) hit = [start, name]; else break; }
  return hit ? `${hit[1]}:${s - hit[0]}` : '';
}
function mapPageLines(text) {
  const page = location.origin + location.pathname;
  return text.replace(/((?:https?|file):\/\/[^\s)]+?):(\d+):(\d+)/g, (m, url, line, col) => {
    const src = url === page || url === location.href.split('#')[0] ? sourceOf(+line) : '';
    return src ? `${src}:${col} (page line ${line}:${col})` : m;
  });
}

// ---------- actions
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch {}
  const ta = document.querySelector('[data-bug-out]'), prev = document.activeElement;
  if (!ta) return false;
  ta.focus(); ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch {}
  if (prev && prev.focus) prev.focus({ preventScroll: true });
  return ok;
}
async function bugClick(t) {
  const d = t.dataset, b = state.bug;
  if (d.bugOpen !== undefined) { b.open = true; state.focus = '[data-bug-text]'; renderSide(); restoreFocus(); loadPageMap(); return; }
  if (d.bugCancel !== undefined) { b.open = false; b.prompt = ''; b.copied = null; renderSide(); return; }
  if (d.bugGenerate !== undefined) {
    await loadPageMap();
    b.prompt = bugPrompt(b.text); b.copied = null;
    renderSide();
    b.copied = await copyText(b.prompt);
    state.focus = '[data-bug-text]';
    renderSide(); restoreFocus();
    return;
  }
  if (d.bugCopy !== undefined) { b.copied = await copyText(b.prompt); renderSide(); }
}
