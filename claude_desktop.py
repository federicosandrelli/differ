"""The sessions of the Claude desktop app's Code tab, read from the files the app keeps for them, and the links that
open them in the app."""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.parse
from pathlib import Path
from typing import NamedTuple

from github import PullRef

SESSIONS = Path(
    os.environ.get("DIFFER_CLAUDE_SESSIONS")
    or Path.home() / "Library" / "Application Support" / "Claude" / "claude-code-sessions"
)
# The ids the app's claude://code/continue link takes.
SESSION_ID = re.compile(r"^local_[A-Za-z0-9-]{1,64}$")
lock = threading.Lock()


class Session(NamedTuple):
    """A Code tab session: the link that opens it in the app, its title, when it was last active, in milliseconds
    since the epoch, and whether the reader archived it."""

    link: str
    title: str
    active: float
    archived: bool = False


# Each session file read so far: its modification time and size when read, and the pull requests it ties its
# session to, each with the session.
read_files: dict[Path, tuple[tuple[int, int], list[tuple[PullRef, Session]]]] = {}


def read(path: Path) -> list[tuple[PullRef, Session]]:
    """The GitHub pull requests a session file ties its session to, each with the session. Empty for a file that
    can't be read or parsed, or for a session without pull requests."""
    try:
        data = path.read_bytes()
    except OSError:
        return []
    if b'"prs"' not in data:
        return []
    try:
        meta = json.loads(data)
    except ValueError:
        return []
    if not isinstance(meta, dict) or not SESSION_ID.match(str(meta.get("sessionId") or "")):
        return []
    link = "claude://code/continue?" + urllib.parse.urlencode({"session": meta["sessionId"]})
    active = meta.get("lastActivityAt")
    active = active if isinstance(active, int | float) else 0.0
    session = Session(link, str(meta.get("title") or ""), active, meta.get("isArchived") is True)
    prs = meta.get("prs")
    tied = []
    for pr in prs if isinstance(prs, list) else []:
        url = pr.get("url") if isinstance(pr, dict) else None
        if isinstance(url, str) and url.startswith("https://github.com/"):
            try:
                tied.append((PullRef.parse(url), session))
            except ValueError:
                pass
    return tied


def sessions() -> dict[PullRef, Session]:
    """The session tied to each pull request: when several are, the one active last among those the reader has not
    archived, or among all of them when the reader archived every one. A file is read again only when its
    modification time or size changes."""
    try:
        paths = set(SESSIONS.glob("*/*/local_*.json"))
    except OSError:
        paths = set()
    found: dict[PullRef, Session] = {}
    with lock:
        for path in set(read_files) - paths:
            del read_files[path]
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            stamp = (stat.st_mtime_ns, stat.st_size)
            if path not in read_files or read_files[path][0] != stamp:
                read_files[path] = (stamp, read(path))
            for ref, session in read_files[path][1]:
                if ref not in found or (not session.archived, session.active) > (
                    not found[ref].archived,
                    found[ref].active,
                ):
                    found[ref] = session
    return found
