"""The normal clone of each repository, with file contents, that the editors open, and how it moves to a pull
request's branch without losing the user's work."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import build
import commands
import github
from github import PullRef


class Refused(Exception):
    """The server won't open the file now; the message says why and what to do."""


class Checkouts:
    """The clones in the folder `root`, one per repository. `head` gives a pull request's head commit on GitHub."""

    def __init__(self, root: Path, head: Callable[[PullRef], str] = build.recent_head) -> None:
        self.root, self.head = root, head
        self._locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()
        self._opened: dict[str, tuple[int, str]] = {}
        self._branches: dict[PullRef, tuple[str, bool]] = {}

    def folder(self, ref: PullRef) -> Path:
        return self.root / ref.owner / ref.repo

    def warm(self, ref: PullRef) -> None:
        """Clone the repository unless its clone exists, and read the pull request's head branch, so that a later
        `Clone.at_head` waits for neither. Raises CommandError when gh or git fails."""
        with self._lock(ref):
            self._clone(ref)
        self._branch(ref)

    @contextmanager
    def held(self, ref: PullRef) -> Iterator[Clone]:
        """The repository's clone, with the repository's lock held until the block ends, so that one clone or
        checkout at a time runs in it. The lock is not reentrant: a `held` block for the same repository inside
        another one waits forever."""
        with self._lock(ref):
            yield Clone(self, ref)

    def _lock(self, ref: PullRef) -> threading.Lock:
        with self._locks_lock:
            return self._locks.setdefault(ref.slug, threading.Lock())

    def _clone(self, ref: PullRef) -> None:
        """Clone the repository, unless its clone exists, with the repository as gh's default there (gh picks the
        parent for a clone of a fork). The clone goes to its folder only once it is complete, so a failed one starts
        over. A GitHub name never holds "~", so the temporary folder is never another repository's clone."""
        folder = self.folder(ref)
        if folder.exists():
            return
        partial = folder.with_name(f"{folder.name}~partial")
        shutil.rmtree(partial, ignore_errors=True)
        github.gh("repo", "clone", ref.slug, str(partial), "--", "--quiet", timeout=commands.LONG_TIMEOUT)
        github.gh("repo", "set-default", ref.slug, cwd=partial)
        partial.rename(folder)

    def _branch(self, ref: PullRef) -> tuple[str, bool]:
        """The pull request's head branch and whether it lives in a fork, read from GitHub once, since neither
        changes."""
        if ref not in self._branches:
            fields = "headRefName,isCrossRepository"
            meta = json.loads(github.gh("pr", "view", str(ref.number), "--repo", ref.slug, "--json", fields))
            self._branches[ref] = meta["headRefName"], meta["isCrossRepository"]
        return self._branches[ref]

    def _at_head(self, ref: PullRef) -> None:
        folder = self.folder(ref)
        self._clone(ref)
        if not self._up_to_date(ref, folder):
            self._check_out(ref, folder)
            self._opened[ref.slug] = (ref.number, _current_branch(folder))

    def _up_to_date(self, ref: PullRef, folder: Path) -> bool:
        """Whether the clone is still on the branch the last checkout made for this pull request, at the pull
        request's head on GitHub, so that it needs no checkout."""
        number, branch = self._opened.get(ref.slug, (0, ""))
        return (
            number == ref.number
            and branch == _current_branch(folder)
            and _git(folder, "rev-parse", "HEAD").stdout.decode().strip() == self.head(ref)
        )

    def _check_out(self, ref: PullRef, folder: Path) -> None:
        changed = _git(folder, "status", "--porcelain", "--untracked-files=no").stdout.strip()
        if changed and _branch_pull_request(folder) != ref.number:
            raise Refused(
                f"The clone at {folder} has uncommitted changes on {_current_branch(folder) or 'a detached HEAD'}. "
                "Commit or stash them first."
            )
        branch, fork = self._branch(ref)
        try:
            github.gh("pr", "checkout", str(ref.number), "--repo", ref.slug, timeout=commands.LONG_TIMEOUT, cwd=folder)
        except commands.CommandError:
            # A force-pushed branch can't fast-forward. When the local branch diverged from the remote one and holds
            # no commit missing from every remote and from every head the remote branch had, checkout -B moves it to
            # the new head; it refuses rather than overwrite an untracked file or an uncommitted change, and the
            # branch keeps its upstream.
            local, remote = f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"
            if fork or _git(folder, "merge-base", "--is-ancestor", local, remote, check=False).returncode != 1:
                raise
            heads = _git(folder, "log", "-g", "--format=%H", remote).stdout.decode().split()
            # The reflog lists each new head; the head the clone started from is the old value of the oldest entry.
            first = _git(folder, "rev-parse", "--verify", "--quiet", f"{remote}@{{{len(heads)}}}", check=False)
            heads += first.stdout.decode().split()
            if int(_git(folder, "rev-list", "--count", local, "--not", *heads, "--remotes").stdout):
                raise
            _git(folder, "checkout", "-B", branch, remote)


class Clone:
    """A repository's clone, while `Checkouts.held` holds the repository's lock."""

    def __init__(self, checkouts: Checkouts, ref: PullRef) -> None:
        self.folder = checkouts.folder(ref)
        self._checkouts, self._ref = checkouts, ref

    def at_head(self) -> Path:
        """Clone the repository the first time, check out the pull request's branch in the clone, at the pull
        request's head, and return the clone's folder. Skips the checkout when the clone is on the branch the last
        checkout made for this pull request, at its head. Moves a force-pushed branch to the new head with
        `git checkout -B` only when that loses no work: it never loses uncommitted changes, untracked files or
        commits missing from every remote. Raises Refused when the clone has uncommitted changes to tracked files on
        a branch of another pull request or of none, and CommandError when gh or git fails."""
        self._checkouts._at_head(self._ref)
        return self.folder


def _git(folder: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return commands.call(["git", "-C", str(folder), *args], check=check)


def _current_branch(folder: Path) -> str:
    return _git(folder, "branch", "--show-current").stdout.decode().strip()


def _branch_pull_request(folder: Path) -> int | None:
    """The number of the pull request that the branch checked out in the clone in the folder belongs to, as gh
    tells it from the branch's config, or None when gh finds none."""
    try:
        return int(github.gh("pr", "view", "--json", "number", "--jq", ".number", cwd=folder))
    except (commands.CommandError, ValueError):
        return None


checkouts = Checkouts(build.CACHE / "checkouts")
