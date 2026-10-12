# Plan: basic CI

A GitHub Actions workflow that runs on every pull request and on every push
to `main`. It type-checks the Python with pyright, lints it with ruff, runs
unit tests with pytest, and runs end-to-end tests that drive the served page
in a real browser with Playwright. Nothing in the repo is set up for any of
this today: there is no `pyproject.toml`, no tests directory and no
`.github/` directory.

## What exists today

- Five Python modules at the repo root (`build.py`, `classify.py`,
  `comments.py`, `render.py`, `server.py`), about 1400 lines, standard
  library only. The README says Python 3.10+; this change makes it 3.13.
- Page and shell JavaScript under `page/` and `shell/`, inlined into the
  HTML by `render.py`. No bundler, no test runner.
- A Tauri shell under `src-tauri/` (Rust). Out of scope for this plan; see
  "Not in this plan".
- Every GitHub access goes through the `gh` command: `gh pr view`,
  `gh repo clone` and `gh api graphql`. Every repo access goes through `git`
  on a local bare clone. This is what makes the end-to-end tests possible
  without network access: a fake `gh` on `PATH` and a local fixture repo.

Baseline on the current `main`:

| Check | Result |
|---|---|
| `pyright *.py` (version 1.1.411, default settings) | 13 errors |
| `ruff check *.py` (version 0.12.0, default rules) | 1 error (unused import in `server.py`) |
| `ruff format --check` | would reformat all 5 files |

## 1. Project file and tool configuration

Add a `pyproject.toml` at the root. It carries no runtime dependencies. It
holds:

- `[project]` with `requires-python = ">=3.13"`. Decided: 3.13 only, no
  3.10 support; the README changes to match.
- A `dev` dependency group: `pytest`, `pyright`, `ruff`, `pytest-playwright`.
- `[tool.pyright]`: `pythonVersion = "3.13"`, `include = ["*.py", "tests"]`, standard type-checking mode (the
  default). No `strict`.
- `[tool.ruff]`: `target-version = "py313"`, `line-length = 120`. Rule
  sets: the defaults (`E4`, `E7`, `E9`, `F`) plus `I` (import order), `UP`
  (pyupgrade) and `B` (bugbear). The line length is for the formatter and
  its check.
- `[tool.pytest.ini_options]`: `testpaths = ["tests"]`, and the `e2e`
  marker so `pytest -m "not e2e"` runs the unit tests alone.

Use `uv` to install the dev group, locally and in CI. The commands to run
everything locally:

```sh
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

The README gets a short "Checks" section with these commands, and its two
mentions of Python 3.10+ become 3.13.

The Tauri bundle copies `../*.py` into the app. `pyproject.toml` and `tests/`
are not matched by that pattern, so the app bundle is unchanged.

## 2. Type checking: pyright

Fix the 13 current errors so the check starts green. They are all small:

- `classify.py`: ten `Optional` accesses on `re.match` results, on the
  current hunk while parsing a patch, and on an `ast` node's `end_lineno`
  (lines 67, 86, 87, 94, 98, 101, 140). None of them is `None` in practice.
  Fix by narrowing or by restructuring the loop.
- `classify.py:385`: `origins` is declared as `dict[tuple[str, str], dict]`
  but starts as a dict of `None` values. Fix the annotation.
- `build.py:163`: `(new or old) + "\n"` where both can be `None` in the type.
  Fix with a narrowing.
- `server.py:248`: `self.server.server_address[1]`; the stub types the
  address as a buffer. Fix by reading the port from the server once at
  start-up.
- `server.py:290`: `log_message(self, fmt, *args)` renames the base class
  parameter `format`. Rename it back.

No `# type: ignore` unless the standard library stubs are wrong.

The pyright and ruff fixes go in the same pull request as the workflow, as
separate commits, one per module.

## 3. Linting: ruff

`ruff check` with the rule sets above. Fix whatever it reports once the `I`,
`UP` and `B` sets are on (the unused import in `server.py` for sure; a few
import-order and `B` findings are likely). The lint job also runs
`ruff format --check`; the one-time reformat lands first in its own pull
request.

## 4. Unit tests: pytest

New directory `tests/` with `conftest.py` and one test file per module.

`conftest.py` sets `DIFF_REVIEW_CACHE` to a temporary directory before any
test imports `build`, because `build.py` reads that variable at import time.
It also adds the repo root to `sys.path` so the tests import the modules as
the scripts do.

A shared helper `tests/helpers.py` builds the input `classify.classify()`
takes from two dicts of file contents, "old" and "new": it creates a
temporary git repo, commits each dict as a tree, runs `git diff -M -U3`
between the two commits and writes `diff.patch`, `old/`, `new/` and
`generated.txt` the way `build.export()` does. Tests then declare the
before and after of a pull request inline instead of hand-writing patches.
The end-to-end tests reuse the same helper to make their fixture repo.

What the tests cover:

- `classify.py` (most of the tests): `parse_patch` on added, deleted,
  renamed and binary files; `build_runs` grouping; `python_structure` for
  import lines and the symbol outline; `collect_renames` and
  `apply_renames`, including a rename that recurs more than once on a line;
  `classify_run` for each class (`import`, `rename`, `whitespace`, `moved`,
  `substantive`); `detect_moves`; `file_kind`; `is_generated` for the
  `.gitattributes` mark, lock files and the header markers; `language`;
  and `classify()` end to end on a small pull request, asserting the
  `totals` and the per-file `stats`.
- `build.py`: `parse_ref` with the URL and `owner/repo#n` forms, upper-case
  names and bad input; `changed_paths` with renames; `page_path`;
  `page_current` and `latest_page` with files whose mtimes are set by the
  test.
- `render.py`: `render_page` produces a document with the title, the data
  payload (with `</` escaped so it cannot close the script tag), every
  `page/*.js` file in name order, and the theme CSS.
- `comments.py`: `_thread` and `fetch` on fixture GraphQL responses, with
  `graphql` replaced by a function that returns canned data. Covers the
  pending review, a resolved thread, an outdated thread and the
  pagination loop.
- `server.py`: `Handler.refused` with a good origin, a foreign origin, a
  missing origin and a wrong content type; `ago`; `page_key`. The routing is
  covered by the end-to-end tests.

Target: under 5 seconds for the unit suite.

## 5. End-to-end tests: Playwright

Python Playwright through `pytest-playwright`, so the whole test stack is
one language and one runner. Chromium only.

The harness, in `tests/e2e/`:

- `fakegh/gh`: an executable Python script placed first on `PATH` for the
  server process. It answers the three `gh` commands the code uses.
  `gh pr view ... --json` prints the fixture pull request's metadata.
  `gh repo clone owner/repo dest -- --bare ...` runs `git clone --bare` from
  the local fixture repo. `gh api graphql --input -` reads the query on
  stdin; a read query returns the fixture threads, reviews and comments; a
  mutation appends to a log file in the test's temp directory and returns a
  canned id, so a test can assert what the page sent.
- A session fixture that builds the fixture repo with the helper from
  section 4: a base commit, a head commit with a few changed files (one
  substantive change, one import-only file, one rename with fallout, one
  generated file), and a `refs/pull/1/head` ref so `build.fetch()` works
  as it does against GitHub.
- A session fixture that starts `server.py` as a subprocess on a free port
  with `DIFF_REVIEW_CACHE` pointing at a temp directory and the fake `gh`
  on `PATH`, waits for it to answer, and stops it at the end.
- Every test blocks the highlight.js CDN request, so the tests do not depend
  on the network and the page is tested in the state it has when the CDN is
  unreachable (the page already handles a missing `hljs`).

Scenarios, one test each:

1. The start page opens a pull request: typing `owner/repo#1` into the
   search bar redirects to the page, the progress page shows while it
   builds, and the review page appears with the file tree.
2. The tree hides the import-only and generated files by default and the
   notice above it counts them; clicking the notice shows them.
3. A fold row opens on click and hides again.
4. `/` focuses the search; typing a word and pressing Enter scrolls to the
   first hit.
5. `r` marks the current file reviewed, and the mark is still there after a
   reload.
6. Comments load from the fake `gh` and appear under their lines; a resolved
   thread is folded to one line; the overview table lists every thread.
7. Posting a single comment on a line sends a POST to `/api/.../comment`
   with the right path, line and side (asserted from the mutation log), and
   the thread appears after the refresh.
8. The shell: opening a pull request from the home tab adds a tab, and the
   tab is still there after a reload.

Target: under 1 minute in CI. Failed tests upload the Playwright trace as an
artifact.

## 6. The workflow

`.github/workflows/ci.yml`, triggered on `pull_request` and on `push` to
`main`, with `concurrency` set to cancel an older run of the same branch.
Three jobs, all on `ubuntu-latest`, each installing with
`astral-sh/setup-uv` and `uv sync`:

- `lint`: `ruff check .`, `ruff format --check .`, then `pyright`. Python 3.13.
- `unit`: `pytest -m "not e2e"`. Python 3.13.
- `e2e`: `playwright install --with-deps chromium`, then `pytest -m e2e`.
  Python 3.13. Uploads `test-results/` on failure.

The three jobs run in parallel. Expected total wall time: about 2 minutes.

Branch protection (requiring the checks before merge) is a repository
setting and not part of this change.

## Not in this plan

- The Tauri shell. `cargo check` needs a Rust toolchain and GTK system
  libraries on Linux, which adds minutes to every run. It can be a separate
  workflow later.
- A JavaScript linter or unit tests for `page/*.js`. The scripts are only
  run inside the rendered page, so the end-to-end tests are their coverage
  for now.

## Order of work

1. A separate pull request that runs `ruff format` once with
   `line-length = 120` and nothing else. Merged before the rest.
2. `pyproject.toml`, ruff and pyright configuration, fix the findings.
3. `tests/` with `conftest.py`, the helper and the unit tests.
4. The end-to-end harness and the eight scenarios.
5. The workflow file, the README section, and a check that the workflow is
   green on the pull request.
