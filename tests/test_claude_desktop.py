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


def write(
    folder: Path, session_id: str, *urls: str, active: int = 0, title: str = "", archived: bool = False, cwd: str = ""
) -> Path:
    path = folder / "account" / "org" / f"{session_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    prs = [{"url": url, "prNumber": 1} for url in urls]
    meta = {
        "sessionId": session_id,
        "title": title,
        "lastActivityAt": active,
        "isArchived": archived,
        "cwd": cwd or str(folder),
        "prs": prs,
    }
    path.write_text(json.dumps(meta))
    return path


def test_each_pull_request_gets_the_session_active_last_with_a_link_that_continues_it(sessions_dir: Path) -> None:
    write(sessions_dir, "local_old", WIDGETS + "1", WIDGETS + "2", active=100, title="First try")
    write(sessions_dir, "local_new", WIDGETS + "1", active=200, title="Second try")
    found = claude_desktop.sessions()
    assert found[PullRef("acme", "widgets", 1)] == claude_desktop.Session(
        "claude://code/continue?session=local_new", "Second try", 200, folder=str(sessions_dir)
    )
    assert found[PullRef("acme", "widgets", 2)].link == "claude://code/continue?session=local_old"


def test_a_session_the_reader_archived_counts_only_when_every_session_of_the_pull_request_is(
    sessions_dir: Path,
) -> None:
    write(sessions_dir, "local_open", WIDGETS + "1", WIDGETS + "2", active=100)
    write(sessions_dir, "local_archived", WIDGETS + "1", active=200, archived=True)
    write(sessions_dir, "local_only", WIDGETS + "3", active=50, archived=True)
    found = claude_desktop.sessions()
    assert found[PullRef("acme", "widgets", 1)].link.endswith("local_open")
    assert found[PullRef("acme", "widgets", 3)].link.endswith("local_only")


def test_sessions_leave_out_other_hosts_bad_ids_and_broken_files(sessions_dir: Path) -> None:
    write(sessions_dir, "local_gitlab", "https://gitlab.com/acme/widgets/pull/3")
    write(sessions_dir, "local_bad id", WIDGETS + "4")
    broken = write(sessions_dir, "local_broken", WIDGETS + "5")
    broken.write_text('{"prs": [')
    (sessions_dir / "account" / "org" / "local_odd.json").write_text('{"sessionId": "local_odd", "prs": 3}')
    assert claude_desktop.sessions() == {}


def test_a_session_file_is_read_again_when_it_changes_and_dropped_when_it_goes(sessions_dir: Path) -> None:
    path = write(sessions_dir, "local_a", WIDGETS + "1")
    assert list(claude_desktop.sessions()) == [PullRef("acme", "widgets", 1)]
    write(sessions_dir, "local_a", WIDGETS + "2", WIDGETS + "3")
    os.utime(path, ns=(1, 1))
    assert set(claude_desktop.sessions()) == {PullRef("acme", "widgets", 2), PullRef("acme", "widgets", 3)}
    path.unlink()
    assert claude_desktop.sessions() == {} and claude_desktop.read_files == {}


def test_sessions_leave_out_a_session_without_its_folder(sessions_dir: Path) -> None:
    worktree = sessions_dir / "worktree"
    worktree.mkdir()
    write(sessions_dir, "local_old", WIDGETS + "1", active=100)
    write(sessions_dir, "local_new", WIDGETS + "1", WIDGETS + "2", active=200, cwd=str(worktree))
    write(sessions_dir, "local_gone", WIDGETS + "3", cwd=str(sessions_dir / "removed"))
    assert set(claude_desktop.sessions()) == {PullRef("acme", "widgets", 1), PullRef("acme", "widgets", 2)}
    worktree.rmdir()
    found = claude_desktop.sessions()
    assert set(found) == {PullRef("acme", "widgets", 1)}
    assert found[PullRef("acme", "widgets", 1)].link.endswith("local_old")
