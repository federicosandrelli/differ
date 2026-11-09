import io
import json
from pathlib import Path
from typing import Any, cast

import pytest

import build
import claude
import github
import mcp
import server
from github import PullRef
from helpers import commit_tree, git, make_repo
from test_server import get

REF = PullRef("acme", "widgets", 1)
OTHER = PullRef("acme", "widgets", 2)


@pytest.fixture(autouse=True)
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "CACHE", tmp_path)
    monkeypatch.setattr(claude, "listeners", {})


def rpc(method: str, params: dict | None = None, request_id: object = 1) -> dict:
    request: dict = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        request["id"] = request_id
    if params is not None:
        request["params"] = params
    return request


def result(method: str, params: dict | None = None) -> dict:
    code, body = mcp.handle(REF, rpc(method, params))
    assert code == 200 and body is not None and "result" in body, body
    return body["result"]


def test_initialize_echoes_the_clients_protocol_version_and_offers_tools() -> None:
    assert result("initialize", {"protocolVersion": "2026-07-28"}) == {
        "protocolVersion": "2026-07-28",
        "capabilities": {"tools": {}},
        "serverInfo": mcp.SERVER,
    }
    assert result("initialize")["protocolVersion"] == mcp.PROTOCOL
    assert result("ping") == {}


def test_tools_list_names_the_tools_with_their_schemas() -> None:
    tools = result("tools/list")["tools"]
    assert [t["name"] for t in tools] == ["pull_request_diff", "read_thread", "reply_in_thread", "describe_file"]
    assert all("inputSchema" in t and t["description"] for t in tools)


def test_a_notification_gets_202_and_no_body_and_an_unknown_method_the_json_rpc_error() -> None:
    assert mcp.handle(REF, rpc("notifications/initialized", request_id=None)) == (202, None)
    code, body = mcp.handle(REF, rpc("server/discover"))
    assert code == 200 and body == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32601, "message": "unknown method server/discover"},
    }
    code, body = mcp.handle(REF, {"hello": 1})
    assert code == 400 and body is not None and body["error"]["code"] == -32600


def test_the_token_is_per_pull_request_and_checked_in_the_authorization_header() -> None:
    assert mcp.token(REF) != mcp.token(OTHER)
    assert mcp.authorized(REF, f"Bearer {mcp.token(REF)}")
    assert not mcp.authorized(REF, f"Bearer {mcp.token(OTHER)}")
    assert not mcp.authorized(REF, mcp.token(REF)) and not mcp.authorized(REF, None)
    config = json.loads(mcp.config(REF, 8766))["mcpServers"]["differ"]
    assert config == {
        "type": "http",
        "url": "http://127.0.0.1:8766/api/acme/widgets/1/mcp",
        "headers": {"Authorization": f"Bearer {mcp.token(REF)}"},
    }


def test_reply_in_thread_answers_the_thread_and_names_its_lines() -> None:
    claude.add(REF, path="src/app.py", line=11, start_line=None, side="RIGHT", commit="abc", code=["x"], body="Why?")
    (c,) = claude.fetch(REF, "abc")["comments"]
    answer = result(
        "tools/call", {"name": "reply_in_thread", "arguments": {"id": c["id"], "body": "Because.", "seen": 1}}
    )
    assert answer == {
        "content": [{"type": "text", "text": "Replied in the thread on src/app.py:11."}],
        "isError": False,
    }
    (c,) = claude.fetch(REF, "abc")["comments"]
    assert c["state"] == "answered" and c["messages"][-1] == {
        **c["messages"][-1],
        "author": "claude",
        "body": "Because.",
    }


def test_read_thread_gives_every_message_of_the_thread_and_seen() -> None:
    claude.add(REF, path="src/app.py", line=11, start_line=None, side="RIGHT", commit="abc", code=["x"], body="Why?")
    (c,) = claude.fetch(REF, "abc")["comments"]
    claude.follow_up(REF, c["id"], "And how?", "abc")
    answer = result("tools/call", {"name": "read_thread", "arguments": {"id": c["id"]}})
    thread = json.loads(answer["content"][0]["text"])
    assert not answer["isError"] and (thread["path"], thread["code"], thread["seen"]) == ("src/app.py", ["x"], 2)
    assert [m["body"] for m in thread["messages"]] == ["Why?", "And how?"]


def test_a_reply_with_seen_leaves_a_thread_the_reader_wrote_in_since_waiting() -> None:
    claude.add(REF, path="src/app.py", line=11, start_line=None, side="RIGHT", commit="abc", code=["x"], body="Why?")
    (c,) = claude.fetch(REF, "abc")["comments"]
    claude.follow_up(REF, c["id"], "And how?", "abc")
    arguments = {"id": c["id"], "body": "Because.", "seen": 1}
    assert not result("tools/call", {"name": "reply_in_thread", "arguments": arguments})["isError"]
    (c,) = claude.fetch(REF, "abc")["comments"]
    assert c["state"] == "waiting" and [m["author"] for m in c["messages"]] == ["reader", "reader", "claude"]


def test_a_tool_failure_comes_back_as_text_with_is_error() -> None:
    answer = result("tools/call", {"name": "reply_in_thread", "arguments": {"id": "nope", "body": "x", "seen": 1}})
    assert answer == {"content": [{"type": "text", "text": "no Claude comment nope"}], "isError": True}
    assert result("tools/call", {"name": "reply_in_thread", "arguments": {"id": "a"}})["isError"]
    assert result("tools/call", {"name": "reply_in_thread", "arguments": {"id": "a", "body": "x"}})["isError"]
    assert result("tools/call", {"name": "fly", "arguments": {}}) == {
        "content": [{"type": "text", "text": "No tool named fly."}],
        "isError": True,
    }
    code, body = mcp.handle(REF, rpc("tools/call", {"arguments": {}}))
    assert code == 200 and body is not None and body["error"]["code"] == -32602


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository with two commits, served as the mirror of REF, whose pull request goes from the first to the
    second: it changes a.py, renames old.py to new.py with one more line, and deletes gone.py."""
    long = "".join(f"line {n}\n" for n in range(20))
    first = {"a.py": "print(1)\n", "b.py": "two\n", "old.py": long, "gone.py": "bye\n"}
    root, base, head = make_repo(
        tmp_path / "repo", first, {"a.py": "print(2)\n", "b.py": "two\n", "new.py": long + "line 20\n"}
    )
    # The base branch moved on after the pull request's branch left it, as GitHub's base commit does.
    git(root, "checkout", "-q", base)
    moved = commit_tree(root, {**first, "c.py": "later\n"}, "later on main")
    pr = github.PullRequest("t", "u", "", "ann", moved, head, "main", "feature", 1, "OPEN", False)
    monkeypatch.setattr(github, "pull_request", lambda ref: pr)
    monkeypatch.setattr(build, "ensure_repo", lambda ref, log: root)
    monkeypatch.setattr(build, "fetch", lambda *args: None)
    (tmp_path / "base.txt").write_text(base)
    return root


def test_pull_request_diff_gives_the_whole_diff_from_the_merge_base_or_one_files(repo: Path, tmp_path: Path) -> None:
    text = result("tools/call", {"name": "pull_request_diff", "arguments": {}})["content"][0]["text"]
    base = (tmp_path / "base.txt").read_text()
    assert text.startswith(f"base {base}\nhead ") and "-print(1)" in text and "+print(2)" in text
    assert "c.py" not in text
    one = result("tools/call", {"name": "pull_request_diff", "arguments": {"path": "a.py"}})["content"][0]["text"]
    assert "+print(2)" in one
    none = result("tools/call", {"name": "pull_request_diff", "arguments": {"path": "b.py"}})["content"][0]["text"]
    assert none.endswith("b.py is not changed by this pull request.")


def test_pull_request_diff_of_a_renamed_file_shows_the_rename(repo: Path) -> None:
    text = result("tools/call", {"name": "pull_request_diff", "arguments": {"path": "new.py"}})["content"][0]["text"]
    assert "rename from old.py\nrename to new.py" in text and "+line 20" in text and "+line 0" not in text


def describe(path: str, body: str) -> dict:
    return result("tools/call", {"name": "describe_file", "arguments": {"path": path, "body": body}})


def test_describe_file_keeps_a_description_of_a_file_of_the_diff_only(repo: Path) -> None:
    assert describe("a.py", "Prints two.") == {
        "content": [{"type": "text", "text": "Described a.py."}],
        "isError": False,
    }
    refused = describe("b.py", "Unchanged.")
    assert refused["isError"] and refused["content"][0]["text"] == "b.py is not a file of this pull request's diff."
    assert claude.descriptions(REF) == {"a.py": "Prints two."}
    assert describe("a.py", "")["content"][0]["text"] == "Removed the description of a.py."
    assert claude.descriptions(REF) == {}
    assert result("tools/call", {"name": "describe_file", "arguments": {"path": "a.py"}})["isError"]


def test_describe_file_names_a_renamed_file_by_its_new_path_and_a_deleted_one_by_its_old_path(repo: Path) -> None:
    assert not describe("new.py", "Renamed.")["isError"] and not describe("gone.py", "Deleted.")["isError"]
    assert describe("old.py", "Renamed.")["isError"]
    assert describe("c.py", "Changed on main only.")["isError"]
    assert claude.descriptions(REF) == {"new.py": "Renamed.", "gone.py": "Deleted."}


def test_describe_file_removes_the_description_of_a_file_that_left_the_diff(repo: Path) -> None:
    claude.describe(REF, "dropped.py", "Was in the diff once.")
    assert not describe("dropped.py", "")["isError"]
    assert claude.descriptions(REF) == {}


def test_a_long_diff_is_cut_with_a_note(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp, "DIFF_MAX", 40)
    text = result("tools/call", {"name": "pull_request_diff", "arguments": {}})["content"][0]["text"]
    assert "[cut after 40 characters; ask for one file with `path`]" in text


def post(path: str, body: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
    """Drive do_POST for a path with the headers; the status code and body it writes."""
    h = server.Handler.__new__(server.Handler)
    h.server = cast(Any, type("FakeServer", (), {"server_address": ("127.0.0.1", 8765)})())
    h.headers = cast(Any, {"Host": "localhost:8765", "Content-Length": str(len(body)), **headers})
    h.path = path
    h.command = "POST"
    h.request_version = "HTTP/1.1"
    h.requestline = f"POST {path} HTTP/1.1"
    h.client_address = ("127.0.0.1", 1)
    h.rfile = cast(Any, io.BytesIO(body))
    out = io.BytesIO()
    h.wfile = cast(Any, out)
    h.do_POST()
    head, answer = out.getvalue().split(b"\r\n\r\n", 1)
    return int(head.split(b" ", 2)[1]), answer


def test_the_endpoint_wants_the_pull_requests_token_and_needs_no_origin() -> None:
    path, ping = "/api/acme/widgets/1/mcp", json.dumps(rpc("ping")).encode()
    assert post(path, ping, {})[0] == 403
    assert post(path, ping, {"Authorization": f"Bearer {mcp.token(OTHER)}"})[0] == 403
    status, body = post(path, ping, {"Authorization": f"Bearer {mcp.token(REF)}"})
    assert status == 200 and json.loads(body) == {"jsonrpc": "2.0", "id": 1, "result": {}}
    note = json.dumps(rpc("notifications/initialized", request_id=None)).encode()
    assert post(path, note, {"Authorization": f"Bearer {mcp.token(REF)}"}) == (202, b"")


def test_a_get_on_the_endpoint_gets_405() -> None:
    status, body = get("/api/acme/widgets/1/mcp")
    assert status == 405 and body == b""
