import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import followed


@pytest.fixture(autouse=True)
def followed_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "cache" / "followed.json"
    monkeypatch.setattr(followed, "PATH", path)
    monkeypatch.setattr(followed.github, "repos_of_recent_prs", lambda since: None)
    return path


@pytest.mark.parametrize(
    "text, name",
    [
        ("acme/widgets", "acme/widgets"),
        ("  Acme/Widgets.js  ", "acme/widgets.js"),
        ("https://github.com/acme/widgets", "acme/widgets"),
        ("https://github.com/acme/widgets/", "acme/widgets"),
        ("http://github.com/acme/my-repo_2", "acme/my-repo_2"),
        ("https://github.com/acme/widgets.git", "acme/widgets"),
        ("acme/widgets.git", "acme/widgets"),
        ("acme/Widgets.GIT", "acme/widgets"),
        ("https://github.com/acme/widgets.git/", "acme/widgets"),
    ],
)
def test_parse_names_a_repository(text: str, name: str) -> None:
    assert followed.parse(text) == name


@pytest.mark.parametrize(
    "text",
    [
        "",
        "acme",
        "acme/",
        "/widgets",
        "acme/widgets#1",
        "https://github.com/acme/widgets/pull/1",
        "acme/widgets extra",
        "-acme/widgets",
        "acme/.git",
        "acme/widgets//",
    ],
)
def test_parse_refuses_other_text(text: str) -> None:
    with pytest.raises(followed.NotARepository, match="Not a repository"):
        followed.parse(text)


def test_load_before_the_first_save_saves_the_repositories_of_the_last_weeks_pull_requests(
    followed_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = []

    def recent(since: date) -> list[str]:
        asked.append(since)
        return ["acme/widgets", "acme/tools"]

    monkeypatch.setattr(followed.github, "repos_of_recent_prs", recent)
    assert followed.load() == ["acme/widgets", "acme/tools"]
    assert asked == [date.today() - timedelta(days=7)]
    assert json.loads(followed_path.read_text()) == ["acme/widgets", "acme/tools"]
    # Once saved, the list is the reader's, even when they empty it: GitHub is not asked again.
    followed.save([])
    assert followed.load() == []
    assert len(asked) == 1


def test_the_first_list_keeps_each_name_once_and_leaves_out_a_name_that_is_no_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        followed.github, "repos_of_recent_prs", lambda since: ["Acme/Widgets", "jdoe_acme/tools", "acme/widgets"]
    )
    assert followed.load() == ["acme/widgets"]
    assert followed.read() == ["acme/widgets"]


def test_a_list_saved_while_github_answers_wins_over_the_first_list(monkeypatch: pytest.MonkeyPatch) -> None:
    def recent(since: date) -> list[str]:
        followed.save(["acme/mine"])
        return ["acme/from-github"]

    monkeypatch.setattr(followed.github, "repos_of_recent_prs", recent)
    assert followed.load() == ["acme/mine"]
    assert followed.read() == ["acme/mine"]


def test_load_before_the_first_save_saves_nothing_when_github_cannot_answer(followed_path: Path) -> None:
    assert followed.load() == []
    assert not followed_path.exists()


def test_load_is_empty_after_a_broken_file(followed_path: Path) -> None:
    followed_path.parent.mkdir(parents=True)
    followed_path.write_text("{not json")
    assert followed.load() == []
    followed_path.write_text('{"repos": ["acme/widgets"]}')
    assert followed.load() == []


def test_save_keeps_each_name_once_in_order_and_load_reads_it_back(followed_path: Path) -> None:
    saved = followed.save(["Acme/Widgets", "https://github.com/acme/tools", "acme/widgets", "acme/tools/"])
    assert saved == ["acme/widgets", "acme/tools"]
    assert followed.load() == saved
    assert json.loads(followed_path.read_text()) == saved
    assert not followed_path.with_name("followed.json.tmp").exists()


def test_save_refuses_a_text_that_names_no_repository_and_keeps_the_old_list() -> None:
    followed.save(["acme/widgets"])
    with pytest.raises(followed.NotARepository, match="Not a repository: 'acme'"):
        followed.save(["acme/tools", "acme"])
    assert followed.load() == ["acme/widgets"]


def test_save_an_empty_list_empties_the_file() -> None:
    followed.save(["acme/widgets"])
    assert followed.save([]) == []
    assert followed.load() == []
