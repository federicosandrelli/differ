"""The pull requests opened last, newest first, as the start page lists them. The list lives in recent.json in the
cache."""

from __future__ import annotations

import html
import json
import os
import threading
import time
from dataclasses import dataclass, replace

import build
import github
import render
from github import PullRef

PATH = build.CACHE / "recent.json"
KEPT = 10
lock = threading.Lock()
refreshing = threading.Lock()


@dataclass(frozen=True)
class Opened:
    """A pull request in the list: the title and status it had when it was last opened or refreshed, and when it
    was last opened, in seconds since the epoch."""

    ref: PullRef
    title: str
    status: str
    opened: float


def load() -> list[Opened]:
    try:
        entries = json.loads(PATH.read_text())
    except (OSError, ValueError):
        return []
    return [
        Opened(PullRef(e["owner"], e["repo"], e["number"]), e["title"], e.get("status", ""), e["opened"])
        for e in entries
    ]


def save(pages: list[Opened]) -> None:
    """Replace recent.json in one step, so a reader sees the old list or the new one and never part of one. The
    list goes to recent.json.tmp first and is renamed over recent.json, so a crash mid-write leaves the old list."""
    entries = [
        {
            "owner": p.ref.owner,
            "repo": p.ref.repo,
            "number": p.ref.number,
            "title": p.title,
            "status": p.status,
            "opened": p.opened,
        }
        for p in pages
    ]
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_name(PATH.name + ".tmp")
    tmp.write_text(json.dumps(entries))
    os.replace(tmp, PATH)


def note_opened(ref: PullRef, page: bytes) -> None:
    """Move the pull request to the top of the list, with the title and status of the page just served."""
    title, status = render.read_head(page)
    with lock:
        rest = [p for p in load() if p.ref != ref]
        save([Opened(ref, title or str(ref), status, time.time()), *rest][:KEPT])


def refresh_statuses() -> None:
    """Save the status GitHub reports now for each pull request in the list. A pull request GitHub can't return
    keeps the status saved before. While one refresh runs, another returns at once."""
    if not refreshing.acquire(blocking=False):
        return
    try:
        live = github.pr_statuses([p.ref for p in load()])
        with lock:
            pages = load()
            updated = [replace(p, status=live.get(p.ref, p.status)) for p in pages]
            if updated != pages:
                save(updated)
    finally:
        refreshing.release()


def items_html() -> str:
    """The list as the start page shows it."""
    return "".join(map(item_html, load())) or '<div class="empty">No pull requests opened yet.</div>'


def item_html(p: Opened) -> str:
    return (
        f'<a class="item" href="{p.ref.path}"><span class="t">{render.pr_icon(p.status)}{html.escape(p.title)}</span>'
        f'<span class="w">{html.escape(ago(p.opened))}</span><span class="m">{html.escape(str(p.ref))}</span></a>'
    )


def ago(ts: float) -> str:
    s = max(0, int(time.time() - ts))
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{s // n}{unit} ago"
    return "just now"
