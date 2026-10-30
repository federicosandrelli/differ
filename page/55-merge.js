// The merge state of the viewer's own pull request, the merge button that names where it stands, and the merge
// panel: its checks, its review, and the actions that merge, turn auto-merge on or off, close, reopen or mark ready.

// M holds GitHub's answer to /merge once it has loaded; `error` holds why the last load or action failed.
const M = { loaded: false, error: '', timer: 0 };
// The labels and words of each merge method: the merge button's label, how the hint says what it does, and how the
// auto-merge hint says it.
const METHODS = {
  SQUASH: { label: 'Squash and merge', does: (n, base) => `Squashes ${plural(n, 'commit')} into ${base}`, auto: 'squashes into' },
  MERGE: { label: 'Create a merge commit', does: (n, base) => `Merges ${plural(n, 'commit')} into ${base} with a merge commit`, auto: 'merges into' },
  REBASE: { label: 'Rebase and merge', does: (n, base) => `Rebases ${plural(n, 'commit')} onto ${base}`, auto: 'rebases onto' },
};
// The merge states in which GitHub merges now. UNKNOWN is a state GitHub has yet to work out, so the merge is tried.
const MERGES_NOW = ['CLEAN', 'UNSTABLE', 'HAS_HOOKS', 'UNKNOWN'];
const POLL_MS = 20000;

const isOwn = () => !!C.viewer && C.viewer === PR.author;
const methodKey = () => `dr:mergeMethod:${PR.repo}`;
// The merge method the reader chose last in this repository, or the first one the repository allows.
function mergeMethod() {
  const saved = lsGet(methodKey());
  return M.methods.includes(saved) ? saved : M.methods[0];
}
const checksIn = (...states) => M.checks.filter(c => states.includes(c.state));
const failedChecks = () => checksIn('failed', 'cancelled');
const runningChecks = () => checksIn('running', 'queued');

async function loadMerge() {
  clearTimeout(M.timer);
  if (!API || !isOwn()) return;
  try {
    Object.assign(M, await api('merge'), { loaded: true, error: '' });
  } catch (e) { M.error = e.message; }
  if (M.loaded && (runningChecks().length || M.auto_merge || M.mergeable === 'UNKNOWN')) {
    M.timer = setTimeout(() => { if (document.visibilityState === 'visible') loadMerge(); }, POLL_MS);
  }
  renderSide();
}
// Send a merge action to GitHub, then load the comments, which carry the status, and the merge state again. False
// when GitHub refused it, with the reason in M.error.
async function mergeSend(action, body) {
  state.busy = true; M.error = ''; renderSide();
  try { await api(action, body); } catch (e) { M.error = e.message; return false; } finally { state.busy = false; }
  await loadComments();
  await loadMerge();
  return true;
}

// Where the pull request stands, as the merge button names it: [label, icon kind, tone]. Null for a merged pull
// request, which has nothing left to do.
function mergeStanding() {
  const status = C.status || PR_STATUS;
  if (status === 'merged') return null;
  if (status === 'closed') return ['Closed', 'x-circle', ''];
  if (status === 'draft') return ['Draft', 'pencil', ''];
  if (M.auto_merge) return ['Auto-merge on', 'clock', 'accent'];
  if (M.mergeable === 'CONFLICTING') return ['Conflicts', 'warning', 'warn'];
  if (failedChecks().length) return ['Checks failed', 'x-circle', 'bad'];
  if (runningChecks().length) return ['Checks running', 'clock', 'warn'];
  if (M.review === 'CHANGES_REQUESTED') return ['Changes requested', 'pencil', 'bad'];
  if (M.review === 'REVIEW_REQUIRED') return ['Waiting on review', 'eye', ''];
  if (M.merge_state === 'BEHIND') return [`Behind ${M.base}`, 'warning', 'warn'];
  if (M.merge_state === 'BLOCKED') return ['Blocked', 'warning', 'warn'];
  return ['Ready to merge', 'check-circle', 'ok'];
}
const iconHtml = (kind, tone) => `<span class="reason-icon k-${kind}${tone ? ' tone-' + tone : ''}" aria-hidden="true"></span>`;
function mergeButton() {
  const standing = M.loaded && mergeStanding();
  if (!standing) return '';
  const [label, kind, tone] = standing;
  return `<button data-merge-panel class="merge ${state.mergePanel ? 'on' : tone ? 'tone-' + tone : ''}">${iconHtml(kind)}${label}</button>`;
}

// ---------- the panel
const CHECK_LOOK = { passed: ['check-circle', 'ok'], failed: ['x-circle', 'bad'], cancelled: ['x-circle', 'bad'], running: ['clock', 'warn'], queued: ['clock', 'warn'], skipped: ['check', ''] };
function duration(from, to) {
  const s = Math.max(0, Math.round((new Date(to || Date.now()) - new Date(from)) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
}
function checkWhen(c) {
  if (c.state === 'queued') return 'queued';
  if (c.state === 'skipped') return 'skipped';
  if (c.state === 'cancelled') return 'cancelled';
  if (!c.started) return '';
  return c.state === 'running' ? `running · ${duration(c.started)}` : c.completed ? duration(c.started, c.completed) : '';
}
function checkHtml(c) {
  const [kind, tone] = CHECK_LOOK[c.state];
  const name = `${c.workflow ? `<span class="wf">${esc(c.workflow)} / </span>` : ''}${esc(c.name)}`;
  let h = `<div class="crow">${iconHtml(kind, tone)}<span class="nm">${name}</span><span class="spacer"></span><span class="hint">${checkWhen(c)}</span></div>`;
  if (c.state === 'failed') {
    h += `<div class="fail">${c.step ? `<div class="tone-bad">${esc(c.step)}</div>` : ''}${c.notes.length ? `<pre>${esc(c.notes.join('\n'))}</pre>` : ''}
      <div class="row">${c.run ? `<button data-merge-rerun="${c.run}" ${state.busy ? 'disabled' : ''}>Re-run failed jobs</button>` : ''}${c.url ? `<a href="${esc(c.url)}" target="_blank">Logs ↗</a>` : ''}</div></div>`;
  }
  return h;
}
// The checks: a summary line that opens and closes the list, and the list, open by default while a check has yet to
// pass.
function checksHtml() {
  const failed = failedChecks().length, running = runningChecks().length, passed = checksIn('passed').length;
  const parts = [passed && `${passed} passed`, running && `${running} running`, failed && `${failed} failed`].filter(Boolean);
  const [kind, tone] = failed ? ['x-circle', 'bad'] : running ? ['clock', 'warn'] : ['check-circle', 'ok'];
  const open = state.checksOpen ?? !!(failed || running);
  const chev = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  if (!M.checks.length) return `<div class="line"><span class="kind">Checks</span><span class="hint">None</span></div>`;
  return `<div class="line"><button class="chev ghost ${open ? 'open' : ''}" data-merge-checks title="${open ? 'Hide' : 'Show'} the checks" aria-expanded="${open}">${chev}</button>
    <span class="kind">Checks</span>${iconHtml(kind, tone)}<span class="tone-${tone}">${parts.join(' · ') || plural(M.checks.length, 'check') + ' skipped'}</span>
    <span class="spacer"></span><a href="${PR.url}/checks" target="_blank">GitHub ↗</a></div>
    ${open ? `<div class="checks">${M.checks.map(checkHtml).join('')}</div>` : ''}`;
}
function reviewLine() {
  const names = (list) => list.map(esc).join(', ');
  const [kind, tone, text] = M.review === 'CHANGES_REQUESTED' ? ['pencil', 'bad', `Changes requested by ${names(M.change_requesters)}`]
    : M.review === 'REVIEW_REQUIRED' ? ['eye', '', 'Waiting on a review']
    : M.approvers.length ? ['check-circle', 'ok', `Approved by ${names(M.approvers)}`] : [];
  return text ? `<div class="line"><span class="kind">Review</span>${iconHtml(kind, tone)}<span class="${tone ? 'tone-' + tone : ''}">${text}</span></div>` : '';
}
// The text the commit message box holds for a method: the reader's own, or the headline and body GitHub suggests.
function mergeText(method) {
  if (state.mergeText[method] != null) return state.mergeText[method];
  const [headline, body] = M.messages[method];
  return body ? `${headline}\n\n${body}` : headline;
}
// The headline and body a merge sends: the first line of the commit message box, and the rest after the blank line.
function mergeMessage(method) {
  if (method === 'REBASE') return {};
  const [headline, ...rest] = mergeText(method).split('\n');
  return { headline, body: rest.join('\n').replace(/^\n+/, '') };
}
// The merge button with the ⋮ that chooses the merge method, when the repository allows more than one.
function methodPick(label, action, off) {
  const method = mergeMethod(), dis = state.busy || off ? 'disabled' : '';
  const button = `<button class="primary" data-merge-do="${action}" ${dis}>${label}</button>`;
  if (M.methods.length < 2) return button;
  const more = `<button class="more primary" data-merge-menu title="Choose how to merge" aria-haspopup="menu" aria-expanded="${state.mergeMenu}" ${state.busy ? 'disabled' : ''}><svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true"><circle cx="8" cy="3.5" r="1.3"/><circle cx="8" cy="8" r="1.3"/><circle cx="8" cy="12.5" r="1.3"/></svg></button>`;
  const menu = state.mergeMenu ? `<div class="menu" role="menu">${M.methods.map(m => `<button role="menuitemradio" aria-checked="${m === method}" data-merge-method="${m}" class="${m === method ? 'on' : ''}">${METHODS[m].label}</button>`).join('')}</div>` : '';
  return `<span class="merge-pick"><span class="seg">${button}${more}</span>${menu}</span>`;
}
// Why GitHub won't merge now, or '' when it will.
function mergeBlock() {
  const required = failedChecks().find(c => c.required);
  if (required) return `The required check ${required.workflow ? `${required.workflow} / ` : ''}${required.name} failed.`;
  if (M.merge_state === 'BEHIND') return `The branch is behind ${M.base}. Rebase it onto ${M.base} to merge.`;
  if (M.merge_state === 'BLOCKED' && !runningChecks().length) return `The rules of ${M.base} block the merge.`;
  return '';
}
function mergeActions() {
  const status = C.status || PR_STATUS, method = mergeMethod(), base = esc(M.base);
  const close = `<span class="spacer"></span><button class="ghost" data-merge-do="close" ${state.busy ? 'disabled' : ''}>Close</button>`;
  if (status === 'closed') return { buttons: `<button data-merge-do="reopen" ${state.busy ? 'disabled' : ''}>Reopen</button>`, hint: '' };
  if (status === 'draft') return { buttons: `<button class="primary" data-merge-do="ready" ${state.busy ? 'disabled' : ''}>Ready for review</button>${close}`, hint: 'GitHub asks the reviewers once the pull request is ready.' };
  if (M.auto_merge) return { buttons: `<button data-merge-do="autooff" ${state.busy ? 'disabled' : ''}>Disable auto-merge</button>${close}`, hint: '' };
  const conflicts = M.mergeable === 'CONFLICTING', now = !conflicts && MERGES_NOW.includes(M.merge_state);
  const running = runningChecks().length, block = mergeBlock();
  const waits = !conflicts && !failedChecks().some(c => c.required) && (running || M.merge_state === 'BLOCKED');
  if (M.auto_merge_allowed && waits) {
    const merge = now ? `<button data-merge-do="merge" ${state.busy ? 'disabled' : ''}>Merge now</button>` : '';
    const until = running ? `the ${running === 1 ? 'running check passes' : `${running} running checks pass`}` : `the rules of ${base} pass`;
    return { buttons: `${methodPick('Enable auto-merge', 'autoon', false)}${merge}${close}`, hint: `Auto-merge ${METHODS[method].auto} ${base} once ${until}.` };
  }
  const hint = conflicts ? '' : block ? `<span class="err">${esc(block)}</span>` : `${METHODS[method].does(M.commits, base)}${M.delete_branch ? ' and deletes the branch' : ''}.`;
  return { buttons: `${methodPick(METHODS[method].label, 'merge', !now || !!block)}${close}`, hint };
}
function mergePanelHtml() {
  if (!M.loaded) return M.error ? `<div class="notice err">Merge state: ${esc(M.error)}</div>` : '';
  const status = C.status || PR_STATUS, method = mergeMethod(), base = esc(M.base);
  const { buttons, hint } = mergeActions();
  const auto = M.auto_merge;
  const banner = auto ? `<div class="banner">Auto-merge is on: GitHub ${METHODS[auto.method].auto} ${base} once the checks pass. Turned on by ${esc(auto.by === C.viewer ? 'you' : auto.by)} ${ago(auto.at)}.</div>`
    : M.mergeable === 'CONFLICTING' && status === 'open' ? `<div class="banner">This branch conflicts with ${base}. Rebase it onto ${base} to merge.</div>` : '';
  const box = status === 'open' && !auto && method !== 'REBASE' ? `<textarea data-merge-text="${method}" placeholder="Commit message" spellcheck="false"></textarea>` : '';
  return `<div class="form card merge">${checksHtml()}${reviewLine()}${banner}${box}
    <div class="actions">${buttons}</div>${hint ? `<div class="hint">${hint}</div>` : ''}${M.error ? `<div class="hint err">${esc(M.error)}</div>` : ''}</div>`;
}

// ---------- actions
const MERGE_ATTRS = '[data-merge-panel],[data-merge-checks],[data-merge-menu],[data-merge-method],[data-merge-do],[data-merge-rerun]';
async function mergeClick(t) {
  const d = t.dataset;
  if (d.mergePanel !== undefined) { state.mergePanel = !state.mergePanel; state.mergeMenu = false; renderSide(); return; }
  if (d.mergeChecks !== undefined) { state.checksOpen = t.getAttribute('aria-expanded') !== 'true'; renderSide(); return; }
  if (d.mergeMenu !== undefined) { state.mergeMenu = !state.mergeMenu; renderSide(); return; }
  if (d.mergeMethod !== undefined) { lsSet(methodKey(), d.mergeMethod); state.mergeMenu = false; renderSide(); return; }
  if (d.mergeRerun !== undefined) { await mergeSend('rerun', { run: Number(d.mergeRerun) }); return; }
  const method = mergeMethod(), base = M.base;
  switch (d.mergeDo) {
    case 'merge':
      if (!confirm(`${METHODS[method].label}: #${PR.number} into ${base}?`)) return;
      if (await mergeSend('merge', { method, head: PR.head, ...mergeMessage(method) })) state.mergePanel = false;
      break;
    case 'autoon': await mergeSend('automerge', { on: true, method, head: PR.head, ...mergeMessage(method) }); break;
    case 'autooff': await mergeSend('automerge', { on: false }); break;
    case 'close': case 'reopen': case 'ready': await mergeSend(d.mergeDo, {}); break;
  }
  renderSide();
}
