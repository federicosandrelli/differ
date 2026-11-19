"""A command run in a pseudoterminal of its own, on the system the server runs on: a POSIX pseudoterminal on macOS
and Linux, a ConPTY pseudo console on Windows. `spawn` starts the command and answers its `Terminal`, which reads,
writes, resizes and closes the pseudoterminal; `stop` ends the command together with the processes it started, in the
stages of STOPS.

On Windows each command runs in a job object that ends its processes once the job's last handle closes, so the
processes of a server that died end with it and leave no orphans behind."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

READ_MAX = 65536

# The stages that end a command and the processes it started, each with the seconds it gets before the next one: a
# hangup, as a closed terminal gives, then a request to end, then an end it cannot refuse.
Stop = Literal["hangup", "terminate", "kill"]
STOPS: tuple[tuple[Stop, float], ...] = (("hangup", 3.0), ("terminate", 2.0), ("kill", 2.0))


class Process(Protocol):
    """The command's process, as much of `subprocess.Popen` as the sessions use."""

    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...


class Terminal(Protocol):
    """The pseudoterminal a command runs in. `read` blocks until the command prints and answers b"" once the
    pseudoterminal is gone; `write` takes what the pseudoterminal takes without blocking, which may be nothing, and
    raises OSError once it is gone; `wait_writable` waits up to `timeout` seconds for room to write."""

    @property
    def proc(self) -> Process: ...

    def read(self) -> bytes: ...

    def write(self, data: memoryview) -> int: ...

    def wait_writable(self, timeout: float) -> None: ...

    def resize(self, cols: int, rows: int) -> None: ...

    def close(self) -> None: ...


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _Coord(ctypes.Structure):
        _fields_ = (("X", wintypes.SHORT), ("Y", wintypes.SHORT))

    class _StartupInfo(ctypes.Structure):
        _fields_ = (
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        )

    class _StartupInfoEx(ctypes.Structure):
        _fields_ = (("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p))

    class _ProcessInformation(ctypes.Structure):
        _fields_ = (
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        )

    class _BasicLimits(ctypes.Structure):
        _fields_ = (
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        )

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = (
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", ctypes.c_uint64 * 6),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        )

    def _declare(name: str, restype: object, *argtypes: object) -> Any:
        function = getattr(_kernel32, name)
        function.restype, function.argtypes = restype, argtypes
        return function

    _HANDLE, _DWORD, _BOOL = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL
    _CreatePipe = _declare(
        "CreatePipe", _BOOL, ctypes.POINTER(_HANDLE), ctypes.POINTER(_HANDLE), ctypes.c_void_p, _DWORD
    )
    _CreatePseudoConsole = _declare(
        "CreatePseudoConsole", ctypes.c_long, _Coord, _HANDLE, _HANDLE, _DWORD, ctypes.POINTER(_HANDLE)
    )
    _ResizePseudoConsole = _declare("ResizePseudoConsole", ctypes.c_long, _HANDLE, _Coord)
    _ClosePseudoConsole = _declare("ClosePseudoConsole", None, _HANDLE)
    _InitializeProcThreadAttributeList = _declare(
        "InitializeProcThreadAttributeList", _BOOL, ctypes.c_void_p, _DWORD, _DWORD, ctypes.POINTER(ctypes.c_size_t)
    )
    _UpdateProcThreadAttribute = _declare(
        "UpdateProcThreadAttribute",
        _BOOL,
        ctypes.c_void_p,
        _DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    _DeleteProcThreadAttributeList = _declare("DeleteProcThreadAttributeList", None, ctypes.c_void_p)
    _CreateProcessW = _declare(
        "CreateProcessW",
        _BOOL,
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        _BOOL,
        _DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(_StartupInfo),
        ctypes.POINTER(_ProcessInformation),
    )
    _CreateJobObjectW = _declare("CreateJobObjectW", _HANDLE, ctypes.c_void_p, wintypes.LPCWSTR)
    _SetInformationJobObject = _declare(
        "SetInformationJobObject", _BOOL, _HANDLE, ctypes.c_int, ctypes.c_void_p, _DWORD
    )
    _AssignProcessToJobObject = _declare("AssignProcessToJobObject", _BOOL, _HANDLE, _HANDLE)
    _TerminateJobObject = _declare("TerminateJobObject", _BOOL, _HANDLE, wintypes.UINT)
    _ResumeThread = _declare("ResumeThread", _DWORD, _HANDLE)
    _TerminateProcess = _declare("TerminateProcess", _BOOL, _HANDLE, wintypes.UINT)
    _WaitForSingleObject = _declare("WaitForSingleObject", _DWORD, _HANDLE, _DWORD)
    _GetExitCodeProcess = _declare("GetExitCodeProcess", _BOOL, _HANDLE, ctypes.POINTER(_DWORD))
    _ReadFile = _declare("ReadFile", _BOOL, _HANDLE, ctypes.c_void_p, _DWORD, ctypes.POINTER(_DWORD), ctypes.c_void_p)
    _WriteFile = _declare("WriteFile", _BOOL, _HANDLE, ctypes.c_void_p, _DWORD, ctypes.POINTER(_DWORD), ctypes.c_void_p)
    _CloseHandle = _declare("CloseHandle", _BOOL, _HANDLE)
    _OpenProcess = _declare("OpenProcess", _HANDLE, _DWORD, _BOOL, _DWORD)

    _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
    _EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    _CREATE_UNICODE_ENVIRONMENT = 0x00000400
    _CREATE_SUSPENDED = 0x00000004
    _STARTF_USESTDHANDLES = 0x00000100
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _INFINITE = 0xFFFFFFFF
    _SYNCHRONIZE = 0x00100000
    _ERROR_INVALID_PARAMETER = 87
    _WAIT_OBJECT_0 = 0
    # The longest a wait holds the process's lock, so another thread's wait or poll is not held up behind it.
    _WAIT_SLICE = 0.1

    def _check(ok: object, what: str) -> None:
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error(), f"{what} failed")

    class _Console:
        """The pseudo console and the job of one command, each closed once, by whoever comes first. Closing the
        console sends its processes CTRL_CLOSE_EVENT, as closing a console window does. Ending the job ends every
        process the command started, also those with no console of their own, which the console's close misses."""

        def __init__(self, console: int, job: int) -> None:
            self.console, self.job = console, job
            self.lock = threading.Lock()

        def close_console(self) -> None:
            with self.lock:
                console, self.console = self.console, 0
            if console:
                _ClosePseudoConsole(console)

        def terminate(self) -> None:
            with self.lock:
                if self.job:
                    _TerminateJobObject(self.job, 1)

        def end_job(self) -> None:
            with self.lock:
                job, self.job = self.job, 0
                if job:
                    _TerminateJobObject(job, 1)
                    _CloseHandle(job)

    class _WindowsProcess:
        """The command's process, as `Process` answers it, from the handle CreateProcessW gave, which it closes once
        it has the exit code."""

        def __init__(self, args: Sequence[str], handle: int, pid: int, console: _Console) -> None:
            self.args, self.handle, self._pid, self.console = list(args), handle, pid, console
            self.returncode: int | None = None
            self.lock = threading.Lock()

        @property
        def pid(self) -> int:
            return self._pid

        def poll(self) -> int | None:
            return self._wait(0)

        def wait(self, timeout: float | None = None) -> int:
            deadline = None if timeout is None else time.monotonic() + timeout
            while True:
                left = _WAIT_SLICE if deadline is None else min(_WAIT_SLICE, max(0.0, deadline - time.monotonic()))
                if (code := self._wait(int(left * 1000))) is not None:
                    return code
                if deadline is not None and time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(self.args, timeout or 0)

        def _wait(self, milliseconds: int) -> int | None:
            with self.lock:
                if self.returncode is None and _WaitForSingleObject(self.handle, milliseconds) == _WAIT_OBJECT_0:
                    code = _DWORD()
                    _check(_GetExitCodeProcess(self.handle, ctypes.byref(code)), "GetExitCodeProcess")
                    self.returncode = code.value
                    _CloseHandle(self.handle)
                return self.returncode

    class _WindowsTerminal:
        """A ConPTY pseudo console. A pseudo console keeps its output pipe open after its process exits, so a thread
        closes it once the process is gone, and the reader then finds the pipe's end."""

        def __init__(self, proc: _WindowsProcess, console: _Console, output: int, input: int) -> None:
            self.proc, self.console, self.output, self.input = proc, console, output, input
            threading.Thread(target=self._close_when_done, daemon=True).start()

        def _close_when_done(self) -> None:
            self.proc.wait()
            self.console.close_console()

        def read(self) -> bytes:
            buffer = ctypes.create_string_buffer(READ_MAX)
            count = _DWORD()
            if not _ReadFile(self.output, buffer, READ_MAX, ctypes.byref(count), None):
                return b""
            return buffer.raw[: count.value]

        def write(self, data: memoryview) -> int:
            """A pipe write blocks only while the pipe is full, and the pseudo console reads its input pipe whether
            or not the process reads what it typed, so this waits no longer than the console takes to read."""
            count = _DWORD()
            chunk = bytes(data)
            _check(_WriteFile(self.input, chunk, len(chunk), ctypes.byref(count), None), "WriteFile")
            return count.value

        def wait_writable(self, timeout: float) -> None:
            """Never needed: a write here blocks instead of answering 0."""

        def resize(self, cols: int, rows: int) -> None:
            with self.console.lock:
                if self.console.console:
                    _ResizePseudoConsole(self.console.console, _Coord(cols, rows))

        def close(self) -> None:
            """Also ends the processes the command started that are still there, as the end of a POSIX session's
            terminal does. The pipes close before the console: before Windows 11 24H2, closing a console waits for its
            last output to be read, which nothing reads once the reader has stopped."""
            self.console.end_job()
            with self.console.lock:
                pipes, self.output, self.input = (self.output, self.input), 0, 0
            for handle in pipes:
                if handle:
                    _CloseHandle(handle)
            self.console.close_console()

    def _environment_block(env: Mapping[str, str]) -> ctypes.Array[ctypes.c_wchar]:
        """The environment as CreateProcessW takes it: NAME=value strings sorted by name without regard to case,
        each ended by a NUL, and one more NUL at the end."""
        entries = "".join(f"{k}={v}\0" for k, v in sorted(env.items(), key=lambda item: item[0].upper()))
        return ctypes.create_unicode_buffer(entries + "\0")

    def spawn(args: Sequence[str], cwd: Path, env: Mapping[str, str], cols: int, rows: int) -> Terminal:
        """Run the command in a new pseudo console of the size. Raises OSError when it cannot start."""
        to_console_read, to_console_write = _HANDLE(), _HANDLE()
        from_console_read, from_console_write = _HANDLE(), _HANDLE()
        _check(_CreatePipe(ctypes.byref(to_console_read), ctypes.byref(to_console_write), None, 0), "CreatePipe")
        _check(_CreatePipe(ctypes.byref(from_console_read), ctypes.byref(from_console_write), None, 0), "CreatePipe")
        console = _HANDLE()
        result = _CreatePseudoConsole(_Coord(cols, rows), to_console_read, from_console_write, 0, ctypes.byref(console))
        # The pseudo console holds its own copies of the ends it was given.
        _CloseHandle(to_console_read)
        _CloseHandle(from_console_write)
        if result != 0:
            _CloseHandle(to_console_write)
            _CloseHandle(from_console_read)
            raise OSError(f"CreatePseudoConsole failed with {result:#x}")
        size = ctypes.c_size_t()
        _InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        attributes = ctypes.create_string_buffer(size.value)
        job = 0
        initialized = False
        try:
            _check(_InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)), "InitializeAttributes")
            initialized = True
            # The attribute's value is the console handle itself, not a pointer to it.
            _check(
                _UpdateProcThreadAttribute(
                    attributes,
                    0,
                    _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                    console,
                    ctypes.sizeof(_HANDLE),
                    None,
                    None,
                ),
                "UpdateProcThreadAttribute",
            )
            info = _StartupInfoEx()
            info.StartupInfo.cb = ctypes.sizeof(_StartupInfoEx)
            info.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
            # Empty standard handles, so a process started from a server whose own are redirected, as under the
            # desktop app or pytest, reads and writes the pseudo console instead of inheriting the server's.
            info.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
            process = _ProcessInformation()
            command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(args))
            _check(
                _CreateProcessW(
                    None,
                    command_line,
                    None,
                    None,
                    False,
                    _EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT | _CREATE_SUSPENDED,
                    _environment_block(env),
                    str(cwd),
                    ctypes.byref(info.StartupInfo),
                    ctypes.byref(process),
                ),
                f"Starting {args[0]}",
            )
        except OSError:
            # The output pipe goes first: closing the console writes a last frame to it, and waits until it is read
            # or the pipe is gone.
            _CloseHandle(from_console_read)
            _CloseHandle(to_console_write)
            _ClosePseudoConsole(console)
            raise
        finally:
            if initialized:
                _DeleteProcThreadAttributeList(attributes)
        try:
            job = _CreateJobObjectW(None, None)
            _check(job, "CreateJobObjectW")
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            _check(
                _SetInformationJobObject(
                    job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
                ),
                "SetInformationJobObject",
            )
            # The process joins the job before it runs, so every process it starts is in the job too.
            _check(_AssignProcessToJobObject(job, process.hProcess), "AssignProcessToJobObject")
        except OSError:
            _TerminateProcess(process.hProcess, 1)
            for handle in (process.hThread, process.hProcess, job, to_console_write, from_console_read):
                if handle:
                    _CloseHandle(handle)
            _ClosePseudoConsole(console)
            raise
        _ResumeThread(process.hThread)
        _CloseHandle(process.hThread)
        shared = _Console(console.value or 0, job)
        proc = _WindowsProcess(args, process.hProcess, process.dwProcessId, shared)
        return _WindowsTerminal(proc, shared, from_console_read.value or 0, to_console_write.value or 0)

    def stop(proc: Process, stage: Stop) -> None:
        """The hangup closes the pseudo console; the job ends the processes that outlive it. A Windows console process
        gets no request to end other than the console's close."""
        if not isinstance(proc, _WindowsProcess):
            return
        if stage == "hangup":
            proc.console.close_console()
        else:
            proc.console.terminate()

    def wait_for_parent(parent: int) -> None:
        """Return once the process with this pid has ended. Windows never hands an orphan to a new parent, so the
        parent pid alone would not change."""
        handle = _OpenProcess(_SYNCHRONIZE, False, parent)
        if not handle:
            if ctypes.get_last_error() == _ERROR_INVALID_PARAMETER:
                return  # It has already ended.
            _check(handle, "OpenProcess")
        try:
            _WaitForSingleObject(handle, _INFINITE)
        finally:
            _CloseHandle(handle)

else:
    import fcntl
    import select
    import signal
    import struct
    import termios

    _SIGNALS: dict[Stop, signal.Signals] = {
        "hangup": signal.SIGHUP,
        "terminate": signal.SIGTERM,
        "kill": signal.SIGKILL,
    }

    class _PosixTerminal:
        """A POSIX pseudoterminal's master. It never blocks: the reader waits for output with select, and a write the
        process does not read yet waits in short steps, so it can stop when the process is gone instead of hanging on
        to the descriptor."""

        def __init__(self, proc: subprocess.Popen[bytes], fd: int) -> None:
            self.proc, self.fd = proc, fd

        def read(self) -> bytes:
            while True:
                try:
                    select.select([self.fd], [], [])
                    return os.read(self.fd, READ_MAX)
                except BlockingIOError:
                    continue
                except (OSError, ValueError):
                    return b""

        def write(self, data: memoryview) -> int:
            try:
                return os.write(self.fd, data)
            except BlockingIOError:
                return 0

        def wait_writable(self, timeout: float) -> None:
            try:
                select.select([], [self.fd], [], timeout)
            except (OSError, ValueError):
                pass

        def resize(self, cols: int, rows: int) -> None:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

        def close(self) -> None:
            try:
                os.close(self.fd)
            except OSError:
                pass

    def spawn(args: Sequence[str], cwd: Path, env: Mapping[str, str], cols: int, rows: int) -> Terminal:
        """Run the command in a new pseudoterminal of the size, as the leader of a session of its own. Raises OSError
        when it cannot start."""
        master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        os.set_blocking(master, False)
        try:
            proc = subprocess.Popen(
                list(args),
                cwd=cwd,
                env=dict(env),
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
                # The call runs in the child between fork and exec, where a lock another thread holds would never
                # open; this one makes one ioctl and takes no lock.
                preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0),
            )
        except OSError:
            os.close(master)
            raise
        finally:
            os.close(slave)
        return _PosixTerminal(proc, master)

    def stop(proc: Process, stage: Stop) -> None:
        """Signal the process group the process leads."""
        try:
            os.killpg(proc.pid, _SIGNALS[stage])
        except OSError:
            pass

    def wait_for_parent(parent: int) -> None:
        """Return once the parent process has ended, checked once a second: its orphan then has another parent."""
        while os.getppid() == parent:
            time.sleep(1)
