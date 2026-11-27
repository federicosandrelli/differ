"""Build the diff the classifier reads from the before and after of a pull request, through a real git repo; and wait
for a condition or a process, for the tests that start processes."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path

import build
import classify

Files = Mapping[str, str | bytes]

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, encoding="utf-8", env=GIT_ENV
    ).stdout


def commit_tree(repo: Path, files: Files, message: str, amend: bool = False) -> str:
    """Replace the working tree with `files`, commit (or amend the last commit), and return the commit's sha."""
    for tracked in git(repo, "ls-files", "-z").split("\0"):
        if tracked:
            (repo / tracked).unlink()
    for path, content in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8", newline="")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", *(["--amend"] if amend else []), "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


def make_repo(root: Path, old: Files, new: Files) -> tuple[Path, str, str]:
    """A git repo at `root` with two commits, and the shas of the base and the head."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    base = commit_tree(root, old, "base")
    head = commit_tree(root, new, "head")
    return root, base, head


def make_diff(root: Path, old: Files, new: Files) -> classify.Diff:
    """The diff `classify.classify()` reads, from a repo holding `old` and `new`."""
    repo, base, head = make_repo(root / "repo", old, new)
    return build.read_diff(repo, base, head, log=lambda line: None)


def wait_for(condition: Callable[[], bool], timeout: float = 15) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


def alive(pid: int) -> bool:
    """Whether the process runs: on POSIX a zombie, which only waits for its parent to collect it, does not."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return (
        subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()[:1] != "Z"
    )
