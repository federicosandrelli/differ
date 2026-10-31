from typing import Any

import pytest

import merge
from github import PullRef

REF = PullRef("acme", "widgets", 7)


def run(name: str, status: str = "COMPLETED", conclusion: str | None = "SUCCESS", **extra: Any) -> dict:
    return {
        "__typename": "CheckRun",
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "startedAt": "2026-10-30T10:00:00Z",
        "completedAt": "2026-10-30T10:01:00Z" if status == "COMPLETED" else None,
        "detailsUrl": f"https://github.com/acme/widgets/actions/runs/9/job/{name}",
        "isRequired": False,
        "checkSuite": {"workflowRun": {"databaseId": 9, "workflow": {"name": "CI"}}},
        "steps": {"nodes": []},
        "annotations": {"nodes": []},
    } | extra


def answer(contexts: list[dict], **pr: Any) -> dict:
    return {
        "repository": {
            "squashMergeAllowed": True,
            "mergeCommitAllowed": False,
            "rebaseMergeAllowed": True,
            "autoMergeAllowed": True,
            "deleteBranchOnMerge": True,
            "pullRequest": {
                "headRefOid": "abc",
                "title": "Add widgets",
                "body": "Adds them.",
                "state": "OPEN",
                "isDraft": False,
                "baseRefName": "main",
                "headRefName": "widgets",
                "headRepositoryOwner": {"login": "ann"},
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
                "reviewDecision": "APPROVED",
                "autoMergeRequest": None,
                "latestReviews": {
                    "nodes": [
                        {"author": {"login": "bob"}, "state": "APPROVED"},
                        {"author": {"login": "cy"}, "state": "COMMENTED"},
                    ]
                },
                "commits": {
                    "totalCount": 3,
                    "nodes": [{"commit": {"statusCheckRollup": {"contexts": {"nodes": contexts}}}}],
                },
            }
            | pr,
        }
    }


def test_fetch_reads_the_checks_reviews_and_merge_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    contexts = [
        run("lint"),
        run("unit", status="IN_PROGRESS", conclusion=None),
        run("deploy", status="QUEUED", conclusion=None),
        {
            "__typename": "StatusContext",
            "context": "ci/legacy",
            "state": "ERROR",
            "targetUrl": None,
            "createdAt": "2026-10-30T10:00:00Z",
            "isRequired": True,
        },
    ]
    monkeypatch.setattr(merge, "graphql", lambda query, **v: answer(contexts))
    m = merge.fetch(REF)
    assert [(c["name"], c["workflow"], c["state"], c["required"]) for c in m["checks"]] == [
        ("lint", "CI", "passed", False),
        ("unit", "CI", "running", False),
        ("deploy", "CI", "queued", False),
        ("ci/legacy", "", "failed", True),
    ]
    assert m["methods"] == ["SQUASH", "REBASE"]
    assert m["approvers"] == ["bob"] and m["change_requesters"] == []
    assert (m["status"], m["base"], m["commits"], m["review"]) == ("open", "main", 3, "APPROVED")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://ci.example.com/1", None])
def test_a_check_keeps_a_web_address_only_as_its_link(url: str | None) -> None:
    check = merge._check(run("lint", detailsUrl=url))
    assert check is not None and check["url"] == ""


@pytest.mark.parametrize(
    ("conclusion", "state"),
    [
        ("FAILURE", "failed"),
        ("TIMED_OUT", "failed"),
        ("CANCELLED", "cancelled"),
        ("SKIPPED", "skipped"),
        ("NEUTRAL", "skipped"),
    ],
)
def test_a_completed_check_run_takes_the_state_of_its_conclusion(conclusion: str, state: str) -> None:
    check = merge._check(run("e2e", conclusion=conclusion))
    assert check is not None and check["state"] == state


def test_a_failed_check_names_its_failing_step_and_its_failure_notes() -> None:
    node = run(
        "e2e",
        conclusion="FAILURE",
        steps={
            "nodes": [{"name": "Set up job", "conclusion": "SUCCESS"}, {"name": "Run pytest", "conclusion": "FAILURE"}]
        },
        annotations={
            "nodes": [
                {"message": "Node.js 20 is deprecated.", "annotationLevel": "WARNING"},
                {"message": "AssertionError: expected 3 files, got 2", "annotationLevel": "FAILURE"},
                {"message": "Process completed with exit code 1.", "annotationLevel": "FAILURE"},
            ]
        },
    )
    check = merge._check(node)
    assert check is not None
    assert (check["step"], check["notes"], check["run"]) == (
        "Run pytest",
        ["AssertionError: expected 3 files, got 2"],
        9,
    )


def test_a_commit_without_checks_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = answer([])
    reply["repository"]["pullRequest"]["commits"]["nodes"][0]["commit"]["statusCheckRollup"] = None
    monkeypatch.setattr(merge, "graphql", lambda query, **v: reply)
    assert merge.fetch(REF)["checks"] == []


def test_fetch_reads_the_auto_merge_request(monkeypatch: pytest.MonkeyPatch) -> None:
    auto = {"mergeMethod": "SQUASH", "enabledAt": "2026-10-30T10:00:00Z", "enabledBy": {"login": "ann"}}
    monkeypatch.setattr(merge, "graphql", lambda query, **v: answer([], autoMergeRequest=auto, isDraft=True))
    m = merge.fetch(REF)
    assert m["auto_merge"] == {"method": "SQUASH", "by": "ann", "at": "2026-10-30T10:00:00Z"}
    assert m["status"] == "draft"


def mutations(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    """The mutations the module sends, by the name of the mutation, with their variables. A read answers the pull
    request's id."""
    sent: list[tuple[str, dict]] = []

    def fake_graphql(query: str, **variables: Any) -> dict:
        if not query.lstrip().startswith("mutation"):
            return {"repository": {"pullRequest": {"id": "PR_7"}}}
        sent.append((query.split("(input")[0].split("{")[-1].strip(), variables))
        return {}

    monkeypatch.setattr(merge, "graphql", fake_graphql)
    return sent


def test_a_squash_sends_the_headline_and_body_and_the_head_it_expects(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = mutations(monkeypatch)
    merge.merge(REF, method="SQUASH", head="abc", headline=" Add widgets (#7) ", body="Adds them.")
    assert sent == [
        (
            "mergePullRequest",
            {"pr": "PR_7", "method": "SQUASH", "head": "abc", "headline": "Add widgets (#7)", "body": "Adds them."},
        )
    ]


@pytest.mark.parametrize(("method", "headline"), [("REBASE", "Add widgets"), ("SQUASH", "  ")])
def test_a_rebase_or_an_empty_headline_leaves_the_message_to_github(
    monkeypatch: pytest.MonkeyPatch, method: merge.Method, headline: str
) -> None:
    sent = mutations(monkeypatch)
    merge.merge(REF, method=method, head="abc", headline=headline, body="Adds them.")
    assert sent[0][1] == {"pr": "PR_7", "method": method, "head": "abc", "headline": None, "body": None}


def test_auto_merge_turns_on_with_the_method_and_off(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = mutations(monkeypatch)
    merge.auto_merge(REF, on=True, method="MERGE", head="abc", headline="Merge #7", body="")
    merge.auto_merge(REF, on=False)
    assert [name for name, _ in sent] == ["enablePullRequestAutoMerge", "disablePullRequestAutoMerge"]
    assert sent[0][1] == {"pr": "PR_7", "method": "MERGE", "head": "abc", "headline": "Merge #7", "body": ""}


@pytest.mark.parametrize(
    ("action", "mutation"),
    [("reopen", "reopenPullRequest"), ("ready", "markPullRequestReadyForReview")],
)
def test_move_sends_the_mutation_of_its_action(monkeypatch: pytest.MonkeyPatch, action: str, mutation: str) -> None:
    sent = mutations(monkeypatch)
    assert merge.move(REF, action) == {action: True}
    assert sent == [(mutation, {"pr": "PR_7"})]


def test_rerun_failed_posts_to_the_runs_rerun_failed_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(merge.github, "gh", lambda *args, **kwargs: calls.append(args) or "")
    merge.rerun_failed(REF, 41)
    assert calls == [("api", "-X", "POST", "repos/acme/widgets/actions/runs/41/rerun-failed-jobs")]
