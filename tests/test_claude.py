import base64
import io
import json
import os
import socket
import struct
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import build
import claude
import github
import server
import websocket
from github import PullRef
from helpers import commit_tree, make_repo

REF = PullRef("acme", "widgets", 1)


@pytest.fixture(autouse=True)
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "CACHE", tmp_path)
    monkeypatch.setattr(claude, "listeners", {})


def add(body: str = "Use a logger here", line: int = 11) -> dict:
    return claude.add(
        REF, path="src/app.py", line=line, start_line=None, side="RIGHT", commit="abc", code=["print(1)"], body=body
    )


def ids() -> list[str]:
    return [c["id"] for c in claude.fetch(REF, "abc")["comments"]]


def states() -> list[str]:
    return [c["state"] for c in claude.fetch(REF, "abc")["comments"]]


def said(c: dict) -> list[tuple[str, str]]:
    return [(m["author"], m["body"]) for m in c["messages"]]


def last_said(text: str | bytes) -> str:
    """The body of the last message of the thread a frame names."""
    return claude.thread(REF, json.loads(text)["id"])["messages"][-1]["body"]


def test_the_accept_key_answers_the_handshake_key_of_the_rfc() -> None:
    assert websocket.accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


@pytest.mark.parametrize("size", [5, 300, 60000])
def test_a_frame_reads_back_with_its_opcode_and_payload(size: int) -> None:
    payload = os.urandom(size)
    assert websocket.read_frame(io.BytesIO(websocket.frame(websocket.TEXT, payload))) == (websocket.TEXT, payload)


def test_a_masked_frame_from_a_client_reads_back_unmasked() -> None:
    mask, payload = b"\x01\x02\x03\x04", b"bye"
    data = bytes([0x88, 0x80 | len(payload)]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    assert websocket.read_frame(io.BytesIO(data)) == (websocket.CLOSE, payload)


def test_a_stream_that_ends_inside_a_frame_raises_eof() -> None:
    with pytest.raises(EOFError):
        websocket.read_frame(io.BytesIO(websocket.frame(websocket.TEXT, b"hello")[:4]))


def test_a_comment_waits_until_a_session_listens_and_then_goes_to_it() -> None:
    add()
    assert states() == ["waiting"]
    frames: list[str] = []
    claude.attach(REF, frames.append)
    (sent,) = claude.fetch(REF, "abc")["comments"]
    assert [json.loads(f) for f in frames] == [
        {"event": "comment", "pr": "acme/widgets#1", "id": sent["id"], "seen": 1, "replay": True}
    ]
    assert claude.thread(REF, sent["id"]) == {
        "id": sent["id"],
        "path": "src/app.py",
        "line": 11,
        "start_line": None,
        "side": "RIGHT",
        "commit": "abc",
        "code": ["print(1)"],
        "messages": sent["messages"],
        "seen": 1,
    }
    assert states() == ["sent"] and claude.fetch(REF, "abc")["listening"]


def test_a_frame_for_a_long_thread_fits_in_a_monitor_event() -> None:
    add("x" * 2000)
    frames: list[str] = []
    claude.attach(REF, frames.append)
    assert len(frames[0]) < 500 and last_said(frames[0]) == "x" * 2000


def test_a_comment_goes_at_once_to_the_session_that_listens() -> None:
    frames: list[str] = []
    claude.attach(REF, frames.append)
    assert frames == []
    add("Rename it")
    assert [(last_said(f), json.loads(f)["replay"]) for f in frames] == [("Rename it", False)]
    assert states() == ["sent"]


def test_a_second_session_is_refused_while_one_listens_and_let_in_after_it_leaves() -> None:
    first, second = [].append, [].append
    claude.attach(REF, first)
    with pytest.raises(claude.Busy):
        claude.attach(REF, second)
    claude.detach(REF, first)
    assert not claude.fetch(REF, "abc")["listening"]
    claude.attach(REF, second)
    assert claude.fetch(REF, "abc")["listening"]


def test_a_detach_by_a_session_that_no_longer_listens_keeps_the_new_listener() -> None:
    def old(text: str) -> None:
        raise OSError("gone")

    claude.attach(REF, old)
    add()
    new: list[str] = []
    claude.attach(REF, new.append)
    claude.detach(REF, old)
    assert claude.fetch(REF, "abc")["listening"] and len(new) == 1


def test_a_session_that_has_gone_stops_listening_and_its_comment_waits() -> None:
    def gone(text: str) -> None:
        raise OSError("broken pipe")

    claude.attach(REF, gone)
    answer = add()
    assert not answer["listening"] and states() == ["waiting"]


def test_a_session_that_connects_again_gets_every_comment_without_a_reply() -> None:
    add("one")
    add("two")
    first = [].append
    claude.attach(REF, first)
    claude.reply(REF, ids()[0], "Done.", seen=1)
    claude.detach(REF, first)
    again: list[str] = []
    claude.attach(REF, again.append)
    assert [last_said(f) for f in again] == ["two"]
    assert states() == ["answered", "sent"]


def test_a_reply_answers_its_comment_and_a_delete_removes_it() -> None:
    add("one")
    add("two")
    one, two = ids()
    c = claude.reply(REF, two, "Renamed it.", seen=1)
    assert c["state"] == "answered" and said(c) == [("reader", "two"), ("claude", "Renamed it.")]
    assert [said(c) for c in claude.delete(REF, one, "abc")["comments"]] == [said(c)]
    assert add("three")["comments"][-1]["id"] not in {one, two}
    with pytest.raises(claude.NoComment):
        claude.reply(REF, one, "Too late.", seen=1)


def test_the_readers_reply_goes_to_the_session_as_a_follow_up_and_waits_for_claude_again() -> None:
    add("Use a logger here")
    (thread,) = ids()
    frames: list[str] = []
    claude.attach(REF, frames.append)
    claude.reply(REF, thread, "Logged it at info.", seen=1)
    (c,) = claude.follow_up(REF, thread, "Make it debug", "abc")["comments"]
    assert c["state"] == "sent"
    last = json.loads(frames[-1])
    assert (last["event"], last["id"], last["seen"], last["replay"]) == ("follow_up", thread, 3, False)
    assert said(claude.thread(REF, thread)) == [
        ("reader", "Use a logger here"),
        ("claude", "Logged it at info."),
        ("reader", "Make it debug"),
    ]
    claude.reply(REF, thread, "Made it debug.", seen=3)
    assert said(claude.fetch(REF, "abc")["comments"][0])[-2:] == [
        ("reader", "Make it debug"),
        ("claude", "Made it debug."),
    ]


def test_claudes_reply_to_an_older_frame_keeps_the_readers_newer_message_waiting() -> None:
    add("Use a logger here")
    (thread,) = ids()
    first = [].append
    claude.attach(REF, first)
    claude.follow_up(REF, thread, "At debug", "abc")
    assert claude.reply(REF, thread, "Logged it.", seen=1)["state"] == "sent"
    claude.detach(REF, first)
    again: list[str] = []
    claude.attach(REF, again.append)
    assert [(json.loads(f)["seen"], last_said(f)) for f in again] == [(3, "Logged it.")]
    assert claude.reply(REF, thread, "Made it debug.", seen=3)["state"] == "answered"


def test_a_reply_written_while_no_session_listens_goes_to_the_next_one_as_a_follow_up() -> None:
    add()
    (thread,) = ids()
    claude.reply(REF, thread, "Done.", seen=1)
    claude.follow_up(REF, thread, "One more thing", "abc")
    assert states() == ["waiting"]
    frames: list[str] = []
    claude.attach(REF, frames.append)
    assert [(json.loads(f)["event"], last_said(f), json.loads(f)["replay"]) for f in frames] == [
        ("follow_up", "One more thing", True)
    ]


def test_a_comment_kept_before_threads_had_messages_reads_as_a_thread() -> None:
    old = {"id": "a1", "path": "src/app.py", "line": 11, "start_line": None, "side": "RIGHT", "commit": "abc"}
    claude.write(
        REF,
        [
            {
                **old,
                "code": [],
                "body": "one",
                "state": "answered",
                "created": "t1",
                "reply": "Done.",
                "answered": "t2",
            },
            {
                **old,
                "id": "b2",
                "code": [],
                "body": "two",
                "state": "waiting",
                "created": "t3",
                "reply": "",
                "answered": "",
            },
        ],
    )
    one, two = claude.fetch(REF, "abc")["comments"]
    assert one["messages"] == [
        {"author": "reader", "body": "one", "at": "t1"},
        {"author": "claude", "body": "Done.", "at": "t2"},
    ]
    assert said(two) == [("reader", "two")] and "reply" not in two


# ---------- through the server


@pytest.fixture
def port() -> Iterator[int]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def handshake(port: int, origin: str | None = None) -> tuple[socket.socket, bytes]:
    """Open the listen WebSocket of REF; the socket and the response head."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    key = base64.b64encode(os.urandom(16)).decode()
    lines = [
        "GET /api/acme/widgets/1/listen HTTP/1.1",
        f"Host: 127.0.0.1:{port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
        *([f"Origin: {origin}"] if origin else []),
    ]
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    head = b""
    while b"\r\n\r\n" not in head:
        head += sock.recv(1)
    if b" 101 " in head.split(b"\r\n")[0]:
        assert f"Sec-WebSocket-Accept: {websocket.accept_key(key)}".encode() in head
    return sock, head


def receive(sock: socket.socket) -> tuple[int, bytes]:
    return websocket.read_frame(sock.makefile("rb"))  # type: ignore[arg-type]


def post(port: int, action: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/acme/widgets/1/{action}",
        data=json.dumps(body).encode(),
        headers={"Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def get_thread(port: int, thread: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/acme/widgets/1/claude_thread?id={thread}") as response:
        return json.loads(response.read())


def until_listening(expected: bool) -> None:
    for _ in range(200):
        if claude.fetch(REF, "abc")["listening"] == expected:
            return
        threading.Event().wait(0.01)
    pytest.fail(f"listening never became {expected}")


def test_a_session_listens_over_a_websocket_and_replies_through_a_post(port: int) -> None:
    sock, head = handshake(port)
    assert head.startswith(b"HTTP/1.1 101")
    until_listening(True)
    post(port, "claude", {"path": "src/app.py", "line": 11, "side": "RIGHT", "commit": "abc", "body": "Log it"})
    opcode, payload = receive(sock)
    thread = json.loads(payload)["id"]
    read = get_thread(port, thread)
    assert opcode == websocket.TEXT and (read["messages"][-1]["body"], read["seen"]) == ("Log it", 1)
    assert post(port, "claude_reply", {"id": thread, "seen": 1, "body": "Logged."})["state"] == "answered"
    post(port, "claude_follow_up", {"id": thread, "body": "At debug"})
    opcode, payload = receive(sock)
    read = get_thread(port, thread)
    assert (json.loads(payload)["event"], read["messages"][-1]["body"], read["seen"]) == ("follow_up", "At debug", 3)
    sock.sendall(bytes([0x88, 0x82, 0, 0, 0, 0]) + struct.pack(">H", 1000))
    assert receive(sock)[0] == websocket.CLOSE
    sock.close()
    until_listening(False)


@pytest.mark.parametrize(
    ("thread", "code", "error"),
    [("nope", 404, "no Claude comment nope"), ("", 400, "Bad request: claude_thread needs ?id=<thread id>")],
)
def test_reading_a_thread_without_a_known_id_fails(port: int, thread: str, code: int, error: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as exc:
        get_thread(port, thread)
    assert (exc.value.code, json.loads(exc.value.read())) == (code, {"error": error})


def test_a_second_websocket_gets_a_close_frame_with_the_reason(port: int) -> None:
    first, _ = handshake(port)
    until_listening(True)
    second, head = handshake(port)
    assert head.startswith(b"HTTP/1.1 101")
    opcode, payload = receive(second)
    assert opcode == websocket.CLOSE and struct.unpack(">H", payload[:2])[0] == 4000
    assert b"Another Claude session listens" in payload
    first.close()
    second.close()
    until_listening(False)


def test_a_websocket_from_a_page_of_another_origin_is_refused(port: int) -> None:
    sock, head = handshake(port, origin="https://evil.example")
    assert head.startswith(b"HTTP/1.0 403")
    sock.close()
    assert not claude.fetch(REF, "abc")["listening"]


LINES = [f"line {n}" for n in range(1, 11)]


def text(lines: list[str]) -> str:
    return "".join(f"{line}\n" for line in lines)


@dataclass
class Pushes:
    """A repo with src/app.py holding LINES at `written`, the commit the comments are written on."""

    repo: Path
    written: str

    def push(self, files: dict[str, str]) -> str:
        """Commit `files` in place of the tree, and answer the new head commit."""
        return commit_tree(self.repo, files, "push")


@pytest.fixture
def pushes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Pushes:
    repo, _, written = make_repo(tmp_path / "repo", {}, {"src/app.py": text(LINES)})
    monkeypatch.setattr(build, "mirror_of", lambda ref: repo)
    return Pushes(repo, written)


def placed(written: str, head: str, line: int, start_line: int | None = None, side: claude.Side = "RIGHT") -> dict:
    claude.add(REF, path="src/app.py", line=line, start_line=start_line, side=side, commit=written, code=[], body="b")
    return claude.fetch(REF, head)["comments"][-1]["placed"]


@pytest.mark.parametrize(
    ("lines", "at"),
    [
        (LINES, {"line": 5, "start_line": 4}),
        (["new", "new", *LINES], {"line": 7, "start_line": 6}),
        ([*LINES[:1], *LINES[2:]], {"line": 4, "start_line": 3}),
        ([*LINES[:6], "changed", *LINES[7:]], {"line": 5, "start_line": 4}),
        ([*LINES[:2], "changed", *LINES[3:]], {"line": 5, "start_line": 4}),
        ([*LINES[:5], "changed", *LINES[6:]], {"line": 5, "start_line": 4}),
        ([*LINES[:5], "added", *LINES[5:]], {"line": 5, "start_line": 4}),
        ([*LINES[:4], "changed", *LINES[5:]], {"line": None, "start_line": None}),
        ([*LINES[:4], "between", *LINES[4:]], {"line": None, "start_line": None}),
        ([*LINES[:3], *LINES[4:]], {"line": None, "start_line": None}),
    ],
    ids=[
        "unchanged",
        "lines added above",
        "line removed above",
        "line changed below",
        "line just above changed",
        "line just below changed",
        "line added just below",
        "line changed",
        "line added between",
        "line removed",
    ],
)
def test_a_comment_follows_its_lines_to_a_new_head_until_they_change(
    pushes: Pushes, lines: list[str], at: dict
) -> None:
    head = pushes.push({"src/app.py": text(lines), "other.py": "x\n"})
    assert placed(pushes.written, head, line=5, start_line=4) == at


def test_a_comment_on_a_renamed_file_is_outdated(pushes: Pushes) -> None:
    head = pushes.push({"src/main.py": text(LINES)})
    assert placed(pushes.written, head, line=5) == {"line": None, "start_line": None}


def test_a_change_answers_the_comments_placed_on_the_head_it_names(pushes: Pushes) -> None:
    head = pushes.push({"src/app.py": text(["new", *LINES])})

    def comment(line: int, commit: str) -> list[dict]:
        return claude.add(
            REF, path="src/app.py", line=line, start_line=None, side="RIGHT", commit=commit, code=[], body="b"
        )["comments"]

    def lines(comments: list[dict]) -> list[int | None]:
        return [c["placed"]["line"] for c in comments]

    (first,) = comment(5, pushes.written)
    assert first["placed"]["line"] == 5
    assert lines(second := comment(1, head)) == [6, 1]
    assert lines(claude.follow_up(REF, first["id"], "And here", head)["comments"]) == [6, 1]
    assert lines(claude.delete(REF, second[1]["id"], head)["comments"]) == [6]


def test_a_comment_on_a_deleted_file_is_outdated(pushes: Pushes) -> None:
    head = pushes.push({"other.py": "x\n"})
    assert placed(pushes.written, head, line=5) == {"line": None, "start_line": None}


def test_a_comment_on_the_old_file_is_outdated_on_another_head(pushes: Pushes) -> None:
    head = pushes.push({"src/app.py": text(LINES), "other.py": "x\n"})
    assert placed(pushes.written, head, line=5, side="LEFT")["line"] is None
    assert placed(pushes.written, pushes.written, line=5, side="LEFT")["line"] == 5


def test_a_comment_is_outdated_when_git_cannot_compare_the_commits(pushes: Pushes) -> None:
    assert placed(pushes.written, "f" * 40, line=5)["line"] is None


def test_a_sweep_deletes_the_comments_of_merged_and_closed_pull_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    statuses: list[github.Status] = ["open", "merged", "closed", "draft"]
    refs: dict[github.Status, PullRef] = {status: PullRef("acme", "widgets", n) for n, status in enumerate(statuses)}
    gone = PullRef("acme", "widgets", 9)
    for ref in [*refs.values(), gone]:
        claude.add(ref, path="a.py", line=1, start_line=None, side="RIGHT", commit="abc", code=[], body="b")
    asked = []

    def standings(asked_for: list[PullRef]) -> dict[PullRef, github.Standing]:
        asked.extend(asked_for)
        return {ref: github.Standing(status, "me") for status, ref in refs.items()}

    monkeypatch.setattr(github, "pr_standings", standings)
    claude.sweep()
    assert sorted(asked, key=lambda r: r.number) == [*refs.values(), gone]
    kept = [ref for ref in [*refs.values(), gone] if claude.fetch(ref, "abc")["comments"]]
    assert kept == [refs["open"], refs["draft"], gone]


@pytest.fixture
def skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(claude, "SKILLS", tmp_path / "skills")
    return tmp_path / "skills" / "differ-comments" / "SKILL.md"


def test_install_skills_writes_each_shipped_skill_under_its_name_with_its_marker(skills: Path) -> None:
    claude.install_skills()
    for name in ["differ-comments", "differ-file-descriptions"]:
        text = (skills.parent.parent / name / "SKILL.md").read_text()
        shipped = (claude.SHIPPED_SKILLS / f"{name}.md").read_text()
        assert text.startswith(shipped.rstrip()) and f"\nname: {name}\n" in shipped
        assert text.endswith(f"\n\n{claude.skill_marker(shipped)}\n")


def test_install_skills_replaces_a_copy_from_another_version(skills: Path) -> None:
    skills.parent.mkdir(parents=True)
    skills.write_text("---\nname: differ-comments\n---\nold words\n\n<!-- differ skill 0123456789ab -->\n")
    claude.install_skills()
    assert "old words" not in skills.read_text()


def test_install_skills_keeps_an_edited_copy_of_the_current_version(skills: Path) -> None:
    claude.install_skills()
    edited = skills.read_text().replace("\n", "\nMy own first line.\n", 1)
    skills.write_text(edited)
    claude.install_skills()
    assert skills.read_text() == edited


def test_install_skills_reports_a_folder_it_cannot_write(skills: Path, capsys: pytest.CaptureFixture[str]) -> None:
    skills.parent.parent.mkdir(parents=True)
    skills.parent.write_text("a file where the folder should be")
    claude.install_skills()
    assert "Could not install the differ-comments skill" in capsys.readouterr().err


def test_kept_lists_the_pull_requests_with_a_comments_file() -> None:
    add()
    other = PullRef("acme", "widgets", 2)
    claude.write(other, [])
    (claude.path_of(other).parent / "notes.json").write_text("{}")
    assert claude.kept() == [REF, other]


def test_kept_lists_a_pull_request_with_file_descriptions_once() -> None:
    add()
    claude.describe(REF, "src/app.py", "The entry point.")
    other = PullRef("acme", "widgets", 2)
    claude.describe(other, "a.py", "A helper.")
    assert claude.kept() == [REF, other]


def test_a_description_replaces_the_one_before_and_a_blank_one_drops_it() -> None:
    claude.describe(REF, "a.py", "One.")
    claude.describe(REF, "b.py", "Two.")
    claude.describe(REF, "a.py", "Three.")
    assert claude.fetch(REF, "abc")["descriptions"] == {"a.py": "Three.", "b.py": "Two."}
    claude.describe(REF, "a.py", "  ")
    assert add()["descriptions"] == {"b.py": "Two."}


def test_expire_deletes_the_file_descriptions_with_the_comments() -> None:
    add()
    claude.describe(REF, "a.py", "One.")
    claude.expire(REF)
    assert claude.fetch(REF, "abc") == {"listening": False, "comments": [], "descriptions": {}}
    assert claude.kept() == []


def test_a_sweep_expires_with_the_function_given_among_the_pull_requests_given(monkeypatch: pytest.MonkeyPatch) -> None:
    add()
    other = PullRef("acme", "widgets", 2)
    asked: list[list[PullRef]] = []

    def standings(refs: list[PullRef]) -> dict[PullRef, github.Standing]:
        asked.append(refs)
        return {ref: github.Standing("merged", "me") for ref in refs}

    monkeypatch.setattr(github, "pr_standings", standings)
    expired: list[PullRef] = []
    claude.sweep(expired.append)
    assert expired == [REF] and ids() != []
    claude.sweep(expired.append, [other])
    assert asked == [[REF], [other]] and expired == [REF, other]
