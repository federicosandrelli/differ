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


def test_ago() -> None:
    now = time.time()
    assert recent.ago(now) == "just now"
    assert recent.ago(now - 90) == "1m ago"
    assert recent.ago(now - 3700) == "1h ago"
    assert recent.ago(now - 2 * 86400) == "2d ago"


def test_note_opened_keeps_the_newest_ten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recent, "PATH", tmp_path / "recent.json")
    assert recent.load() == []
    for n in range(12):
        recent.note_opened(PullRef("o", "r", n), f"<title>#{n} Title &amp; {n}</title>".encode())
    recent.note_opened(PullRef("o", "r", 3), b"<html>no title</html>")
    pages = recent.load()
    assert [p["number"] for p in pages] == [3, 11, 10, 9, 8, 7, 6, 5, 4, 2]
    assert pages[0]["title"] == "o/r#3"
    assert pages[1]["title"] == "Title & 11"
    saved = json.loads((tmp_path / "recent.json").read_text())
    assert saved[1] == {
        "owner": "o",
        "repo": "r",
        "number": 11,
        "title": "Title & 11",
        "status": "",
        "opened": pages[1]["opened"],
    }


def test_recent_items_escapes_titles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recent, "PATH", tmp_path / "recent.json")
    assert "No pull requests opened yet" in recent.items_html()
    recent.note_opened(PullRef("o", "r", 1), b"<title>#1 a &lt;b&gt;</title>")
    items = recent.items_html()
    assert 'href="/o/r/pull/1"' in items
    assert re.search(r'<span class="t">a &lt;b&gt;</span>', items)


def test_refresh_statuses_saves_the_statuses_github_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recent, "PATH", tmp_path / "recent.json")
    for n in range(4):
        recent.note_opened(PullRef("o", "r", n), b'<meta name="pr-status" content="open">')
    answer = {
        "data": {
            "p0": {"pullRequest": None},
            "p1": None,
            "p2": {"pullRequest": {"state": "OPEN", "isDraft": True}},
            "p3": {"pullRequest": {"state": "MERGED", "isDraft": False}},
        },
        "errors": [{"message": "Could not resolve to a Repository"}],
    }
    sent = {}

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        sent.update(json.loads(kwargs["input"])["variables"])
        return subprocess.CompletedProcess(args, 1, json.dumps(answer).encode(), b"")

    monkeypatch.setattr(commands.subprocess, "run", fake_run)
    recent.refresh_statuses()
    assert sent["n0"] == 3 and sent["o0"] == "o" and sent["r0"] == "r"
    assert {p["number"]: p["status"] for p in recent.load()} == {3: "open", 2: "open", 1: "draft", 0: "merged"}


def test_refresh_statuses_keeps_the_saved_statuses_when_gh_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(recent, "PATH", tmp_path / "recent.json")
    recent.note_opened(PullRef("o", "r", 1), b'<meta name="pr-status" content="merged">')
    before = (tmp_path / "recent.json").read_text()
    monkeypatch.setattr(
        commands.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, b"", b"gh: not logged in"),
    )
    recent.refresh_statuses()
    assert (tmp_path / "recent.json").read_text() == before


def test_refresh_statuses_skips_github_while_another_refresh_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(recent, "PATH", tmp_path / "recent.json")
    recent.note_opened(PullRef("o", "r", 1), b'<meta name="pr-status" content="open">')

    def fail(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise AssertionError("gh ran during another refresh")

    monkeypatch.setattr(commands.subprocess, "run", fail)
    with recent.refreshing:
        recent.refresh_statuses()
    assert recent.load()[0]["status"] == "open"


def test_note_opened_keeps_the_old_list_when_a_write_dies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recent, "PATH", tmp_path / "recent.json")
    recent.note_opened(PullRef("o", "r", 1), b"<title>#1 First</title>")
    before = (tmp_path / "recent.json").read_text()

    def die(self: Path, data: str, **kwargs: Any) -> int:
        self.write_bytes(data[: len(data) // 2].encode())
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", die)
    with pytest.raises(OSError):
        recent.note_opened(PullRef("o", "r", 2), b"<title>#2 Second</title>")
    assert (tmp_path / "recent.json").read_text() == before
    assert [p["number"] for p in recent.load()] == [1]


def test_an_entry_saved_without_a_status_loads_with_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recent, "PATH", tmp_path / "recent.json")
    (tmp_path / "recent.json").write_text('[{"owner": "o", "repo": "r", "number": 1, "title": "T", "opened": 0}]')
    assert recent.load()[0]["status"] == ""
    assert 'href="/o/r/pull/1"' in recent.items_html()
