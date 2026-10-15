// The docs: a window in the middle of the page that describes every feature of differ, opened from the sidebar's footer.

const DOCS_ICON = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2 4h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2zM22 4h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z"/></svg>';

const DOCS_HTML = `
<h2>What differ is</h2>
<p>differ shows a GitHub pull request on one page, with every changed file in a single scrolling pane. It hides the
changes that you do not need to read, such as changed imports and lines that only follow a rename.</p>

<h2>Hidden files</h2>
<p>By default, differ hides two kinds of files:</p>
<ul>
  <li><b>Generated files.</b> These are lock files, files marked as generated in <code>.gitattributes</code>, and
    files that say near the top that a tool generated them (for example "DO NOT EDIT").</li>
  <li><b>Files with only noise.</b> These are files whose only changes are imports, lines that follow a rename,
    whitespace, or code that moved without other changes.</li>
</ul>
<p>A notice above the file tree says how many files are hidden. Click "show" in the notice to show them.</p>

<h2>Folded lines</h2>
<p>differ folds some lines into a single row. Click the row to open it, and click it again to fold the lines back.
differ folds lines in these cases:</p>
<ul>
  <li><b>Unchanged lines.</b> Between two changes, differ shows the 4 unchanged lines next to each change and folds
    the rest. It shows a block of 10 unchanged lines or fewer in full. The fold row lists the Python functions and
    classes that start inside it.</li>
  <li><b>Import sections.</b> In Python files, every import section folds to one row, even when nothing in it
    changed. The row says how many lines were added and removed.</li>
  <li><b>Whitespace.</b> A change where every line is blank, or where each removed line equals its added line once
    all spaces, tabs and line breaks are removed.</li>
  <li><b>Rename fallout.</b> A change where each removed line becomes its added line exactly by applying a rename. A
    rename counts when the same word changes the same way in at least two places in the pull request, or when it
    follows a renamed file. The change that renames the definition itself (the <code>class</code>,
    <code>def</code>, or assignment) stays open.</li>
  <li><b>Moved code.</b> A block of removed lines that appears again, unchanged, as a block of added lines
    somewhere else in the pull request, in the same file or another one. The block needs 4 lines or more that are
    not blank. The fold row names the other place.</li>
  <li><b>Several folds close together.</b> When folds of the same kind are separated only by a few unchanged lines,
    they fold into one row that says how many places it covers. Opening it gives each place its own row.</li>
</ul>
<p>Inside a real change, a line that only follows a rename appears pale, with a "rename" tag.</p>
<p>A fold always stays open when it holds a comment thread or the comment you are writing. The numbers in the
overview count the lines of every file but the generated ones, and the buttons under them show or hide each kind of
noise in every file at once. While a comment or one of these buttons keeps a fold open, its row only names the fold,
and clicking it does nothing.</p>

<h2>The class or function above a change</h2>
<p>differ also shows the lines above each change, up to the start of the innermost class or function that holds it.
Outside any class or function, it shows the lines up to the start of the call that holds the change, such as a
long <code>setup(...)</code>. You can then read the class line, the signature or the call without opening a fold.
This works in Python files, for changes that add lines and are not noise.</p>
<p>When the body above the change is long, differ shows the class line, the signature or the first line of the call,
and the 4 lines before the change. The lines between them fold into a small button on its own line. Click the
button to show the lines, and click it again to fold them.</p>

<h2>Reviewed files</h2>
<p>When you mark a file as reviewed, its section closes and its name in the tree turns pale. differ remembers your
reviewed files on this computer only; GitHub does not see them.</p>

<h2>Search</h2>
<ul>
  <li><kbd>Cmd</kbd>+<kbd>F</kbd> starts the search with the text you have selected.</li>
  <li>While you search, the tree shades the files without a match, and shows the number of matches next to each
    one.</li>
  <li><kbd>Enter</kbd> goes to the next match and <kbd>Shift</kbd>+<kbd>Enter</kbd> to the previous one. The match
    you land on pops for a moment.</li>
  <li>Click a file in the tree, or move to it with the arrow keys, to go to its first match. <kbd>Enter</kbd> goes on
    from there, in the tree as in the search box. The arrow keys pass a closed file without opening it; press
    <kbd>Enter</kbd> on it to open it at its first match.</li>
  <li>The search only looks at lines you can see. It skips folded lines, closed files and hidden files, and it never
    opens a fold.</li>
</ul>

<h2>Comments and reviews</h2>
<ul>
  <li><b>Comment on several lines.</b> Press the <kbd>+</kbd> on the first line and drag to the last one. You can
    also click the first line number, then hold <kbd>Shift</kbd> and click the last one.</li>
  <li><b>Pending review.</b> "Start a review" and "Add review comment" keep a comment in a pending review that only
    you can see. "Review" in the sidebar header sends the pending review as approve, request changes or comment, or
    discards it.</li>
  <li><b>Your verdict.</b> After you approve or request changes, the sidebar button shows "Approved" or "Changes
    requested" until the pull request gets new commits. Until then you can't send the same verdict again, but you
    can change it or comment.</li>
  <li><b>Drafts.</b> If you open a comment box on another line, the text you typed stays as a draft. It comes back
    when you click the last line number of its range again. <kbd>Escape</kbd> leaves a comment box and keeps its
    text. If you close a tab that has a draft, differ asks you to close it a second time.</li>
  <li><b>Reloading.</b> Comments reload after every change you make, and when you come back to the window after 30
    seconds away.</li>
</ul>

<h2>Keyboard shortcuts</h2>
<table>
  <tr><td><kbd>↑</kbd> <kbd>↓</kbd> <kbd>←</kbd> <kbd>→</kbd></td><td>Move through the file tree, and close or open
    folders (click the tree first).</td></tr>
  <tr><td><kbd>o</kbd></td><td>Go back to the overview.</td></tr>
  <tr><td><kbd>Cmd</kbd>+<kbd>Option</kbd>+<kbd>←</kbd> / <kbd>→</kbd></td><td>Go to the previous or next tab (desktop
    app).</td></tr>
</table>

<h2>Link to a file or a line</h2>
<p>Add <code>#path/to/file</code> to the address of a page to open it at a file, or <code>#path/to/file:42</code> to
open it at line 42 of that file. Add <code>?refresh=1</code> to check GitHub for new commits at once and build the page again when
there are some.</p>

<h2>Report a bug</h2>
<p>"Report a bug" turns your description into a prompt for Claude Code and copies it to the clipboard. The prompt
also contains the state of the page, your last clicks and keys, and any errors. Paste it into a Claude Code session
opened in the differ repository.</p>
`;

function docsButtonHtml() { return `<button data-docs-open class="docs">${DOCS_ICON}Docs</button>`; }

function openDocs() {
  let dialog = document.getElementById('docs');
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.id = 'docs';
    dialog.innerHTML = `<div class="box"><div class="top"><h1>How to use differ</h1><button data-docs-close aria-label="Close">✕</button></div>
      <div class="body">${DOCS_HTML}</div></div>`;
    // A click on the dialog itself, outside its box, is a click on the backdrop.
    dialog.addEventListener('click', (e) => { if (e.target === dialog || e.target.closest('[data-docs-close]')) dialog.close(); });
    document.body.appendChild(dialog);
  }
  dialog.showModal();
  dialog.querySelector('.body').scrollTop = 0;
}
