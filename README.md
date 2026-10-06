# diff-review

A static prototype of a pull request viewer for agent-written code. It renders one
PR as a single HTML page with the emphasis on files and reading order rather than
on a flat list of hunks.

What it does, per PR:

- **Deterministic classifier** (`classify.py`): parses `git diff -M` output and
  splits every hunk into change blocks. Each block gets a class the reviewer can
  trust because it is proved, not guessed:
  - `import`: every changed line is inside an import statement (Python `ast`).
  - `rename`: the removed line becomes the added line exactly by applying a rename
    that recurs in at least two blocks of the PR (or a git file rename). The block
    with the definition is the origin; the rest is fallout.
  - `whitespace`: lines equal once whitespace is removed.
  - `moved`: an identical block removed in one place and added in another.
  - `substantive`: everything else.
  It also emits the symbol outline of every Python file and which symbols changed.
- **Annotations** (`annotations/<pr>.json`): written by a Claude session that read
  the diff. The PR summary, where to look, file groups in reading order, a one-line
  summary per file, test-to-implementation links, and line-anchored notes.
- **Renderer** (`render.py`): one HTML page. Overview with reading order and the
  rename table; per file, the whole new file with the removed lines interleaved,
  noise collapsed to one row per class, the outline on the right, notes inline,
  and a reviewed mark per file kept in `localStorage`.

## Run

```sh
# export a PR from a checkout of the repo
BASE=<base sha> HEAD=<head sha> ./export.sh <repo dir> input/<pr>
python3 classify.py input/<pr> > out/<pr>.classified.json
python3 render.py out/<pr>.classified.json annotations/<pr>.json out/<pr>.html
python3 -m http.server 8765 --directory out
```

Keys in the page: `j` / `k` next and previous file, `o` overview, `w` whole file,
`r` toggle reviewed.
