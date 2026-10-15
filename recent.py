"""The pull requests opened last, newest first, as the start page lists them in three sections: the viewer's own,
other people's, and the merged ones. The list lives in recent.json in the cache."""

from __future__ import annotations

import html
import json
import os
import threading
import time
from collections import Counter
from typing import TypedDict

import build
import github
import render
from github import PullRef

PATH = build.CACHE / "recent.json"
KEPT = 20
SECTIONS = (("mine", "mine"), ("other", "other"), ("merged", "merged"))
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
    """What recent.json holds: the viewer's login as of the last refresh (empty before the first), and the pull
    requests opened last, newest first."""

    viewer: str
    pages: list[Opened]


def ref_of(p: Opened) -> PullRef:
    return PullRef(p["owner"], p["repo"], p["number"])


def section_of(p: Opened, viewer: str) -> str:
    """The section of the start page that lists the pull request: merged ones under "merged", the rest under
    "mine" when the viewer wrote them and "other" otherwise. With no viewer known, nothing is the viewer's."""
    if p["status"] == "merged":
        return "merged"
    return "mine" if viewer and p["author"] == viewer else "other"


def load() -> Recent:
    try:
        data = json.loads(PATH.read_text())
    except (OSError, ValueError):
        return Recent(viewer="", pages=[])
    if isinstance(data, list):  # a list saved before recent.json kept the viewer
        data = {"viewer": "", "pages": data}
    recent = Recent(viewer=data["viewer"], pages=data["pages"])
    for p in recent["pages"]:
        p.setdefault("status", "")  # an entry saved before the list kept statuses has none
        p.setdefault("author", "")  # an entry saved before the list kept authors has none
    return recent


def save(recent: Recent) -> None:
    """Replace recent.json in one step, so a reader sees the old list or the new one and never part of one. The
    list goes to recent.json.tmp first and is renamed over recent.json, so a crash mid-write leaves the old list.
    Each section keeps its newest KEPT pull requests; the older ones leave the list."""
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_name(PATH.name + ".tmp")
    tmp.write_text(json.dumps(Recent(viewer=recent["viewer"], pages=trimmed(recent))))
    os.replace(tmp, PATH)


def trimmed(recent: Recent) -> list[Opened]:
    """The pages of the list with each section cut to its newest KEPT pull requests."""
    counts: Counter[str] = Counter()
    kept = []
    for p in recent["pages"]:
        section = section_of(p, recent["viewer"])
        counts[section] += 1
        if counts[section] <= KEPT:
            kept.append(p)
    return kept


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
    """Save the viewer's login, and the status and author GitHub reports now for each pull request in the list. A
    pull request GitHub can't return keeps what was saved before, and so does the login when gh fails. While one
    refresh runs, another returns at once."""
    if not refreshing.acquire(blocking=False):
        return
    try:
        viewer, live = github.pr_standings([ref_of(p) for p in load()["pages"]])
        with lock:
            recent = load()
            changed = bool(viewer) and viewer != recent["viewer"]
            if changed:
                recent["viewer"] = viewer
            for p in recent["pages"]:
                standing = live.get(ref_of(p))
                if standing and (standing.status, standing.author) != (p["status"], p["author"]):
                    p["status"], p["author"] = standing.status, standing.author
                    changed = True
            if changed:
                save(recent)
    finally:
        refreshing.release()


def items_html() -> str:
    """The list as the start page shows it: one section per entry of SECTIONS, each with its heading."""
    recent = load()
    sections = []
    for key, heading in SECTIONS:
        items = "".join(item_html(p) for p in recent["pages"] if section_of(p, recent["viewer"]) == key)
        sections.append(
            f'<section class="prs {key}"><h2>{heading}</h2>'
            f'<div class="list">{items or '<div class="empty">None opened yet.</div>'}</div></section>'
        )
    return "".join(sections)


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
