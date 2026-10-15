"""Build the diff the classifier reads from the before and after of a pull request, through a real git repo."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
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
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=GIT_ENV
    ).stdout


def commit_tree(repo: Path, files: Files, message: str) -> str:
    """Replace the working tree with `files`, commit, and return the commit's sha."""
    for tracked in git(repo, "ls-files", "-z").split("\0"):
        if tracked:
            (repo / tracked).unlink()
    for path, content in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
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
