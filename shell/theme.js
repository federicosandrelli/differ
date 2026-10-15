// The theme of every document the server sends, in one place: the saved choice applied before the first paint
// (this script sits in the head), the native window following it in the desktop app, every open document
// following a change made in another, and the picker on the document that has one (buttons with data-theme).
const Theme = (() => {
  const THEMES = ['light', 'sepia', 'dark'], DEFAULT = 'dark', KEY = 'dr:theme';
  const valid = (t) => THEMES.includes(t) ? t : DEFAULT;
  const get = () => { try { return valid(localStorage.getItem(KEY)); } catch { return DEFAULT; } };
  function apply(t) {
    document.documentElement.dataset.theme = t;
    const win = window.__TAURI__ && window.__TAURI__.window;
    if (win) win.getCurrentWindow().setTheme(t === 'dark' ? 'dark' : 'light').catch(() => {});
    for (const b of document.querySelectorAll('button[data-theme]')) b.classList.toggle('on', b.dataset.theme === t);
  }
  function set(t) { try { localStorage.setItem(KEY, t); } catch {} apply(t); }
  apply(get());
  document.addEventListener('DOMContentLoaded', () => apply(get()));
  document.addEventListener('click', (e) => { const b = e.target.closest('button[data-theme]'); if (b) set(b.dataset.theme); });
  window.addEventListener('storage', (e) => { if (e.key === KEY) apply(valid(e.newValue)); });
  return { THEMES, get, set };
})();
