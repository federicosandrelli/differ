from typing import Any

import pytest

import comments
from comments import Verdict
from github import PullRef

REF = PullRef("acme", "widgets", 1)


def comment(cid: str, state: str = "SUBMITTED", review: str | None = None, reply_to: str | None = None) -> dict:
    return {
        "id": cid,
        "author": {"login": "ann"},
        "body": f"body {cid}",
        "bodyHTML": f"<p>{cid}</p>",
        "createdAt": "2026-10-01T00:00:00Z",
        "url": f"https://github.com/c/{cid}",
        "state": state,
        "replyTo": {"id": reply_to} if reply_to else None,
        "pullRequestReview": {"id": review, "state": state} if review else None,
    }


def thread(tid: str, nodes: list[dict], resolved: bool = False, outdated: bool = False) -> dict:
    return {
        "id": tid,
        "isResolved": resolved,
        "isOutdated": outdated,
        "path": "app.py",
        "line": 5,
        "startLine": None,
        "diffSide": "RIGHT",
        "startDiffSide": None,
        "originalLine": 5,
        "originalStartLine": None,
        "resolvedBy": {"login": "bob"} if resolved else None,
        "comments": {"nodes": nodes},
    }


def test_thread_keeps_pending_comments_of_the_viewers_review_only() -> None:
    t = thread(
        "T1",
        [
            comment("c1"),
            comment("c2", "PENDING", review="mine", reply_to="c1"),
            comment("c3", "PENDING", review="theirs"),
        ],
        resolved=True,
    )
    result = comments._thread(t, "mine")
    assert result is not None
    assert [(c["id"], c["pending"], c["reply"]) for c in result["comments"]] == [
        ("c1", False, False),
        ("c2", True, True),
    ]
    assert result["resolved"] is True and result["resolved_by"] == "bob"
    assert result["side"] == "RIGHT" and result["line"] == 5


def test_thread_without_visible_comments_is_dropped() -> None:
    assert comments._thread(thread("T", [comment("c", "PENDING", review="theirs")]), None) is None
    assert comments._thread(thread("T", []), None) is None


def test_author_tolerates_missing_nodes() -> None:
    assert comments._author(None) == ""
    assert comments._author({"author": None}) == ""
    assert comments._author({"author": {"login": "x"}}) == "x"


def test_fetch_reads_every_page_and_shapes_reviews(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_graphql(query: str, **variables: Any) -> dict:
        calls.append(variables)
        if variables.get("after") is None:
            return {
                "viewer": {"login": "ann"},
                "repository": {
                    "pullRequest": {
                        "id": "PR1",
                        "headRefOid": "deadbeef",
                        "bodyHTML": "<p>hi</p>",
                        "state": "MERGED",
                        "isDraft": False,
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "CUR"},
                            "nodes": [
                                thread("T1", [comment("c1")]),
                                thread("T2", [comment("c2", "PENDING", review="R0")]),
                            ],
                        },
                        "pending": {"nodes": [{"id": "R0", "author": {"login": "ann"}}]},
                        "reviews": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "id": "R1",
                                    "author": {"login": "bob"},
                                    "state": "APPROVED",
                                    "body": None,
                                    "bodyHTML": None,
                                    "createdAt": "2026-10-01T00:00:00Z",
                                    "submittedAt": "2026-10-02T00:00:00Z",
                                    "url": "u1",
                                },
                            ],
                        },
                        "verdicts": {"nodes": [review("R1", "APPROVED", author="bob")]},
                        "comments": {"pageInfo": {"hasNextPage": True, "endCursor": "K"}, "nodes": [comment("k1")]},
                    }
                },
            }
        if variables["after"] == "K":
            more = {"comments": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [comment("k2")]}}
            return {"repository": {"pullRequest": more}}
        return {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [thread("T3", [comment("c3")], outdated=True)],
                    }
                }
            }
        }

    monkeypatch.setattr(comments, "graphql", fake_graphql)
    result = comments.fetch(PullRef("o", "r", 3))
    assert [c.get("after") for c in calls] == [None, "CUR", "K"]
    assert result["viewer"] == "ann" and result["pr_id"] == "PR1" and result["head"] == "deadbeef"
    assert result["status"] == "merged"
    assert result["pending"] == {"id": "R0"}
    assert [t["id"] for t in result["threads"]] == ["T1", "T2", "T3"]
    assert result["threads"][1]["comments"][0]["pending"] is True
    assert result["threads"][2]["outdated"] is True
    assert result["reviews"] == [
        {
            "id": "R1",
            "author": "bob",
            "state": "APPROVED",
            "body": "",
            "html": "",
            "created": "2026-10-02T00:00:00Z",
            "url": "u1",
        }
    ]
    assert result["verdict"] is None
    assert [c["id"] for c in result["comments"]] == ["k1", "k2"]


def test_request_changes_without_a_summary_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_graphql(query: str, **variables: Any) -> dict:
        raise AssertionError("GitHub must not be called")

    monkeypatch.setattr(comments, "graphql", fake_graphql)
    with pytest.raises(comments.ReviewRefused, match="needs a summary"):
        comments.submit(REF, event="REQUEST_CHANGES", body="  ")


def review(rid: str, state: str, author: str = "ann", commit: str = "c1") -> dict:
    return {"id": rid, "author": {"login": author}, "state": state, "commit": {"oid": commit}}


def test_verdict_is_the_viewers_latest_approval_or_change_request() -> None:
    reviews = [
        review("R1", "APPROVED", commit="c1"),
        review("R2", "CHANGES_REQUESTED", commit="c2"),
        review("R3", "APPROVED", author="bob", commit="c3"),
    ]
    assert comments._verdict(reviews, "ann") == Verdict("CHANGES_REQUESTED", "c2")
    assert comments._verdict(reviews, "bob") == Verdict("APPROVED", "c3")
    assert comments._verdict(reviews, "cid") is None


def test_a_dismissed_latest_review_leaves_no_verdict() -> None:
    assert comments._verdict([review("R1", "APPROVED"), review("R2", "DISMISSED")], "ann") is None


def pending_review_reply(reviews: list[dict], head: str = "c1") -> dict:
    return {
        "viewer": {"login": "ann"},
        "repository": {
            "pullRequest": {"id": "PR1", "headRefOid": head, "pending": {"nodes": []}, "verdicts": {"nodes": reviews}}
        },
    }


@pytest.mark.parametrize(
    ("event", "state", "message"),
    [("APPROVE", "APPROVED", "approved"), ("REQUEST_CHANGES", "CHANGES_REQUESTED", "requested changes on")],
)
def test_repeating_a_verdict_on_the_head_commit_is_refused(
    monkeypatch: pytest.MonkeyPatch, event: comments.Event, state: str, message: str
) -> None:
    def fake_graphql(query: str, **variables: Any) -> dict:
        assert not query.lstrip().startswith("mutation"), "nothing may be posted"
        return pending_review_reply([review("R1", state, commit="c1")])

    monkeypatch.setattr(comments, "graphql", fake_graphql)
    with pytest.raises(comments.ReviewRefused, match=f"You {message} this commit already"):
        comments.submit(REF, event=event, body="Again")


@pytest.mark.parametrize(("event", "head"), [("APPROVE", "c2"), ("REQUEST_CHANGES", "c1")])
def test_a_verdict_on_an_older_commit_or_a_different_verdict_goes_out(
    monkeypatch: pytest.MonkeyPatch, event: comments.Event, head: str
) -> None:
    posted: list[str] = []

    def fake_graphql(query: str, **variables: Any) -> dict:
        if query.lstrip().startswith("mutation"):
            posted.append(variables["event"])
            return {}
        return pending_review_reply([review("R1", "APPROVED", commit="c1")], head=head)

    monkeypatch.setattr(comments, "graphql", fake_graphql)
    comments.submit(REF, event=event, body="Again")
    assert posted == [event]
