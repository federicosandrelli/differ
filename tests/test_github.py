import json
import subprocess
from datetime import date
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
    [
        ("OPEN", False, "open"),
        ("OPEN", True, "draft"),
        ("MERGED", True, "merged"),
        ("CLOSED", False, "closed"),
        ("ARCHIVED", False, None),
    ],
)
def test_status_of_a_pull_request(state: str, draft: bool, status: str | None) -> None:
    assert github.status_of(state, draft) == status


def test_pr_standings_leaves_out_a_state_without_a_status(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = (
        b'{"data": {"viewer": {"login": "me"}, "p0": {"pullRequest": {"state": "ARCHIVED", "isDraft": false}}, '
        b'"p1": {"pullRequest": {"state": "MERGED", "isDraft": false, "author": {"login": "ann"}}}}}'
    )
    monkeypatch.setattr(
        commands.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, answer, b"")
    )
    assert github.pr_standings([PullRef("o", "r", 1), PullRef("o", "r", 2)]) == (
        "me",
        {PullRef("o", "r", 2): github.Standing("merged", "ann")},
    )


def test_pr_standings_asks_for_the_viewer_alone_with_no_pull_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = {}

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        sent.update(json.loads(kwargs["input"]))
        return subprocess.CompletedProcess(args, 0, b'{"data": {"viewer": {"login": "me"}}}', b"")

    monkeypatch.setattr(commands.subprocess, "run", fake_run)
    assert github.pr_standings([]) == ("me", {})
    assert sent["query"] == "query { viewer { login } }"


def test_pr_standings_returns_nothing_when_gh_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        commands.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, b"", b"gh: not logged in"),
    )
    assert github.pr_standings([PullRef("o", "r", 1)]) == ("", {})


def test_repo_file_moves_parts_from_the_path_to_the_ref_until_github_finds_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoints = []

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        endpoints.append(args[-1])
        found = args[-1].endswith("?ref=feature/shots")
        return subprocess.CompletedProcess(args, 0 if found else 1, b"PNG" if found else b"", b"HTTP 404: Not Found")

    monkeypatch.setattr(commands.subprocess, "run", run)
    assert github.repo_file("https://github.com/o/r/blob/feature/shots/a%20b/c.png?raw=true") == b"PNG"
    assert endpoints == [
        "repos/o/r/contents/shots/a%20b/c.png?ref=feature",
        "repos/o/r/contents/a%20b/c.png?ref=feature/shots",
    ]


def test_repo_file_names_the_link_when_github_has_no_such_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        commands.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, b"", b"gh: Not Found (HTTP 404)"),
    )
    with pytest.raises(commands.CommandError, match=r"GitHub has no file at https://raw\..*: gh: Not Found"):
        github.repo_file("https://raw.githubusercontent.com/o/r/main/a.png")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/o/r/blob/main/a.png",
        "https://github.com/o/r/issues/1.png",
        "https://github.com/o/r/blob/main",
        "https://github.com/o/r/blob/main/../../../user.png",
        "https://github.com/o/r/raw/main//a.png",
    ],
)
def test_repo_file_refuses_a_link_to_anything_but_a_repository_file(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands.subprocess, "run", lambda args, **kwargs: pytest.fail("ran gh"))
    with pytest.raises(ValueError, match="is not a link to a file of a GitHub repository"):
        github.repo_file(url)


def test_repos_of_recent_prs_names_the_repository_of_each_pull_request_newest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = {}
    answer = {
        "data": {
            "search": {
                "nodes": [
                    {"repository": {"nameWithOwner": "Acme/Widgets"}},
                    {"repository": {"nameWithOwner": "acme/tools"}},
                    {"repository": {"nameWithOwner": "acme/widgets"}},
                    {},
                ]
            }
        }
    }

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        sent.update(json.loads(kwargs["input"]))
        return subprocess.CompletedProcess(args, 0, json.dumps(answer).encode(), b"")

    monkeypatch.setattr(commands.subprocess, "run", fake_run)
    assert github.repos_of_recent_prs(date(2026, 10, 20)) == ["Acme/Widgets", "acme/tools", "acme/widgets"]
    assert sent["variables"] == {"q": "is:pr author:@me created:>=2026-10-20 sort:created-desc"}


@pytest.mark.parametrize(
    ("returncode", "answer"),
    [(1, b""), (0, b'{"data": {"viewer": {"login": "me"}}}'), (0, b"not json")],
)
def test_repos_of_recent_prs_is_none_when_github_gives_no_search(
    returncode: int, answer: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        commands.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, returncode, answer, b"")
    )
    assert github.repos_of_recent_prs(date(2026, 10, 20)) is None
