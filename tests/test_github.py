import email.message
import io
import json
import subprocess
from datetime import date
from pathlib import Path
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
        b'{"data": {"p0": {"pullRequest": {"state": "ARCHIVED", "isDraft": false}}, '
        b'"p1": {"pullRequest": {"state": "MERGED", "isDraft": false, "author": {"login": "ann"}}}}}'
    )
    monkeypatch.setattr(
        commands.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, answer, b"")
    )
    assert github.pr_standings([PullRef("o", "r", 1), PullRef("o", "r", 2)]) == {
        PullRef("o", "r", 2): github.Standing("merged", "ann")
    }


def test_pr_standings_asks_github_nothing_without_pull_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands.subprocess, "run", lambda args, **kwargs: pytest.fail("asked GitHub"))
    assert github.pr_standings([]) == {}


def test_pr_standings_returns_nothing_when_gh_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        commands.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, b"", b"gh: not logged in"),
    )
    assert github.pr_standings([PullRef("o", "r", 1)]) == {}


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


ASSET = "https://github.com/user-attachments/assets/8aa87a1e-60d9-43fa-bcf7-4de5637fb27c"
SIGNED = "https://github-production-user-asset-6210df.s3.amazonaws.com/1/a.png?X-Amz-Signature=abc"


def fake_github(
    monkeypatch: pytest.MonkeyPatch, ctype: str = "image/png", status: int = 302, location: str = SIGNED
) -> list[tuple[str, str, dict[str, str]]]:
    """Stand in for gh, github.com and the address GitHub redirects to; the requests sent to each, as host, path or
    address, and headers."""
    sent: list[tuple[str, str, dict[str, str]]] = []

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        assert args == ["gh", "auth", "token", "--hostname", "github.com"]
        return subprocess.CompletedProcess(args, 0, b"gho_secret\n", b"")

    class Connection:
        def __init__(self, host: str, timeout: float) -> None:
            self.host = host

        def request(self, method: str, path: str, headers: dict[str, str]) -> None:
            sent.append((self.host, path, headers))

        def getresponse(self) -> Any:
            return type("Answer", (), {"status": status, "getheader": lambda self, name: location})()

        def close(self) -> None:
            pass

    def urlopen(url: str, timeout: float) -> Any:
        sent.append(("", url, {}))
        headers = email.message.Message()
        headers["Content-Type"] = ctype
        answer = io.BytesIO(b"PNG")
        answer.headers = headers  # type: ignore[attr-defined]
        return answer

    monkeypatch.setattr(commands.subprocess, "run", run)
    monkeypatch.setattr(github.http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(github.urllib.request, "urlopen", urlopen)
    return sent


def test_user_attachment_sends_the_token_to_github_only_and_follows_the_redirect_without_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = fake_github(monkeypatch, "image/png; charset=binary")
    assert github.user_attachment(ASSET) == (b"PNG", "image/png")
    assert sent == [
        (
            "github.com",
            "/user-attachments/assets/8aa87a1e-60d9-43fa-bcf7-4de5637fb27c",
            {"Authorization": "Bearer gho_secret", "User-Agent": "differ"},
        ),
        ("", SIGNED, {}),
    ]


def test_user_attachment_refuses_a_file_that_is_not_an_image(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_github(monkeypatch, "text/html")
    with pytest.raises(ValueError, match="is not an image but text/html"):
        github.user_attachment(ASSET)


@pytest.mark.parametrize(("status", "location"), [(404, ""), (302, ""), (302, SIGNED.replace("https:", "http:"))])
def test_user_attachment_fails_when_github_sends_no_secure_redirect(
    status: int, location: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = fake_github(monkeypatch, status=status, location=location)
    with pytest.raises(commands.CommandError, match=f"GitHub has no file at {ASSET}: HTTP {status}"):
        github.user_attachment(ASSET)
    assert len(sent) == 1


def test_user_attachment_fails_when_the_redirect_does_not_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_github(monkeypatch)

    def urlopen(url: str, timeout: float) -> Any:
        raise OSError("timed out")

    monkeypatch.setattr(github.urllib.request, "urlopen", urlopen)
    with pytest.raises(commands.CommandError, match=f"GitHub did not send {ASSET}: timed out"):
        github.user_attachment(ASSET)


@pytest.mark.parametrize(
    "url",
    [
        ASSET.replace("github.com", "example.com"),
        ASSET.replace("github.com", "github.com.example.com"),
        ASSET.replace("https://", "http://"),
        ASSET.replace("/assets/", "/files/"),
        "https://github.com/user-attachments/assets/shot.png",
        "https://github.com/user-attachments/assets/",
        "https://github.com/user-attachments/assets/../../o/r/raw/main/a.png",
        ASSET + "/../../../o/r",
        ASSET + "?x=1",
        ASSET + "\n",
        "https://github.com/o/r/blob/main/a.png",
    ],
)
def test_user_attachment_refuses_a_link_to_anything_but_a_pasted_file(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commands.subprocess, "run", lambda args, **kwargs: pytest.fail("ran gh"))
    with pytest.raises(ValueError, match="is not a link to a file pasted on GitHub"):
        github.user_attachment(url)


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


def test_search_prs_runs_each_search_on_its_own_and_keeps_their_order(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        q = json.loads(kwargs["input"])["variables"]["q"]
        nodes = [{"number": int(q[-1])}, {}, None]
        answer = {"data": {"viewer": {"login": "me"}, "search": {"nodes": nodes}}}
        return subprocess.CompletedProcess(args, 0, json.dumps(answer).encode(), b"")

    monkeypatch.setattr(commands.subprocess, "run", fake_run)
    assert github.search_prs(["a 1", "b 2", "c 3"]) == ("me", [[{"number": 1}], [{"number": 2}], [{"number": 3}]])


def test_search_prs_is_none_when_one_search_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        if json.loads(kwargs["input"])["variables"]["q"] == "bad":
            return subprocess.CompletedProcess(args, 1, b"", b"gh: HTTP 502")
        answer = {"data": {"viewer": {"login": "me"}, "search": {"nodes": []}}}
        return subprocess.CompletedProcess(args, 0, json.dumps(answer).encode(), b"")

    monkeypatch.setattr(commands.subprocess, "run", fake_run)
    assert github.search_prs(["good", "bad"]) is None
    assert github.search_prs(["good"]) == ("me", [[]])


def fake_upload(
    monkeypatch: pytest.MonkeyPatch, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""
) -> list[tuple[list[str], bytes]]:
    """Stand in for gh: the repository's id is 42, and an upload answers as given. The uploads gh received, as
    arguments and the bytes of the file each sent."""
    uploads: list[tuple[list[str], bytes]] = []

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        if args[:3] == ["gh", "api", "repos/o/r"]:
            return subprocess.CompletedProcess(args, 0, b"42\n", b"")
        uploads.append((args, Path(args[args.index("--input") + 1]).read_bytes()))
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    monkeypatch.setattr(commands.subprocess, "run", run)
    github.repository_id.cache_clear()
    return uploads


def test_attach_uploads_the_bytes_from_a_file_and_returns_the_link(monkeypatch: pytest.MonkeyPatch) -> None:
    uploads = fake_upload(monkeypatch, stdout=json.dumps({"url": ASSET}).encode())
    assert github.attach(PullRef("o", "r", 1), "shot 1.png", "image/png", b"PNG") == ASSET
    [(args, data)] = uploads
    assert data == b"PNG"
    assert args[-1] == f"{github.ATTACHMENTS}?name=shot+1.png&content_type=image%2Fpng&repository_id=42"


def test_attach_names_write_access_when_github_answers_404(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_upload(monkeypatch, 1, stderr=b"gh: Not Found (HTTP 404)")
    with pytest.raises(commands.CommandError, match="GitHub takes images only from people who can write to o/r"):
        github.attach(PullRef("o", "r", 1), "a.png", "image/png", b"PNG")


@pytest.mark.parametrize(
    "stdout", [b"", b"[]", b'{"url": "https://example.com/a.png"}', b'{"url": "' + ASSET.encode() + b')x"}']
)
def test_attach_fails_when_github_answers_without_a_link_to_a_pasted_file(
    stdout: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_upload(monkeypatch, stdout=stdout)
    with pytest.raises(commands.CommandError, match="answered without its link"):
        github.attach(PullRef("o", "r", 1), "a.png", "image/png", b"PNG")


def test_repository_id_fails_when_gh_prints_no_number(monkeypatch: pytest.MonkeyPatch) -> None:
    github.repository_id.cache_clear()
    monkeypatch.setattr(
        commands.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, b"null\n", b"")
    )
    with pytest.raises(commands.CommandError, match="GitHub gave o/r no repository id: 'null'"):
        github.repository_id("o/r")
