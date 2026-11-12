"""The Claude Code session of each pull request: one `claude` process in a pseudoterminal, started by the server in
the pull request's worktree with a prompt that makes it listen for the pull request's comments, resumed by id across
starts, shown in the page's terminal over a WebSocket, and ended when the tab closes and when the server exits. Once
the pull request is merged or closed and the reader has not opened it for KEEP_ENDED, `sweep` deletes the session's
file and worktree, unless the session runs or starts.

A pull request's session file, its `records.SESSIONS` record, holds the session id, the pid of the last process and
whether the id has a conversation to resume. The process's output is kept in a buffer of BUFFER_MAX bytes, replayed
to a terminal that connects, so a reloaded page shows what the session showed. The last terminal title the process
set says whether Claude works and on what task. Claude Code's hooks, which the process posts to the pull request's
`hook` endpoint, say whether Claude asks the reader a question.

Address with Claude puts a review thread in the session's queue of addresses (`address`). The server pastes the first
one only while Claude waits at its prompt, so each address reads as `queued` until the title shows Claude working on
it, then `working` until Claude waits again.

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
import unicodedata
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import build
import checkout
import claude
import commands
import github
import mcp
import records
from github import PullRef

ROOT = Path(__file__).resolve().parent
PROMPT = ROOT / "prompts" / "session.md"
# What the shipped prompt says about the worktree, of an open or draft pull request and of a merged or closed one.
ON_BRANCH = (
    "This folder is the pull request's worktree on branch {branch}, made from differ's mirror of the repository: commit "
    "and push from it as you would from any clone, and follow this repository's own rules for commits and pushes."
)
DETACHED = (
    "The pull request is {status}. This folder is its worktree at its head without a branch, since GitHub may have "
    "deleted the branch: read the code and answer, but commit and push nothing."
)
# The oldest Claude Code the session runs on: the one the MCP transport, the start flags and the hooks were checked
# with.
MIN_VERSION = (2, 1, 319)
BUFFER_MAX = 256 * 1024
# A process that ends this soon after a start did not start: its output says why.
QUICK_EXIT = 15
# How long a merged or closed pull request keeps its session and its comments after the reader last opened it.
KEEP_ENDED = 3 * 24 * 60 * 60
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
# The tool Claude Code shows the reader a question with. Its PreToolUse hook fires as the question shows and its
# PostToolUse hook once the reader answers. No hook fires when the reader cancels the question with CANCEL_KEY,
# Escape, which the terminal sends as this byte alone, and Claude then waits at its prompt. Ctrl+C leaves the question
# open.
QUESTION_TOOL = "AskUserQuestion"
CANCEL_KEY = b"\x1b"
# The seconds Claude Code waits for the server to answer a hook before it goes on without it.
HOOK_TIMEOUT = 5
# The models a session can start with, as `--model` takes them.
Model = Literal["claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
DEFAULT_MODEL: Model = "claude-opus-5-5"
# What a start found missing for the session to run: no `claude`, or one older than MIN_VERSION. The terminal offers to
# fix it (`install`).
Setup = Literal["", "missing", "old"]
# The command that installs Claude Code where `binary` looks first after the PATH, ~/.local/bin: the native installer
# Claude Code's docs give. A `claude` that is too old updates itself with UPDATE_ARGS.
INSTALL_COMMAND = ["bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"]
UPDATE_ARGS = ["update"]
# The seconds an address that was pasted waits for the title to show Claude working, before it leaves the queue: a
# title of another shape never shows it.
ADDRESS_START_WAIT = 20.0
# The seconds a paste of an address waits for the terminal to settle before it goes, such as for a CANCEL_KEY that was
# typed as Claude turned to wait, after which it looks again whether the address may go.
ADDRESS_SETTLE = 0.5


def setup_command(setup: Setup) -> str:
    """The command the reader can run by hand to fix what a start found missing; empty when nothing is."""
    if setup == "missing":
        return INSTALL_COMMAND[-1]
    return " ".join(["claude", *UPDATE_ARGS]) if setup == "old" else ""


class SetupNeeded(RuntimeError):
    """A start that cannot run until the reader installs or updates Claude Code."""

    def __init__(self, setup: Setup, message: str) -> None:
        super().__init__(message)
        self.setup: Setup = setup


@dataclass(eq=False)
class Address:
    """A review thread the reader had Claude address: the thread's id, the prompt, and its `stage`: `queued` until its
    paste is under way, `settling` while the paste waits ADDRESS_SETTLE and goes, `sent` once its Enter went, and
    `started` once the title shows Claude working after that. Two addresses are the same only when they are one
    object."""

    thread: str
    text: str
    stage: Literal["queued", "settling", "sent", "started"] = "queued"


@dataclass
class Session:
    """A pull request's session: its id, its process while one runs, what the process printed, and the terminals that
    show it. `starting` is set from a start until the process runs or the start fails; `error` says why the last start
    failed or the last process ended, until the next start. `asking` is set while Claude waits for the reader's answer
    to a question. `setup` says what the last start found missing, until a start gets past it, and `installing` is set
    while `proc` installs or updates Claude Code instead of running it. `addresses` is the queue of the process's
    addresses, oldest first, and `typed` is set while the reader has typed into the terminal since their last Enter, so
    no paste of an address submits what they typed. `working_since` is the time the title last turned to Claude working,
    as `claude.now()` gives it, while it shows Claude working. `generation` counts the starts, so a start that finishes
    after a close or a later start is dropped. `held` is a prompt that a differ:// link brought, until it goes into
    the session's prompt or the session closes. `writes` is held around every write to the pseudoterminal and its close,
    `sends` around every send to the terminals, and `pastes` around a paste and its Enter, so two pastes never mix; all
    three outside the module lock."""

    ref: PullRef
    id: str = ""
    resume: bool = False
    model: Model = DEFAULT_MODEL
    proc: subprocess.Popen[bytes] | None = None
    fd: int | None = None
    started_at: float = 0.0
    starting: bool = False
    error: str = ""
    setup: Setup = ""
    installing: bool = False
    generation: int = 0
    output: bytearray = field(default_factory=bytearray)
    title: str = ""
    asking: bool = False
    typed: bool = False
    working_since: str = ""
    addresses: list[Address] = field(default_factory=list)
    clients: set[Send] = field(default_factory=set)
    size: tuple[int, int] = (80, 24)
    branch: str = ""
    head: str = ""
    held: str = ""
    writes: threading.Lock = field(default_factory=threading.Lock)
    sends: threading.Lock = field(default_factory=threading.Lock)
    pastes: threading.Lock = field(default_factory=threading.Lock)


lock = threading.RLock()
sessions: dict[PullRef, Session] = {}


def session_of(ref: PullRef) -> Session:
    """The pull request's session, loaded from its file the first time. Call it with the lock held."""
    if ref not in sessions:
        saved = records.SESSIONS.read(ref, dict)
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
    """The shipped prompt with the pull request, its author, the reader and its worktree filled in."""
    worktree = DETACHED if pr.status in claude.ENDED else ON_BRANCH
    return (
        PROMPT.read_text()
        .replace("{ref}", str(ref))
        .replace("{author}", pr.author or "unknown")
        .replace("{viewer}", viewer or "unknown")
        .replace("{worktree}", worktree.format(branch=pr.head_ref, status=pr.status))
    )


def listen_prompt(ref: PullRef, port: int, pr: github.PullRequest) -> str:
    """The prompt the process starts with, which makes it arm its Monitor: the only text the server gives Claude."""
    url = f"ws://127.0.0.1:{port}/api/{ref.slug}/{ref.number}/listen"
    where = "without a branch" if pr.status in claude.ENDED else f"on branch {pr.head_ref}"
    return (
        f"Listen for differ comments on {ref} at {url}. This folder is the pull request's worktree {where}, at head "
        f"{pr.head[:12]}. Arm the Monitor first, then say in one line that you listen."
    )


def status(ref: PullRef) -> dict:
    """Where the pull request's session stands, for the page: `starting`, `running`, `error`, the branch and the head
    the worktree is on, from the terminal title, whether Claude is `working` and its `task`, empty without one, and
    whether Claude is `asking` the reader a question; `addresses`, the threads in the queue of addresses by id, each
    `queued` or `working`; `working_since`, the time the title turned to Claude working, empty while Claude is not;
    `setup`, what the last start found missing, with the `command` that fixes it by hand, and `installing`, while the
    terminal installs or updates Claude Code, which is not `running`."""
    with lock:
        s = session_of(ref)
        alive = s.proc is not None and s.proc.poll() is None
        running = alive and not s.installing
        working, task = title_state(s.title) if running else (False, "")
        return {
            "starting": s.starting,
            "running": running,
            "error": s.error,
            "branch": s.branch,
            "head": s.head,
            "working": working,
            "task": task,
            "asking": running and s.asking,
            "working_since": s.working_since if working else "",
            "addresses": {a.thread: "working" if a.stage == "started" else "queued" for a in s.addresses}
            if running
            else {},
            "held": bool(s.held),
            "setup": s.setup,
            "command": setup_command(s.setup),
            "installing": alive and s.installing,
        }


def hook_settings(ref: PullRef, port: int) -> str:
    """The `--settings` JSON that has the process post the hooks of QUESTION_TOOL to the pull request's endpoint."""
    url = f"http://127.0.0.1:{port}/api/{ref.slug}/{ref.number}/hook"
    handler = {
        "type": "http",
        "url": url,
        "headers": {"Authorization": f"Bearer {mcp.token(ref)}"},
        "timeout": HOOK_TIMEOUT,
    }
    matcher = [{"matcher": QUESTION_TOOL, "hooks": [handler]}]
    return json.dumps({"hooks": {"PreToolUse": matcher, "PostToolUse": matcher, "PostToolUseFailure": matcher}})


def hook(ref: PullRef, event: dict) -> None:
    """Take a hook the pull request's process posted: Claude asks a question from QUESTION_TOOL's PreToolUse hook on,
    until any other hook. The end of a question may let the next address go."""
    with lock:
        s = session_of(ref)
        s.asking = event.get("hook_event_name") == "PreToolUse"
        if not s.asking:
            _next_address(s)


def address(ref: PullRef, thread: str, text: str) -> dict:
    """Put the review thread in the queue of addresses, with the prompt that has Claude address it, unless it is in the
    queue already, in which case its prompt becomes this one while it is still `queued`, and paste the first address if Claude
    waits for one. Answers the session's status."""
    with lock:
        s = session_of(ref)
        queued = next((a for a in s.addresses if a.thread == thread), None)
        if queued is None:
            s.addresses.append(Address(thread, text))
        elif queued.stage == "queued":
            queued.text = text
        _next_address(s)
    return status(ref)


def _next_address(s: Session) -> None:
    """Paste the first address in the queue, in a thread of its own, while the process runs, Claude waits at its
    prompt, the reader has typed nothing since their last Enter and no address before it waits for Claude or has Claude
    working. The address is `settling` from then on, so no second paste of it starts. Call it with the lock held."""
    if not s.addresses or s.addresses[0].stage != "queued" or not _may_paste(s):
        return
    first = s.addresses[0]
    first.stage = "settling"
    threading.Thread(target=_paste_address, args=(s, first), daemon=True).start()


def _may_paste(s: Session) -> bool:
    """Whether an address may go now: the process runs, Claude waits at its prompt and the reader has typed nothing
    since their last Enter. Call it with the lock held."""
    return s.proc is not None and not s.installing and not s.asking and not s.typed and not title_state(s.title)[0]


def _paste_address(s: Session, a: Address) -> None:
    """Paste the address once the terminal settled, if it is still the first and may still go, and give Claude
    ADDRESS_START_WAIT seconds from its Enter to start on it. Otherwise it is `queued` again and goes on a later turn
    of the title to waiting, or a later Enter or end of a question. The paste takes `pastes` before it settles, so no
    quick prompt slips in between the look and the paste. Once the Enter went the address is `sent`, or `started` when
    the title already shows Claude working, since Claude Code takes a prompt submitted while it works into that work."""
    with s.pastes:
        time.sleep(ADDRESS_SETTLE)
        with lock:
            if not (s.addresses and s.addresses[0] is a and _may_paste(s)):
                a.stage = "queued"
                return
        _paste(s, a.text, True)
        with lock:
            a.stage = "started" if title_state(s.title)[0] else "sent"
    timer = threading.Timer(ADDRESS_START_WAIT, _address_unstarted, args=(s, a))
    timer.daemon = True
    timer.start()


def _address_unstarted(s: Session, a: Address) -> None:
    """Drop an address whose paste the title never showed Claude working on, and paste the next."""
    with lock:
        if a in s.addresses and a.stage == "sent":
            s.addresses.remove(a)
            _next_address(s)


def _title_turned(s: Session, working: bool) -> None:
    """Move the queue of addresses on when the title turns: an address that was `sent` has Claude working on it once
    the title shows Claude working, and leaves once Claude waits again, unless it waits for the reader's answer to a
    question. A turn to working also sets `working_since`. Call it with the lock held."""
    first = s.addresses[0] if s.addresses else None
    if working:
        s.working_since = claude.now()
        if first and first.stage == "sent":
            first.stage = "started"
        return
    if first and first.stage == "started" and not s.asking:
        s.addresses.pop(0)
    _next_address(s)


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


def install(ref: PullRef, port: int, cols: int, rows: int) -> dict:
    """Install Claude Code, or update the one a start found too old, in the session's pseudoterminal, so the reader
    sees it in the terminal and can answer it, unless a process runs or starts; once it succeeds, start the session.
    With nothing to install, as for a page that has not seen the last start or once the reader installed Claude Code
    by hand, it starts the session. Answers `status`.
    Raises RuntimeError when the command cannot run."""
    with lock:
        s = session_of(ref)
        if s.starting or (s.proc is not None and s.proc.poll() is None):
            return status(ref)
        if not s.setup or (s.setup == "missing" and binary() is not None):
            return start(ref, port, cols, rows, s.model)
        s.size = (max(20, cols), max(5, rows))
        s.error = ""
        s.generation += 1
        claude_bin = binary()
        args = [str(claude_bin), *UPDATE_ARGS] if claude_bin is not None and s.setup == "old" else INSTALL_COMMAND
        try:
            proc, master = _open(s, args, Path.home())
        except OSError as exc:
            raise RuntimeError(f"Could not run {args[0]}: {exc}") from exc
        s.installing = True
        threading.Thread(target=_read_install, args=(s, proc, master, port, s.generation), daemon=True).start()
        return status(ref)


def _read_install(s: Session, proc: subprocess.Popen[bytes], fd: int, port: int, generation: int) -> None:
    """Relay the install to the terminals until it ends, then start the session, or say that it failed: its output
    in the terminal says why."""
    code = _relay(s, proc, fd, claude_code=False)
    with lock:
        if s.generation != generation or s.proc is not proc:
            return
        s.proc, s.installing = None, False
        if code != 0:
            s.error = (
                f"The {'update' if s.setup == 'old' else 'install'} ended with code {code}. The terminal shows why."
            )
            return
        s.starting, s.error = True, ""
        s.generation += 1
        generation = s.generation
    threading.Thread(target=_start, args=(s.ref, port, generation), daemon=True).start()


def _start(ref: PullRef, port: int, generation: int) -> None:
    try:
        claude_bin = binary()
        if claude_bin is None:
            raise SetupNeeded("missing", "Claude Code is not installed, or not on the PATH.")
        if problem := too_old(version_of(claude_bin)):
            raise SetupNeeded("old", problem)
        with lock:
            session_of(ref).setup = ""
        with ThreadPoolExecutor(max_workers=1) as pool:
            viewer_read = pool.submit(github.viewer_login)
            pr = github.pull_request(ref)
            folder = checkout.worktree(ref, pr.head, pr.head_ref, pr.from_fork, detached=pr.status in claude.ENDED)
            mirror = build.mirror_of(ref)
            try:
                trust(mirror)
            except (OSError, ValueError) as exc:
                sys.stderr.write(f"Could not record the trust of {mirror}: {exc}\n")
            viewer = viewer_read.result()
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
                if isinstance(exc, SetupNeeded):
                    s.setup = exc.setup


def _spawn(s: Session, claude_bin: Path, folder: Path, port: int, pr: github.PullRequest, viewer: str) -> None:
    """Run Claude Code in a new pseudoterminal and read it in a thread. Call it with the lock held."""
    session_flag = ["--resume", s.id] if s.resume else ["--session-id", s.id]
    args = [
        str(claude_bin),
        *session_flag,
        "--model",
        s.model,
        "--setting-sources",
        "user,project,local",
        "--strict-mcp-config",
        "--mcp-config",
        mcp.config(s.ref, port),
        "--settings",
        hook_settings(s.ref, port),
        "--system-prompt-snapshot",
        "off",
        "--append-system-prompt",
        system_prompt(s.ref, pr, viewer),
        listen_prompt(s.ref, port, pr),
    ]
    proc, master = _open(s, args, folder)
    s.installing = False
    records.SESSIONS.write(s.ref, {"id": s.id, "resume": s.resume, "pid": proc.pid})
    threading.Thread(target=_read, args=(s, proc, master, port, s.generation), daemon=True).start()


def _open(s: Session, args: list[str], folder: Path) -> tuple[subprocess.Popen[bytes], int]:
    """Run the command in a new pseudoterminal of the session's size, as the session's process, with a clean screen
    for the terminals that connect later. Answers the process and the pseudoterminal's master. Call it with the lock
    held."""
    rows, cols = s.size[1], s.size[0]
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    # The master never blocks: the reader waits for output with select, and a write the process does not read yet
    # waits in short steps, so it can stop when the process is gone instead of hanging on to the descriptor.
    os.set_blocking(master, False)
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
    except OSError:
        os.close(master)
        raise
    finally:
        os.close(slave)
    s.proc, s.fd, s.started_at, s.starting = proc, master, time.time(), False
    s.output.clear()
    s.title, s.asking, s.typed, s.working_since = "", False, False, ""
    s.addresses.clear()
    return proc, master


def _relay(s: Session, proc: subprocess.Popen[bytes], fd: int, claude_code: bool) -> int:
    """Pass the process's output to the buffer and the terminals until the process ends, and answer its exit code.
    Only Claude Code's output sets the title, and keeps its conversation for a resume once it has run QUICK_EXIT."""
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
            # A title that turns from waiting to working ends a question, as a backstop for a way out of it that posts
            # no hook. Only the turn counts, since the hook that starts a question can arrive before the last title of
            # the work that led to it.
            if claude_code and (titles := TITLE.findall(s.output, max(0, len(s.output) - len(data) - TITLE_MAX))):
                title = titles[-1].decode(errors="replace")
                working, was = title_state(title)[0], title_state(s.title)[0]
                if working and not was:
                    s.asking = False
                s.title = title
                if working != was:
                    _title_turned(s, working)
            clients = list(s.clients)
            if claude_code and not s.resume and time.time() - s.started_at > QUICK_EXIT:
                s.resume = True
                records.SESSIONS.write(s.ref, {"id": s.id, "resume": True, "pid": proc.pid})
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
    return code


def _read(s: Session, proc: subprocess.Popen[bytes], fd: int, port: int, generation: int) -> None:
    """Pass the process's output to the buffer and the terminals until the process ends, then tell why it ended. A
    start whose id has no conversation gets a new id and starts again; one whose id is in use already resumes it."""
    code = _relay(s, proc, fd, claude_code=True)
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
            records.SESSIONS.write(s.ref, {"id": s.id, "resume": s.resume, "pid": 0})
            return
        s.starting, s.error = True, ""
        s.generation += 1
        generation = s.generation
        records.SESSIONS.write(s.ref, {"id": s.id, "resume": s.resume, "pid": 0})
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
    """Type the bytes into the session, if a process runs, and end a question when they are CANCEL_KEY.
    Waits while the process has not read what was typed before, and stops once the process is gone."""
    with lock:
        s = session_of(ref)
        cancel = data == CANCEL_KEY and s.asking
        if cancel:
            s.asking = False
    _write(s, data)
    if cancel:
        # Claude waits at its prompt once the reader cancels its question, done with the address it worked on. The
        # queue moves on only after the Escape went, so the next paste never lands before it.
        with lock:
            if not title_state(s.title)[0]:
                _title_turned(s, False)


def type_in(ref: PullRef, data: bytes) -> None:
    """Type what the reader typed in the terminal into the session, as `write` does, and note whether they left
    something unsent at the prompt (`typed`). The terminal sends each key as typed, escape sequences included: a
    bracketed paste of the reader's sets `typed`; otherwise an Enter clears it, as does Ctrl+C, on which Claude Code
    empties its prompt; keys that are only control bytes or escape sequences, such as Escape, Backspace or the arrows,
    leave it as it is; and printable text sets it. Once `typed` clears, the next address may go."""
    with lock:
        s = session_of(ref)
        if b"\x1b[200~" in data:
            s.typed = True
        elif b"\r" in data or b"\x03" in data:
            s.typed = False
            _next_address(s)
        elif not (data.startswith(b"\x1b") or all(b < 0x20 or b == 0x7F for b in data)):
            s.typed = True
    write(ref, data)


def _write(s: Session, data: bytes) -> None:
    """Write the bytes to the session's pseudoterminal, waiting while the process has not read what came before."""
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
HELD_MAX = 500


def paste(ref: PullRef, text: str, submit: bool = False) -> None:
    """Paste the text into the session's prompt as one bracketed paste, so that it lands where the cursor is and
    nothing in it counts as a key. The text loses its escape characters, so nothing in it can end the paste early.
    With `submit`, press Enter `SUBMIT_WAIT` seconds after it, which sends the prompt with whatever the prompt held
    before, and return after the Enter. A submit clears `typed`, since the Enter sends what the reader typed too, and
    lets the next address go."""
    with lock:
        s = session_of(ref)
    with s.pastes:
        _paste(s, text, submit)
    if submit:
        with lock:
            s.typed = False
            _next_address(s)


def _paste(s: Session, text: str, submit: bool) -> None:
    """Paste the text as `paste` does. Call it with the session's `pastes` held, and the lock not held."""
    write(s.ref, b"\x1b[200~" + text.replace("\x1b", "").encode() + b"\x1b[201~")
    if submit:
        time.sleep(SUBMIT_WAIT)
        write(s.ref, b"\r")


def hold(ref: PullRef, text: str) -> None:
    """Keep the text for `paste_held`, in place of any text held before, as one line the reader can read whole: line
    breaks and tabs become spaces, other control and invisible format characters go, and the text stops at
    HELD_MAX characters, short of the length at which Claude Code shows a paste as a placeholder."""
    line = "".join(" " if c in "\t\n\r" else c for c in text if c in "\t\n\r" or unicodedata.category(c)[0] != "C")
    with lock:
        session_of(ref).held = " ".join(line.split())[:HELD_MAX]


def paste_held(ref: PullRef) -> bool:
    """Paste the held text into the session's prompt, unsent, and forget it. Whether there was text to paste. The text
    waits at the prompt as text the reader typed does (`typed`), so no address submits it."""
    with lock:
        s = session_of(ref)
        text, s.held = s.held, ""
    if text:
        paste(ref, text)
        with lock:
            s.typed = True
    return bool(text)


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
        s = session_of(ref)
        s.held = ""
        proc = _forget(s)
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
        records.SESSIONS.write(s.ref, {"id": s.id, "resume": s.resume, "pid": 0})
    return proc


def expire(ref: PullRef) -> None:
    """End the pull request's session, delete its worktree and its files, with its comments."""
    close(ref)
    with lock:
        sessions.pop(ref, None)
        records.SESSIONS.delete(ref)
        records.OPENED.delete(ref)
    claude.expire(ref)


def note_opened(ref: PullRef) -> None:
    """Record that the reader opened the pull request's page now."""
    records.OPENED.write(ref, {"at": time.time()})


def sweep() -> None:
    """Expire every pull request with a record in the cache that GitHub reports merged or closed, that the reader has
    not opened for KEEP_ENDED, and whose session neither runs nor starts. A pull request GitHub can't return keeps its
    records."""
    unopened = [ref for ref in records.kept() if unopened_long(ref)]
    for ref, standing in github.pr_standings(unopened).items():
        # The reader may have opened the page, or started the session, while GitHub answered.
        if standing.status in claude.ENDED and unopened_long(ref) and not busy(ref):
            expire(ref)


def unopened_long(ref: PullRef) -> bool:
    """Whether the reader has not opened the pull request's page for KEEP_ENDED."""
    return time.time() - opened_at(ref) > KEEP_ENDED


def opened_at(ref: PullRef) -> float:
    """When the reader last opened the pull request's page, in seconds since the epoch; 0 without a record of it."""
    at = records.OPENED.read(ref, dict).get("at")
    return at if isinstance(at, int | float) else 0


def busy(ref: PullRef) -> bool:
    """Whether the pull request's session runs or starts."""
    with lock:
        s = sessions.get(ref)
        return s is not None and (s.starting or (s.proc is not None and s.proc.poll() is None))


def stop_all() -> None:
    """End every running session at once, keeping the worktrees: the server exits."""
    with lock:
        procs = [proc for s in sessions.values() if (proc := _forget(s)) is not None]
    end(procs)


def reap_orphans() -> None:
    """End the processes of sessions a server that died left behind: a pid a session file names counts only while
    `ps` shows a `claude` with that session id running under it with no parent left, so a pid the system gave to
    another process since, and a session of another server on the same cache, are left alone."""
    for ref in records.SESSIONS.refs():
        saved = records.SESSIONS.read(ref, dict)
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
        records.SESSIONS.write(ref, {**saved, "pid": 0})
