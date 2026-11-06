"""The comments the reader sends to the Claude Code session that works on a pull request, instead of to GitHub, and
the session that listens for them.

A pull request's comments live in claude/<owner>/<repo>/<number>.json in the cache, oldest first, until the pull
request is merged or closed. Each one is a thread
on its lines: the reader's messages and Claude's replies, oldest first. A session listens over a WebSocket, one
session per pull request. While one listens, a new comment, or the reader's reply in a thread, goes to it at once as a
text frame, and when one connects, every thread without Claude's reply to the reader's last message goes to it."""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import sys
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import build
import classify
import commands
import github
from github import PullRef

Side = Literal["LEFT", "RIGHT"]
Author = Literal["reader", "claude"]
# Where the reader's last message stands. waiting: no session has it yet; sent: a listening session has it;
# answered: Claude replied to it.
State = Literal["waiting", "sent", "answered"]
# Sends one text frame to a listening session. Raises OSError once the session has gone.
Send = Callable[[str], None]
# The statuses that end a pull request, and with it its comments.
ENDED = ("merged", "closed")
SHA = re.compile(r"[0-9a-f]{40}")
# The skill that tells a session how to listen and reply, and the folder of the user's Claude Code skills, where
# install_skill writes it as differ-comments/SKILL.md.
SKILL = Path(__file__).parent / "skill" / "SKILL.md"
SKILLS = Path(os.environ.get("DIFFER_SKILLS") or Path.home() / ".claude" / "skills")

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
    return [with_messages(c) for c in data if isinstance(c, dict)] if isinstance(data, list) else []


def with_messages(c: dict) -> dict:
    """The comment with its messages, converted in place. A comment kept before threads had messages holds the
    reader's body and Claude's reply instead."""
    if "messages" in c:
        return c
    old = {k: c.pop(k, "") for k in ("body", "reply", "answered")}
    c["messages"] = [message("reader", old["body"], c.pop("created", ""))]
    if old["reply"]:
        c["messages"].append(message("claude", old["reply"], old["answered"]))
    return c


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


def message(author: Author, body: str, at: str) -> dict:
    return {"author": author, "body": body, "at": at}


def answer(ref: PullRef, listening: bool, comments: list[dict], head: str) -> dict:
    """Whether a session listens on the pull request, and its comments, each with the lines it sits on in the head
    commit (`placed`)."""
    return {"listening": listening, "comments": [{**c, "placed": place(ref, c, head)} for c in comments]}


def fetch(ref: PullRef, head: str) -> dict:
    with lock:
        listening, comments = ref in listeners, read(ref)
    return answer(ref, listening, comments, head)


def place(ref: PullRef, c: dict, head: str) -> dict:
    """The lines a comment sits on in the head commit, both None when the comment is outdated there. A comment on
    the new file follows its lines from the commit it was written on, and is outdated once one of them changes, or
    when the clone lacks what git needs to tell; a comment on the old file is outdated on any other commit."""
    if c["commit"] == head:
        return {"line": c["line"], "start_line": c["start_line"]}
    outdated = {"line": None, "start_line": None}
    if c["side"] == "LEFT" or not (SHA.fullmatch(c["commit"]) and SHA.fullmatch(head)):
        return outdated
    hunks = changes(build.mirror_of(ref), c["commit"], head, c["path"])
    lines = None if hunks is None else moved(hunks, c["start_line"] or c["line"], c["line"])
    if lines is None:
        return outdated
    first, last = lines
    return {"line": last, "start_line": None if c["start_line"] is None else first}


@functools.lru_cache(maxsize=256)
def changes(mirror: Path, old: str, new: str, path: str) -> list[classify.Hunk] | None:
    """The hunks that change the file at path from one commit to another, without unchanged lines, and empty when the
    file is the same on both; None when git can't compare them. Git reads only what the clone holds, and fetches
    nothing it lacks."""
    try:
        patch = build.git(
            mirror,
            "--no-lazy-fetch",
            "--literal-pathspecs",
            "diff-tree",
            "-p",
            "--no-renames",
            "-U0",
            old,
            new,
            "--",
            path,
        )
    except commands.CommandError:
        return None
    files = classify.parse_patch(patch.decode(errors="replace"))
    return files[0].hunks if files else []


def moved(hunks: list[classify.Hunk], first: int, last: int) -> tuple[int, int] | None:
    """Where lines first to last of the old file sit in the new one, given the hunks of a diff without unchanged
    lines; None when a hunk changes one of them or adds a line between two of them."""
    shift = 0
    for h in hunks:
        removed = [old for tag, old, _, _ in h.lines if tag == "-" and old is not None]
        added = sum(1 for line in h.lines if line[0] == "+")
        # A hunk that only adds lines puts them after its old_start.
        lo, hi = (removed[0], removed[-1]) if removed else (h.old_start + 1, h.old_start)
        if hi < first:
            shift += added - len(removed)
        elif lo <= last:
            return None
    return first + shift, last + shift


def frame(ref: PullRef, c: dict, replay: bool) -> str:
    """The text frame that brings a thread to the session: where it sits, the code it is on, every message of the
    thread, and `seen`, the number of those messages, for Claude's reply to name. The event is `comment` for a new
    thread, and `follow_up` once the reader replied in it. `replay` marks a thread the session may have had before it
    connected again."""
    keys = ("id", "path", "line", "start_line", "side", "commit", "code", "messages")
    event = "comment" if len(c["messages"]) == 1 else "follow_up"
    return json.dumps(
        {"event": event, "pr": str(ref), **{k: c[k] for k in keys}, "seen": len(c["messages"]), "replay": replay}
    )


def deliver(ref: PullRef, comments: list[dict], replay: bool) -> None:
    """Send the comments to the session that listens on the pull request, if one does, and mark each one it took
    sent, with `sent_count`, the number of messages the thread had, so that `reply` can tell whether the reader wrote
    again before Claude answered. A session that has gone stops listening. Call it with the lock held."""
    for c in comments:
        if not (send := listeners.get(ref)):
            return
        try:
            send(frame(ref, c, replay))
        except OSError:
            del listeners[ref]
            return
        c["sent_count"] = len(c["messages"])
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
    a session listens, and the comments, placed on the commit the new one was written on."""
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
            "state": "waiting",
            "messages": [message("reader", body, now())],
        }
        comments.append(c)
        deliver(ref, [c], replay=False)
        write(ref, comments)
        listening = ref in listeners
    return answer(ref, listening, comments, commit)


def find(comments: list[dict], comment_id: str) -> dict:
    for c in comments:
        if c.get("id") == comment_id:
            return c
    raise NoComment(f"no Claude comment {comment_id}")


def reply(ref: PullRef, comment_id: str, body: str, seen: int | None = None) -> dict:
    """Add Claude's reply to the end of a comment's thread. `seen` is the number of the thread's messages that
    Claude read before it replied; when None, the number the thread had when `deliver` last sent it, or all of them
    for a thread never sent. The state becomes `answered` unless the reader wrote after those messages. Answers the
    comment as kept."""
    with lock:
        comments = read(ref)
        c = find(comments, comment_id)
        if seen is None:
            seen = c.get("sent_count", len(c["messages"]))
        unread = c["messages"][seen:]
        c["messages"].append(message("claude", body, now()))
        if not any(m["author"] == "reader" for m in unread):
            c["state"] = "answered"
        write(ref, comments)
        return c


def follow_up(ref: PullRef, comment_id: str, body: str, head: str) -> dict:
    """Add the reader's reply to the end of a comment's thread, and send the thread to the session if one listens,
    else keep it waiting. Answers whether a session listens, and the comments, placed on the head commit."""
    with lock:
        comments = read(ref)
        c = find(comments, comment_id)
        c["messages"].append(message("reader", body, now()))
        c["state"] = "waiting"
        deliver(ref, [c], replay=False)
        write(ref, comments)
        listening = ref in listeners
    return answer(ref, listening, comments, head)


def delete(ref: PullRef, comment_id: str, head: str) -> dict:
    """Drop a comment. Answers whether a session listens, and the comments left, placed on the head commit."""
    with lock:
        comments = read(ref)
        comments.remove(find(comments, comment_id))
        write(ref, comments)
        listening = ref in listeners
    return answer(ref, listening, comments, head)


def expire(ref: PullRef) -> None:
    """Delete all of the pull request's comments."""
    with lock:
        path_of(ref).unlink(missing_ok=True)


def skill_marker(text: str) -> str:
    """The line that ends an installed copy of the skill, and names the shipped text it was copied from."""
    return f"<!-- differ skill {hashlib.sha256(text.encode()).hexdigest()[:12]} -->"


def install_skill() -> None:
    """Write the skill into the user's Claude Code skills, unless the copy there was installed from the text that
    ships now. A copy from an older or a newer differ is replaced; one the user edited stays until the shipped text
    changes. A failure to write goes to stderr."""
    text = SKILL.read_text()
    marker = skill_marker(text)
    path = SKILLS / "differ-comments" / "SKILL.md"
    try:
        if path.exists() and path.read_bytes().rstrip().endswith(marker.encode()):
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{text.rstrip()}\n\n{marker}\n")
    except OSError as e:
        sys.stderr.write(f"Could not install the differ-comments skill in {path.parent}: {e}\n")


def kept() -> list[PullRef]:
    """The pull requests with a comments file in the cache."""
    files = sorted((build.CACHE / "claude").glob("*/*/*.json"))
    return [PullRef(f.parent.parent.name, f.parent.name, int(f.stem)) for f in files if f.stem.isdigit()]


def sweep(expire: Callable[[PullRef], None] = expire, refs: list[PullRef] | None = None) -> None:
    """Expire every pull request among `refs`, by default those with a comments file, that GitHub reports merged or
    closed, with `expire`, which deletes its comments by default. A pull request GitHub can't return keeps them."""
    for ref, standing in github.pr_standings(kept() if refs is None else refs).items():
        if standing.status in ENDED:
            expire(ref)


def attach(ref: PullRef, send: Send) -> None:
    """Make the session that send reaches the listener of the pull request, and send it every comment without
    Claude's reply to the reader's last message. Raises Busy while another session listens."""
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
