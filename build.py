#!/usr/bin/env python3
"""Build the review page for one GitHub pull request.

Usage: build.py <owner>/<repo>#<number> | <pull request URL>

Keeps its own bare clone of each repo, without file contents, in the cache; git fetches the files the diff
needs. Diffs the head against the merge base, as GitHub does.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path

import classify
import commands
import github
import render
from github import PullRef

ROOT = Path(__file__).resolve().parent
CACHE = Path(os.environ.get("DIFF_REVIEW_CACHE", Path.home() / ".cache" / "diff-review"))
PAGES_KEPT = 2
PAGE_MAX_AGE = 30 * 86400

Log = Callable[[str], None]


def git(repo_dir: Path, *args: str, input: bytes | None = None, timeout: float = commands.TIMEOUT) -> bytes:
    return commands.run(["git", "-C", str(repo_dir), *args], input, timeout)


def pages_dir(ref: PullRef) -> Path:
    return CACHE / "pages" / ref.owner / ref.repo / str(ref.number)


def page_path(ref: PullRef, head: str) -> Path:
    return pages_dir(ref) / f"{head[:12]}.html"


def sources() -> list[Path]:
    return [ROOT / "build.py", ROOT / "classify.py", *render.sources()]


def page_current(page: Path) -> bool:
    """Whether the page exists and was built after the last change to the code that builds pages."""
    return page.exists() and page.stat().st_mtime >= max(src.stat().st_mtime for src in sources())


def latest_page(ref: PullRef) -> Path | None:
    """The newest current page built for the pull request, whatever head commit it was built from."""
    pages = sorted(pages_dir(ref).glob("*.html"), key=lambda p: p.stat().st_mtime)
    return next((p for p in reversed(pages) if page_current(p)), None)


def forget_pages(ref: PullRef) -> None:
    shutil.rmtree(pages_dir(ref), ignore_errors=True)


def mtime(path: Path) -> float:
    """The modification time of a file, or 0 for a file another thread deleted since it was listed."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def prune(ref: PullRef) -> None:
    """Delete the work directory of the pull request and all of its pages but the newest PAGES_KEPT."""
    shutil.rmtree(work_dir(ref), ignore_errors=True)
    pages = sorted(pages_dir(ref).glob("*.html"), key=mtime)
    for page in pages[:-PAGES_KEPT]:
        page.unlink(missing_ok=True)


def prune_all() -> None:
    """Delete every work directory and the pages older than PAGE_MAX_AGE. The bare clones in repos/ stay."""
    shutil.rmtree(CACHE / "work", ignore_errors=True)
    cutoff = time.time() - PAGE_MAX_AGE
    for page in (CACHE / "pages").glob("*/*/*/*.html"):
        if mtime(page) < cutoff:
            page.unlink(missing_ok=True)


def work_dir(ref: PullRef) -> Path:
    return CACHE / "work" / ref.owner / ref.repo / str(ref.number)


def ensure_repo(ref: PullRef, log: Log) -> Path:
    mirror = CACHE / "repos" / ref.owner / f"{ref.repo}.git"
    if not mirror.exists():
        log(f"Cloning {ref.slug} without file contents (first time for this repo)")
        mirror.parent.mkdir(parents=True, exist_ok=True)
        github.clone(ref, mirror)
    else:
        log(f"Using the cached clone of {ref.slug}")
    return mirror


def has_commit(repo_dir: Path, sha: str) -> bool:
    return commands.call(["git", "-C", str(repo_dir), "cat-file", "-e", f"{sha}^{{commit}}"]).returncode == 0


def fetch(repo_dir: Path, number: int, base: str, head: str, log: Log) -> None:
    if has_commit(repo_dir, base) and has_commit(repo_dir, head):
        return
    log("Fetching the pull request's commits")
    git(repo_dir, "fetch", "--quiet", "--no-tags", "origin", f"pull/{number}/head", base, timeout=commands.LONG_TIMEOUT)
    if not has_commit(repo_dir, head):
        git(repo_dir, "fetch", "--quiet", "--no-tags", "origin", head, timeout=commands.LONG_TIMEOUT)


def read_blobs(repo_dir: Path, specs: list[str]) -> dict[str, bytes]:
    """The content of every `<commit>:<path>` spec, read in one git process; missing specs are left out."""
    out = git(repo_dir, "cat-file", "--batch", "--buffer", input="".join(s + "\n" for s in specs).encode())
    blobs: dict[str, bytes] = {}
    pos = 0
    for spec in specs:
        end = out.index(b"\n", pos)
        header = out[pos:end].decode(errors="replace").split(" ")
        pos = end + 1
        if header[-1] == "missing":
            continue
        size = int(header[2])
        blobs[spec] = out[pos : pos + size]
        pos += size + 1
    return blobs


def changed_paths(names: str) -> list[tuple[str | None, str | None]]:
    """(old path, new path) per entry of `git diff --name-status`; None on the side where the file does not exist."""
    pairs: list[tuple[str | None, str | None]] = []
    for line in names.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith(("R", "C")):
            pairs.append((parts[1], parts[2]))
        elif status == "D":
            pairs.append((parts[1], None))
        elif status == "A":
            pairs.append((None, parts[1]))
        else:
            pairs.append((parts[1], parts[1]))
    return pairs


def export(repo_dir: Path, base: str, head: str, out: Path, log: Log) -> None:
    log("Computing the diff")
    (out / "diff.patch").write_bytes(git(repo_dir, "diff", "-M", "-U3", base, head))
    names = git(repo_dir, "diff", "-M", "--name-status", base, head).decode(errors="replace")
    (out / "names.txt").write_text(names)
    pairs = changed_paths(names)
    log(f"Reading {len(pairs)} changed files")
    wanted = [
        (side, path) for old, new in pairs for side, sha, path in (("old", base, old), ("new", head, new)) if path
    ]
    blobs = read_blobs(repo_dir, [f"{base if side == 'old' else head}:{path}" for side, path in wanted])
    for side, path in wanted:
        target = out / side / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blobs.get(f"{base if side == 'old' else head}:{path}", b""))
    paths = "".join(path + "\n" for old, new in pairs if (path := new or old))
    # A git older than 2.40 has no --source and fails here, so it marks no file; lock files and generator headers
    # still mark theirs.
    attrs = commands.call(
        ["git", "-C", str(repo_dir), "check-attr", f"--source={head}", "--stdin", "linguist-generated"], paths.encode()
    )
    generated = [
        line.rsplit(": ", 2)[0]
        for line in attrs.stdout.decode(errors="replace").splitlines()
        if line.rsplit(": ", 1)[-1] in ("set", "true")
    ]
    (out / "generated.txt").write_text("".join(path + "\n" for path in generated))


def build(ref: PullRef, log: Log = print) -> Path:
    log(f"Reading {ref} from GitHub")
    pr = github.pull_request(ref)
    target = page_path(ref, pr.head)
    if page_current(target):
        log("Already built for this head commit")
        return target

    repo_dir = ensure_repo(ref, log)
    fetch(repo_dir, ref.number, pr.base, pr.head, log)
    merge_base = git(repo_dir, "merge-base", pr.base, pr.head).decode().strip()

    work = work_dir(ref) / pr.head[:12]
    shutil.rmtree(work, ignore_errors=True)
    (work / "old").mkdir(parents=True)
    (work / "new").mkdir(parents=True)
    export(repo_dir, merge_base, pr.head, work, log)

    annotations = {
        "pr": {
            "number": ref.number,
            "title": pr.title,
            "url": pr.url,
            "repo": ref.slug,
            "base": merge_base,
            "head": pr.head,
            "author": pr.author,
            "commits": pr.commits,
            "body": pr.body,
            "base_ref": pr.base_ref,
            "head_ref": pr.head_ref,
            "state": pr.state,
            "draft": pr.draft,
            "status": pr.status,
        }
    }
    (work / "annotations.json").write_text(json.dumps(annotations, indent=2, ensure_ascii=False) + "\n")

    log("Classifying changes")
    classified = classify.classify(work)
    (work / "classified.json").write_text(json.dumps(classified, ensure_ascii=False))
    log("Rendering the page")
    html = render.render_page(classified, annotations)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html)
    prune(ref)
    log("Done")
    return target


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    try:
        print(build(PullRef.parse(sys.argv[1])))
    except (ValueError, commands.CommandError) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
