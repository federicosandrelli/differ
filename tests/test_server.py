import io
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, cast

import pytest

import server


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
    monkeypatch.setattr(server, "start_job", lambda key: pytest.fail("a cross-site GET started a build"))
    monkeypatch.setattr(server.build, "latest_page", lambda *key: pytest.fail("a cross-site GET read a page"))
    status, body = get(path, "cross-site")
    assert status == 403
    assert b"Forbidden: the request comes from another site (Sec-Fetch-Site: cross-site)" in body


@pytest.mark.parametrize("site", [None, "same-origin", "none"])
def test_a_get_without_a_cross_site_header_passes(site: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    started = []
    monkeypatch.setattr(server, "start_job", lambda key: started.append(key))
    monkeypatch.setattr(server.build, "latest_page", lambda *key: None)
    status, body = get("/o/r/pull/1", site)
    assert status == 200 and started == [("o", "r", 1)]
    assert b"Building o/r#1" in body
    assert get("/open?pr=o/r%231", site)[0] == 303
    status, body = get("/status/o/r/1", site)
    assert status == 200 and json.loads(body)["state"] == "none"


def test_the_home_page_serves_a_cross_site_get(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "RECENT", tmp_path / "recent.json")
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


def test_page_key_lowercases_owner_and_repo() -> None:
    m = server.PAGE.match("/Owner/Repo/pull/12")
    assert m is not None
    assert server.page_key(m) == ("owner", "repo", 12)
    assert server.PAGE.match("/owner/repo/pull/12/files") is None


def test_ago() -> None:
    now = time.time()
    assert server.ago(now) == "just now"
    assert server.ago(now - 90) == "1m ago"
    assert server.ago(now - 3700) == "1h ago"
    assert server.ago(now - 2 * 86400) == "2d ago"


def test_note_opened_keeps_the_newest_ten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "RECENT", tmp_path / "recent.json")
    assert server.recent_pages() == []
    for n in range(12):
        server.note_opened(("o", "r", n), f"<title>#{n} Title &amp; {n}</title>".encode())
    server.note_opened(("o", "r", 3), b"<html>no title</html>")
    recent = server.recent_pages()
    assert [p["number"] for p in recent] == [3, 11, 10, 9, 8, 7, 6, 5, 4, 2]
    assert recent[0]["title"] == "o/r#3"
    assert recent[1]["title"] == "Title & 11"
    assert json.loads((tmp_path / "recent.json").read_text()) == recent


def test_recent_items_escapes_titles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "RECENT", tmp_path / "recent.json")
    assert "No pull requests opened yet" in server.recent_items()
    server.note_opened(("o", "r", 1), b"<title>#1 a &lt;b&gt;</title>")
    items = server.recent_items()
    assert 'href="/o/r/pull/1"' in items
    assert re.search(r'<span class="t">a &lt;b&gt;</span>', items)


def test_refresh_statuses_saves_the_statuses_github_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "RECENT", tmp_path / "recent.json")
    for n in range(4):
        server.note_opened(("o", "r", n), b'<meta name="pr-status" content="open">')
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
        return subprocess.CompletedProcess(args, 1, json.dumps(answer), "")

    monkeypatch.setattr(server.build.subprocess, "run", fake_run)
    server.refresh_statuses()
    assert sent["n0"] == 3 and sent["o0"] == "o" and sent["r0"] == "r"
    assert {p["number"]: p["status"] for p in server.recent_pages()} == {3: "open", 2: "open", 1: "draft", 0: "merged"}


def test_refresh_statuses_keeps_the_saved_statuses_when_gh_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "RECENT", tmp_path / "recent.json")
    server.note_opened(("o", "r", 1), b'<meta name="pr-status" content="merged">')
    before = (tmp_path / "recent.json").read_text()
    monkeypatch.setattr(
        server.build.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", "gh: not logged in"),
    )
    server.refresh_statuses()
    assert (tmp_path / "recent.json").read_text() == before


def test_refresh_statuses_skips_github_while_another_refresh_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "RECENT", tmp_path / "recent.json")
    server.note_opened(("o", "r", 1), b'<meta name="pr-status" content="open">')

    def fail(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise AssertionError("gh ran during another refresh")

    monkeypatch.setattr(server.build.subprocess, "run", fail)
    with server.refreshing:
        server.refresh_statuses()
    assert server.recent_pages()[0]["status"] == "open"


def request(method: str, path: str, body: bytes = b"") -> server.Handler:
    h = handler("http://localhost:8765", "application/json")
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
