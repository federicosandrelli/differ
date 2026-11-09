# differ

differ is a pull request viewer for agent-written code. It shows a pull request
as one page: the file tree on the left, every changed file in one scrolling pane
on the right, with imports, rename fallout and generated files folded away. You
can comment, review and merge from the page, and send a comment to the Claude
Code session that works on the pull request.

## Install

### Desktop app (macOS, Apple Silicon)

```sh
brew install --cask federicosandrelli/tap/differ
```

The cask installs Python 3.13 and the GitHub CLI (`gh`). The app offers each
new version in a card at the bottom of the window and updates through Homebrew.

If you have not signed in to `gh` yet, sign in once:

```sh
gh auth login
```

### From source

You need Python 3.13 or newer, git, and `gh` signed in (`gh auth login`).

```sh
git clone https://github.com/federicosandrelli/differ
cd differ
python3 server.py
```

Open http://localhost:8765/ and paste a pull request URL or `owner/repo#123`.
`python3 server.py 9000` serves on another port.

## What it does

- The start page lists the pull requests that need you, your drafts, the ones
  waiting for a reviewer or being reviewed, and the ones you opened recently.
- A page is built on the first visit and cached per head commit in
  `~/.cache/differ`. Cmd+R rebuilds it.
- Hover a line and click `+` to comment on it. A comment goes to GitHub at once
  or into a pending review. Threads take replies and can be resolved.
- On your own pull requests, the sidebar button opens the merge panel: the
  checks, the review decision, merge and auto-merge.
- The `</>` button in a file header opens the repository in your editor (VS
  Code, Cursor or a JetBrains IDE) on the pull request's branch, or opens the
  file on github.com.
- Cmd+, opens the settings: the theme, Auto start for the Claude session, and
  the repositories you follow.
- A `differ://open?pr=<pull request URL>` link opens the pull request in a tab
  of the desktop app. For a pull request from a fork or of a repository you
  don't follow, the app asks first, since a Claude session on the page runs the
  pull request's hooks.

## Claude in the page

Every pull request gets a Claude Code session of its own, started when its
page opens in a tab of differ. Turn Auto start off in the settings to start it from the terminal
instead. The bot button in the sidebar footer shows its terminal under the
file tree, and the arrow next to it moves the terminal to a column of its own
between the tree and the code. differ starts `claude` in a worktree of the
repository, on the pull request's branch, kept in `~/.cache/differ/worktrees`.
The session needs Claude Code 2.1.316 or later, and runs on your own login.

- "Send to Claude" in a comment box sends the comment to the session instead of
  GitHub. The session listens for these comments, and its reply shows in the
  thread. The bot's dot is lit while it listens.
- "Mention in terminal" in a comment box puts the lines into the session's
  prompt, so you can ask about them there.
- The session runs with permissions bypassed: it reads, edits, commits and
  pushes without asking. The repository's own `CLAUDE.md`, hooks and settings
  load, on anyone's pull request, as soon as its page opens while Auto start is
  on. If you also push to the branch from your own clone, the two can race, and
  the later push has to rebase.
- Before each start, differ records the repository's mirror as trusted in
  `~/.claude.json` when that file exists, the entry Claude Code's docs name for
  trusting a folder by hand, so Claude Code does not ask whether you trust the
  folder. Other first-run questions, such as accepting bypassed permissions,
  still show in the terminal.
- Closing the tab ends the session and removes the worktree. The next
  start resumes the conversation.

The server still writes the skill a session you start by hand follows to
`~/.claude/skills/differ-comments/SKILL.md`. Such a session listens with the URL
`ws://127.0.0.1:<port>/api/<owner>/<repo>/<number>/listen` while differ's own
session for the pull request does not. It also writes
`~/.claude/skills/differ-file-descriptions/SKILL.md`, which tells differ's own
session how to describe why the pull request changes each file.

## Develop

Install the dev tools with [uv](https://docs.astral.sh/uv/):

```sh
uv sync
uv run playwright install chromium
```

Run the same checks as CI:

```sh
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
npm install && npm run lint
```

`uv run pytest -m "not e2e"` skips the browser tests.

### Desktop app

The app is a Tauri shell in `src-tauri/` that starts `server.py` on port 8766.
Building it needs Rust (`cargo`) and Node.

```sh
npm install
npx tauri dev      # run from source
npx tauri build    # differ.app and a .dmg under src-tauri/target/release/bundle/
```

## Release

Every push to `main` that passes CI releases a new version. The `Release`
workflow builds the `.dmg`, publishes a GitHub release tagged with the version,
and updates the cask in
[federicosandrelli/homebrew-tap](https://github.com/federicosandrelli/homebrew-tap).
The patch number is the workflow run number. To start a new major or minor
version, change `version` in `src-tauri/tauri.conf.json`.

`CONTEXT.md` names the terms the code uses.
