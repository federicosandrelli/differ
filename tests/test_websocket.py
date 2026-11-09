import io
import os
import struct

import pytest

import websocket

MASK = b"\x01\x02\x03\x04"


def client_frame(opcode: int, payload: bytes = b"") -> bytes:
    """One final frame as a client sends it: masked, with a payload of at most 125 bytes."""
    return bytes([0x80 | opcode, 0x80 | len(payload)]) + MASK + bytes(b ^ MASK[i % 4] for i, b in enumerate(payload))


def sent(out: io.BytesIO) -> list[tuple[int, bytes]]:
    """The frames the server wrote, in order."""
    stream, frames = io.BytesIO(out.getvalue()), []
    while stream.tell() < len(out.getvalue()):
        frames.append(websocket.read_frame(stream))
    return frames


class Gone(io.BytesIO):
    """The stream to a client that has gone: every write fails, as a socket's does."""

    def write(self, data: object) -> int:
        raise BrokenPipeError("the client has gone")


def connection(*frames: bytes) -> tuple[websocket.Connection, io.BytesIO]:
    out = io.BytesIO()
    return websocket.Connection(io.BytesIO(b"".join(frames)), out), out


def test_the_accept_key_answers_the_handshake_key_of_the_rfc() -> None:
    assert websocket.accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


@pytest.mark.parametrize("size", [5, 300, 60000])
def test_a_frame_reads_back_with_its_opcode_and_payload(size: int) -> None:
    payload = os.urandom(size)
    assert websocket.read_frame(io.BytesIO(websocket.frame(websocket.TEXT, payload))) == (websocket.TEXT, payload)


def test_a_masked_frame_from_a_client_reads_back_unmasked() -> None:
    assert websocket.read_frame(io.BytesIO(client_frame(websocket.CLOSE, b"bye"))) == (websocket.CLOSE, b"bye")


def test_a_stream_that_ends_inside_a_frame_raises_eof() -> None:
    with pytest.raises(EOFError):
        websocket.read_frame(io.BytesIO(websocket.frame(websocket.TEXT, b"hello")[:4]))


def test_the_messages_are_the_text_and_binary_frames_and_a_ping_gets_its_pong() -> None:
    ws, out = connection(
        client_frame(websocket.TEXT, b'{"cols": 80}'),
        client_frame(websocket.PING, b"are you there"),
        client_frame(websocket.PONG, b"unasked"),
        client_frame(websocket.BINARY, b"\x03"),
    )
    assert list(ws.messages()) == [(websocket.TEXT, b'{"cols": 80}'), (websocket.BINARY, b"\x03")]
    assert sent(out) == [(websocket.PONG, b"are you there")]


def test_a_close_gets_a_close_with_its_code_and_ends_the_messages() -> None:
    code = struct.pack(">H", 1000)
    ws, out = connection(
        client_frame(websocket.TEXT, b"before"),
        client_frame(websocket.CLOSE, code + b"bye"),
        client_frame(websocket.TEXT, b"after"),
    )
    assert list(ws.messages()) == [(websocket.TEXT, b"before")]
    assert sent(out) == [(websocket.CLOSE, code)]


def test_the_messages_end_when_the_client_goes_away() -> None:
    ws, out = connection(client_frame(websocket.TEXT, b"hello"), client_frame(websocket.BINARY, b"cut")[:5])
    assert list(ws.messages()) == [(websocket.TEXT, b"hello")]
    assert out.getvalue() == b""


def test_the_messages_end_when_the_pong_finds_the_client_gone() -> None:
    ws = websocket.Connection(io.BytesIO(client_frame(websocket.PING) + client_frame(websocket.TEXT, b"late")), Gone())
    assert list(ws.messages()) == []


def test_a_send_to_a_client_that_has_gone_raises_os_error() -> None:
    with pytest.raises(OSError):
        websocket.Connection(io.BytesIO(), Gone()).send(websocket.TEXT, b"hello")


def test_a_close_carries_its_code_and_a_reason_cut_to_fit_a_control_frame() -> None:
    ws, out = connection()
    ws.close(4000, "Another session listens. " * 10)
    [(opcode, payload)] = sent(out)
    assert opcode == websocket.CLOSE and len(payload) == 125
    assert struct.unpack(">H", payload[:2])[0] == 4000 and payload[2:].startswith(b"Another session listens.")
