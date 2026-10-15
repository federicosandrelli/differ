import io
import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

import commands
import recent
import server
from github import PullRef


def handler(
    origin: str | None = None, ctype: str | None = None, port: int = 8765, host: str | None = None
) -> server.Handler:
    h = server.Handler.__new__(server.Handler)
    h.server = cast(Any, type("FakeServer", (), {"server_address": ("127.0.0.1", port)})())
    headers = {k: v for k, v in (("Origin", origin), ("Content-Type", ctype), ("Host", host)) if v is not None}
    h.headers = cast(Any, headers)
    return h


def get(path: str, site: str | None = None) -> tuple[int, bytes]:
    """Drive do_GET for a path with the given Sec-Fetch-Site header; the status code and body it writes."""
    h = handler(None, None)
    h.headers = cast(Any, {"Host": "localhost:8765", **({"Sec-Fetch-Site": site} if site else {})})
    h.path = path
    h.command = "GET"
    h.request_version = "HTTP/1.1"
    h.requestline = f"GET {path} HTTP/1.1"
    h.client_address = ("127.0.0.1", 1)
    out = io.BytesIO()
    h.wfile = cast(Any, out)
    h.do_GET()
    head, body = out.getvalue().split(b"\r\n\r\n", 1)
    return int(head.split(b" ", 2)[1]), body


@pytest.mark.parametrize("path", ["/o/r/pull/1", "/open?pr=o/r%231", "/status/o/r/1"])
def test_a_cross_site_get_to_a_page_open_or_status_is_refused(path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "start_job", lambda ref: pytest.fail("a cross-site GET started a build"))
    monkeypatch.setattr(server.build, "latest_page", lambda ref: pytest.fail("a cross-site GET read a page"))
    status, body = get(path, "cross-site")
    assert status == 403
    assert b"Forbidden: the request comes from another site (Sec-Fetch-Site: cross-site)" in body


@pytest.mark.parametrize("site", [None, "same-origin", "none"])
def test_a_get_without_a_cross_site_header_passes(site: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    started = []
    monkeypatch.setattr(server, "start_job", lambda ref: started.append(ref))
    monkeypatch.setattr(server.build, "latest_page", lambda ref: None)
    status, body = get("/o/r/pull/1", site)
    assert status == 200 and started == [PullRef("o", "r", 1)]
    assert b"Building o/r#1" in body
    assert get("/open?pr=o/r%231", site)[0] == 303
    status, body = get("/status/o/r/1", site)
    assert status == 200 and json.loads(body)["state"] == "none"


def test_the_home_page_serves_a_cross_site_get(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recent, "PATH", tmp_path / "recent.json")
    status, body = get("/home", "cross-site")
    assert status == 200
    assert b"No pull requests opened yet" in body


def test_refused_accepts_a_loopback_origin_with_json() -> None:
    assert handler("http://localhost:8765", "application/json").refused() is None
    assert handler("http://127.0.0.1:8765", "application/json; charset=utf-8").refused() is None


@pytest.mark.parametrize(
    ("origin", "ctype", "why"),
    [
        (None, "application/json", "Origin (missing) is not this server"),
        ("https://evil.example", "application/json", "Origin https://evil.example is not this server"),
        ("http://localhost:9999", "application/json", "Origin http://localhost:9999 is not this server"),
        ("http://localhost:8765", None, "Content-Type (missing) is not application/json"),
        ("http://localhost:8765", "text/plain", "Content-Type text/plain is not application/json"),
    ],
)
def test_refused_rejects_other_origins_and_content_types(origin: str | None, ctype: str | None, why: str) -> None:
    assert handler(origin, ctype).refused() == why


@pytest.mark.parametrize("host", ["localhost:8765", "127.0.0.1:8765", "[::1]:8765"])
def test_wrong_host_accepts_this_server_on_a_loopback_host(host: str) -> None:
    assert handler(host=host).wrong_host() is None


@pytest.mark.parametrize(
    ("host", "why"),
    [
        (None, "Host (missing) is not this server"),
        ("localhost", "Host localhost is not this server"),
        ("localhost:9999", "Host localhost:9999 is not this server"),
        ("evil.example:8765", "Host evil.example:8765 is not this server"),
        ("localhost.evil.example:8765", "Host localhost.evil.example:8765 is not this server"),
    ],
)
def test_wrong_host_rejects_other_hosts_and_ports(host: str | None, why: str) -> None:
    assert handler(host=host).wrong_host() == why


def test_wrong_host_uses_the_port_the_server_listens_on() -> None:
    assert handler(host="localhost:8766", port=8766).wrong_host() is None
    assert handler(host="localhost:8765", port=8766).wrong_host() == "Host localhost:8765 is not this server"


@pytest.mark.parametrize("path", ["/../../pull/1", "/./x/pull/1", "/-x/y/pull/1", "/a/../pull/1", "/a/-b/pull/1"])
def test_routes_refuse_names_that_are_not_github_names(path: str) -> None:
    assert server.PAGE.match(path) is None
    assert server.STATUS.match("/status" + path.replace("/pull", "")) is None
    assert server.API.match("/api" + path.replace("/pull", "") + "/comments") is None


def test_routes_accept_github_names() -> None:
    assert server.PAGE.match("/octo-org/my.repo/pull/7") is not None
    assert server.STATUS.match("/status/octo-org/my.repo/7") is not None
    assert server.API.match("/api/octo-org/my.repo/7/comments") is not None


def test_a_route_names_its_pull_request_in_lower_case() -> None:
    m = server.PAGE.match("/Owner/Repo/pull/12")
    assert m is not None
    assert PullRef.from_match(m) == PullRef("owner", "repo", 12)
    assert server.PAGE.match("/owner/repo/pull/12/files") is None


def request(method: str, path: str, body: bytes = b"") -> server.Handler:
    h = handler("http://localhost:8765", "application/json", host="localhost:8765")
    h.command, h.path, h.request_version = method, path, "HTTP/1.1"
    h.requestline = f"{method} {path} HTTP/1.1"
    h.client_address = ("127.0.0.1", 1)
    h.headers["Content-Length"] = str(len(body))
    h.rfile, h.wfile = io.BytesIO(body), io.BytesIO()
    return h


def response(h: server.Handler) -> tuple[int, bytes]:
    raw = cast(io.BytesIO, h.wfile).getvalue()
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split(b" ")[1]), body


def test_get_answers_an_unexpected_error_with_the_error_page(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def explode() -> bytes:
        raise RuntimeError("boom")

    monkeypatch.setattr(server, "tabs_page", explode)
    h = request("GET", "/")
    h.do_GET()
    code, body = response(h)
    assert code == 500
    assert b"Could not open that" in body
    assert b"Unexpected error: RuntimeError: boom" in body
    assert "RuntimeError: boom" in capsys.readouterr().err


def test_api_answers_an_unexpected_error_as_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args: Any, **kwargs: Any) -> dict:
        raise RuntimeError("boom")

    monkeypatch.setattr(server.comments, "fetch", explode)
    h = request("GET", "/api/o/r/1/comments")
    h.do_GET()
    code, body = response(h)
    assert (code, json.loads(body)) == (500, {"error": "Unexpected error: RuntimeError: boom"})

    monkeypatch.setattr(server.comments, "resolve", explode)
    h = request("POST", "/api/o/r/1/resolve", json.dumps({"thread": "T_1"}).encode())
    h.do_POST()
    code, body = response(h)
    assert (code, json.loads(body)) == (500, {"error": "Unexpected error: RuntimeError: boom"})


def gh_run(returncode: int) -> Any:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(args)
        return subprocess.CompletedProcess(args, returncode, b"", b"")

    fake_run.calls = calls  # type: ignore[attr-defined]
    return fake_run


def test_start_page_shows_a_banner_when_gh_is_signed_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recent, "PATH", tmp_path / "recent.json")
    monkeypatch.setattr(server, "gh_checked", (0.0, None))
    monkeypatch.setattr(commands.subprocess, "run", gh_run(1))
    page = server.start_page().decode()
    assert 'class="banner"' in page
    assert "The GitHub CLI (gh) is not signed in." in page
    assert "<code>gh auth login</code>" in page
    assert page.index('class="banner"') < page.index("<form")


def test_start_page_shows_a_banner_when_gh_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recent, "PATH", tmp_path / "recent.json")
    monkeypatch.setattr(server, "gh_checked", (0.0, None))

    def missing(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise FileNotFoundError(args[0])

    monkeypatch.setattr(commands.subprocess, "run", missing)
    page = server.start_page().decode()
    assert "The GitHub CLI (gh) is not installed or not on PATH." in page
    assert "<code>gh auth login</code>" in page


def test_start_page_shows_no_banner_when_gh_is_signed_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recent, "PATH", tmp_path / "recent.json")
    monkeypatch.setattr(server, "gh_checked", (0.0, None))
    monkeypatch.setattr(commands.subprocess, "run", gh_run(0))
    page = server.start_page().decode()
    assert 'class="banner"' not in page
    assert "gh auth login" not in page


def test_gh_problem_checks_gh_at_most_once_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "gh_checked", (0.0, None))
    run = gh_run(1)
    monkeypatch.setattr(commands.subprocess, "run", run)
    now = 1_000_000.0
    monkeypatch.setattr(server.time, "time", lambda: now)
    assert server.gh_problem() == "The GitHub CLI (gh) is not signed in."
    assert server.gh_problem() == "The GitHub CLI (gh) is not signed in."
    assert len(run.calls) == 1
    assert run.calls[0] == ["gh", "auth", "status"]
    now += server.GH_TTL
    monkeypatch.setattr(commands.subprocess, "run", gh_run(0))
    assert server.gh_problem() is None
    assert len(run.calls) == 1


def test_open_log_appends_and_starts_over_past_the_size_limit(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "server.log"
    with server.open_log(log) as out:
        out.write("first\n")
    with server.open_log(log) as out:
        out.write("second\n")
    assert log.read_text() == "first\nsecond\n"
    log.write_text("x" * (server.LOG_MAX + 1))
    with server.open_log(log) as out:
        out.write("fresh\n")
    assert log.read_text() == "fresh\n"


def test_parse_args() -> None:
    args = server.parse_args([])
    assert (args.port, args.log, args.exit_with_parent) == (8765, None, False)
    args = server.parse_args(["8766", "--exit-with-parent", "--log", "/tmp/differ/server.log"])
    assert (args.port, args.log, args.exit_with_parent) == (8766, Path("/tmp/differ/server.log"), True)


def test_port_taken_skips_an_address_family_the_host_does_not_support(monkeypatch: pytest.MonkeyPatch) -> None:
    real_socket = server.socket.socket

    def ipv4_only(family: int, *args: Any) -> Any:
        if family == server.socket.AF_INET6:
            raise OSError(97, "Address family not supported by protocol")
        return real_socket(family, *args)

    monkeypatch.setattr(server.socket, "socket", ipv4_only)
    with real_socket(server.socket.AF_INET, server.socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert server.port_taken(port) == "127.0.0.1"
    assert server.port_taken(port) is None


def test_a_failed_command_answers_502_as_json_on_the_api_and_as_the_error_page_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise commands.CommandError("`gh api graphql --input -` failed: boom")

    monkeypatch.setattr(server.comments, "fetch", fail)
    h = request("GET", "/api/o/r/1/comments")
    h.do_GET()
    code, body = response(h)
    assert (code, json.loads(body)) == (502, {"error": "`gh api graphql --input -` failed: boom"})

    monkeypatch.setattr(server.github, "head_sha", fail)
    h = request("GET", "/o/r/pull/1?refresh=1")
    h.do_GET()
    code, body = response(h)
    assert code == 502 and b"Could not open that" in body and b"failed: boom" in body

    monkeypatch.setattr(server.comments, "discard", fail)
    h = request("POST", "/api/o/r/1/discard", b"{}")
    h.do_POST()
    assert response(h)[0] == 502


def test_a_reference_that_names_no_pull_request_answers_400() -> None:
    h = request("GET", "/open?pr=junk")
    h.do_GET()
    code, body = response(h)
    assert code == 400 and b"Not a pull request reference" in body


@pytest.mark.parametrize(
    ("action", "fields", "why"),
    [
        ("submit", {"event": "MERGE"}, "'MERGE' is not one of 'APPROVE', 'REQUEST_CHANGES', 'COMMENT'"),
        ("reply", {"thread": "T", "body": "b", "mode": "now"}, "'now' is not one of 'review', 'single'"),
        ("comment", {"path": "a.py", "body": "b", "line": 1, "side": "UP"}, "'UP' is not one of 'LEFT', 'RIGHT'"),
    ],
)
def test_a_request_field_outside_its_type_answers_400(
    action: str, fields: dict, why: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server.comments, "graphql", lambda *args, **kwargs: pytest.fail("GitHub was called"))
    h = request("POST", f"/api/o/r/1/{action}", json.dumps(fields).encode())
    h.do_POST()
    code, body = response(h)
    assert (code, json.loads(body)) == (400, {"error": f"Bad request: {why}"})


def test_a_refused_review_answers_400_with_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    h = request("POST", "/api/o/r/1/submit", json.dumps({"event": "REQUEST_CHANGES", "body": " "}).encode())
    h.do_POST()
    code, body = response(h)
    assert (code, json.loads(body)) == (400, {"error": "Requesting changes needs a summary."})
