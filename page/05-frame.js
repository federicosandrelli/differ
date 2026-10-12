// Inside the tabbed shell (the page at /), every page runs in an iframe and leaves navigation to the shell:
// a link to another page opens or switches a tab, a link to GitHub opens outside, and the shell learns the
// page's title and its number of unresolved threads. SHELL is null when the page is the top-level document.
const SHELL = window.parent === window ? null : {
  tell(message) { window.parent.postMessage(message, location.origin); },
};

if (SHELL) {
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[href]');
    if (!a || (a.getAttribute('href') || '').startsWith('#')) return;
    if (a.target === '_blank' || a.origin !== location.origin) { e.preventDefault(); SHELL.tell({ type: 'external', url: a.href }); return; }
    if (a.pathname === location.pathname) return;
    e.preventDefault();
    SHELL.tell({ type: 'open', url: a.pathname + a.search });
  }, true);
  document.addEventListener('submit', (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement) || (form.method || 'get').toLowerCase() !== 'get') return;
    const url = new URL(form.action, location.href);
    if (url.origin !== location.origin) return;
    e.preventDefault();
    url.search = new URLSearchParams(new FormData(form)).toString();
    SHELL.tell({ type: 'open', url: url.pathname + url.search });
  }, true);
  // The shell says when the tab comes to the front; pages listen for 'tab-shown' on the document.
  window.addEventListener('message', (e) => {
    if (e.origin === location.origin && e.data && e.data.type === 'shown') document.dispatchEvent(new Event('tab-shown'));
  });
  SHELL.tell({ type: 'page', url: location.pathname + location.search, title: document.title });
}
