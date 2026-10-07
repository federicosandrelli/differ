# diff-review

A pull request viewer for agent-written code. It shows a PR as one page: the
file tree on the left, every changed file in one scrolling pane on the right,
with import sections and rename fallout folded away.

## Run

```sh
python3 server.py            # http://localhost:8765/
```

The start page lists the open PRs waiting for your review and your own, plus
the pages you opened recently. Paste any PR URL or `owner/repo#123` to open
another. A page is built on first visit and cached by head commit, so a new
push rebuilds it and an unchanged PR opens at once. Add `?refresh=1` to a page
URL to rebuild it anyway.

To build one page without the server:

```sh
python3 build.py https://github.com/owner/repo/pull/123
```

Requirements: Python 3.10+, git, and the GitHub CLI (`gh`) signed in.

## How a page is built

`build.py`:

1. Reads the PR from GitHub with `gh pr view`: title, description, author,
   base and head commits.
2. Keeps its own bare clone of the repo, without file contents, in
   `~/.cache/diff-review/repos/`. The first PR from a repo clones its history;
   later ones only fetch new commits, and git downloads just the files the
   diff needs.
3. Fetches the PR's head and base commits and diffs the head against their
   merge base, which is what GitHub shows.
4. Writes the diff, the old and new content of every changed file, and the PR
   metadata to `~/.cache/diff-review/work/`.
5. Runs `classify.py` and `render.py` and writes the page to
   `~/.cache/diff-review/pages/<owner>/<repo>/<number>/<head>.html`.

`DIFF_REVIEW_CACHE` moves the cache.

## Pieces

- `classify.py` parses the diff and classifies every change block. A block is
  folded only when the classifier can prove what it is: an import statement
  (Python, through `ast`), or a line that becomes its replacement exactly by
  applying a rename that recurs elsewhere in the PR. It also emits the symbol
  outline of every Python file.
- `render.py` turns the classified diff and the PR metadata into one HTML page:
  the PR description, the noise totals and the rename table, a file tree with
  change colours and search, a reviewed mark per file kept in `localStorage`,
  and every file with its removed lines interleaved.
- `server.py` serves the start page and the pages, building them in the
  background with a progress page.

Keys in the page: `j` / `k` next and previous file, arrow keys in the tree,
`/` search and Enter for the next hit, `c` collapse a file, `r` mark reviewed,
`w` whole files, `o` back to the top.
