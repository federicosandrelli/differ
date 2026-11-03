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
gh auth login
```

The cask installs Python 3.13 and the GitHub CLI (`gh`). The app offers each
new version in a card at the bottom of the window and updates through Homebrew.

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
- Cmd+, opens the settings: the theme and the repositories you follow.

## Comments for Claude

"Send to Claude" in a comment box sends the comment to the Claude Code session
that works on the pull request, instead of GitHub. Paste the prompt from "Copy
connect prompt", in the bot menu in the sidebar footer, into the session. The
bot's dot is green while a session listens.

Nothing needs installing: each time the server starts, it writes the skill the
session follows to `~/.claude/skills/differ-comments/SKILL.md`. If you
installed the old differ plugin, remove it:

```sh
claude plugin uninstall differ@differ
claude plugin marketplace remove differ
```

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
