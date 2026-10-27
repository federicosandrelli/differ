import json
import subprocess
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


def test_note_opened_moves_the_page_to_the_top_and_saves_it(recent_path: Path) -> None:
    assert recent.load() == {"pages": []}
    for n in range(3):
        recent.note_opened(PullRef("o", "r", n), f"Title & {n}", "open", "ann")
    recent.note_opened(PullRef("o", "r", 1), None, "", "")
    pages = recent.load()["pages"]
    assert [p["number"] for p in pages] == [1, 2, 0]
    assert pages[0]["title"] == "o/r#1"
    assert pages[0]["author"] == "ann"
    assert pages[1]["title"] == "Title & 2"
    saved = json.loads(recent_path.read_text())
    assert saved["pages"][1] == {
        "owner": "o",
        "repo": "r",
        "number": 2,
        "title": "Title & 2",
        "status": "open",
        "author": "ann",
        "opened": pages[1]["opened"],
    }


def test_the_list_keeps_its_newest_thirty() -> None:
    for n in range(32):
        recent.note_opened(PullRef("o", "r", n), None, "open", "me")
    assert [p["number"] for p in recent.load()["pages"]] == list(range(31, 1, -1))


def test_refresh_saves_the_statuses_and_authors_github_reports(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert {p["number"]: (p["status"], p["author"]) for p in data["pages"]} == {
        3: ("open", ""),
        2: ("open", ""),
        1: ("draft", "me"),
        0: ("merged", "ann"),
    }


def test_refresh_keeps_what_was_saved_when_gh_fails(recent_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_a_list_saved_with_the_old_sections_loads_its_newest_thirty(recent_path: Path) -> None:
    pages = [{"owner": "o", "repo": "r", "number": n, "title": "T", "opened": 0} for n in range(60)]
    recent_path.write_text(json.dumps({"viewer": "me", "pages": pages}))
    assert [p["number"] for p in recent.load()["pages"]] == list(range(30))


def test_a_list_saved_before_statuses_and_authors_loads_without_them(recent_path: Path) -> None:
    recent_path.write_text('[{"owner": "o", "repo": "r", "number": 1, "title": "T", "opened": 0}]')
    assert recent.load() == {
        "pages": [{"owner": "o", "repo": "r", "number": 1, "title": "T", "opened": 0, "status": "", "author": ""}],
    }
    recent_path.write_text(
        '{"viewer": "me", "pages": [{"owner": "o", "repo": "r", "number": 2, "title": "T", "opened": 0}]}'
    )
    assert [p["number"] for p in recent.load()["pages"]] == [2]


def test_opening_a_page_again_keeps_the_status_the_list_saved_and_fills_in_the_author() -> None:
    recent.note_opened(PullRef("o", "r", 1), "T", "open", "")
    data = recent.load()
    data["pages"][0]["status"] = "merged"
    recent.save(data)
    recent.note_opened(PullRef("o", "r", 1), "T", "open", "ann")
    assert [(p["number"], p["status"], p["author"]) for p in recent.load()["pages"]] == [(1, "merged", "ann")]
