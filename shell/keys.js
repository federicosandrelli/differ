// The modifier a shortcut holds down: Cmd on a Mac, Ctrl elsewhere, as each system's own apps have it. `primary` says
// whether a key event holds that modifier and not the other one, `label` names a chord as the system writes it, and
// `relabel` turns the Mac's key names in the <kbd>s and `.chord`s under an element, and in its titles, into this system's.
const Keys = (() => {
  const MAC = /Mac|iPhone|iPad/.test(navigator.platform);
  const primary = (e) => MAC ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
  const label = (key, shift = false) => MAC ? `${shift ? '⇧' : ''}⌘${key}` : `Ctrl+${shift ? 'Shift+' : ''}${key}`;
  const NAMES = { Cmd: 'Ctrl', Option: 'Alt' };
  const chords = (text) => text.replace(/⇧?⌘(\S)/g, (m, key) => label(key, m.startsWith('⇧')));
  function relabel(root) {
    if (MAC) return;
    for (const el of root.querySelectorAll('kbd, .chord')) el.textContent = NAMES[el.textContent] ?? chords(el.textContent);
    for (const el of root.querySelectorAll('[title*="⌘"]')) el.title = chords(el.title);
  }
  document.addEventListener('DOMContentLoaded', () => relabel(document));
  return { primary, label, relabel };
})();
