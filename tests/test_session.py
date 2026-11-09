import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

import build
import checkout
import claude
import github
import records
import server
import session
from github import PullRef

FAKE = Path(__file__).resolve().parent / "fakeclaude"
REF = PullRef("acme", "widgets", 1)
PR = github.PullRequest(
    title="Greet",
    url="https://github.com/acme/widgets/pull/1",
    body="",
    author="ann",
    base="b" * 40,
    head="c" * 40,
    base_ref="main",
    head_ref="greeting",
    commits=1,
    state="OPEN",
    draft=False,
)


@pytest.fixture(autouse=True)
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """The session module against the fake claude, a worktree folder that needs no git, and a pull request that needs
    no GitHub. Every session is ended after the test."""
    monkeypatch.setattr(build, "CACHE", tmp_path)
    monkeypatch.setattr(claude, "listeners", {})
    monkeypatch.setattr(session, "sessions", {})
    monkeypatch.setattr(session, "QUICK_EXIT", 3.0)
    monkeypatch.setenv("DIFFER_CLAUDE", str(FAKE))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "starts.log"))
    monkeypatch.delenv("FAKE_CLAUDE_FAIL", raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    folder = tmp_path / "worktree"
    folder.mkdir()
    removed: list[PullRef] = []
    monkeypatch.setattr(checkout, "worktree", lambda ref, head=None: folder)
    monkeypatch.setattr(checkout, "remove_worktree", removed.append)
    monkeypatch.setattr(github, "pull_request", lambda ref: PR)
    monkeypatch.setattr(github, "viewer_login", lambda: "tester")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield SimpleNamespace(folder=folder, removed=removed, log=tmp_path / "starts.log", port=httpd.server_address[1])
    session.stop_all()
    httpd.shutdown()
    httpd.server_close()


def starts(log: Path) -> list[dict]:
    """The starts the fake claude logged, leaving out a line it is still writing."""
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines(keepends=True) if line.endswith("\n")]


def wait_for(condition: Callable[[], bool], timeout: float = 15) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


def output() -> str:
    with session.lock:
        return bytes(session.session_of(REF).output).decode(errors="replace")


def running() -> bool:
    return session.status(REF)["running"]


def typing_ready() -> bool:
    """Whether the fake claude runs and has its terminal in raw mode, which it tells with its banner."""
    return running() and "fake claude" in output()


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return (
        subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()[:1] != "Z"
    )


def test_the_binary_is_the_one_differ_claude_names_else_the_first_on_the_path(monkeypatch: pytest.MonkeyPatch) -> None:
    assert session.binary() == FAKE
    monkeypatch.delenv("DIFFER_CLAUDE")
    monkeypatch.setattr(session.shutil, "which", lambda name: "/opt/bin/claude" if name == "claude" else None)
    assert session.binary() == Path("/opt/bin/claude")
    monkeypatch.setattr(session.shutil, "which", lambda name: None)
    monkeypatch.setattr(Path, "home", lambda: Path("/nowhere"))
    assert session.binary() is None


def test_the_version_is_read_from_claude_and_an_old_one_is_refused(
    fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert session.version_of(FAKE) == (2, 1, 319) and session.too_old((2, 1, 319)) is None
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "old")
    assert session.version_of(FAKE) == (2, 1, 200)
    assert session.too_old((2, 1, 200)) == "Claude Code 2.1.200 is too old. differ needs 2.1.319 or later."
    assert session.too_old(()) == "Claude Code an unknown version is too old. differ needs 2.1.319 or later."


def test_a_too_old_claude_does_not_start_and_the_status_says_why(
    fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "old")
    assert session.start(REF, fake.port, 80, 24)["starting"]
    wait_for(lambda: not session.status(REF)["starting"])
    status = session.status(REF)
    assert "too old" in status["error"] and not status["running"] and starts(fake.log) == []


def test_a_start_runs_claude_in_the_worktree_with_a_new_id_and_the_flags(fake: SimpleNamespace) -> None:
    session.config_path().parent.mkdir()
    session.config_path().write_text("{}")
    session.start(REF, fake.port, 100, 30)
    wait_for(running)
    wait_for(lambda: starts(fake.log) != [])
    (start,) = starts(fake.log)
    args = start["args"]
    session_id = args[args.index("--session-id") + 1]
    assert len(session_id) == 36 and start["cwd"] == str(fake.folder)
    assert args[args.index("--model") + 1] == "claude-opus-5-5"
    assert args[args.index("--permission-mode") + 1] == "bypassPermissions"
    assert args[args.index("--setting-sources") + 1] == "user,project,local"
    assert "--strict-mcp-config" in args
    config = json.loads(args[args.index("--mcp-config") + 1])["mcpServers"]["differ"]
    assert config["type"] == "http" and config["url"] == f"http://127.0.0.1:{fake.port}/api/acme/widgets/1/mcp"
    assert config["headers"]["Authorization"].startswith("Bearer ")
    prompt = args[args.index("--append-system-prompt") + 1]
    assert "acme/widgets#1" in prompt and "ann" in prompt and "tester" in prompt and "greeting" in prompt
    assert f"ws://127.0.0.1:{fake.port}/api/acme/widgets/1/listen" in args[-1] and "greeting" in args[-1]
    assert start["env"]["TERM"] == "xterm-256color" and "CLAUDECODE" not in start["env"]
    saved = records.SESSIONS.read(REF, dict)
    assert saved == {"id": session_id, "resume": False, "pid": start["pid"]}
    status = session.status(REF)
    assert status == {
        "starting": False,
        "running": True,
        "error": "",
        "branch": "greeting",
        "head": "c" * 40,
        "working": False,
        "task": "",
        "asking": False,
    }
    trusted = json.loads(session.config_path().read_text())["projects"]
    assert trusted == {os.path.realpath(build.mirror_of(REF)): {"hasTrustDialogAccepted": True}}


def test_a_second_open_while_the_session_runs_starts_nothing(fake: SimpleNamespace) -> None:
    session.start(REF, fake.port, 80, 24)
    wait_for(lambda: len(starts(fake.log)) == 1)
    session.start(REF, fake.port, 80, 24)
    time.sleep(0.3)
    assert len(starts(fake.log)) == 1


def test_a_submit_presses_enter_in_a_write_of_its_own_after_the_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(session, "write", lambda ref, data: calls.append(data))
    monkeypatch.setattr(session.time, "sleep", calls.append)
    session.paste(REF, "rebase it", submit=True)
    assert calls == [b"\x1b[200~rebase it\x1b[201~", session.SUBMIT_WAIT, b"\r"]


def test_a_paste_drops_the_escapes_that_would_end_it_early(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(session, "write", lambda ref, data: calls.append(data))
    session.paste(REF, "fix it\x1b[201~ now")
    assert calls == [b"\x1b[200~fix it[201~ now\x1b[201~"]


def test_two_pastes_at_once_go_in_one_after_the_other_each_with_its_enter(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bytes] = []

    def slow_write(ref: PullRef, data: bytes) -> None:
        calls.append(data)
        time.sleep(0.05)

    monkeypatch.setattr(session, "write", slow_write)
    monkeypatch.setattr(session, "SUBMIT_WAIT", 0.05)
    pastes = [threading.Thread(target=session.paste, args=(REF, text, True)) for text in ("one", "two")]
    for t in pastes:
        t.start()
    for t in pastes:
        t.join()
    assert calls[1] == calls[3] == b"\r"
    assert sorted([calls[0], calls[2]]) == [b"\x1b[200~one\x1b[201~", b"\x1b[200~two\x1b[201~"]


def test_what_the_terminal_types_comes_back_a_paste_is_bracketed_and_a_submit_presses_enter(
    fake: SimpleNamespace,
) -> None:
    session.start(REF, fake.port, 80, 24)
    wait_for(running)
    frames: list[bytes] = []
    session.attach(REF, frames.append)
    wait_for(lambda: b"fake claude --session-id" in b"".join(frames))
    session.write(REF, b"hi\r")
    wait_for(lambda: b"HI" in b"".join(frames))
    session.paste(REF, "@src/app.py#L11 ")
    wait_for(lambda: b"\x1b[200~@SRC/APP.PY#L11 \x1b[201~" in b"".join(frames))
    session.paste(REF, "rebase it", submit=True)
    wait_for(lambda: b"\x1b[200~REBASE IT\x1b[201~\r" in b"".join(frames))
    assert "HI" in output()
    session.detach(REF, frames.append)


def test_the_terminal_title_says_whether_claude_works_and_on_what_task(fake: SimpleNamespace) -> None:
    session.start(REF, fake.port, 80, 24)
    wait_for(running)
    wait_for(lambda: "fake claude" in output())
    # The fake claude echoes what the terminal types, so a title typed into it comes back as the process's title. The
    # first half comes back before the second goes in, so the title arrives in two reads.
    session.write(REF, "\x1b]0;\u25d0 fix".encode())
    wait_for(lambda: "FIX" in output())
    assert not session.status(REF)["working"]
    session.write(REF, b" the login\x07")
    wait_for(lambda: session.status(REF)["working"])
    assert session.status(REF)["task"] == "FIX THE LOGIN"
    session.write(REF, "\x1b]2;\u2733 fix the login\x1b\\".encode())
    wait_for(lambda: not session.status(REF)["working"])
    assert session.status(REF)["task"] == "FIX THE LOGIN"
    session.write(REF, "\x1b]0;\u25d0 fix the login\x07".encode())
    wait_for(lambda: session.status(REF)["working"])
    session.close(REF)
    assert not session.status(REF)["working"]
    # A new process starts without the title of the last one.
    session.start(REF, fake.port, 80, 24)
    wait_for(running)
    assert (session.status(REF)["working"], session.status(REF)["task"]) == (False, "")


@pytest.mark.parametrize(
    ("title", "state"),
    [
        ("\u25d0 fix the login", (True, "fix the login")),
        ("\u2733 fix the login", (False, "fix the login")),
        ("\u25d1 Claude Code", (True, "")),
        ("\u2733 Claude Code", (False, "")),
        ("\u25d0", (True, "")),
        ("Claude Code", (False, "")),
        ("", (False, "")),
    ],
)
def test_a_title_says_whether_claude_works_and_its_task(title: str, state: tuple[bool, str]) -> None:
    assert session.title_state(title) == state


def post_hook(handler: dict, event: str, headers: dict | None = None) -> int:
    """Post a hook of the question tool to a handler of the process's settings, as Claude Code does; the status."""
    body = json.dumps({"hook_event_name": event, "tool_name": session.QUESTION_TOOL}).encode()
    headers = handler["headers"] if headers is None else headers
    request = urllib.request.Request(handler["url"], data=body, headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request) as answer:
            return answer.status
    except urllib.error.HTTPError as exc:
        return exc.code


def asking() -> bool:
    return session.status(REF)["asking"]


def test_the_question_tools_hooks_say_whether_claude_asks_the_reader(fake: SimpleNamespace) -> None:
    session.start(REF, fake.port, 80, 24)
    wait_for(typing_ready)
    args = starts(fake.log)[0]["args"]
    hooks = json.loads(args[args.index("--settings") + 1])["hooks"]
    (pre,) = hooks["PreToolUse"]
    (post,) = hooks["PostToolUse"]
    (failure,) = hooks["PostToolUseFailure"]
    assert pre["matcher"] == post["matcher"] == failure["matcher"] == session.QUESTION_TOOL
    assert post_hook(pre["hooks"][0], "PreToolUse", headers={}) == 403
    assert not asking()
    assert post_hook(pre["hooks"][0], "PreToolUse") == 200
    assert asking()
    assert post_hook(post["hooks"][0], "PostToolUse") == 200
    assert not asking()
    post_hook(pre["hooks"][0], "PreToolUse")
    assert post_hook(failure["hooks"][0], "PostToolUseFailure") == 200
    assert not asking()

    # A reader who cancels the question gets no hook: the Escape key ends the question, and an arrow key, which
    # starts with the same byte, and Ctrl+C, which leaves the question open, keep it.
    post_hook(pre["hooks"][0], "PreToolUse")
    session.write(REF, b"\x1b[B")
    session.write(REF, b"\x03")
    assert asking()
    session.write(REF, b"\x1b")
    assert not asking()

    # A way out of the question that posts no hook ends it once Claude works again.
    session.write(REF, "\x1b]0;◐ pick a colour\x07".encode())
    wait_for(lambda: session.status(REF)["working"])
    post_hook(pre["hooks"][0], "PreToolUse")
    session.write(REF, "\x1b]0;✳ pick a colour\x07".encode())
    wait_for(lambda: not session.status(REF)["working"])
    assert asking()
    session.write(REF, "\x1b]0;◑ pick a colour\x07".encode())
    wait_for(lambda: session.status(REF)["working"])
    assert not asking()

    # A question outlives neither its process nor the start of the next.
    post_hook(pre["hooks"][0], "PreToolUse")
    session.close(REF)
    assert not asking()
    session.start(REF, fake.port, 80, 24)
    wait_for(running)
    assert not asking()


def test_a_terminal_that_connects_later_gets_what_the_session_printed(fake: SimpleNamespace) -> None:
    session.start(REF, fake.port, 80, 24)
    wait_for(running)
    wait_for(lambda: "fake claude" in output())
    frames: list[bytes] = []
    session.attach(REF, frames.append)
    assert b"fake claude --session-id" in frames[0]


def test_a_close_ends_the_process_and_removes_the_worktree(fake: SimpleNamespace) -> None:
    session.start(REF, fake.port, 80, 24)
    wait_for(running)
    pid = records.SESSIONS.read(REF, dict)["pid"]
    assert alive(pid)
    session.close(REF)
    assert not alive(pid) and not running() and fake.removed == [REF]
    saved = records.SESSIONS.read(REF, dict)
    assert saved["pid"] == 0 and len(saved["id"]) == 36


def test_stop_all_ends_the_sessions_and_keeps_the_worktrees(fake: SimpleNamespace) -> None:
    session.start(REF, fake.port, 80, 24)
    wait_for(running)
    pid = records.SESSIONS.read(REF, dict)["pid"]
    session.stop_all()
    assert not alive(pid) and fake.removed == []


def test_expire_ends_the_session_and_deletes_its_file_and_the_comments(fake: SimpleNamespace) -> None:
    claude.add(REF, path="a.py", line=1, start_line=None, side="RIGHT", commit="abc", code=[], body="b")
    session.start(REF, fake.port, 80, 24)
    wait_for(running)
    session.expire(REF)
    assert not running() and records.SESSIONS.refs() == [] and fake.removed == [REF]
    assert claude.fetch(REF, "abc")["comments"] == []


def test_a_sweep_expires_the_merged_and_closed_pull_requests_with_a_record_and_keeps_the_rest(
    fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    statuses: list[github.Status] = ["open", "merged", "closed", "draft"]
    refs: dict[github.Status, PullRef] = {status: PullRef("acme", "widgets", n) for n, status in enumerate(statuses, 1)}
    gone, described, idle = (PullRef("acme", "widgets", n) for n in (7, 8, 9))
    for ref in [*refs.values(), gone]:
        claude.add(ref, path="a.py", line=1, start_line=None, side="RIGHT", commit="abc", code=[], body="b")
    claude.describe(described, "a.py", "A helper.")
    records.SESSIONS.write(idle, {"id": "d" * 32, "resume": True, "pid": 0})
    asked: list[PullRef] = []

    reported: dict[PullRef, github.Status] = {ref: status for status, ref in refs.items()}
    reported |= {described: "merged", idle: "closed"}

    def standings(asked_for: list[PullRef]) -> dict[PullRef, github.Standing]:
        asked.extend(asked_for)
        return {ref: github.Standing(status, "me") for ref, status in reported.items()}

    monkeypatch.setattr(github, "pr_standings", standings)
    session.sweep()
    assert sorted(asked, key=lambda r: r.number) == [*refs.values(), gone, described, idle]
    assert records.kept() == [refs["open"], refs["draft"], gone]
    assert sorted(fake.removed, key=lambda r: r.number) == [refs["merged"], refs["closed"], described, idle]


def test_a_thread_reaches_the_session_which_replies_through_the_mcp_server(fake: SimpleNamespace) -> None:
    session.start(REF, fake.port, 80, 24)
    wait_for(lambda: claude.fetch(REF, "abc")["listening"])
    claude.add(
        REF, path="src/app.py", line=11, start_line=None, side="RIGHT", commit="abc", code=["x"], body="Use a logger"
    )
    wait_for(lambda: claude.fetch(REF, "abc")["comments"][0]["state"] == "answered")
    (c,) = claude.fetch(REF, "abc")["comments"]
    assert [(m["author"], m["body"]) for m in c["messages"]] == [
        ("reader", "Use a logger"),
        ("claude", "Fake reply to: Use a logger"),
    ]
    wait_for(lambda: "called reply_in_thread" in output())


def test_a_resume_of_a_lost_conversation_starts_again_with_a_new_id(
    fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    records.SESSIONS.write(REF, {"id": "a" * 32, "resume": True, "pid": 0})
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "resume")
    session.start(REF, fake.port, 80, 24)
    wait_for(lambda: len(starts(fake.log)) == 2 and running())
    first, second = [s["args"] for s in starts(fake.log)]
    assert first[:2] == ["--resume", "a" * 32]
    assert second[0] == "--session-id" and len(second[1]) == 36 and second[1] != "a" * 32
    assert records.SESSIONS.read(REF, dict)["id"] == second[1]


def test_a_start_with_its_id_in_use_resumes_it_with_the_same_model(
    fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "in-use")
    session.start(REF, fake.port, 80, 24, "claude-sonnet-5-5")
    wait_for(lambda: len(starts(fake.log)) == 2 and running())
    first, second = [s["args"] for s in starts(fake.log)]
    assert first[0] == "--session-id" and second[:2] == ["--resume", first[1]]
    assert first[first.index("--model") + 1] == second[second.index("--model") + 1] == "claude-sonnet-5-5"


def test_the_next_start_resumes_once_the_first_process_lived_past_the_quick_exit(fake: SimpleNamespace) -> None:
    session.start(REF, fake.port, 80, 24)
    wait_for(running)
    time.sleep(session.QUICK_EXIT + 0.1)
    session.write(REF, b"x")
    wait_for(lambda: records.SESSIONS.read(REF, dict).get("resume") is True)
    session.close(REF)
    session.start(REF, fake.port, 80, 24)
    wait_for(lambda: len(starts(fake.log)) == 2 and running())
    first, second = [s["args"] for s in starts(fake.log)]
    assert second[:2] == ["--resume", first[1]]


def test_a_process_that_ends_on_its_own_leaves_an_error_and_the_next_open_starts_again(fake: SimpleNamespace) -> None:
    session.start(REF, fake.port, 80, 24)
    wait_for(running)
    time.sleep(session.QUICK_EXIT + 0.1)
    pid = records.SESSIONS.read(REF, dict)["pid"]
    os.kill(pid, 15)
    wait_for(lambda: not running() and session.status(REF)["error"] != "")
    assert session.status(REF)["error"].startswith("Claude Code exited with code")
    session.start(REF, fake.port, 80, 24)
    wait_for(lambda: len(starts(fake.log)) == 2 and running())
    assert session.status(REF)["error"] == ""


def test_reap_orphans_ends_a_claude_with_the_session_id_whose_parent_is_gone_and_leaves_the_rest_alone() -> None:
    other = PullRef("acme", "widgets", 2)
    sleeper = ["-c", "import time; time.sleep(60)", "claude", "--resume"]
    # The orphan is started by a process that exits at once, so the system becomes its parent, as it does for the
    # session of a server that died. The bystander has the same arguments, but its parent, this process, lives.
    starter = (
        "import subprocess as s, sys; q = s.DEVNULL; "
        "print(s.Popen([sys.executable, *sys.argv[1:]], start_new_session=True, stdin=q, stdout=q, stderr=q).pid)"
    )
    orphan_pid = int(subprocess.run([sys.executable, "-c", starter, *sleeper, "d" * 32], capture_output=True).stdout)
    bystander = subprocess.Popen([sys.executable, *sleeper, "e" * 32], start_new_session=True)
    try:
        records.SESSIONS.write(REF, {"id": "d" * 32, "resume": True, "pid": orphan_pid})
        records.SESSIONS.write(other, {"id": "e" * 32, "resume": True, "pid": bystander.pid})
        wait_for(
            lambda: subprocess.run(["ps", "-o", "ppid=", "-p", str(orphan_pid)], capture_output=True).stdout.strip()
            == b"1"
        )
        session.reap_orphans()
        wait_for(lambda: not alive(orphan_pid))
        assert bystander.poll() is None
        assert records.SESSIONS.read(REF, dict)["pid"] == 0 and records.SESSIONS.read(other, dict)["pid"] == 0
    finally:
        try:
            os.killpg(orphan_pid, 9)
        except OSError:
            pass
        bystander.kill()
        bystander.wait()


def test_a_close_during_a_start_drops_the_start(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    checking_out, closed = threading.Event(), threading.Event()

    def slow_worktree(ref: PullRef, head: str | None = None) -> Path:
        checking_out.set()
        closed.wait(10)
        return fake.folder

    monkeypatch.setattr(checkout, "worktree", slow_worktree)
    session.start(REF, fake.port, 80, 24)
    assert checking_out.wait(10) and session.status(REF)["starting"]
    session.close(REF)
    closed.set()
    time.sleep(0.5)
    assert session.status(REF) == {
        "starting": False,
        "running": False,
        "error": "",
        "branch": "",
        "head": "",
        "working": False,
        "task": "",
        "asking": False,
    }
    assert starts(fake.log) == [] and fake.removed == [REF]


def test_a_failure_the_start_did_not_expect_shows_in_the_status(
    fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(ref: PullRef) -> github.PullRequest:
        raise KeyError("headRefName")

    monkeypatch.setattr(github, "pull_request", broken)
    session.start(REF, fake.port, 80, 24)
    wait_for(lambda: not session.status(REF)["starting"])
    assert session.status(REF)["error"] == "'headRefName'"


def test_a_merged_pull_request_gets_no_session(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github, "pull_request", lambda ref: replace(PR, state="MERGED"))
    session.start(REF, fake.port, 80, 24)
    wait_for(lambda: not session.status(REF)["starting"])
    assert session.status(REF)["error"] == "The pull request is merged, so it gets no session."
    assert starts(fake.log) == []


def test_a_paste_the_session_does_not_read_holds_up_nothing_else(
    fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_DEAF", "1")
    session.start(REF, fake.port, 80, 24)
    wait_for(typing_ready)

    def write() -> None:
        try:
            session.write(REF, b"x" * 65536)
        except OSError:
            pass

    writer = threading.Thread(target=write, daemon=True)
    writer.start()
    time.sleep(0.5)
    assert writer.is_alive()
    before = time.time()
    assert session.status(REF)["running"]
    session.resize(REF, 100, 30)
    assert time.time() - before < 1
    session.close(REF)
    writer.join(10)
    assert not writer.is_alive()


def test_the_environment_drops_the_variables_of_a_parent_claude_session(
    fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    monkeypatch.setenv("CLAUDE_AGENT_SDK_VERSION", "1")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/keep")
    env = session.environment()
    assert "CLAUDECODE" not in env and "CLAUDE_CODE_ENTRYPOINT" not in env and "CLAUDE_AGENT_SDK_VERSION" not in env
    assert "CLAUDE_CODE_CHILD_SESSION" not in env
    assert env["CLAUDE_CONFIG_DIR"] == "/keep" and env["TERM"] == "xterm-256color"


def handshake(port: int, *headers: str) -> bytes:
    lines = ["GET /api/acme/widgets/1/terminal HTTP/1.1", f"Host: 127.0.0.1:{port}", "Upgrade: websocket"]
    lines += [
        "Connection: Upgrade",
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version: 13",
        *headers,
    ]
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def handshake_answer(sock: socket.socket) -> bytes:
    head = b""
    while b"\r\n\r\n" not in head:
        head += sock.recv(1)
    return head


@pytest.mark.parametrize("origin", [None, "https://evil.example", "http://localhost:1"])
def test_the_relay_refuses_a_handshake_without_this_servers_origin(fake: SimpleNamespace, origin: str | None) -> None:
    sock = socket.create_connection(("127.0.0.1", fake.port), timeout=5)
    sock.sendall(handshake(fake.port, *([f"Origin: {origin}"] if origin else [])))
    assert b" 403 " in handshake_answer(sock)
    sock.close()


def test_the_relay_carries_bytes_both_ways_and_a_text_frame_sets_the_size(fake: SimpleNamespace) -> None:
    session.start(REF, fake.port, 80, 24)
    wait_for(typing_ready)
    sock = socket.create_connection(("127.0.0.1", fake.port), timeout=5)
    sock.sendall(handshake(fake.port, f"Origin: http://127.0.0.1:{fake.port}"))
    assert b" 101 " in handshake_answer(sock)
    stream = sock.makefile("rb")
    masked = lambda opcode, payload: bytes([0x80 | opcode, 0x80 | len(payload)]) + b"\0\0\0\0" + payload  # noqa: E731
    sock.sendall(masked(0x1, json.dumps({"cols": 132, "rows": 40}).encode()))
    sock.sendall(masked(0x2, b"abc"))
    got = b""
    while b"ABC" not in got:
        opcode, payload = server.websocket.read_frame(stream)  # type: ignore[arg-type]
        assert opcode == server.websocket.BINARY
        got += payload
    with session.lock:
        assert session.session_of(REF).size == (132, 40)
    sock.close()


def replace_file(path: Path, data: dict) -> None:
    """Write the file as Claude Code does, as a new file that takes the old one's place, so it gets a new inode
    whatever the clock's resolution."""
    new = path.with_name(path.name + ".new")
    new.write_text(json.dumps(data))
    os.replace(new, path)


def test_trust_adds_the_mirror_to_the_config_and_keeps_the_rest(tmp_path: Path) -> None:
    config = session.config_path()
    config.parent.mkdir()
    config.write_text(json.dumps({"numStartups": 3, "projects": {"/elsewhere": {"allowedTools": ["Bash"]}}}))
    config.chmod(0o600)
    mirror = tmp_path / "repos" / "acme" / "widgets.git"
    session.trust(mirror)
    assert json.loads(config.read_text()) == {
        "numStartups": 3,
        "projects": {
            "/elsewhere": {"allowedTools": ["Bash"]},
            os.path.realpath(mirror): {"hasTrustDialogAccepted": True},
        },
    }
    assert config.stat().st_mode & 0o777 == 0o600
    assert list(config.parent.iterdir()) == [config]


def test_trust_keeps_the_rest_of_an_entry_and_leaves_a_trusted_one_alone(tmp_path: Path) -> None:
    config = session.config_path()
    config.parent.mkdir()
    mirror = tmp_path / "repos" / "acme" / "widgets.git"
    key = os.path.realpath(mirror)
    config.write_text(json.dumps({"projects": {key: {"allowedTools": ["Bash"], "hasTrustDialogAccepted": False}}}))
    session.trust(mirror)
    assert json.loads(config.read_text())["projects"] == {
        key: {"allowedTools": ["Bash"], "hasTrustDialogAccepted": True}
    }
    before = config.stat()
    session.trust(mirror)
    after = config.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_trust_writes_nothing_when_there_is_no_config(tmp_path: Path) -> None:
    config = session.config_path()
    session.trust(tmp_path / "repos" / "acme" / "widgets.git")
    assert not config.exists() and not config.parent.exists()
    config.parent.mkdir()
    config.symlink_to(tmp_path / "missing" / "claude.json")
    session.trust(tmp_path / "repos" / "acme" / "widgets.git")
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("text", ["[]", '{"projects": []}', "{not json"])
def test_trust_leaves_a_config_that_is_not_claude_codes_alone(tmp_path: Path, text: str) -> None:
    config = session.config_path()
    config.parent.mkdir()
    config.write_text(text)
    with pytest.raises(ValueError):
        session.trust(tmp_path / "repos" / "acme" / "widgets.git")
    assert config.read_text() == text


def test_a_start_goes_on_when_the_trust_cannot_be_recorded(
    fake: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    config = session.config_path()
    config.parent.mkdir()
    config.write_text("[]")
    session.start(REF, fake.port, 100, 30)
    wait_for(running)
    assert "Could not record the trust" in capsys.readouterr().err and config.read_text() == "[]"


def test_trust_writes_through_a_symbolic_link(tmp_path: Path) -> None:
    config = session.config_path()
    config.parent.mkdir()
    target = tmp_path / "dotfiles" / "claude.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"numStartups": 1}))
    config.symlink_to(target)
    mirror = tmp_path / "repos" / "acme" / "widgets.git"
    session.trust(mirror)
    assert config.is_symlink()
    assert json.loads(target.read_text())["projects"] == {os.path.realpath(mirror): {"hasTrustDialogAccepted": True}}


def test_trust_from_two_threads_keeps_both_mirrors(tmp_path: Path) -> None:
    config = session.config_path()
    config.parent.mkdir()
    config.write_text("{}")
    mirrors = [tmp_path / "repos" / "acme" / f"repo{i}.git" for i in range(8)]
    threads = [threading.Thread(target=session.trust, args=(m,)) for m in mirrors]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert set(json.loads(config.read_text())["projects"]) == {os.path.realpath(m) for m in mirrors}
    assert list(config.parent.iterdir()) == [config]


def test_trust_keeps_a_change_another_process_made_while_it_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = session.config_path()
    config.parent.mkdir()
    config.write_text(json.dumps({"numStartups": 1}))
    read = session._read_config

    def read_then_change(path: Path) -> dict:
        data = read(path)
        if json.loads(config.read_text())["numStartups"] == 1:
            replace_file(config, {"numStartups": 2, "theme": "light"})
        return data

    monkeypatch.setattr(session, "_read_config", read_then_change)
    mirror = tmp_path / "repos" / "acme" / "widgets.git"
    session.trust(mirror)
    assert json.loads(config.read_text()) == {
        "numStartups": 2,
        "theme": "light",
        "projects": {os.path.realpath(mirror): {"hasTrustDialogAccepted": True}},
    }


def test_trust_gives_up_on_a_config_that_keeps_changing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = session.config_path()
    config.parent.mkdir()
    config.write_text(json.dumps({"numStartups": 0}))
    read = session._read_config

    def read_then_change(path: Path) -> dict:
        data = read(path)
        replace_file(config, {"numStartups": data["numStartups"] + 1})
        return data

    monkeypatch.setattr(session, "_read_config", read_then_change)
    with pytest.raises(OSError, match="kept changing"):
        session.trust(tmp_path / "repos" / "acme" / "widgets.git")
    assert json.loads(config.read_text()) == {"numStartups": session.TRUST_TRIES}
    assert list(config.parent.iterdir()) == [config]


def test_a_start_while_the_session_starts_keeps_the_size_the_terminal_set(
    fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = threading.Event()
    monkeypatch.setattr(github, "viewer_login", lambda: gate.wait(10) and "tester")
    session.start(REF, fake.port, 80, 24)
    session.resize(REF, 132, 40)
    session.start(REF, fake.port, 80, 24)
    gate.set()
    wait_for(running)
    with session.lock:
        assert session.session_of(REF).size == (132, 40)
