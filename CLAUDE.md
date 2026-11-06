# Project preferences

## Tests

Don't write tests for things you can see on the page, such as an icon, a label or a colour. Check them by eye in the
preview instead. Write tests for behaviour you can't see at a glance.

## Design system

Every change to the page must follow the design system. Before you style anything, look at how the page already
styles the same kind of thing (pills, counts, notices, buttons), and reuse it.

- Name a token, never a value: a colour token from `palette()` in `render.py`, or a custom property from `:root` in
  `shell/base.css`, such as `--dim` for content the reader can see but should skip. If none fits, add one there and
  list it in `CONTEXT.md`.
- Tooltips: give the element a `title`. Never draw a tooltip of your own.

## Desktop app

The app shows the pages in macOS WKWebView, through wry, not in Chromium, where the tests and the preview run. Never
rely on a browser feature that the webview may lack. `alert`, `confirm` and `prompt` are the known case: the webview
answers them at once without showing a dialog. To ask before an action that is hard to undo, use `confirmed()` in
`page/50-comments.js`, which asks with a second click on the same button. ESLint's `no-alert` rule and the e2e
`no_native_dialogs` fixture enforce this.

## File types and languages

Never branch on a path's suffix, or on a file name that names a file type (such as `Makefile`), to choose
behaviour. Add a field to `FileTypeSupport` in `file_types.py` or a method to `LanguageSupport` in `languages.py`
instead, and fill it for every member. Pyright fails when a `match` in `support()` misses a member.

## Git

Never merge the base branch into a branch. To bring a branch up to date, or to resolve conflicts with the base
branch, rebase it onto the latest base branch (`git fetch origin main`, then `git rebase origin/main`), and push
with `git push --force-with-lease`. This rule also applies when the desktop app's Auto-fix asks for a merge.
