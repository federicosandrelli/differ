from typing import Any

import pytest

import actions
import commands
from github import PullRef

REF = PullRef("o", "r", 1)


@pytest.mark.parametrize(
    ("action", "fields", "why"),
    [
        ("submit", {"event": "MERGE"}, "'MERGE' is not one of 'APPROVE', 'REQUEST_CHANGES', 'COMMENT'"),
        ("comment", {"path": "a.py", "body": "b", "line": 1, "side": "UP"}, "'UP' is not one of 'LEFT', 'RIGHT'"),
        ("comment", {"path": "a.py", "body": "b", "line": None}, "None is not a line number"),
        ("comment", {"path": "a.py", "body": "b", "line": 2, "start_line": "1"}, "'1' is not a line number"),
        ("open", {"editor": "vscode", "path": "a.py", "line": 0}, "0 is not a line number"),
        ("open", {"editor": "vscode", "line": 1}, "'path'"),
        (
            "merge",
            {"method": "FAST_FORWARD", "head": "abc"},
            "'FAST_FORWARD' is not one of 'SQUASH', 'MERGE', 'REBASE'",
        ),
        ("automerge", {"method": "SQUASH"}, "'on'"),
        ("automerge", {"on": "false"}, "'false' is not true or false"),
        ("rebase", {}, "'head'"),
        ("viewed", {"path": "a.py", "viewed": "true", "head": "abc"}, "'true' is not true or false"),
        ("viewed", {"viewed": True, "head": "abc"}, "'path'"),
        ("viewed", {"path": "a.py", "viewed": True}, "'head'"),
        ("rerun", {"run": "41"}, "'41' is not a workflow run id"),
        ("rerun", {"run": 0}, "0 is not a workflow run id"),
        ("claude_open", {"model": "gpt-4"}, "'gpt-4' is not one of"),
        ("claude_open", {"cols": -1}, "-1 is not a count"),
        ("claude_paste", {"text": " "}, "' ' is not a text"),
        ("claude", {"path": "a.py", "line": 1, "commit": "c", "body": "b", "code": [1]}, "[1] is not a list of lines"),
        ("claude_reply", {"id": "c1", "body": "b", "seen": True}, "True is not a count"),
    ],
)
def test_a_field_outside_its_type_answers_400_and_runs_nothing(
    action: str, fields: dict, why: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def ran(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("the action ran")

    monkeypatch.setattr(actions.comments, "graphql", ran)
    monkeypatch.setattr(actions.merge, "graphql", ran)
    monkeypatch.setattr(actions.merge.github, "gh", ran)
    for name in ("start", "paste"):
        monkeypatch.setattr(actions.session, name, ran)
    for name in ("add", "reply"):
        monkeypatch.setattr(actions.claude, name, ran)
    code, answer = actions.answer(REF, action, fields, 8765)
    assert code == 400 and answer["error"].startswith(f"Bad request: {why}")


def test_a_refused_review_answers_400_with_the_reason() -> None:
    assert actions.answer(REF, "submit", {"event": "REQUEST_CHANGES", "body": " "}, 8765) == (
        400,
        {"error": "Requesting changes needs a summary."},
    )


def test_a_failed_command_answers_502_with_its_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any) -> dict:
        raise commands.CommandError("`gh api graphql --input -` failed: boom")

    monkeypatch.setattr(actions.comments, "discard", fail)
    assert actions.answer(REF, "discard", {}, 8765) == (502, {"error": "`gh api graphql --input -` failed: boom"})


def test_an_unknown_action_answers_404() -> None:
    assert actions.answer(REF, "close", {}, 8765) == (404, {"error": "Unknown action close"})


def test_an_unexpected_failure_is_raised_for_the_server_to_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args: Any) -> dict:
        raise RuntimeError("boom")

    monkeypatch.setattr(actions.comments, "resolve", explode)
    with pytest.raises(RuntimeError, match="boom"):
        actions.answer(REF, "resolve", {"thread": "T_1"}, 8765)


@pytest.mark.parametrize(
    ("action", "body", "called"),
    [
        ("claude_open", {}, ("start", (REF, 8765, 80, 24, "claude-opus-5-5"))),
        (
            "claude_open",
            {"cols": 100, "rows": 30, "model": "claude-haiku-5-5"},
            ("start", (REF, 8765, 100, 30, "claude-haiku-5-5")),
        ),
        ("reopen", {}, ("move", (REF, "reopen"))),
        ("ready", {}, ("move", (REF, "ready"))),
        ("rerun", {"run": 41}, ("rerun_failed", (REF, 41))),
        ("claude_reply", {"id": "c1", "body": "Done", "seen": 2}, ("reply", (REF, "c1", "Done", 2))),
    ],
)
def test_an_action_reaches_its_module_with_the_fields_read(
    action: str, body: dict, called: tuple[str, tuple], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, tuple]] = []
    for module, name in (
        (actions.session, "start"),
        (actions.merge, "move"),
        (actions.merge, "rerun_failed"),
        (actions.claude, "reply"),
    ):
        monkeypatch.setattr(module, name, lambda *args, name=name: calls.append((name, args)) or {"ok": True})
    assert actions.answer(REF, action, body, 8765) == (200, {"ok": True})
    assert calls == [called]
