import os
from pathlib import Path

import pytest

import build
import records
from github import PullRef

REF = PullRef("acme", "widgets", 1)
OTHER = PullRef("acme", "widgets", 2)


@pytest.fixture(autouse=True)
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "CACHE", tmp_path)


def test_a_record_reads_back_what_was_written_until_it_is_deleted() -> None:
    records.COMMENTS.write(REF, [{"id": "a"}])
    records.COMMENTS.write(REF, [{"id": "b"}])
    assert records.COMMENTS.read(REF, list) == [{"id": "b"}]
    assert records.DESCRIPTIONS.read(REF, dict) == {} and records.COMMENTS.read(OTHER, list) == []
    records.COMMENTS.delete(REF)
    records.COMMENTS.delete(REF)
    assert records.COMMENTS.read(REF, list) == []


def test_a_record_lives_at_its_folder_owner_repo_and_number_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    records.SESSIONS.write(REF, {"id": "x"})
    assert [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()] == [
        "sessions/acme/widgets/1.json"
    ]


@pytest.mark.parametrize("text", ["not json", '{"a": 1}'])
def test_a_broken_record_or_one_of_another_shape_reads_empty(text: str) -> None:
    records.COMMENTS.path(REF).parent.mkdir(parents=True)
    records.COMMENTS.path(REF).write_text(text)
    assert records.COMMENTS.read(REF, list) == []


def test_kept_lists_each_pull_request_with_a_record_once_and_skips_files_that_are_no_record() -> None:
    later = PullRef("acme", "widgets", 3)
    records.SESSIONS.write(later, {})
    records.DESCRIPTIONS.write(OTHER, {})
    records.COMMENTS.write(OTHER, [])
    records.COMMENTS.write(REF, [])
    records.SESSIONS.write(REF, {})
    (records.COMMENTS.path(REF).parent / "notes.json").write_text("{}")
    assert records.COMMENTS.refs() == [REF, OTHER]
    assert records.SESSIONS.refs() == [REF, later]
    assert records.kept() == [REF, OTHER, later]


def test_a_write_that_fails_raises_and_leaves_the_old_file_and_no_temporary_file() -> None:
    records.COMMENTS.write(REF, [{"id": "a"}])

    def full(self: Path, text: str, **_: object) -> None:
        Path.open(self, "w").close()
        raise OSError(28, "No space left on device")

    with pytest.MonkeyPatch.context() as patch, pytest.raises(OSError):
        patch.setattr(Path, "write_text", full)
        records.COMMENTS.write(REF, [{"id": "b"}])
    assert records.COMMENTS.read(REF, list) == [{"id": "a"}]
    assert [p.name for p in records.COMMENTS.path(REF).parent.iterdir()] == ["1.json"]


def refusing_replace(monkeypatch: pytest.MonkeyPatch, refusals: int) -> list[int]:
    """Make os.replace refuse the first `refusals` moves, as Windows does while a reader has the file open; the
    calls it saw."""
    real, calls = os.replace, []

    def replace(src: Path, dst: Path) -> None:
        calls.append(1)
        if len(calls) <= refusals:
            raise PermissionError("in use")
        real(src, dst)

    monkeypatch.setattr(records.os, "replace", replace)
    monkeypatch.setattr(records, "REPLACE_WAIT", 0)
    return calls


def test_a_write_on_windows_waits_for_a_reader_to_let_go_of_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(records.sys, "platform", "win32")
    calls = refusing_replace(monkeypatch, records.REPLACE_TRIES - 1)
    records.SESSIONS.write(REF, {"id": "x"})
    assert records.SESSIONS.read(REF, dict) == {"id": "x"} and len(calls) == records.REPLACE_TRIES


def test_a_write_on_windows_that_is_refused_every_time_fails_and_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(records.sys, "platform", "win32")
    refusing_replace(monkeypatch, records.REPLACE_TRIES)
    with pytest.raises(PermissionError):
        records.SESSIONS.write(REF, {"id": "x"})
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]


def test_a_refused_write_elsewhere_fails_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(records.sys, "platform", "linux")
    calls = refusing_replace(monkeypatch, 1)
    with pytest.raises(PermissionError):
        records.SESSIONS.write(REF, {"id": "x"})
    assert len(calls) == 1
