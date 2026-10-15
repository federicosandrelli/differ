"""The pull requests opened last, newest first, as the start page lists them. The list lives in recent.json in the
cache."""

from __future__ import annotations

import html
import json
import os
import threading
import time
from typing import TypedDict

import build
import github
import render
from github import PullRef

PATH = build.CACHE / "recent.json"
KEPT = 10
lock = threading.Lock()
refreshing = threading.Lock()


class Opened(TypedDict):
    """A pull request in the list: the title and status it had when it was last opened or refreshed, and when it
    was last opened, in seconds since the epoch."""

    owner: str
    repo: str
    number: int
    title: str
    status: str
    opened: float


def ref_of(p: Opened) -> PullRef:
    return PullRef(p["owner"], p["repo"], p["number"])


def load() -> list[Opened]:
    try:
        pages: list[Opened] = json.loads(PATH.read_text())
    except (OSError, ValueError):
        return []
    for p in pages:
        p.setdefault("status", "")  # an entry saved before the list kept statuses has none
    return pages


def save(pages: list[Opened]) -> None:
    """Replace recent.json in one step, so a reader sees the old list or the new one and never part of one. The
    list goes to recent.json.tmp first and is renamed over recent.json, so a crash mid-write leaves the old list."""
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_name(PATH.name + ".tmp")
    tmp.write_text(json.dumps(pages))
    os.replace(tmp, PATH)


def note_opened(ref: PullRef, page: bytes) -> None:
    """Move the pull request to the top of the list, with the title of the page just served. A pull request new to
    the list takes the status the page was built with; one already there keeps its status, which
    refresh_statuses keeps up to date, since a merge or a close leaves the page as it was."""
    title, status = render.read_head(page)
    with lock:
        pages = load()
        saved = next((p for p in pages if ref_of(p) == ref), None)
        entry = Opened(
            owner=ref.owner,
            repo=ref.repo,
            number=ref.number,
            title=title or str(ref),
            status=saved["status"] if saved else status,
            opened=time.time(),
        )
        save([entry, *(p for p in pages if p is not saved)][:KEPT])


def refresh_statuses() -> None:
    """Save the status GitHub reports now for each pull request in the list. A pull request GitHub can't return
    keeps the status saved before. While one refresh runs, another returns at once."""
    if not refreshing.acquire(blocking=False):
        return
    try:
        live = github.pr_statuses([ref_of(p) for p in load()])
        with lock:
            pages = load()
            stale = [p for p in pages if live.get(ref_of(p), p["status"]) != p["status"]]
            for p in stale:
                p["status"] = live[ref_of(p)]
            if stale:
                save(pages)
    finally:
        refreshing.release()


def items_html() -> str:
    """The list as the start page shows it."""
    return "".join(map(item_html, load())) or '<div class="empty">No pull requests opened yet.</div>'


def item_html(p: Opened) -> str:
    ref = ref_of(p)
    return (
        f'<a class="item" href="{ref.path}"><span class="t">{render.pr_icon(p["status"])}'
        f'{html.escape(p["title"])}</span><span class="w">{html.escape(ago(p["opened"]))}</span>'
        f'<span class="m">{html.escape(str(ref))}</span></a>'
    )


def ago(ts: float) -> str:
    s = max(0, int(time.time() - ts))
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{s // n}{unit} ago"
    return "just now"
