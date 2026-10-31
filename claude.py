"""The comments the reader sends to the Claude Code session that works on a pull request, instead of to GitHub, and
the session that listens for them.

A pull request's comments live in claude/<owner>/<repo>/<number>.json in the cache, oldest first. A session listens
over a WebSocket, one session per pull request. While one listens, a new comment goes to it at once as a text frame,
and when one connects, every comment without a reply goes to it."""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import build
from github import PullRef

Side = Literal["LEFT", "RIGHT"]
# waiting: no session has the comment yet; sent: a listening session has it; answered: Claude replied.
State = Literal["waiting", "sent", "answered"]
# Sends one text frame to a listening session. Raises OSError once the session has gone.
Send = Callable[[str], None]

lock = threading.Lock()
listeners: dict[PullRef, Send] = {}


class Busy(Exception):
    """Another session listens on the pull request."""


class NoComment(ValueError):
    """The pull request has no Claude comment with that id."""


def path_of(ref: PullRef) -> Path:
    return build.CACHE / "claude" / ref.owner / ref.repo / f"{ref.number}.json"


def read(ref: PullRef) -> list[dict]:
    """The pull request's comments; empty when it has none, or when the file is broken."""
    try:
        data = json.loads(path_of(ref).read_text())
    except (OSError, ValueError):
        return []
    return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []


def write(ref: PullRef, comments: list[dict]) -> None:
    """Replace the pull request's file with the comments, through a temporary file, so a crash mid-write leaves the
    old comments. Call it with the lock held."""
    path = path_of(ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(comments))
    os.replace(tmp, path)


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def answer(ref: PullRef, comments: list[dict]) -> dict:
    """Whether a session listens on the pull request, and its comments. Call it with the lock held."""
    return {"listening": ref in listeners, "comments": comments}


def fetch(ref: PullRef) -> dict:
    with lock:
        return answer(ref, read(ref))


def frame(ref: PullRef, c: dict, replay: bool) -> str:
    """The text frame that brings a comment to the session: where it sits, the code it is on, and what it says.
    `replay` marks a comment the session may have had before it connected again."""
    keys = ("id", "path", "line", "start_line", "side", "commit", "code", "body")
    return json.dumps({"event": "comment", "pr": str(ref), **{k: c[k] for k in keys}, "replay": replay})


def deliver(ref: PullRef, comments: list[dict], replay: bool) -> None:
    """Send the comments to the session that listens on the pull request, if one does, and mark each one it took
    sent. A session that has gone stops listening. Call it with the lock held."""
    for c in comments:
        if not (send := listeners.get(ref)):
            return
        try:
            send(frame(ref, c, replay))
        except OSError:
            del listeners[ref]
            return
        if c["state"] == "waiting":
            c["state"] = "sent"


def add(
    ref: PullRef,
    *,
    path: str,
    line: int,
    start_line: int | None,
    side: Side,
    commit: str,
    code: list[str],
    body: str,
) -> dict:
    """Keep a new comment, with an id never used before, and send it to the session if one listens. Answers whether
    a session listens, and the comments."""
    with lock:
        comments = read(ref)
        c = {
            "id": uuid.uuid4().hex[:12],
            "path": path,
            "line": line,
            "start_line": start_line,
            "side": side,
            "commit": commit,
            "code": code,
            "body": body,
            "state": "waiting",
            "created": now(),
            "reply": "",
            "answered": "",
        }
        comments.append(c)
        deliver(ref, [c], replay=False)
        write(ref, comments)
        return answer(ref, comments)


def find(comments: list[dict], comment_id: str) -> dict:
    for c in comments:
        if c.get("id") == comment_id:
            return c
    raise NoComment(f"no Claude comment {comment_id}")


def reply(ref: PullRef, comment_id: str, body: str) -> dict:
    """Keep Claude's reply to a comment, in place of an earlier one, and answer the comment."""
    with lock:
        comments = read(ref)
        c = find(comments, comment_id)
        c.update(reply=body, state="answered", answered=now())
        write(ref, comments)
        return c


def delete(ref: PullRef, comment_id: str) -> dict:
    """Drop a comment. Answers whether a session listens, and the comments left."""
    with lock:
        comments = read(ref)
        comments.remove(find(comments, comment_id))
        write(ref, comments)
        return answer(ref, comments)


def attach(ref: PullRef, send: Send) -> None:
    """Make the session that send reaches the listener of the pull request, and send it every comment without a
    reply. Raises Busy while another session listens."""
    with lock:
        if ref in listeners:
            raise Busy(f"Another Claude session listens for differ comments on {ref}.")
        listeners[ref] = send
        comments = read(ref)
        if unanswered := [c for c in comments if c["state"] != "answered"]:
            deliver(ref, unanswered, replay=True)
            write(ref, comments)


def detach(ref: PullRef, send: Send) -> None:
    """Stop the session that send reaches from listening; a later session that listens now stays."""
    with lock:
        if listeners.get(ref) is send:
            del listeners[ref]
