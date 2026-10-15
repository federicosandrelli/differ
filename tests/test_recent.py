import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

import commands
import recent
from github import PullRef


@pytest.fixture(autouse=True)
def recent_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "recent.json"
    monkeypatch.setattr(recent, "PATH", path)
    return path


def set_viewer(login: str) -> None:
    data = recent.load()
    data["viewer"] = login
    recent.save(data)


def numbers_in(section: str) -> list[int]:
    """The pull request numbers the section of items_html lists, in order."""
    m = re.search(rf'<section class="prs {section}">.*?</section>', recent.items_html(), re.S)
    assert m
    return [int(n) for n in re.findall(r'href="/o/r/pull/(\d+)"', m[0])]


def test_ago() -> None:
    now = time.time()
    assert recent.ago(now) == "just now"
    assert recent.ago(now - 90) == "1m ago"
    assert recent.ago(now - 3700) == "1h ago"
    assert recent.ago(now - 2 * 86400) == "2d ago"


def test_note_opened_moves_the_page_to_the_top_and_saves_it(recent_path: Path) -> None:
    assert recent.load() == {"viewer": "", "pages": []}
    for n in range(3):
        recent.note_opened(PullRef("o", "r", n), f"Title & {n}", "open", "ann")
    recent.note_opened(PullRef("o", "r", 1), None, "", "")
    pages = recent.load()["pages"]
    assert [p["number"] for p in pages] == [1, 2, 0]
    assert pages[0]["title"] == "o/r#1"
    assert pages[0]["author"] == "ann"
    assert pages[1]["title"] == "Title & 2"
    saved = json.loads(recent_path.read_text())
    assert saved["viewer"] == ""
    assert saved["pages"][1] == {
        "owner": "o",
        "repo": "r",
        "number": 2,
        "title": "Title & 2",
        "status": "open",
        "author": "ann",
        "opened": pages[1]["opened"],
    }


def test_each_section_keeps_its_newest_twenty() -> None:
    set_viewer("me")
    for n in range(22):
        recent.note_opened(PullRef("o", "r", n), None, "open", "me")
    for n in range(100, 122):
        recent.note_opened(PullRef("o", "r", n), None, "open", "ann")
    for n in range(200, 222):
        recent.note_opened(PullRef("o", "r", n), None, "merged", "me" if n % 2 else "ann")
    assert numbers_in("mine") == list(range(21, 1, -1))
    assert numbers_in("other") == list(range(121, 101, -1))
    assert numbers_in("merged") == list(range(221, 201, -1))
    assert len(recent.load()["pages"]) == 60


def test_a_merged_pull_request_leaves_its_section_for_the_merged_one() -> None:
    set_viewer("me")
    for n in range(21):
        recent.note_opened(PullRef("o", "r", n), None, "open", "me")
    recent.note_opened(PullRef("o", "r", 50), None, "merged", "me")
    assert numbers_in("mine") == list(range(20, 0, -1))
    assert numbers_in("merged") == [50]
    data = recent.load()
    data["pages"][1]["status"] = "merged"
    recent.save(data)
    assert numbers_in("mine") == list(range(19, 0, -1))
    assert numbers_in("merged") == [50, 20]


def test_the_sections_follow_the_viewer_saved_last() -> None:
    recent.note_opened(PullRef("o", "r", 1), None, "open", "me")
    recent.note_opened(PullRef("o", "r", 2), None, "open", "ann")
    recent.note_opened(PullRef("o", "r", 3), None, "closed", "")
    assert numbers_in("mine") == []
    assert numbers_in("other") == [3, 2, 1]
    set_viewer("me")
    assert numbers_in("mine") == [1]
    assert numbers_in("other") == [3, 2]
    assert numbers_in("merged") == []


def test_recent_items_escapes_titles_and_names_each_section() -> None:
    html = recent.items_html()
    assert html.count('<div class="empty">None opened yet.</div>') == 3
    assert re.findall(r"<h2>(.*?)</h2>", html) == ["mine", "other", "merged"]
    recent.note_opened(PullRef("o", "r", 1), "a <b>", "", "")
    items = recent.items_html()
    assert 'href="/o/r/pull/1"' in items
    assert re.search(r'<span class="t">a &lt;b&gt;</span>', items)


def test_refresh_saves_the_viewer_statuses_and_authors_github_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    for n in range(4):
        recent.note_opened(PullRef("o", "r", n), None, "open", "")
    answer = {
        "data": {
            "viewer": {"login": "me"},
            "p0": {"pullRequest": None},
            "p1": None,
            "p2": {"pullRequest": {"state": "OPEN", "isDraft": True, "author": {"login": "me"}}},
            "p3": {"pullRequest": {"state": "MERGED", "isDraft": False, "author": {"login": "ann"}}},
        },
        "errors": [{"message": "Could not resolve to a Repository"}],
    }
    sent = {}

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        sent.update(json.loads(kwargs["input"])["variables"])
        return subprocess.CompletedProcess(args, 1, json.dumps(answer).encode(), b"")

    monkeypatch.setattr(commands.subprocess, "run", fake_run)
    recent.refresh()
    assert sent["n0"] == 3 and sent["o0"] == "o" and sent["r0"] == "r"
    data = recent.load()
    assert data["viewer"] == "me"
    assert {p["number"]: (p["status"], p["author"]) for p in data["pages"]} == {
        3: ("open", ""),
        2: ("open", ""),
        1: ("draft", "me"),
        0: ("merged", "ann"),
    }
    assert numbers_in("mine") == [1]
    assert numbers_in("other") == [3, 2]
    assert numbers_in("merged") == [0]


def test_refresh_keeps_what_was_saved_when_gh_fails(recent_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_viewer("me")
    recent.note_opened(PullRef("o", "r", 1), None, "merged", "ann")
    before = recent_path.read_text()
    monkeypatch.setattr(
        commands.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, b"", b"gh: not logged in"),
    )
    recent.refresh()
    assert recent_path.read_text() == before


def test_refresh_skips_github_while_another_refresh_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    recent.note_opened(PullRef("o", "r", 1), None, "open", "")

    def fail(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise AssertionError("gh ran during another refresh")

    monkeypatch.setattr(commands.subprocess, "run", fail)
    with recent.refreshing:
        recent.refresh()
    assert recent.load()["pages"][0]["status"] == "open"


def test_note_opened_keeps_the_old_list_when_a_write_dies(recent_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recent.note_opened(PullRef("o", "r", 1), "First", "", "")
    before = recent_path.read_text()

    def die(self: Path, data: str, **kwargs: Any) -> int:
        self.write_bytes(data[: len(data) // 2].encode())
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", die)
    with pytest.raises(OSError):
        recent.note_opened(PullRef("o", "r", 2), "Second", "", "")
    assert recent_path.read_text() == before
    assert [p["number"] for p in recent.load()["pages"]] == [1]


def test_a_list_saved_before_the_viewer_and_authors_loads_without_them(recent_path: Path) -> None:
    recent_path.write_text('[{"owner": "o", "repo": "r", "number": 1, "title": "T", "opened": 0}]')
    assert recent.load() == {
        "viewer": "",
        "pages": [{"owner": "o", "repo": "r", "number": 1, "title": "T", "opened": 0, "status": "", "author": ""}],
    }
    assert numbers_in("other") == [1]


def test_opening_a_page_again_keeps_the_status_the_list_saved_and_fills_in_the_author() -> None:
    recent.note_opened(PullRef("o", "r", 1), "T", "open", "")
    data = recent.load()
    data["pages"][0]["status"] = "merged"
    recent.save(data)
    recent.note_opened(PullRef("o", "r", 1), "T", "open", "ann")
    assert [(p["number"], p["status"], p["author"]) for p in recent.load()["pages"]] == [(1, "merged", "ann")]
