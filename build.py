#!/usr/bin/env python3
"""Build the review page for one GitHub pull request.

Usage: build.py <owner>/<repo>#<number> | <pull request URL>

Keeps its own bare clone of each repo, without file contents, in the cache; git fetches the files the diff
needs. Diffs the head against the merge base, as GitHub does.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import classify
import commands
import github
import render
from github import PullRef

ROOT = Path(__file__).resolve().parent
CACHE = Path(os.environ.get("DIFF_REVIEW_CACHE", Path.home() / ".cache" / "diff-review"))
PAGES_KEPT = 2
PAGE_MAX_AGE = 30 * 86400
HEAD_TTL = 20

Log = Callable[[str], None]


def git(repo_dir: Path, *args: str, input: bytes | None = None, timeout: float = commands.TIMEOUT) -> bytes:
    return commands.run(["git", "-C", str(repo_dir), *args], input, timeout)


def pages_dir(ref: PullRef) -> Path:
    return CACHE / "pages" / ref.owner / ref.repo / str(ref.number)


def page_path(ref: PullRef, head: str) -> Path:
    return pages_dir(ref) / f"{head[:12]}.html"


def sources() -> list[Path]:
    """Every file that shapes a page, so a page older than any of them is rebuilt."""
    return [ROOT / "build.py", ROOT / "classify.py", ROOT / "github.py", *render.sources()]


def page_current(page: Path) -> bool:
    """Whether the page exists and was built after the last change to the code that builds pages."""
    return page.exists() and page.stat().st_mtime >= max(src.stat().st_mtime for src in sources())


def latest_page(ref: PullRef) -> Path | None:
    """The newest current page built for the pull request, whatever head commit it was built from."""
    pages = sorted(pages_dir(ref).glob("*.html"), key=lambda p: p.stat().st_mtime)
    return next((p for p in reversed(pages) if page_current(p)), None)


def forget_pages(ref: PullRef) -> None:
    shutil.rmtree(pages_dir(ref), ignore_errors=True)


@dataclass
class Job:
    """A page building in the background, as /status reports it."""

    state: Literal["building", "done", "error"] = "building"
    log: list[str] = field(default_factory=list)
    error: str = ""


jobs: dict[PullRef, Job] = {}
# When the head commit of each pull request was last read from GitHub.
head_checked: dict[PullRef, float] = {}
lock = threading.Lock()


def serve(ref: PullRef) -> Path | None:
    """The newest current page of the pull request, or None while its first page builds. A page that has none
    starts building, and serving a page checks the head commit in the background, at most once every HEAD_TTL
    seconds, to build the page for a new push while the reader reads the old one."""
    page = latest_page(ref)
    if not page:
        start(ref)
        return None
    with lock:
        if time.time() - head_checked.get(ref, 0.0) < HEAD_TTL:
            return page
        head_checked[ref] = time.time()

    def check() -> None:
        try:
            if head_moved(ref):
                start(ref)
        except commands.CommandError as exc:
            sys.stderr.write(f"head check for {ref} failed: {exc}\n")

    threading.Thread(target=check, daemon=True).start()
    return page


def refresh(ref: PullRef) -> None:
    """Check the head commit now, and when the newest page was built from an older one, drop the old pages and
    build the page for the head, so the next visit waits for it. Raises CommandError when gh can't tell the head."""
    if head_moved(ref):
        forget_pages(ref)
        start(ref)


def head_moved(ref: PullRef) -> bool:
    """Whether the head commit on GitHub has no current page. Raises CommandError when gh can't tell the head."""
    head = github.head_sha(ref)
    with lock:
        head_checked[ref] = time.time()
    return not page_current(page_path(ref, head))


def start(ref: PullRef) -> Job:
    """Build the pull request's page in the background, unless a build of it is running."""
    with lock:
        job = jobs.get(ref)
        if job and job.state == "building":
            return job
        job = jobs[ref] = Job()

    def work() -> None:
        try:
            build(ref, log=job.log.append)
            job.state = "done"
        except Exception as exc:
            job.error = str(exc)
            job.state = "error"

    threading.Thread(target=work, daemon=True).start()
    return job


def job_of(ref: PullRef) -> Job | None:
    with lock:
        return jobs.get(ref)


def mtime(path: Path) -> float:
    """The modification time of a file, or 0 for a file another thread deleted since it was listed."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def prune(ref: PullRef) -> None:
    """Delete all of the pull request's pages but the newest PAGES_KEPT."""
    pages = sorted(pages_dir(ref).glob("*.html"), key=mtime)
    for page in pages[:-PAGES_KEPT]:
        page.unlink(missing_ok=True)


def prune_all() -> None:
    """Delete the pages older than PAGE_MAX_AGE. The bare clones in repos/ stay."""
    cutoff = time.time() - PAGE_MAX_AGE
    for page in (CACHE / "pages").glob("*/*/*/*.html"):
        if mtime(page) < cutoff:
            page.unlink(missing_ok=True)


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


def read_files(repo_dir: Path, commit: str, paths: list[str]) -> dict[str, bytes]:
    """The content of each path at the commit, read in one git process; a path git can't find there is left out."""
    out = git(repo_dir, "cat-file", "--batch", "--buffer", input="".join(f"{commit}:{p}\n" for p in paths).encode())
    files: dict[str, bytes] = {}
    pos = 0
    for path in paths:
        end = out.index(b"\n", pos)
        header = out[pos:end].decode(errors="replace").split(" ")
        pos = end + 1
        if header[-1] == "missing":
            continue
        size = int(header[2])
        files[path] = out[pos : pos + size]
        pos += size + 1
    return files


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


def read_diff(repo_dir: Path, base: str, head: str, log: Log) -> classify.Diff:
    log("Computing the diff")
    patch = git(repo_dir, "diff", "-M", "-U3", base, head).decode(errors="replace")
    pairs = changed_paths(git(repo_dir, "diff", "-M", "--name-status", base, head).decode(errors="replace"))
    log(f"Reading {len(pairs)} changed files")
    old = read_files(repo_dir, base, [old for old, _ in pairs if old])
    new = read_files(repo_dir, head, [new for _, new in pairs if new])
    paths = "".join(path + "\n" for old, new in pairs if (path := new or old))
    # A git older than 2.40 has no --source and fails here, so it marks no file; lock files and generator headers
    # still mark theirs.
    attrs = commands.call(
        ["git", "-C", str(repo_dir), "check-attr", f"--source={head}", "--stdin", "linguist-generated"], paths.encode()
    )
    generated = {
        line.rsplit(": ", 2)[0]
        for line in attrs.stdout.decode(errors="replace").splitlines()
        if line.rsplit(": ", 1)[-1] in ("set", "true")
    }
    return classify.Diff(patch, old, new, generated)


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
    diff = read_diff(repo_dir, merge_base, pr.head, log)

    # The page diffs the head against the merge base, as GitHub does, so that is the base it names.
    page_pr = asdict(pr) | {"number": ref.number, "repo": ref.slug, "base": merge_base, "status": pr.status or ""}
    log("Classifying changes")
    classified = classify.classify(diff)
    log("Rendering the page")
    html = render.render_page(classified, page_pr)
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
