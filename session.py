"""The Claude Code session of each pull request: one `claude` process in a pseudoterminal, started by the server in
the pull request's worktree with a prompt that makes it listen for the pull request's comments, resumed by id across
starts, shown in the page's terminal over a WebSocket, and ended when the tab closes, when the pull request ends and
when the server exits.

A pull request's session file, sessions/<owner>/<repo>/<number>.json in the cache, holds the session id, the pid of
the last process and whether the id has a conversation to resume. The process's output is kept in a buffer of
BUFFER_MAX bytes, replayed to a terminal that connects, so a reloaded page shows what the session showed. The last
terminal title the process set says whether Claude works and on what task.

Before a start, `trust` records the repository's bare mirror as trusted in Claude Code's config file, so the process
starts without the folder trust dialog: Claude Code keys the trust of a worktree on its repository, which for the
pull request's worktree is the mirror.

The module lock guards the sessions' state and is never held across a read or write of a pseudoterminal or a socket:
a session's own `writes` and `sends` locks order those, so a paste the process does not read yet, or a terminal that
stops reading, holds up that session alone."""

from __future__ import annotations

import fcntl
import json
import os
import re
import select
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import build
import checkout
import claude
import commands
import github
import mcp
from github import PullRef

ROOT = Path(__file__).resolve().parent
PROMPT = ROOT / "prompts" / "session.md"
# The oldest Claude Code the session runs on: the one the MCP transport and the start flags were checked with.
MIN_VERSION = (2, 1, 316)
BUFFER_MAX = 256 * 1024
# A process that ends this soon after a start did not start: its output says why.
QUICK_EXIT = 15
# The variables a server started from a Claude Code session inherits, which would tie the child to that session, put
# it in bare mode, without the user's login, or turn its transcript off, so that nothing could resume it.
DROPPED_VARIABLES = (
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SIMPLE",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_CHILD_SESSION",
)
DROPPED_PREFIXES = ("CLAUDE_AGENT_SDK",)
# Sends bytes of the process's output to one terminal. Raises OSError once the terminal has gone.
Send = Callable[[bytes], None]
NO_CONVERSATION = "No conversation found"
# Claude Code sets the terminal's title to a mark, a space and the task, with NO_TASK in place of the task before it
# has one. The mark is IDLE_MARK while it waits, and a spinner glyph that changes while it works. Claude Code does not
# document the format.
TITLE = re.compile(rb"\x1b\][02];([^\x07\x1b]*)(?:\x07|\x1b\\)")
TITLE_MAX = 1024
IDLE_MARK = "\u2733"
NO_TASK = "Claude Code"
IN_USE = "already in use"
# The models a session can start with, as `--model` takes them.
Model = Literal["claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
DEFAULT_MODEL: Model = "claude-opus-5-5"


@dataclass
class Session:
    """A pull request's session: its id, its process while one runs, what the process printed, and the terminals that
    show it. `starting` is set from a start until the process runs or the start fails; `error` says why the last start
    failed or the last process ended, until the next start. `generation` counts the starts, so a start that finishes
    after a close or a later start is dropped. `writes` is held around every write to the pseudoterminal and its
    close, `sends` around every send to the terminals, and `pastes` around a paste and its Enter, so two pastes never
    mix; all three outside the module lock."""

    ref: PullRef
    id: str = ""
    resume: bool = False
    model: Model = DEFAULT_MODEL
    proc: subprocess.Popen[bytes] | None = None
    fd: int | None = None
    started_at: float = 0.0
    starting: bool = False
    error: str = ""
    generation: int = 0
    output: bytearray = field(default_factory=bytearray)
    title: str = ""
    clients: set[Send] = field(default_factory=set)
    size: tuple[int, int] = (80, 24)
    branch: str = ""
    head: str = ""
    writes: threading.Lock = field(default_factory=threading.Lock)
    sends: threading.Lock = field(default_factory=threading.Lock)
    pastes: threading.Lock = field(default_factory=threading.Lock)


lock = threading.RLock()
sessions: dict[PullRef, Session] = {}


def path_of(ref: PullRef) -> Path:
    return build.CACHE / "sessions" / ref.owner / ref.repo / f"{ref.number}.json"


def kept() -> list[PullRef]:
    """The pull requests with a session file in the cache."""
    files = sorted((build.CACHE / "sessions").glob("*/*/*.json"))
    return [PullRef(f.parent.parent.name, f.parent.name, int(f.stem)) for f in files if f.stem.isdigit()]


def read_file(ref: PullRef) -> dict:
    try:
        data = json.loads(path_of(ref).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_file(ref: PullRef, data: dict) -> None:
    path = path_of(ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def session_of(ref: PullRef) -> Session:
    """The pull request's session, loaded from its file the first time. Call it with the lock held."""
    if ref not in sessions:
        saved = read_file(ref)
        sessions[ref] = Session(ref, id=str(saved.get("id") or ""), resume=bool(saved.get("resume")))
    return sessions[ref]


def binary() -> Path | None:
    """The `claude` to run: the one DIFFER_CLAUDE names, else the first on the PATH, in ~/.local/bin or in
    ~/.claude/local."""
    if named := os.environ.get("DIFFER_CLAUDE"):
        return Path(named)
    if found := shutil.which("claude"):
        return Path(found)
    for candidate in (Path.home() / ".local" / "bin" / "claude", Path.home() / ".claude" / "local" / "claude"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def version_of(claude_bin: Path) -> tuple[int, ...]:
    """The version `claude --version` prints, as numbers; empty when it prints none."""
    text = commands.run([str(claude_bin), "--version"], timeout=30).decode(errors="replace")
    found = re.search(r"\d+(?:\.\d+)+", text)
    return tuple(int(part) for part in found[0].split(".")) if found else ()


def too_old(version: tuple[int, ...]) -> str | None:
    """Why the version cannot run the session, or None."""
    if version < MIN_VERSION:
        shown = ".".join(map(str, version)) or "an unknown version"
        return f"Claude Code {shown} is too old. differ needs {'.'.join(map(str, MIN_VERSION))} or later."
    return None


def environment() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in DROPPED_VARIABLES and not k.startswith(DROPPED_PREFIXES)}
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    return env


def config_path() -> Path:
    """Claude Code's config file, `~/.claude.json`, which CLAUDE_CONFIG_DIR moves with the rest of `~/.claude`. Claude
    Code may read the legacy `~/.claude/.config.json` instead; this path never names that file."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home()) / ".claude.json"


# Held around every change to Claude Code's config file, so two threads never write it at once.
config_lock = threading.Lock()
# How many times `trust` tries to write the config file before it gives up.
TRUST_TRIES = 5


def trust(folder: Path) -> None:
    """Record in Claude Code's config file that the user trusts the folder, the entry Claude Code's docs name as the
    way to trust a folder by hand, unless the file has it already. Writes nothing when the config file is missing, so
    Claude Code still finds it missing and offers its backup. A config file behind a symbolic link is written where
    the link leads. The new file is written next to the old one, with its mode, and takes its place only while the old
    one is as it was when read; otherwise the write starts again from the changed file. Raises ValueError when the file
    is not a JSON object, and OSError when it cannot be read or written, or keeps changing."""
    key = os.path.realpath(folder)
    with config_lock:
        for _ in range(TRUST_TRIES):
            path = config_path().resolve()
            before = _stat(path)
            if before is None:
                return
            data = _read_config(path)
            projects = data.get("projects", {})
            if not isinstance(projects, dict):
                raise ValueError(f"{path} is not the config file Claude Code writes.")
            project = projects.get(key)
            if not isinstance(project, dict):
                project = {}
            if project.get("hasTrustDialogAccepted") is True:
                return
            data["projects"] = {**projects, key: {**project, "hasTrustDialogAccepted": True}}
            fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.differ-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(json.dumps(data, indent=2, ensure_ascii=False))
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(name, stat.S_IMODE(before.st_mode))
                if _same(_stat(path), before):
                    os.replace(name, path)
                    return
            finally:
                Path(name).unlink(missing_ok=True)
        raise OSError(f"{config_path()} kept changing while differ wrote it.")


def _read_config(path: Path) -> dict:
    """The config file's JSON object. Raises ValueError when the file holds anything else."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not the config file Claude Code writes.")
    return data


def _stat(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except FileNotFoundError:
        return None


def _same(a: os.stat_result | None, b: os.stat_result) -> bool:
    """Whether a second look at a file found it unchanged: the same file, with the same size and time."""
    if a is None:
        return False
    return (a.st_ino, a.st_size, a.st_mtime_ns) == (b.st_ino, b.st_size, b.st_mtime_ns)


def system_prompt(ref: PullRef, pr: github.PullRequest, viewer: str) -> str:
    """The shipped prompt with the pull request, its author and the reader filled in."""
    return (
        PROMPT.read_text()
        .replace("{ref}", str(ref))
        .replace("{author}", pr.author or "unknown")
        .replace("{viewer}", viewer or "unknown")
        .replace("{branch}", pr.head_ref)
    )


def listen_prompt(ref: PullRef, port: int, branch: str, head: str) -> str:
    """The prompt the process starts with, which makes it arm its Monitor: the only text the server gives Claude."""
    url = f"ws://127.0.0.1:{port}/api/{ref.slug}/{ref.number}/listen"
    return (
        f"Listen for differ comments on {ref} at {url}. This folder is the pull request's worktree on branch "
        f"{branch}, at head {head[:12]}. Arm the Monitor first, then say in one line that you listen."
    )


def status(ref: PullRef) -> dict:
    """Where the pull request's session stands, for the page: `starting`, `running`, `error`, the branch and the head
    the worktree is on, and from the terminal title, whether Claude is `working` and its `task`, empty without one."""
    with lock:
        s = session_of(ref)
        running = s.proc is not None and s.proc.poll() is None
        working, task = title_state(s.title) if running else (False, "")
        return {
            "starting": s.starting,
            "running": running,
            "error": s.error,
            "branch": s.branch,
            "head": s.head,
            "working": working,
            "task": task,
        }


def title_state(title: str) -> tuple[bool, str]:
    """Whether Claude works, and its task or an empty string, from a terminal title Claude Code set. A title without
    a mark of one symbol, such as one of a later Claude Code that changed the format, reads as waiting without a task."""
    mark, _, task = title.partition(" ")
    if len(mark) != 1 or mark.isalnum():
        return False, ""
    return mark != IDLE_MARK, "" if task == NO_TASK else task


def start(ref: PullRef, port: int, cols: int, rows: int, model: Model = DEFAULT_MODEL) -> dict:
    """Start the pull request's session with the model unless it runs or starts, in a background thread, since the
    first start may clone and check out the repository. Answers `status`."""
    with lock:
        s = session_of(ref)
        if s.starting or (s.proc is not None and s.proc.poll() is None):
            return status(ref)
        s.size = (max(20, cols), max(5, rows))
        s.model = model
        s.starting, s.error = True, ""
        s.generation += 1
        generation = s.generation
    threading.Thread(target=_start, args=(ref, port, generation), daemon=True).start()
    return status(ref)


def _start(ref: PullRef, port: int, generation: int) -> None:
    try:
        claude_bin = binary()
        if claude_bin is None:
            raise RuntimeError("claude is not installed, or not on the PATH. Install Claude Code and try again.")
        if problem := too_old(version_of(claude_bin)):
            raise RuntimeError(problem)
        pr = github.pull_request(ref)
        if pr.status in claude.ENDED:
            raise RuntimeError(f"The pull request is {pr.status}, so it gets no session.")
        viewer = github.viewer_login()
        folder = checkout.worktree(ref, pr.head)
        mirror = build.mirror_of(ref)
        try:
            trust(mirror)
        except (OSError, ValueError) as exc:
            sys.stderr.write(f"Could not record the trust of {mirror}: {exc}\n")
        with lock:
            s = session_of(ref)
            if s.generation != generation:
                return
            s.branch, s.head = pr.head_ref, pr.head
            if not s.id:
                s.id, s.resume = str(uuid.uuid4()), False
            _spawn(s, claude_bin, folder, port, pr, viewer)
    except Exception as exc:  # noqa: BLE001 - whatever failed, the start is over and the header must say so
        with lock:
            s = session_of(ref)
            if s.generation == generation:
                s.starting, s.error = False, str(exc) or type(exc).__name__


def _spawn(s: Session, claude_bin: Path, folder: Path, port: int, pr: github.PullRequest, viewer: str) -> None:
    """Run the process in a new pseudoterminal and read it in a thread. Call it with the lock held."""
    rows, cols = s.size[1], s.size[0]
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    # The master never blocks: the reader waits for output with select, and a write the process does not read yet
    # waits in short steps, so it can stop when the process is gone instead of hanging on to the descriptor.
    os.set_blocking(master, False)
    session_flag = ["--resume", s.id] if s.resume else ["--session-id", s.id]
    args = [
        str(claude_bin),
        *session_flag,
        "--model",
        s.model,
        "--permission-mode",
        "bypassPermissions",
        "--setting-sources",
        "user,project,local",
        "--strict-mcp-config",
        "--mcp-config",
        mcp.config(s.ref, port),
        "--system-prompt-snapshot",
        "off",
        "--append-system-prompt",
        system_prompt(s.ref, pr, viewer),
        listen_prompt(s.ref, port, pr.head_ref, pr.head),
    ]
    try:
        proc = subprocess.Popen(
            args,
            cwd=folder,
            env=environment(),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
            # The call runs in the child between fork and exec, where a lock another thread holds would never open;
            # this one makes one ioctl and takes no lock.
            preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0),
        )
    finally:
        os.close(slave)
    s.proc, s.fd, s.started_at, s.starting = proc, master, time.time(), False
    s.output.clear()
    s.title = ""
    write_file(s.ref, {"id": s.id, "resume": s.resume, "pid": proc.pid})
    threading.Thread(target=_read, args=(s, proc, master, port, s.generation), daemon=True).start()


def _read(s: Session, proc: subprocess.Popen[bytes], fd: int, port: int, generation: int) -> None:
    """Pass the process's output to the buffer and the terminals until the process ends, then tell why it ended. A
    start whose id has no conversation gets a new id and starts again; one whose id is in use already resumes it."""
    while True:
        try:
            select.select([fd], [], [])
            data = os.read(fd, 65536)
        except BlockingIOError:
            continue
        except (OSError, ValueError):
            data = b""
        if not data:
            break
        with lock:
            s.output += data
            if len(s.output) > BUFFER_MAX:
                del s.output[: len(s.output) - BUFFER_MAX]
            # A title can arrive split across two reads, so the search reaches back into what came before.
            if titles := TITLE.findall(s.output, max(0, len(s.output) - len(data) - TITLE_MAX)):
                s.title = titles[-1].decode(errors="replace")
            clients = list(s.clients)
            if not s.resume and time.time() - s.started_at > QUICK_EXIT:
                s.resume = True
                write_file(s.ref, {"id": s.id, "resume": True, "pid": proc.pid})
        _send(s, clients, data)
    code = proc.wait()
    # The descriptor goes once no write holds it, and the session forgets it first, so no write starts on a closed
    # descriptor or on one the system gave to something else since.
    with s.writes:
        with lock:
            if s.fd == fd:
                s.fd = None
        try:
            os.close(fd)
        except OSError:
            pass
    with lock:
        if s.generation != generation or s.proc is not proc:
            return
        s.proc = None
        quick = time.time() - s.started_at < QUICK_EXIT
        shown = s.output.decode(errors="replace")
        again = None
        if quick and s.resume and NO_CONVERSATION in shown:
            s.id, s.resume, again = str(uuid.uuid4()), False, "a new id"
        elif quick and not s.resume and IN_USE in shown:
            s.resume, again = True, "a resume"
        if again is None:
            s.resume = s.resume or not quick
            s.error = f"Claude Code exited with code {code}."
            write_file(s.ref, {"id": s.id, "resume": s.resume, "pid": 0})
            return
        s.starting, s.error = True, ""
        s.generation += 1
        generation = s.generation
        write_file(s.ref, {"id": s.id, "resume": s.resume, "pid": 0})
    sys.stderr.write(f"The session of {s.ref} did not start; trying again with {again}.\n")
    threading.Thread(target=_start, args=(s.ref, port, generation), daemon=True).start()


def _send(s: Session, clients: list[Send], data: bytes) -> None:
    """Send bytes to the terminals, in the order the process printed them, and forget a terminal that has gone."""
    gone = []
    with s.sends:
        for send in clients:
            try:
                send(data)
            except OSError:
                gone.append(send)
    if gone:
        with lock:
            for send in gone:
                s.clients.discard(send)


def attach(ref: PullRef, send: Send) -> None:
    """Show the session in one more terminal: send it what the process printed so far, then every new byte."""
    with lock:
        s = session_of(ref)
    with s.sends:
        with lock:
            printed = bytes(s.output)
            s.clients.add(send)
        if printed:
            send(printed)


def detach(ref: PullRef, send: Send) -> None:
    with lock:
        session_of(ref).clients.discard(send)


# How long a write waits for the process to read before it looks again whether the process is still there.
WRITE_WAIT = 0.2


def write(ref: PullRef, data: bytes) -> None:
    """Type the bytes into the session, if a process runs. Waits while the process has not read what was typed before,
    and stops once the process is gone."""
    with lock:
        s = session_of(ref)
    left = memoryview(bytes(data))
    while left:
        with s.writes:
            with lock:
                fd = s.fd
            if fd is None:
                return
            try:
                left = left[os.write(fd, left) :]
                continue
            except BlockingIOError:
                pass
        try:
            select.select([], [fd], [], WRITE_WAIT)
        except (OSError, ValueError):
            pass


# How long a submit waits after its paste before it presses Enter. Claude Code takes an Enter that arrives soon after
# the paste as part of the paste; 0.1 seconds was not always enough. A Claude Code that stalls for longer still can.
SUBMIT_WAIT = 0.5


def paste(ref: PullRef, text: str, submit: bool = False) -> None:
    """Paste the text into the session's prompt as one bracketed paste, so that it lands where the cursor is and
    nothing in it counts as a key. The text loses its escape characters, so nothing in it can end the paste early.
    With `submit`, press Enter `SUBMIT_WAIT` seconds after it, which sends the prompt with whatever the prompt held
    before, and return after the Enter."""
    with lock:
        s = session_of(ref)
    with s.pastes:
        write(ref, b"\x1b[200~" + text.replace("\x1b", "").encode() + b"\x1b[201~")
        if submit:
            time.sleep(SUBMIT_WAIT)
            write(ref, b"\r")


def resize(ref: PullRef, cols: int, rows: int) -> None:
    """Set the size of the session's terminal. The call never waits, so it runs under the lock, where the descriptor
    stays open: the reader forgets it under the lock before it closes it."""
    with lock:
        s = session_of(ref)
        s.size = (max(20, cols), max(5, rows))
        if s.fd is not None:
            fcntl.ioctl(s.fd, termios.TIOCSWINSZ, struct.pack("HHHH", s.size[1], s.size[0], 0, 0))


# The signals that end a session's process group, each with the time it gets before the next one.
SIGNALS = ((signal.SIGHUP, 3.0), (signal.SIGTERM, 2.0), (signal.SIGKILL, 2.0))


def end(procs: list[subprocess.Popen[bytes]]) -> None:
    """End the processes and their process groups together: a hangup, as a closed terminal gives, then SIGTERM, then
    SIGKILL, each to the processes still running after the previous one had its moment. The reader thread closes the
    pseudoterminal once the process is gone; closing it here, under a read that blocks on it, hangs on macOS."""
    for sig, wait in SIGNALS:
        procs = [proc for proc in procs if proc.poll() is None]
        if not procs:
            return
        for proc in procs:
            try:
                os.killpg(proc.pid, sig)
            except OSError:
                pass
        deadline = time.time() + wait
        for proc in procs:
            try:
                proc.wait(timeout=max(0.0, deadline - time.time()))
            except subprocess.TimeoutExpired:
                pass


def close(ref: PullRef, remove_worktree: bool = True) -> None:
    """End the pull request's session and, by default, remove its worktree. The session file stays, so the next
    start resumes the conversation."""
    with lock:
        proc = _forget(session_of(ref))
    if proc is not None:
        end([proc])
    if remove_worktree:
        checkout.remove_worktree(ref)


def _forget(s: Session) -> subprocess.Popen[bytes] | None:
    """Drop the session's start and its process, for the caller to end, and note in its file that none runs. Call
    it with the lock held."""
    s.generation += 1
    s.starting = False
    proc = s.proc
    s.proc, s.fd = None, None
    if proc is not None:
        write_file(s.ref, {"id": s.id, "resume": s.resume, "pid": 0})
    return proc


def expire(ref: PullRef) -> None:
    """End the pull request's session, delete its worktree and its files, with its comments: the pull request is
    merged or closed."""
    close(ref)
    with lock:
        sessions.pop(ref, None)
        path_of(ref).unlink(missing_ok=True)
    claude.expire(ref)


def sweep() -> None:
    """Expire every pull request with a session file, comments or file descriptions that GitHub reports merged or
    closed."""
    claude.sweep(expire, list(dict.fromkeys(claude.kept() + kept())))


def stop_all() -> None:
    """End every running session at once, keeping the worktrees: the server exits."""
    with lock:
        procs = [proc for s in sessions.values() if (proc := _forget(s)) is not None]
    end(procs)


def reap_orphans() -> None:
    """End the processes of sessions a server that died left behind: a pid a session file names counts only while
    `ps` shows a `claude` with that session id running under it with no parent left, so a pid the system gave to
    another process since, and a session of another server on the same cache, are left alone."""
    for ref in kept():
        saved = read_file(ref)
        pid, session_id = saved.get("pid"), saved.get("id")
        if not isinstance(pid, int) or pid <= 0 or not session_id:
            continue
        shown = (
            commands.call(["ps", "-o", "ppid=,args=", "-p", str(pid)]).stdout.decode(errors="replace").split(None, 1)
        )
        if len(shown) == 2 and shown[0] == "1" and "claude" in shown[1] and session_id in shown[1]:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(pid, sig)
                except OSError:
                    break
                time.sleep(1)
        write_file(ref, {**saved, "pid": 0})
