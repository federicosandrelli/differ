import json
import os
from pathlib import Path

import pytest

import claude_desktop
from github import PullRef

WIDGETS = "https://github.com/acme/widgets/pull/"


@pytest.fixture(autouse=True)
def sessions_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(claude_desktop, "SESSIONS", tmp_path)
    monkeypatch.setattr(claude_desktop, "read_files", {})
    return tmp_path


def write(folder: Path, session_id: str, *urls: str, active: int = 0, title: str = "") -> Path:
    path = folder / "account" / "org" / f"{session_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    prs = [{"url": url, "prNumber": 1} for url in urls]
    path.write_text(json.dumps({"sessionId": session_id, "title": title, "lastActivityAt": active, "prs": prs}))
    return path


def test_each_pull_request_gets_the_session_active_last_with_a_link_that_continues_it(sessions_dir: Path) -> None:
    write(sessions_dir, "local_old", WIDGETS + "1", WIDGETS + "2", active=100, title="First try")
    write(sessions_dir, "local_new", WIDGETS + "1", active=200, title="Second try")
    found = claude_desktop.sessions()
    assert found[PullRef("acme", "widgets", 1)] == claude_desktop.Session(
        "claude://code/continue?session=local_new", "Second try", 200
    )
    assert found[PullRef("acme", "widgets", 2)].link == "claude://code/continue?session=local_old"


def test_sessions_leave_out_other_hosts_bad_ids_and_broken_files(sessions_dir: Path) -> None:
    write(sessions_dir, "local_gitlab", "https://gitlab.com/acme/widgets/pull/3")
    write(sessions_dir, "local_bad id", WIDGETS + "4")
    broken = write(sessions_dir, "local_broken", WIDGETS + "5")
    broken.write_text('{"prs": [')
    assert claude_desktop.sessions() == {}


def test_a_session_file_is_read_again_when_it_changes_and_dropped_when_it_goes(sessions_dir: Path) -> None:
    path = write(sessions_dir, "local_a", WIDGETS + "1")
    assert list(claude_desktop.sessions()) == [PullRef("acme", "widgets", 1)]
    write(sessions_dir, "local_a", WIDGETS + "2", WIDGETS + "3")
    os.utime(path, ns=(1, 1))
    assert set(claude_desktop.sessions()) == {PullRef("acme", "widgets", 2), PullRef("acme", "widgets", 3)}
    path.unlink()
    assert claude_desktop.sessions() == {} and claude_desktop.read_files == {}
