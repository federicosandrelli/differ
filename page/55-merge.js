// The merge state of the viewer's own pull request, the merge button that names where it stands, and the merge
// panel: its checks, its review, and the actions that merge, turn auto-merge on or off, close, reopen or mark ready.

// M holds GitHub's answer to /merge once it has loaded; `error` holds why the last load or action failed. `started`
// and `applied` count the loads as LOADS does for the comments, and `rerunAt` is when the reader last re-ran a check.
const M = { loaded: false, error: '', timer: 0, started: 0, applied: 0, rerunAt: 0 };
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
// How long after a re-run the panel keeps asking GitHub again, so it sees the new check runs start.
const RERUN_MS = 120000;

const isOwn = () => !!C.viewer && C.viewer === PR.author;
// The status the merge state reports, which the poll keeps current; until it loads, the comments' status.
const prStatus = () => M.loaded ? M.status : C.status || PR_STATUS;
const methodKey = () => `dr:mergeMethod:${PR.repo}`;
// The merge method the reader chose last in this repository, or the first one the repository allows.
function mergeMethod() {
  const saved = lsGet(methodKey());
  return M.methods.includes(saved) ? saved : M.methods[0];
}
const checksIn = (...states) => M.checks.filter(c => states.includes(c.state));
const failedChecks = () => checksIn('failed', 'cancelled');
const runningChecks = () => checksIn('running', 'queued');
const mergesNow = () => M.mergeable !== 'CONFLICTING' && MERGES_NOW.includes(M.merge_state);

async function loadMerge() {
  clearTimeout(M.timer); M.timer = 0;
  if (!API || !isOwn()) return;
  const load = ++M.started;
  let d = null, error = '';
  try { d = await api('merge'); } catch (e) { error = e.message; }
  if (load < M.applied) return;
  M.applied = load;
  if (d) Object.assign(M, d, { loaded: true, error: '' }); else M.error = error;
  if (M.loaded && M.status !== (C.status || PR_STATUS)) loadComments();
  if (M.loaded && M.status === 'merged') state.mergePanel = false;
  if (M.loaded && M.status === 'open' && (runningChecks().length || M.auto_merge || M.mergeable === 'UNKNOWN' || Date.now() - M.rerunAt < RERUN_MS)) pollMerge();
  renderSide();
}
// Load the merge state again in POLL_MS, or later while the page is hidden.
function pollMerge() {
  M.timer = setTimeout(() => { if (document.visibilityState === 'visible') loadMerge(); else pollMerge(); }, POLL_MS);
}
// Send a merge action to GitHub, then load the comments and the merge state again. False when GitHub refused it, with
// the reason in M.error.
async function mergeSend(action, body) {
  state.busy = true; M.error = ''; renderSide();
  try {
    await api(action, body);
  } catch (e) {
    M.error = e.message;
    return false;
  } finally { state.busy = false; renderSide(); }
  await loadComments();
  await loadMerge();
  return true;
}

// Where the pull request stands, as the merge button names it: [label, icon kind, tone]. Null for a merged pull
// request, which has nothing left to do.
function mergeStanding() {
  const status = prStatus();
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
// Why the merge state failed to load, while the merge button waits for it.
function mergeNotice() {
  if (!API || !isOwn() || M.loaded || !M.error) return '';
  return `<div class="notice err">Merge state: ${esc(M.error)} · <a href="#" data-merge-reload>retry</a></div>`;
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
const mergeText = (method) => state.mergeText[method] || '';
// The headline and body a merge sends: the first line of the commit message box, and the rest after the blank line.
// None when the box is empty, so GitHub writes its own message, as its merge button does.
function mergeMessage(method) {
  if (method === 'REBASE' || !mergeText(method).trim()) return {};
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
// Why GitHub won't merge now, or '' when it will or when the panel shows the reason in a banner.
function mergeBlock() {
  const required = failedChecks().find(c => c.required), running = runningChecks().length;
  if (required) return `The required check ${required.workflow ? `${required.workflow} / ` : ''}${required.name} failed.`;
  if (M.merge_state === 'BEHIND') return `The branch is behind ${M.base}. Rebase it onto ${M.base} to merge.`;
  if (M.merge_state === 'BLOCKED' && running) return `GitHub merges once the ${running === 1 ? 'running check passes' : `${running} running checks pass`}.`;
  if (M.merge_state === 'BLOCKED') return `The rules of ${M.base} block the merge.`;
  return '';
}
function mergeActions() {
  const status = prStatus(), method = mergeMethod(), base = esc(M.base), off = state.busy ? 'disabled' : '';
  const close = `<span class="spacer"></span><button class="ghost" data-merge-do="close" ${off}>Close</button>`;
  if (status === 'closed') return { buttons: `<button data-merge-do="reopen" ${off}>Reopen</button>`, hint: '' };
  if (status === 'draft') return { buttons: `<button class="primary" data-merge-do="ready" ${off}>Ready for review</button>${close}`, hint: 'GitHub asks the reviewers once the pull request is ready.' };
  if (M.auto_merge) return { buttons: `<button data-merge-do="autooff" ${off}>Disable auto-merge</button>${close}`, hint: '' };
  const conflicts = M.mergeable === 'CONFLICTING', running = runningChecks().length;
  // Auto-merge waits for what blocks the merge now: a running check or a rule of the base branch. GitHub refuses it
  // for a pull request it can merge now.
  if (M.auto_merge_allowed && M.merge_state === 'BLOCKED' && !conflicts && !failedChecks().some(c => c.required)) {
    const until = running ? `the ${running === 1 ? 'running check passes' : `${running} running checks pass`}` : `the rules of ${base} pass`;
    return { buttons: `${methodPick('Enable auto-merge', 'autoon', false)}${close}`, hint: `Auto-merge ${METHODS[method].auto} ${base} once ${until}.` };
  }
  const block = mergeBlock();
  const hint = conflicts ? '' : block ? `<span class="err">${esc(block)}</span>` : `${METHODS[method].does(M.commits, base)}${M.delete_branch ? ' and deletes the branch' : ''}.`;
  return { buttons: `${methodPick(METHODS[method].label, 'merge', !mergesNow() || !!block)}${close}`, hint };
}
function mergePanelHtml() {
  const status = prStatus();
  if (!M.loaded || status === 'merged') return '';
  const method = mergeMethod(), base = esc(M.base), auto = M.auto_merge;
  const { buttons, hint } = mergeActions();
  const banner = auto ? `<div class="banner">Auto-merge is on: GitHub ${METHODS[auto.method].auto} ${base} once the checks pass. Turned on by ${esc(auto.by === C.viewer ? 'you' : auto.by)} ${ago(auto.at)}.</div>`
    : M.mergeable === 'CONFLICTING' && status === 'open' ? `<div class="banner">This branch conflicts with ${base}. Rebase it onto ${base} to merge.</div>` : '';
  const box = status === 'open' && !auto && method !== 'REBASE' ? `<textarea data-merge-text="${method}" placeholder="Commit message: empty for GitHub's default" spellcheck="false"></textarea>` : '';
  return `<div class="form card merge">${checksHtml()}${reviewLine()}${banner}${box}
    <div class="actions">${buttons}</div>${hint ? `<div class="hint">${hint}</div>` : ''}${M.error ? `<div class="hint err">${esc(M.error)}</div>` : ''}</div>`;
}

// ---------- actions
const MERGE_ATTRS = '[data-merge-panel],[data-merge-checks],[data-merge-menu],[data-merge-method],[data-merge-do],[data-merge-rerun],[data-merge-reload]';
async function mergeClick(t) {
  const d = t.dataset;
  if (d.mergePanel !== undefined) { state.mergePanel = !state.mergePanel; state.mergeMenu = false; renderSide(); return; }
  if (d.mergeChecks !== undefined) { state.checksOpen = t.getAttribute('aria-expanded') !== 'true'; renderSide(); return; }
  if (d.mergeMenu !== undefined) { state.mergeMenu = !state.mergeMenu; renderSide(); return; }
  if (d.mergeMethod !== undefined) { lsSet(methodKey(), d.mergeMethod); state.mergeMenu = false; renderSide(); return; }
  if (d.mergeReload !== undefined) { M.error = ''; renderSide(); loadMerge(); return; }
  if (d.mergeRerun !== undefined) { M.rerunAt = Date.now(); await mergeSend('rerun', { run: Number(d.mergeRerun) }); return; }
  const method = mergeMethod();
  switch (d.mergeDo) {
    case 'merge':
      if (!confirm(`${METHODS[method].label}: #${PR.number} into ${M.base}?`)) return;
      await mergeSend('merge', { method, head: PR.head, ...mergeMessage(method) });
      break;
    case 'autoon': await mergeSend('automerge', { on: true, method, head: PR.head, ...mergeMessage(method) }); break;
    case 'autooff': await mergeSend('automerge', { on: false }); break;
    case 'close': case 'reopen': case 'ready': await mergeSend(d.mergeDo, {}); break;
  }
}
