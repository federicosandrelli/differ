import base64
import io
import json
import os
import socket
import struct
import threading
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import build
import claude
import server
import websocket
from github import PullRef

REF = PullRef("acme", "widgets", 1)


@pytest.fixture(autouse=True)
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "CACHE", tmp_path)
    monkeypatch.setattr(claude, "listeners", {})


def add(body: str = "Use a logger here", line: int = 11) -> dict:
    return claude.add(
        REF, path="src/app.py", line=line, start_line=None, side="RIGHT", commit="abc", code=["print(1)"], body=body
    )


def states() -> list[str]:
    return [c["state"] for c in claude.fetch(REF)["comments"]]


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
    assert [json.loads(f) for f in frames] == [
        {
            "event": "comment",
            "pr": "acme/widgets#1",
            "id": "c1",
            "path": "src/app.py",
            "line": 11,
            "start_line": None,
            "side": "RIGHT",
            "commit": "abc",
            "code": ["print(1)"],
            "body": "Use a logger here",
            "replay": True,
        }
    ]
    assert states() == ["sent"] and claude.fetch(REF)["listening"]


def test_a_comment_goes_at_once_to_the_session_that_listens() -> None:
    frames: list[str] = []
    claude.attach(REF, frames.append)
    assert frames == []
    add("Rename it")
    assert [(json.loads(f)["body"], json.loads(f)["replay"]) for f in frames] == [("Rename it", False)]
    assert states() == ["sent"]


def test_a_second_session_is_refused_while_one_listens_and_let_in_after_it_leaves() -> None:
    first, second = [].append, [].append
    claude.attach(REF, first)
    with pytest.raises(claude.Busy):
        claude.attach(REF, second)
    claude.detach(REF, first)
    assert not claude.fetch(REF)["listening"]
    claude.attach(REF, second)
    assert claude.fetch(REF)["listening"]


def test_a_detach_by_a_session_that_no_longer_listens_keeps_the_new_listener() -> None:
    def old(text: str) -> None:
        raise OSError("gone")

    claude.attach(REF, old)
    add()
    new: list[str] = []
    claude.attach(REF, new.append)
    claude.detach(REF, old)
    assert claude.fetch(REF)["listening"] and len(new) == 1


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
    claude.reply(REF, "c1", "Done.")
    claude.detach(REF, first)
    again: list[str] = []
    claude.attach(REF, again.append)
    assert [json.loads(f)["body"] for f in again] == ["two"]
    assert states() == ["answered", "sent"]


def test_a_reply_answers_its_comment_and_a_delete_removes_it() -> None:
    add("one")
    add("two")
    c = claude.reply(REF, "c2", "Renamed it.")
    assert (c["state"], c["reply"]) == ("answered", "Renamed it.") and c["answered"]
    assert [c["id"] for c in claude.delete(REF, "c1")["comments"]] == ["c2"]
    assert add("three")["comments"][-1]["id"] == "c3"
    with pytest.raises(claude.NoComment):
        claude.reply(REF, "c1", "Too late.")


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


def until_listening(expected: bool) -> None:
    for _ in range(200):
        if claude.fetch(REF)["listening"] == expected:
            return
        threading.Event().wait(0.01)
    pytest.fail(f"listening never became {expected}")


def test_a_session_listens_over_a_websocket_and_replies_through_a_post(port: int) -> None:
    sock, head = handshake(port)
    assert head.startswith(b"HTTP/1.1 101")
    until_listening(True)
    post(port, "claude", {"path": "src/app.py", "line": 11, "side": "RIGHT", "commit": "abc", "body": "Log it"})
    opcode, payload = receive(sock)
    assert opcode == websocket.TEXT and json.loads(payload)["body"] == "Log it"
    assert post(port, "claude_reply", {"id": "c1", "body": "Logged."})["state"] == "answered"
    sock.sendall(bytes([0x88, 0x82, 0, 0, 0, 0]) + struct.pack(">H", 1000))
    assert receive(sock)[0] == websocket.CLOSE
    sock.close()
    until_listening(False)


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
    assert not claude.fetch(REF)["listening"]
