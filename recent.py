"""The pull requests opened last, newest first, in recent.json in the cache."""

from __future__ import annotations

import json
import os
import threading
import time
from typing import TypedDict

import build
import github
from github import PullRef

PATH = build.CACHE / "recent.json"
KEPT = 30
lock = threading.Lock()
refreshing = threading.Lock()


class Opened(TypedDict):
    """A pull request in the list: the title of the page last served for it, its status and author (the page's when
    it joined the list, then GitHub's as of the last refresh), and when it was last opened, in seconds since the
    epoch."""

    owner: str
    repo: str
    number: int
    title: str
    status: str
    author: str
    opened: float


class Recent(TypedDict):
    """What recent.json holds: the pull requests opened last, newest first."""

    pages: list[Opened]


def ref_of(p: Opened) -> PullRef:
    return PullRef(p["owner"], p["repo"], p["number"])


def load() -> Recent:
    try:
        data = json.loads(PATH.read_text())
    except (OSError, ValueError):
        return Recent(pages=[])
    # A list saved before recent.json was an object, or before it kept one list, holds more than KEPT.
    pages = (data if isinstance(data, list) else data["pages"])[:KEPT]
    for p in pages:
        p.setdefault("status", "")  # an entry saved before the list kept statuses has none
        p.setdefault("author", "")  # an entry saved before the list kept authors has none
    return Recent(pages=pages)


def save(recent: Recent) -> None:
    """Replace recent.json in one step, so a reader sees the old list or the new one and never part of one. The
    list goes to recent.json.tmp first and is renamed over recent.json, so a crash mid-write leaves the old list.
    The list keeps its newest KEPT pull requests; the older ones leave it."""
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_name(PATH.name + ".tmp")
    tmp.write_text(json.dumps(Recent(pages=recent["pages"][:KEPT])))
    os.replace(tmp, PATH)


def note_opened(ref: PullRef, title: str | None, status: str, author: str) -> None:
    """Move the pull request to the top of the list, with the title, status and author of the page just served (see
    render.read_head). A pull request new to the list takes that status; one already there keeps its own, which
    refresh keeps up to date, since a merge or a close leaves the page as it was."""
    with lock:
        recent = load()
        saved = next((p for p in recent["pages"] if ref_of(p) == ref), None)
        entry = Opened(
            owner=ref.owner,
            repo=ref.repo,
            number=ref.number,
            title=title or str(ref),
            status=saved["status"] if saved else status,
            author=author or (saved["author"] if saved else ""),
            opened=time.time(),
        )
        recent["pages"] = [entry, *(p for p in recent["pages"] if ref_of(p) != ref)]
        save(recent)


def refresh() -> None:
    """Save the status and author GitHub reports now for each pull request in the list. A pull request GitHub can't
    return keeps what was saved before. While one refresh runs, another returns at once."""
    if not refreshing.acquire(blocking=False):
        return
    try:
        live = github.pr_standings([ref_of(p) for p in load()["pages"]])
        with lock:
            recent = load()
            changed = False
            for p in recent["pages"]:
                standing = live.get(ref_of(p))
                if standing and (standing.status, standing.author) != (p["status"], p["author"]):
                    p["status"], p["author"] = standing.status, standing.author
                    changed = True
            if changed:
                save(recent)
    finally:
        refreshing.release()
