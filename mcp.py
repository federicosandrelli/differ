"""The tools Claude acts on differ with, served as a Model Context Protocol (MCP) server over HTTP at
/api/<owner>/<repo>/<number>/mcp, one address per pull request, so no tool takes a pull request argument.

The server answers JSON-RPC over POST: `initialize`, `ping`, `tools/list` and `tools/call` do the work, a notification
(a request without an id) gets 202 and an empty body, and any other method gets the JSON-RPC error -32601. A GET gets
405, which a client that tries one for a stream of its own takes quietly. The session's process sends `Authorization: Bearer <token>` with every request; the token is an HMAC of the pull
request's ref with a secret minted once per server start, so one process reaches only its own pull request, and a
stray local process reaches none."""

from __future__ import annotations

import hmac
import json
import secrets
from pathlib import Path
from typing import Any

import build
import classify
import claude
import github
from fields import Invalid, count_of, field, line_number, one_of, optional, string_of, text_of
from github import PullRef

PROTOCOL = "2025-06-18"
SERVER = {"name": "differ", "version": "1"}
# The most of a diff a tool answers; Claude Code cuts a result over about 25,000 tokens anyway.
DIFF_MAX = 120_000
NOT_FOUND = -32601
INVALID = -32602
SECRET = secrets.token_hex(32)

TOOLS: list[dict] = [
    {
        "name": "pull_request_diff",
        "description": (
            "The diff of this pull request, from the commit where its branch left the base branch to its head "
            "commit, as `git diff` prints it; with `path`, the diff of that one file. Read it before you talk about the change: the worktree holds the head only, "
            "so this is the way to see what changed and the old side of a file."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "A file's path, to get its diff alone."}},
        },
    },
    {
        "name": "read_thread",
        "description": (
            "Read a thread the reader sent to Claude from differ, by the `id` of a frame the Monitor received: its "
            "lines, the code it is on, every message of the thread, oldest first, and `seen`, the number of those "
            "messages, to send back with your reply."
        ),
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    },
    {
        "name": "reply_in_thread",
        "description": (
            "Reply in a thread the reader sent to Claude from differ. `id` and `seen` come from the thread as "
            "read_thread gave it: `seen` is the number of its messages you read, so differ knows which "
            "of the reader's messages the reply answers. differ shows the reply in the thread on the lines, as "
            "Markdown. Reply once per thread you handle, in one or two plain sentences."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "body": {"type": "string"}, "seen": {"type": "integer"}},
            "required": ["id", "body", "seen"],
        },
    },
    {
        "name": "start_thread",
        "description": (
            "Start a new thread for the reader on lines of a file of this pull request, with your first "
            "message, in Markdown: differ shows it on the lines like a comment the reader sent you, and the reader's "
            "reply in it reaches you as a `follow_up` frame. `path` is the file's path in the diff: its new path "
            "when the pull request renames it. `line` is the last line, `start_line` the first when the thread "
            "covers several. `side` is `RIGHT` (the default) for lines of the file at the head commit, `LEFT` for "
            "lines the pull request removes, as the file before the pull request numbers them. Lines are numbered as at the pull request's head commit on "
            "GitHub, which leaves out commits you have not pushed. Answers the thread's `id`, that commit and the "
            "code the thread covers."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "line": {"type": "integer", "minimum": 1},
                "start_line": {"type": "integer", "minimum": 1},
                "side": {"type": "string", "enum": ["RIGHT", "LEFT"]},
                "body": {"type": "string"},
            },
            "required": ["path", "line", "body"],
        },
    },
    {
        "name": "describe_file",
        "description": (
            "Set the description of why this pull request changes one file, and what it changes, in Markdown. "
            "differ shows it under the file's header, in place of the description before. An empty body removes "
            "it. `path` is the file's path in the diff: its new path when the pull request renames it. Follow the "
            "differ-file-descriptions skill to write the body."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "body": {"type": "string"}},
            "required": ["path", "body"],
        },
    },
]


def token(ref: PullRef) -> str:
    return hmac.new(SECRET.encode(), str(ref).encode(), "sha256").hexdigest()


def authorized(ref: PullRef, header: str | None) -> bool:
    """Whether an Authorization header carries the pull request's token."""
    expected = f"Bearer {token(ref)}"
    return header is not None and hmac.compare_digest(header.strip(), expected)


def config(ref: PullRef, port: int) -> str:
    """The `--mcp-config` JSON that points a session at its pull request's endpoint."""
    url = f"http://127.0.0.1:{port}/api/{ref.slug}/{ref.number}/mcp"
    server = {"type": "http", "url": url, "headers": {"Authorization": f"Bearer {token(ref)}"}}
    return json.dumps({"mcpServers": {"differ": server}})


def handle(ref: PullRef, request: object) -> tuple[int, dict | None]:
    """Answer one JSON-RPC request: the HTTP status and the body, None for a notification, which gets no body."""
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
        return 400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "not a JSON-RPC request"}}
    method, request_id, params = request["method"], request.get("id"), request.get("params") or {}
    if request_id is None:
        return 202, None
    if method == "initialize":
        version = params.get("protocolVersion") if isinstance(params, dict) else None
        result: Any = {
            "protocolVersion": version if isinstance(version, str) else PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return 200, error(request_id, INVALID, "tools/call needs a tool name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return 200, error(request_id, INVALID, "arguments is not an object")
        text, failed = call(ref, params["name"], arguments)
        result = {"content": [{"type": "text", "text": text}], "isError": failed}
    else:
        return 200, error(request_id, NOT_FOUND, f"unknown method {method}")
    return 200, {"jsonrpc": "2.0", "id": request_id, "result": result}


def error(request_id: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def call(ref: PullRef, name: str, arguments: dict) -> tuple[str, bool]:
    """Run a tool. Answers its text and whether it failed; a failure's text says why, for Claude to tell the reader."""
    try:
        if name == "pull_request_diff":
            return diff(ref, optional(arguments, "path", string_of, "") or None), False
        if name == "read_thread":
            return json.dumps(claude.thread(ref, field(arguments, "id", text_of))), False
        if name == "reply_in_thread":
            thread_id, body = field(arguments, "id", text_of), field(arguments, "body", text_of)
            c = claude.reply(ref, thread_id, body, field(arguments, "seen", count_of))
            return f"Replied in the thread on {c['path']}:{c['line']}.", False
        if name == "start_thread":
            return start_thread(ref, arguments), False
        if name == "describe_file":
            path, body = field(arguments, "path", text_of), field(arguments, "body", string_of)
            if body.strip() and path not in paths(*commits(ref)):
                raise Invalid(f"{path} is not a file of this pull request's diff.")
            claude.describe(ref, path, body)
            return f"{'Described' if body.strip() else 'Removed the description of'} {path}.", False
        return f"No tool named {name}.", True
    except Invalid as exc:
        return f"{name}: {exc}", True
    except Exception as exc:  # noqa: BLE001 - every failure must reach Claude as text
        return f"{type(exc).__name__}: {exc}", True


def start_thread(ref: PullRef, arguments: dict) -> str:
    """Start Claude's thread on the lines the arguments name, with the code they cover in the side's file, numbered
    as the page numbers them. Raises Invalid for a file outside the diff, a file git's diff calls binary, which the
    page shows no lines of, lines outside the file, and, on the LEFT side, lines the pull request does not remove,
    which the page comments on from the RIGHT side. Answers the thread's id, the commit the lines are numbered at,
    and the code, for Claude to check it named the lines it meant."""
    path, line, body = (
        field(arguments, "path", text_of),
        field(arguments, "line", line_number),
        field(arguments, "body", text_of),
    )
    side: claude.Side = optional(arguments, "side", side_of, None) or "RIGHT"
    first = optional(arguments, "start_line", line_number, line)
    if first > line:
        raise Invalid("start_line comes after line.")
    mirror, base, head = commits(ref)
    old = paths(mirror, base, head).get(path)
    if old is None:
        raise Invalid(f"{path} is not a file of this pull request's diff.")
    (changed,) = classify.parse_patch(patch(mirror, base, head, path, old))
    if changed.binary:
        raise Invalid(f"{path} is binary: differ shows no lines of it.")
    commit, file = (head, path) if side == "RIGHT" else (base, old)
    if (content := build.read_files(mirror, commit, [file]).get(file)) is None:
        raise Invalid(f"{path} has no {'new' if side == 'RIGHT' else 'old'} side: use the other side.")
    lines = classify.text_lines(classify.decoded(content))
    if line > len(lines):
        where = "at the head" if side == "RIGHT" else "before the pull request"
        raise Invalid(f"{file} has {len(lines)} lines {where}.")
    if side == "LEFT" and not set(range(first, line + 1)) <= {n for h in changed.hunks for n in h.removed()}:
        raise Invalid("on the LEFT side, every line must be one the pull request removes: comment on the RIGHT side.")
    code = lines[first - 1 : line]
    start = None if first == line else first
    c = claude.start_thread(ref, path=path, line=line, start_line=start, side=side, commit=head, code=code, body=body)
    numbered = "".join(f"\n{n:>5}  {text}" for n, text in enumerate(code, first))
    return f"Started thread {c['id']} on {path}:{line_range(first, line)} ({side}) at {commit[:12]}:{numbered}"


def side_of(value: object) -> claude.Side:
    return one_of(value, claude.Side)


def line_range(first: int, last: int) -> str:
    return str(last) if first == last else f"{first}-{last}"


def commits(ref: PullRef) -> tuple[Path, str, str]:
    """The mirror, fetched up to the pull request's head, the commit its branch left the base branch at, and the
    head."""
    pr = github.pull_request(ref)
    mirror = build.ensure_repo(ref, lambda _: None)
    build.fetch(mirror, ref.number, pr.base, pr.head, lambda _: None)
    return mirror, build.base_of(mirror, pr), pr.head


def diff(ref: PullRef, path: str | None) -> str:
    """The pull request's diff from the mirror, from the commit its branch left the base branch at, as the page's,
    headed by that commit and the head, cut at DIFF_MAX characters."""
    mirror, base, head_commit = commits(ref)
    names = dict.fromkeys([path, paths(mirror, base, head_commit).get(path, path)]) if path else {}
    text = patch(mirror, base, head_commit, *names)
    head = f"base {base}\nhead {head_commit}\n\n"
    if not text:
        return head + (f"{path} is not changed by this pull request." if path else "The diff is empty.")
    if len(text) > DIFF_MAX:
        text = text[:DIFF_MAX] + f"\n[cut after {DIFF_MAX} characters; ask for one file with `path`]\n"
    return head + text


def patch(mirror: Path, base: str, head: str, *names: str) -> str:
    """The patch from base to head, as the page's, of the files with those paths, or of every file without one. git
    pairs a rename only when both of its paths are among the names."""
    pathspec = ["--", *names] if names else []
    args = ["-c", "diff.suppressBlankEmpty=false", "--literal-pathspecs", "diff-tree", "-p", "-M", "-U3", base, head]
    return build.git(mirror, *args, *pathspec).decode(errors="replace")


def paths(mirror: Path, base: str, head: str) -> dict[str, str]:
    """The paths of the files that change from base to head, as the page names them (a renamed file by its new path),
    each with the file's path at base."""
    fields = build.git(mirror, "diff-tree", "-r", "-M", "-z", "--name-status", base, head).decode(errors="replace")
    words, found = iter(fields.split("\0")), {}
    for status in words:
        if not status:
            continue
        old = next(words)
        found[next(words) if status[0] in "RC" else old] = old
    return found
