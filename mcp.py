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
import claude
import github
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
        "name": "reply_in_thread",
        "description": (
            "Reply in a thread the reader sent to Claude from differ. `id` and `seen` come from the latest frame of "
            "the thread the Monitor received: `seen` is the number of its messages you read, so differ knows which "
            "of the reader's messages the reply answers. differ shows the reply in the thread on the lines, as "
            "Markdown. Reply once per thread you handle, in one or two plain sentences."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "body": {"type": "string"}, "seen": {"type": "integer"}},
            "required": ["id", "body"],
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
            path = arguments.get("path")
            return diff(ref, path if isinstance(path, str) and path else None), False
        if name == "reply_in_thread":
            thread_id, body, seen = arguments.get("id"), arguments.get("body"), arguments.get("seen")
            if not isinstance(thread_id, str) or not isinstance(body, str) or not body.strip():
                return "reply_in_thread needs the thread's id and a body.", True
            c = claude.reply(
                ref, thread_id, body, seen if isinstance(seen, int) and not isinstance(seen, bool) else None
            )
            return f"Replied in the thread on {c['path']}:{c['line']}.", False
        if name == "describe_file":
            path, body = arguments.get("path"), arguments.get("body")
            if not isinstance(path, str) or not isinstance(body, str):
                return "describe_file needs a path and a body.", True
            if body.strip() and path not in paths(*commits(ref)):
                return f"{path} is not a file of this pull request's diff.", True
            claude.describe(ref, path, body)
            return f"{'Described' if body.strip() else 'Removed the description of'} {path}.", False
        return f"No tool named {name}.", True
    except claude.NoComment as exc:
        return str(exc), True
    except Exception as exc:  # noqa: BLE001 - every failure must reach Claude as text
        return f"{type(exc).__name__}: {exc}", True


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
    args = ["-c", "diff.suppressBlankEmpty=false", "--literal-pathspecs", "diff-tree", "-p", "-M", "-U3", base]
    args.append(head_commit)
    if path:
        # git pairs a rename only when both of its paths are in the pathspec.
        args += ["--", *dict.fromkeys([path, paths(mirror, base, head_commit).get(path, path)])]
    patch = build.git(mirror, *args).decode(errors="replace")
    head = f"base {base}\nhead {head_commit}\n\n"
    if not patch:
        return head + (f"{path} is not changed by this pull request." if path else "The diff is empty.")
    if len(patch) > DIFF_MAX:
        patch = patch[:DIFF_MAX] + f"\n[cut after {DIFF_MAX} characters; ask for one file with `path`]\n"
    return head + patch


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
