"""The part of the WebSocket protocol (RFC 6455) that a server needs to exchange text and bytes with one client: the
handshake's accept key, and a `Connection` over the client's streams once the handshake is answered, which sends
frames, reads the client's, and answers its control frames."""

from __future__ import annotations

import base64
import hashlib
import struct
import threading
from collections.abc import Iterator
from io import BufferedIOBase

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
TEXT, BINARY, CLOSE, PING, PONG = 0x1, 0x2, 0x8, 0x9, 0xA
# The largest frame read from a client. A control frame holds at most 125 bytes.
MAX_READ = 1 << 16


def accept_key(key: str) -> str:
    """The Sec-WebSocket-Accept value that answers a handshake's Sec-WebSocket-Key."""
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def frame(opcode: int, payload: bytes = b"") -> bytes:
    """One final, unmasked frame, as a server sends it."""
    n = len(payload)
    if n < 126:
        head = struct.pack(">BB", 0x80 | opcode, n)
    elif n < 1 << 16:
        head = struct.pack(">BBH", 0x80 | opcode, 126, n)
    else:
        head = struct.pack(">BBQ", 0x80 | opcode, 127, n)
    return head + payload


def read_frame(stream: BufferedIOBase) -> tuple[int, bytes]:
    """The opcode and the unmasked payload of the next frame. Raises EOFError when the stream ends first, or when
    the frame is longer than MAX_READ."""

    def exactly(n: int) -> bytes:
        data = stream.read(n)
        if len(data) < n:
            raise EOFError("the WebSocket closed")
        return data

    first, second = exactly(2)
    n = second & 0x7F
    if n == 126:
        n = struct.unpack(">H", exactly(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", exactly(8))[0]
    if n > MAX_READ:
        raise EOFError(f"a WebSocket frame of {n} bytes")
    mask = exactly(4) if second & 0x80 else b""
    payload = exactly(n)
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return first & 0x0F, payload


class Connection:
    """One client's WebSocket, once the server has answered its handshake: frames go out on `writer`, and come in on
    `reader`. Any thread may send; the frames never mix."""

    def __init__(self, reader: BufferedIOBase, writer: BufferedIOBase) -> None:
        self.reader, self.writer = reader, writer
        self.writes = threading.Lock()

    def send(self, opcode: int, payload: bytes = b"") -> None:
        """Send one frame. Raises OSError once the client has gone."""
        data = frame(opcode, payload)
        with self.writes:
            self.writer.write(data)
            self.writer.flush()

    def close(self, code: int, reason: str) -> None:
        """Send a close frame with the code and the reason, cut to fit a control frame. Raises OSError once the client
        has gone."""
        self.send(CLOSE, struct.pack(">H", code) + reason.encode()[:123])

    def messages(self) -> Iterator[tuple[int, bytes]]:
        """The opcode and the payload of each text and binary frame the client sends, in order, until the client
        closes the connection or goes away. A ping gets its pong; a close gets a close with the client's code, and
        ends the messages."""
        try:
            while True:
                opcode, payload = read_frame(self.reader)
                if opcode == CLOSE:
                    self.send(CLOSE, payload[:2])
                    return
                if opcode == PING:
                    self.send(PONG, payload)
                elif opcode in (TEXT, BINARY):
                    yield opcode, payload
        except (EOFError, OSError):
            return
