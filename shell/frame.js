// The page side of the tabbed shell. Inside the shell (the page at /) every page runs in an iframe and leaves
// navigation to the shell: a link to another page opens or switches a tab, a link to GitHub opens outside, and
// the shell learns the page's title, its pull request status and its number of unresolved threads. SHELL is null
// when the page is the top-level document. PR_STATUS is the status a review page names in its head (a key of
// PR_ICONS in render.py), or empty. The messages between the two sides are known only here and in tabs.js.
const PR_STATUS = (document.querySelector('meta[name="pr-status"]') || {}).content || '';
const SHELL = window.parent === window ? null : {
  tell(message) { window.parent.postMessage(message, location.origin); },
  open(url) { this.tell({ type: 'open', url }); },
  external(url) { this.tell({ type: 'external', url }); },
  step(dir) { this.tell({ type: 'step', dir }); },
  zoom(dir) { this.tell({ type: 'zoom', dir }); },
  badge(n) { this.tell({ type: 'badge', n }); },
  status(status) { this.tell({ type: 'status', status }); },
  page(url, title, status) { this.tell({ type: 'page', url, title, status }); },
  draft(on) { if (on !== this.drafting) { this.drafting = on; this.tell({ type: 'draft', on }); } },
  drafting: false,
};

if (SHELL) {
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[href]');
    if (!a || (a.getAttribute('href') || '').startsWith('#')) return;
    if (a.target === '_blank' || a.origin !== location.origin) { e.preventDefault(); SHELL.external(a.href); return; }
    if (a.pathname === location.pathname) return;
    e.preventDefault();
    SHELL.open(a.pathname + a.search);
  }, true);
  document.addEventListener('submit', (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement) || (form.method || 'get').toLowerCase() !== 'get') return;
    const url = new URL(form.action, location.href);
    if (url.origin !== location.origin) return;
    e.preventDefault();
    url.search = new URLSearchParams(new FormData(form)).toString();
    SHELL.open(url.pathname + url.search);
  }, true);
  // Cmd+Option+Left/Right steps between the shell's tabs. Focus sits inside the page, so the page forwards the chord.
  document.addEventListener('keydown', (e) => {
    if (!e.metaKey || !e.altKey || e.ctrlKey || e.shiftKey || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return;
    e.preventDefault();
    SHELL.step(e.key === 'ArrowLeft' ? -1 : 1);
  }, true);
  // An Escape the page leaves alone reaches the macOS window, which then leaves full screen, so the page cancels
  // every Escape. Cancelling it also stops a modal dialog from closing on Escape, so the page closes it.
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    e.preventDefault();
    const dialog = document.querySelector('dialog[open]');
    if (dialog) dialog.close();
  }, true);
  // Cmd+Plus, Cmd+Minus and Cmd+0 zoom the desktop app's window. The shell owns the zoom, so the page forwards the
  // chord; it leaves the default in place, so a browser still zooms by itself.
  document.addEventListener('keydown', (e) => {
    if (!e.metaKey || e.altKey || e.ctrlKey) return;
    const dir = e.key === '=' || e.key === '+' ? 1 : e.key === '-' || e.key === '_' ? -1 : e.key === '0' ? 0 : null;
    if (dir !== null) SHELL.zoom(dir);
  }, true);
  // The shell says when the tab comes to the front; pages listen for 'tab-shown' on the document.
  window.addEventListener('message', (e) => {
    if (e.origin === location.origin && e.data && e.data.type === 'shown') document.dispatchEvent(new Event('tab-shown'));
  });
  SHELL.page(location.pathname + location.search, document.title, PR_STATUS);
}
