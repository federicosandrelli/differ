"""The pseudoterminal on the system the tests run on: what the sessions rely on, checked against a child process that
records what it reads instead of echoing it, since a Windows pseudo console renders what a process prints."""

import os
import signal
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

import terminal
from helpers import alive, wait_for

# Raw mode, then a grandchild that sleeps, so a test can see whether ending the child ends the processes it started,
# then a line `ready <grandchild pid>`. After that it writes every byte it reads to the file its first argument names;
# a ? prints the terminal's size, and a q sets a title and exits. With a second argument `detached`, the grandchild on
# Windows gets no console of its own to share, as Claude Code's MCP servers and background shells have none.
CHILD = r"""
import os, subprocess, sys
if sys.platform == "win32":
    import ctypes, msvcrt
    ENABLE_VIRTUAL_TERMINAL_INPUT, UTF8 = 0x0200, 65001
    # The input mode alone is raw input: no echo, no line editing, and Ctrl+C as a byte.
    ctypes.windll.kernel32.SetConsoleMode(msvcrt.get_osfhandle(0), ENABLE_VIRTUAL_TERMINAL_INPUT)
    ctypes.windll.kernel32.SetConsoleOutputCP(UTF8)
    ctypes.windll.kernel32.SetConsoleCP(UTF8)
else:
    import tty
    tty.setraw(0)
flags = subprocess.CREATE_NO_WINDOW if sys.argv[2:] == ["detached"] else 0
grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], creationflags=flags)
os.write(1, f"ready {grandchild.pid}\r\n".encode())
with open(sys.argv[1], "ab", buffering=0) as log:
    while data := os.read(0, 1024):
        log.write(data)
        if b"?" in data:
            os.write(1, "size {} {}\r\n".format(*os.get_terminal_size(1)).encode())
        if b"q" in data:
            os.write(1, "\x1b]0;✳ done\x07bye\r\n".encode())
            break
"""


class Child:
    """The child in a pseudoterminal, with what it printed so far, read in a thread until the pseudoterminal ends."""

    def __init__(self, tmp_path: Path, *extra: str) -> None:
        self.log = tmp_path / "read.log"
        self.log.touch()
        script = tmp_path / "child.py"
        script.write_text(CHILD, encoding="utf-8")
        args = [sys.executable, str(script), str(self.log), *extra]
        self.term = terminal.spawn(args, tmp_path, dict(os.environ), 80, 24)
        self.output = bytearray()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        wait_for(lambda: b"ready" in self.output)
        self.grandchild = int(bytes(self.output).split(b"ready ")[1].split()[0])

    def _read(self) -> None:
        while data := self.term.read():
            self.output += data

    def type(self, data: bytes) -> None:
        left = memoryview(data)
        while left:
            if written := self.term.write(left):
                left = left[written:]
            else:
                self.term.wait_writable(0.2)

    def end(self) -> None:
        """Stop the child and its grandchild, whatever a test left running, and close the pseudoterminal."""
        for stage, _ in terminal.STOPS:
            if self.term.proc.poll() is not None:
                break
            terminal.stop(self.term.proc, stage)
        self.reader.join(10)
        self.term.close()
        if alive(self.grandchild):
            os.kill(self.grandchild, signal.SIGTERM if sys.platform == "win32" else signal.SIGKILL)


@pytest.fixture
def child(tmp_path: Path) -> Iterator[Child]:
    started = Child(tmp_path)
    yield started
    started.end()


def test_the_process_reads_what_is_typed_byte_for_byte(child: Child) -> None:
    typed = b"\x1b[200~first line\nsecond line \xe2\x9c\xb3\x1b[201~" + b"\x1b" + b"x\x03" + b"\r"
    child.type(typed)
    wait_for(lambda: child.log.read_bytes() == typed)
    child.type(b"q")
    assert child.term.proc.wait(timeout=10) == 0
    child.reader.join(10)
    assert not child.reader.is_alive()


def test_the_reader_sees_the_title_the_process_sets_and_the_end_of_the_output(child: Child) -> None:
    child.type(b"q")
    child.reader.join(10)
    assert not child.reader.is_alive()
    printed = bytes(child.output)
    assert "\x1b]0;✳ done\x07".encode() in printed or "\x1b]2;✳ done\x07".encode() in printed
    assert b"bye" in printed


def test_the_stops_end_the_process_and_the_processes_it_started(child: Child) -> None:
    assert alive(child.grandchild)
    for stage, wait in terminal.STOPS:
        if child.term.proc.poll() is not None:
            break
        terminal.stop(child.term.proc, stage)
        try:
            child.term.proc.wait(timeout=wait)
        except Exception:  # noqa: BLE001 - the next stage follows
            pass
    assert child.term.proc.poll() is not None
    wait_for(lambda: not alive(child.grandchild), timeout=5)
    child.reader.join(10)
    assert not child.reader.is_alive()


def test_a_resize_reaches_the_process(child: Child) -> None:
    child.term.resize(100, 30)
    child.type(b"?")
    wait_for(lambda: b"size 100 30" in child.output)
    child.type(b"q")
    assert child.term.proc.wait(timeout=10) == 0


@pytest.mark.skipif(sys.platform != "win32", reason="The job object of a Windows pseudo console")
def test_closing_the_terminal_ends_a_process_the_command_started_without_a_console(tmp_path: Path) -> None:
    detached = Child(tmp_path, "detached")
    try:
        detached.type(b"q")
        assert detached.term.proc.wait(timeout=10) == 0
        detached.reader.join(10)
        assert alive(detached.grandchild)
        detached.term.close()
        wait_for(lambda: not alive(detached.grandchild), timeout=5)
    finally:
        if alive(detached.grandchild):
            os.kill(detached.grandchild, signal.SIGTERM)
