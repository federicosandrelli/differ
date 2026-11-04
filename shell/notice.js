// The `.notice` lines (base.css) about work that runs while the reader keeps reading. A busy notice leads with a
// spinner and ends with the last line of the work's log; a failed one gives the reason and a link that tries again.
const Notice = (() => {
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const busy = (text, log = '') => `<div class="notice busy"><span class="spinner" aria-hidden="true"></span>${esc(text)}${log ? `<span class="log" title="${esc(log)}">${esc(log)}</span>` : ''}</div>`;
  const failed = (what, error, attr) => `<div class="notice err">${esc(what)} failed: ${esc(error)} · <a href="#" ${attr}>try again</a></div>`;
  return { busy, failed };
})();
