"""The records the cache keeps per pull request: one JSON file per pull request and kind of record, at
<folder>/<owner>/<repo>/<number>.json in the cache, until the pull request is merged or closed.

A record reads as an empty list or dict when its file is missing, broken or of another shape, and is replaced in one
step: the data goes to a temporary file beside it first, named for the process and the thread that write it, which
then takes the place of the record, so a reader sees the old record or the new one and never part of one, a crash
mid-write leaves the old one, and two writers never share a temporary file."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import build
from github import PullRef


class Record:
    """One kind of record: the files in one folder of the cache, one per pull request."""

    def __init__(self, folder: str) -> None:
        self.folder = folder

    def path(self, ref: PullRef) -> Path:
        return build.CACHE / self.folder / ref.owner / ref.repo / f"{ref.number}.json"

    def read[T: (list, dict)](self, ref: PullRef, shape: type[T]) -> T:
        """The pull request's record; an empty one when it has none, or when its file is broken or holds JSON of
        another shape."""
        try:
            data = json.loads(self.path(ref).read_text())
        except (OSError, ValueError):
            return shape()
        return data if isinstance(data, shape) else shape()

    def write(self, ref: PullRef, data: list | dict) -> None:
        """Replace the pull request's record with the data, in one step."""
        path = self.path(ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, path)

    def delete(self, ref: PullRef) -> None:
        """Delete the pull request's record, if it has one."""
        self.path(ref).unlink(missing_ok=True)

    def refs(self) -> list[PullRef]:
        """The pull requests with a record of this kind, in the order of their files' paths. A file whose name is not
        a number is no record."""
        files = sorted((build.CACHE / self.folder).glob("*/*/*.json"))
        return [PullRef(f.parent.parent.name, f.parent.name, int(f.stem)) for f in files if f.stem.isdigit()]


# The comments for Claude, oldest first (claude.py).
COMMENTS = Record("claude")
# The file descriptions Claude gave, by path (claude.py).
DESCRIPTIONS = Record("descriptions")
# The Session's id, the pid of its last process and whether the id has a conversation to resume (session.py).
SESSIONS = Record("sessions")
KINDS = (COMMENTS, DESCRIPTIONS, SESSIONS)


def kept() -> list[PullRef]:
    """The pull requests with a record of any kind, each once: those with comments first, then those with file
    descriptions, then those with a session, each kind in the order of its files' paths."""
    return list(dict.fromkeys(ref for kind in KINDS for ref in kind.refs()))
