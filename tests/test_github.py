import subprocess
from typing import Any

import pytest

import commands
import github
from github import PullRef


@pytest.mark.parametrize(
    "text",
    [
        "https://github.com/Owner/Repo/pull/12",
        "https://github.com/owner/repo/pull/12/files",
        "owner/repo#12",
        "  Owner/Repo#12\n",
        "see github.com/owner/repo/pull/12 please",
    ],
)
def test_parse_accepts_urls_and_short_refs_lowercased(text: str) -> None:
    assert PullRef.parse(text) == PullRef("owner", "repo", 12)


@pytest.mark.parametrize("text", ["", "owner/repo", "https://github.com/owner/repo", "12"])
def test_parse_rejects_other_text(text: str) -> None:
    with pytest.raises(ValueError, match="Not a pull request reference"):
        PullRef.parse(text)


@pytest.mark.parametrize(
    "text",
    [
        "/../../pull/1",
        "/./x/pull/1",
        "/-x/y/pull/1",
        "a/..#1",
        "a/.#1",
        "a/-b#1",
        "a.b/c#1",
        "https://github.com/-x/y/pull/1",
    ],
)
def test_parse_rejects_names_that_are_not_github_names(text: str) -> None:
    with pytest.raises(ValueError, match="Not a pull request reference"):
        PullRef.parse(text)


@pytest.mark.parametrize(
    ("text", "ref"),
    [
        ("octo-org/my.repo#7", ("octo-org", "my.repo", 7)),
        ("a/.b#1", ("a", ".b", 1)),
        ("a/..b#1", ("a", "..b", 1)),
        ("a/b.#1", ("a", "b.", 1)),
        ("a/b-#1", ("a", "b-", 1)),
    ],
)
def test_parse_accepts_github_names(text: str, ref: tuple[str, str, int]) -> None:
    assert PullRef.parse(text) == PullRef(*ref)


def test_a_ref_names_its_page_and_prints_as_github_does() -> None:
    ref = PullRef.parse("https://github.com/Octo-Org/My.Repo/pull/7")
    assert (ref.slug, ref.path, str(ref)) == ("octo-org/my.repo", "/octo-org/my.repo/pull/7", "octo-org/my.repo#7")


def test_a_command_that_runs_out_of_time_is_named_with_its_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(commands.subprocess, "run", hang)
    with pytest.raises(commands.CommandError, match=r"`gh pr view 7` did not finish within 5 seconds"):
        github.gh("pr", "view", "7", "--repo", "o/r", timeout=5)


def test_a_missing_gh_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise FileNotFoundError(2, "No such file or directory", args[0])

    monkeypatch.setattr(commands.subprocess, "run", missing)
    with pytest.raises(commands.CommandError, match=r"The GitHub CLI \(gh\) is not installed or not on PATH"):
        github.gh("pr", "view", "7")
    with pytest.raises(commands.CommandError, match=r"The GitHub CLI \(gh\) is not installed or not on PATH"):
        github.graphql("query { viewer { login } }")
    assert github.auth_problem() == "The GitHub CLI (gh) is not installed or not on PATH."


def test_graphql_names_the_errors_github_answers_with(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = b'{"data": null, "errors": [{"message": "one"}, {"message": "two"}]}'
    monkeypatch.setattr(
        commands.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 1, answer, b"")
    )
    with pytest.raises(commands.CommandError, match="^one; two$"):
        github.graphql("query { viewer { login } }")


@pytest.mark.parametrize(
    ("state", "draft", "status"),
    [("OPEN", False, "open"), ("OPEN", True, "draft"), ("MERGED", True, "merged"), ("CLOSED", False, "closed")],
)
def test_status_of_a_pull_request(state: str, draft: bool, status: str) -> None:
    assert github.status_of(state, draft) == status
