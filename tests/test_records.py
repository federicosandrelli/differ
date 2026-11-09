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
