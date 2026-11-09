"""The page actions: what a review page asks the server to do for its pull request, each a POST to
/api/<owner>/<repo>/<number>/<action> with a JSON object, answered with a status and a JSON object.

  comment, reply, resolve, delete      a review thread on a line, a reply in one, its resolution, a comment's removal
  submit, discard                      the viewer's pending review, sent with a verdict or thrown away
  conversation                         a comment on the conversation
  request_review                       adds the viewer to the requested reviewers
  viewed                               marks a file viewed or not viewed while the head is the page's
  open                                 opens a file in an editor, on the pull request's branch
  merge, automerge, rebase             merges the pull request, asks GitHub to once it can, or has GitHub rebase the
                                       branch onto its base branch
  reopen, ready                        reopens the pull request, or marks it ready for review (merge.MOVES)
  rerun                                re-runs a workflow run's failed jobs
  rebuild                              rebuilds the page on the pull request's head commit
  claude, claude_follow_up, claude_reply, claude_delete
                                       a comment for Claude, the reader's reply in its thread, Claude's reply, its
                                       removal
  claude_open, claude_close, claude_paste
                                       the embedded session: start it with the terminal's cols and rows and the
                                       model, end it, paste text into its prompt and, with submit, send it
  claude_install                       install Claude Code, or update one too old, in the session's terminal, then
                                       start the session

`answer` reads the fields an action needs, each of its type, runs the action and gives the status of its answer: 404
for an action it does not know; 400 for a field that is missing or of another type, and for a review or an editor
that refuses (with the reason); 502 when a gh or git command fails. Any other failure is raised, for the server to
answer 500. The server checks who asks, and reads the JSON object, before it calls `answer`."""

from __future__ import annotations

from collections.abc import Callable

import build
import claude
import commands
import comments
import editors
import merge
import session
from fields import count_of, flag, line_number, lines_of, one_of, run_id, text_of
from github import PullRef

# An action: the pull request, the request's JSON object and the port this server listens on, to the answer's JSON.
Action = Callable[[PullRef, dict, int], dict]
ACTIONS: dict[str, Action] = {}


def answer(ref: PullRef, action: str, body: dict, port: int) -> tuple[int, dict]:
    """The status and the JSON object that answer the page's action on the pull request."""
    if (run := ACTIONS.get(action)) is None:
        return 404, {"error": f"Unknown action {action}"}
    try:
        return 200, run(ref, body, port)
    except (KeyError, ValueError) as exc:
        return 400, {"error": f"Bad request: {exc}"}
    except (comments.ReviewRefused, editors.Refused) as exc:
        return 400, {"error": str(exc)}
    except commands.CommandError as exc:
        return 502, {"error": str(exc)}


def action(*names: str) -> Callable[[Action], Action]:
    """Add the function to ACTIONS under each name."""

    def add(run: Action) -> Action:
        for name in names:
            ACTIONS[name] = run
        return run

    return add


@action("claude_open")
def _claude_open(ref: PullRef, body: dict, port: int) -> dict:
    return session.start(
        ref,
        port,
        count_of(body.get("cols", 80)),
        count_of(body.get("rows", 24)),
        one_of(body.get("model", session.DEFAULT_MODEL), session.Model),
    )


@action("claude_install")
def _claude_install(ref: PullRef, body: dict, port: int) -> dict:
    return session.install(ref, port, count_of(body.get("cols", 80)), count_of(body.get("rows", 24)))


@action("claude_close")
def _claude_close(ref: PullRef, body: dict, port: int) -> dict:
    session.close(ref)
    return {}


@action("claude_paste")
def _claude_paste(ref: PullRef, body: dict, port: int) -> dict:
    session.paste(ref, text_of(body["text"]), body.get("submit") is True)
    return {}


@action("comment")
def _comment(ref: PullRef, body: dict, port: int) -> dict:
    start_side = body.get("start_side")
    return comments.add_thread(
        ref,
        path=body["path"],
        body=body["body"],
        line=line_number(body["line"]),
        side=one_of(body.get("side", "RIGHT"), comments.Side),
        start_line=None if body.get("start_line") is None else line_number(body["start_line"]),
        start_side=None if start_side is None else one_of(start_side, comments.Side),
    )


@action("reply")
def _reply(ref: PullRef, body: dict, port: int) -> dict:
    return comments.reply(body["thread"], body["body"])


@action("resolve")
def _resolve(ref: PullRef, body: dict, port: int) -> dict:
    return comments.resolve(body["thread"], bool(body.get("resolved", True)))


@action("delete")
def _delete(ref: PullRef, body: dict, port: int) -> dict:
    return comments.delete_comment(body["comment"])


@action("submit")
def _submit(ref: PullRef, body: dict, port: int) -> dict:
    return comments.submit(ref, event=one_of(body["event"], comments.Event), body=body.get("body", ""))


@action("discard")
def _discard(ref: PullRef, body: dict, port: int) -> dict:
    return comments.discard(ref)


@action("conversation")
def _conversation(ref: PullRef, body: dict, port: int) -> dict:
    return comments.add_conversation_comment(ref, body=body["body"])


@action("request_review")
def _request_review(ref: PullRef, body: dict, port: int) -> dict:
    return comments.request_review(ref)


@action("viewed")
def _viewed(ref: PullRef, body: dict, port: int) -> dict:
    return comments.mark_viewed(ref, path=str(body["path"]), viewed=flag(body["viewed"]), head=str(body["head"]))


@action("merge")
def _merge(ref: PullRef, body: dict, port: int) -> dict:
    return merge.merge(
        ref,
        method=one_of(body["method"], merge.Method),
        head=str(body["head"]),
        headline=str(body.get("headline", "")),
        body=str(body.get("body", "")),
    )


@action("automerge")
def _automerge(ref: PullRef, body: dict, port: int) -> dict:
    return merge.auto_merge(
        ref,
        on=flag(body["on"]),
        method=one_of(body.get("method", "SQUASH"), merge.Method),
        head=str(body.get("head", "")),
        headline=str(body.get("headline", "")),
        body=str(body.get("body", "")),
    )


@action("rebase")
def _rebase(ref: PullRef, body: dict, port: int) -> dict:
    return merge.rebase(ref, head=str(body["head"]))


def _move(name: str) -> Action:
    def run(ref: PullRef, body: dict, port: int) -> dict:
        return merge.move(ref, name)

    return run


for _name in merge.MOVES:
    ACTIONS[_name] = _move(_name)


@action("rerun")
def _rerun(ref: PullRef, body: dict, port: int) -> dict:
    return merge.rerun_failed(ref, run_id(body["run"]))


@action("claude")
def _claude(ref: PullRef, body: dict, port: int) -> dict:
    start_line = body.get("start_line")
    return claude.add(
        ref,
        path=str(body["path"]),
        line=line_number(body["line"]),
        start_line=None if start_line is None else line_number(start_line),
        side=one_of(body.get("side", "RIGHT"), claude.Side),
        commit=str(body["commit"]),
        code=lines_of(body.get("code", [])),
        body=text_of(body["body"]),
    )


@action("claude_follow_up")
def _claude_follow_up(ref: PullRef, body: dict, port: int) -> dict:
    return claude.follow_up(ref, str(body["id"]), text_of(body["body"]), str(body.get("head", "")))


@action("claude_reply")
def _claude_reply(ref: PullRef, body: dict, port: int) -> dict:
    return claude.reply(ref, str(body["id"]), text_of(body["body"]), count_of(body["seen"]))


@action("claude_delete")
def _claude_delete(ref: PullRef, body: dict, port: int) -> dict:
    return claude.delete(ref, str(body["id"]), str(body.get("head", "")))


@action("rebuild")
def _rebuild(ref: PullRef, body: dict, port: int) -> dict:
    return {"state": build.rebuild(ref, shown=str(body["head"]))}


@action("open")
def _open(ref: PullRef, body: dict, port: int) -> dict:
    editors.open_file(ref, str(body["editor"]), str(body["path"]), line_number(body["line"]))
    return {}
