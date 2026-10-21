# differ

A pull request viewer for agent-written code. It shows a PR as one page: the
file tree on the left, every changed file in one scrolling pane on the right,
with import sections and rename fallout folded away.

## Run

```sh
python3 server.py            # http://localhost:8765/
```

The page at `/` is a tabbed shell: a home tab with the start page, and one tab
per pull request. The start page (`/home` on its own) has a search bar and the
pull requests you opened last, newest first, in three sections: yours, other
people's, and the merged ones (yours and others'). Each section keeps up to
twenty; the list is fetched again each time its tab comes to the front. Paste any PR URL or `owner/repo#123` to open
another. Opening a pull request from the start page opens it in a tab, or
brings its tab to the front when it has one; once a page's comments load,
its tab shows the number of unresolved threads and the pull request's status
as GitHub reports it now, and the open tabs survive a relaunch. A page is built on first visit and cached by head commit. A later
visit serves the newest built page at once and checks the head commit in the
background: after a new push the next page is built while you read, and the
page offers it in a notice once its comments load. Add `?refresh=1` to a page
URL to check the head commit at once and build the page for it if the one
served is older.

To build one page without the server:

```sh
python3 build.py https://github.com/owner/repo/pull/123
```

Requirements: Python 3.13, git, and the GitHub CLI (`gh`) signed in. The
server runs `gh auth status` when it starts; when `gh` is missing or signed
out, the start page shows a banner with the command to run, and checks again
at most once a minute, so signing in clears the banner without a restart.

`server.py --log <path>` appends the server's stderr to that file; a file past
5 MB starts over when the server starts.

## Comments

The page shows the pull request's review threads under the lines they refer
to, with resolved threads folded to one line and outdated ones listed at the
top of their file. The overview has the conversation (comments and submitted
reviews) and a table of every thread. The tree counts unresolved threads per
file.

Hover a line and click `+` to comment on it. To comment on a range, drag from
`+` to the last line, or shift-click another line number. "Suggest a change" inserts a `suggestion` block with
the selected lines, which the page renders as a diff. A comment can go into a
pending review ("Start a review" / "Add review comment") or out at once ("Add
single comment"); threads take replies the same way, and can be resolved.
"Review" in the sidebar submits the pending review as approve, request changes
or comment, or discards it. Cmd+Enter sends a form. Escape in a comment or
reply box only leaves the box, and keeps it open with its text. A comment box
with text stays as a draft when another line's box opens, and comes back when
the last line number of its range is clicked again.

Comments load from GitHub after the page, through `/api/...` on the server, so
they need the server (not a page opened from disk). They refresh after every
change and when the window regains focus after 30 seconds away, unless a
comment box has focus.

## Open in an editor

The `</>` button in a file's header opens the whole repository in a new window
of your editor, on the pull request's branch, at the first changed line of the
file (deleted files have no button). There you can read the code around the
change, run it, commit and push. The server keeps one clone of each
repository, with file contents, in `~/.cache/differ/checkouts/<owner>/<repo>`,
and runs `gh pr checkout` in it on each click, which fast-forwards the branch
to the pull request's head. When a pull request's page opens, the server
clones the repository in the background if it has no clone yet, so the first
click doesn't wait for it. A click on a pull request that is already checked
out at its head skips the checkout: the server only checks the head on GitHub,
unless it read it in the last 20 seconds. While a JetBrains IDE is still
opening the last click from the same repository, for example while PyCharm
waits on a dialog, the next click in that IDE shows an error instead. A click
in another editor opens the file.

The server never loses uncommitted changes or commits that were never pushed.
When it can't switch the clone to the branch safely, the page shows why.

The server knows VS Code, Cursor, and the JetBrains IDEs PyCharm, IntelliJ
IDEA, WebStorm and GoLand, which open the folder as their "Open project in"
setting says. When a running JetBrains IDE has the clone open as a project, as
its list of recent projects says, the server opens the file through the
IDE's URL scheme (`pycharm://open?file=…`), which takes a fraction of a second
instead of the launcher's two. GitHub is always in the list: it opens the file
on github.com at the pull request's head commit, at the same line, in the
browser, with no clone. When an editor is installed besides GitHub, a `⋮` next
to the button opens a menu of them; choosing one opens the file in it, and the
page saves the choice as `dr:editor` for every file. It forgets a saved editor
that is no longer installed.

## Desktop app

The same pages in a native window, with the tab strip across the title bar.
The Tauri shell in `src-tauri/` starts `server.py` as a child process on port
8766 (or a free port when that one is taken), opens the window on it, and stops
the server when the app quits (the server also exits on its own if the app
dies). The server's stderr goes to `~/Library/Logs/differ/server.log`, and
the bug report names that file. The page keeps its settings in
the webview's `localStorage`, so the fixed port is what makes them survive a
relaunch. Links to GitHub open in the system browser. Cmd+Option+Left and
Cmd+Option+Right step between the tabs. Cmd+Plus and Cmd+Minus zoom the pages
in steps from 50% to 200%, Cmd+0 resets the zoom, and the zoom survives a
relaunch; the tab strip keeps its size.

```sh
npm install          # the Tauri CLI
npx tauri dev        # run from source
npx tauri build      # differ.app and a .dmg under src-tauri/target/release/bundle/
```

The app needs Rust (`cargo`) to build. It ships the Python files, not an
interpreter, and needs Python 3.13 or newer to run. It looks for the
interpreter in this order and takes the first that exists:
`$HOMEBREW_PREFIX/bin/python3.13` when `HOMEBREW_PREFIX` is set,
`/opt/homebrew/bin/python3.13`, `/usr/local/bin/python3.13`, then `python3.13`
and `python3` on the login shell's PATH. When none is found, or the one found
is older than 3.13, the app shows an error dialog and quits. The Homebrew cask
pulls in `python@3.13` and `gh`; `git` stays external.

## Releasing

The app is signed ad hoc (`signingIdentity` `"-"` in `tauri.conf.json`),
and it runs on Apple Silicon. Friends install it from the cask in
[federicosandrelli/homebrew-tap](https://github.com/federicosandrelli/homebrew-tap):

```sh
brew install --cask federicosandrelli/tap/differ
```

The cask removes the `com.apple.quarantine` attribute after the install, so
macOS opens the app on the first launch.

Every push to `main` releases a new version. After the `CI` workflow passes
on the push, the `Release` workflow (`.github/workflows/release.yml`):

1. Takes the major and minor version from `version` in
   `src-tauri/tauri.conf.json` and sets the patch to the workflow run number,
   for example `0.1.57`. The workflow never commits the new version to `main`.
2. Builds the `.dmg` on macOS and publishes it in a release with the tag
   `v0.1.57` on the pushed commit.
3. Sets `version` and `sha256` in `Casks/differ.rb` in `homebrew-tap` to the
   new release, and pushes the change. The push uses the `HOMEBREW_TAP_TOKEN`
   secret: a fine-grained personal access token with read and write access
   to the contents of `federicosandrelli/homebrew-tap`.

To start a new major or minor version, set `version` in `tauri.conf.json` to
it, for example `0.2.0`, and merge the change into `main`. The workflow
ignores the patch in that file.

A push releases nothing when its `CI` run fails, or when a newer push to
`main` cancels its `CI` run. Before it builds, the workflow also skips the
release when the commit is no longer the tip of `main`, or when a tag
already points at the commit (for example, after a re-run of `CI`). While a
release runs, GitHub keeps only the newest waiting release and cancels older
ones.

If the cask update fails after the release is published, a re-run skips the
commit because it has a tag. Update `Casks/differ.rb` by hand instead:

```sh
gh release download v0.1.57 --repo federicosandrelli/differ --pattern '*.dmg' --output - | shasum -a 256
```

## Checks

The same checks run locally and in CI (`.github/workflows/ci.yml`): ruff for
linting and formatting, pyright for types, and pytest for the unit tests and
the end-to-end tests. The dev tools are installed with [uv](https://docs.astral.sh/uv/):

```sh
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

`pytest -m "not e2e"` runs the unit tests alone. The end-to-end tests under
`tests/e2e/` start `server.py` against a local fixture repo and a fake `gh`,
and drive the pages in Chromium through Playwright; they need
`uv run playwright install chromium` once. A test that needs files of its own
opens a pull request of them with the `add_pr` fixture.

ESLint checks the scripts under `page/` and `shell/`. `eslint.config.js`
reads the names each file declares at the top level, since `render.py`
inlines the files into one script and a name declared in one is a global in
the others:

```sh
npm install
npm run lint
```

The desktop app has its own CI job, `desktop`, on macOS: `cargo fmt --check`
and `cargo clippy -- -D warnings` in `src-tauri/`, then
`npx tauri build --no-bundle`. It runs on every push to `main`, and on a pull
request that changes `src-tauri/`, `web/`, `package.json`,
`package-lock.json` or the workflow itself.

## How a page is built

`build.py`:

1. Reads the PR from GitHub with `gh pr view`: title, description, author,
   base and head commits.
2. Keeps its own bare clone of the repo, without file contents, in
   `~/.cache/differ/repos/`. The first PR from a repo clones its history;
   later ones only fetch new commits, and git downloads just the files the
   diff needs.
3. Fetches the PR's head and base commits and diffs the head against their
   merge base, which is what GitHub shows.
4. Reads the old and new content of every changed file, and hands them with
   the diff to `classify.py`, then the result and the PR metadata to
   `render.py`.
5. Writes the page to
   `~/.cache/differ/pages/<owner>/<repo>/<number>/<head>.html`.

`DIFFER_CACHE` moves the cache. The cache prunes itself: after a build,
`build.py` keeps only the pull request's two newest pages, so the page for the
previous head commit stays while the new one builds. When `server.py` starts,
it deletes the pages older than 30 days. The bare clones in `repos/` stay: they are what makes
the second build of a repository fast, and `git fetch` keeps them small. The clones in `checkouts/`, which the
editors open, stay too.

## Pieces

- `classify.py` parses the diff and classifies every change block. A block is
  folded only when the classifier can prove what it is: an import statement
  (Python through `ast`, Java from the header before the first type), or a
  line that becomes its replacement exactly by applying a rename that recurs elsewhere in the PR. It also emits the classes,
  functions and multi-line calls of every Python file, and flags generated
  files: those marked `linguist-generated` in `.gitattributes`, lock files,
  and files with a generator's header in the first five lines ("DO NOT EDIT",
  "@generated", "this file was generated", "automatically generated by").
- The page hides generated files, and files with only folded changes (imports,
  rename fallout, whitespace or moved code), by default.
  A notice above the tree shows how many it hides and toggles them back.
- `render.py` inlines `page/page.css`, the theme and frame scripts from
  `shell/`, highlight.js from `page/vendor/` (version 11.9.0, with its BSD
  licence next to it), and the scripts in `page/` concatenated in name order,
  and turns the classified diff and the PR metadata into one HTML
  page: the PR description and the rename table, a file tree
  with change colours and search, a reviewed mark per file kept in
  `localStorage`, and every file with its removed lines interleaved.
- The page's script is split by concern: `00-data.js` holds the data, the
  state and shared helpers; `10-files.js` which files show and the search hits;
  `20-highlight.js` syntax highlighting (any language highlight.js knows,
  chosen from the file extension by `classify.py`); `25-address.js` the row
  keys that name a line of a file on either side; `30-rows.js` the view of one
  file: its rows, blocks and folds, and `reveal`, which opens whatever hides a
  row; `35-marks.js` the search matches and word changes marked inside a
  line; `40-code.js` draws that view as the diff table; `50-comments.js` the
  review threads and forms; `60-overview.js` the overview; `70-sidebar.js` the tree;
  `75-bugreport.js` the bug report prompt; `76-docs.js` the docs window; `80-main.js` the main pane; `90-events.js` the event handlers and start-up.
- The main pane keeps one section per shown file. A section always has its
  header; its diff table is cut into chunks of 100 rows that hold their rows
  only while they are near the viewport and keep their height when emptied, so
  the DOM stays small whatever the size of the pull request. A file's rows are
  computed once per state they depend on, so toggling a fold or typing in the
  search costs only the files on screen.
- `server.py` serves the tabbed shell, the start page and the pages, with a
  progress page while a page builds, and the comments API. `build.serve`
  decides which page to serve and when to build one in the background. Those
  documents are the files in `shell/`: `tabs.html` and `tabs.js` are the shell,
  which holds each page in an iframe; `frame.js` runs in every page and hands
  the shell its title and its links, so a click opens or switches a tab;
  `theme.js` applies the saved theme in every document and keeps the native
  window and the other open documents in step with a change; `document.html`
  wraps the start, progress and error pages (`start.html`, `progress.html`,
  `error.html`).
- `comments.py` reads and writes review threads, reviews and conversation
  comments through GitHub's GraphQL API.
- `github.py` is the one place that talks to GitHub: every `gh` command the
  app runs goes through its `gh`, and it holds `PullRef`, the name of a pull
  request (`owner/repo#123`).
  `commands.py` runs `gh` and `git` with a time limit and says how a command
  failed.
- `editors.py` knows the editors the page can open a file in, and starts them
  through one adapter per kind of editor: VS Code and Cursor, a JetBrains IDE,
  or GitHub, which opens the file on github.com.
  `checkout.py` keeps the clone of each repository that they open, and moves it
  to the pull request's branch without losing the user's work.
- `recent.py` keeps the start page's list of the pull requests opened last,
  with the status and author of each and the viewer's login, in `recent.json`
  in the cache.

Keys in the page: arrow keys in the tree, Option+Up and Option+Down for the
previous and next shown file, Cmd+F search (it starts from the selected text)
and Enter for the next hit, Shift+Cmd+F to search the file paths instead (the
tree then lists only the files found, and Enter goes to the next one) and again
to go back, `o` back to the top. Escape leaves the focused element and
ends the search; in a comment box it only leaves the box.

The search looks at the lines a reader can see: added, removed and unchanged
lines in the open sections of the shown files. It skips the lines inside a
closed fold, the sections closed by a review mark or the chevron, and the
files the filters hide. A match never opens a fold. Opening or closing a fold
or a section updates the count. A file stays on the page while its view holds
a match, so closing its section keeps it in place, without a count. Enter
then goes on from that file to the next hit. The matches of the hit Enter
lands on pop for a moment (they fade instead when the system asks for less
motion). While searching, a file opened from the tree (a click, or the arrow
keys) opens at its first hit, and Enter, in the tree or the search box, goes
on from there. The arrow keys pass a closed section without opening it; Enter
on it opens it at its first hit.

## Docs

"Docs" at the left of the sidebar's footer opens a window in the middle of the
page that describes every feature of the page, in plain words. Escape, the
close button or a click outside the window closes it.

## Reporting a bug

"Report a bug" under the file tree takes a description of the issue and
turns it into a prompt for Claude Code, shown in the sidebar and copied to the
clipboard. The prompt holds the description, the page's address and pull
request, the file and row in view, the page's state, the comments' state, the
last clicks and keys, the errors since the page loaded (with page line
numbers mapped back to the source files), the differ commit the page was
built from, and how to run the app. Paste it into a Claude Code session
opened in this repository.
