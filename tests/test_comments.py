from typing import Any

import pytest

import comments


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


def test_fetch_pages_threads_and_shapes_reviews(monkeypatch: pytest.MonkeyPatch) -> None:
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
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "CUR"},
                            "nodes": [
                                thread("T1", [comment("c1")]),
                                thread("T2", [comment("c2", "PENDING", review="R0")]),
                            ],
                        },
                        "reviews": {
                            "nodes": [
                                {
                                    "id": "R0",
                                    "author": {"login": "ann"},
                                    "state": "PENDING",
                                    "body": "",
                                    "bodyHTML": "",
                                    "createdAt": "2026-10-01T00:00:00Z",
                                    "submittedAt": None,
                                    "url": "u0",
                                },
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
                            ]
                        },
                        "comments": {"nodes": [comment("k1")]},
                    }
                },
            }
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
    result = comments.fetch("o", "r", 3)
    assert [c.get("after") for c in calls] == [None, "CUR"]
    assert result["viewer"] == "ann" and result["pr_id"] == "PR1" and result["head"] == "deadbeef"
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
    assert [c["id"] for c in result["comments"]] == ["k1"]
