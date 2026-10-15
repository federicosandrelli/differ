#!/usr/bin/env python3
"""Read and write the review comments of one GitHub pull request through `gh api graphql`.

Usage: comments.py <owner>/<repo>#<number>   prints the comments as JSON

Inline comments are grouped in review threads. A new comment goes into the viewer's pending review when there
is one (and starts one otherwise), or is posted at once as a review of its own, which is what GitHub's
"Add single comment" button does.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from typing import Literal

import github
from github import PullRef, graphql

Side = Literal["LEFT", "RIGHT"]
Event = Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"]
# "review" adds a comment to the viewer's pending review, starting one when needed; "single" posts it at once.
Mode = Literal["review", "single"]
VerdictState = Literal["APPROVED", "CHANGES_REQUESTED"]


class ReviewRefused(Exception):
    """GitHub would take the review, but the page must not send it."""


@dataclass(frozen=True)
class Verdict:
    state: VerdictState
    commit: str


@dataclass(frozen=True)
class Target:
    """What a write to the pull request needs: its node id and head commit, the id of the viewer's pending review
    if there is one, and the viewer's verdict."""

    pr_id: str
    head: str
    pending: str | None
    verdict: Verdict | None


THREAD_FIELDS = """
  id isResolved isOutdated path line startLine diffSide startDiffSide originalLine originalStartLine
  resolvedBy { login }
  comments(first: 100) { nodes { id author { login } body bodyHTML createdAt url state replyTo { id } pullRequestReview { id state } } }
"""

# The approvals, change requests and dismissed reviews, newest last, that _verdict reads.
VERDICTS = "verdicts: reviews(last: 100, states: [APPROVED, CHANGES_REQUESTED, DISMISSED]) { nodes { author { login } state commit { oid } } }"

# The viewer's pending review, which GitHub shows only to its author. It is asked for by state, so it is found
# however many reviews the pull request has.
PENDING = "pending: reviews(first: 10, states: [PENDING]) { nodes { id author { login } } }"

READ = """
query($owner: String!, $repo: String!, $number: Int!, $after: String) {
  viewer { login }
  repository(owner: $owner, name: $repo) { pullRequest(number: $number) {
    id headRefOid bodyHTML state isDraft
    reviewThreads(first: 100, after: $after) { pageInfo { hasNextPage endCursor } nodes { %s } }
    reviews(first: 100) { nodes { id author { login } state body bodyHTML createdAt submittedAt url } }
    %s
    %s
    comments(first: 100) { nodes { id author { login } body bodyHTML createdAt url } }
  } }
}
""" % (THREAD_FIELDS, PENDING, VERDICTS)

MORE_THREADS = (
    """
query($owner: String!, $repo: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $repo) { pullRequest(number: $number) {
    reviewThreads(first: 100, after: $after) { pageInfo { hasNextPage endCursor } nodes { %s } }
  } }
}
"""
    % THREAD_FIELDS
)


def _author(node: dict | None) -> str:
    return ((node or {}).get("author") or {}).get("login", "")


def _thread(t: dict, pending_review: str | None) -> dict | None:
    """The thread as the page shows it, or None when it has no comments left: a thread outlives its comments, and
    for a while after a pending review is deleted its comments still come back as pending."""
    comments = [
        {
            "id": c["id"],
            "author": _author(c),
            "body": c["body"],
            "html": c.get("bodyHTML") or "",
            "created": c["createdAt"],
            "url": c["url"],
            "pending": c["state"] == "PENDING",
            "reply": bool(c.get("replyTo")),
        }
        for c in t["comments"]["nodes"]
        if c["state"] != "PENDING" or (c.get("pullRequestReview") or {}).get("id") == pending_review
    ]
    if not comments:
        return None
    return {
        "id": t["id"],
        "path": t["path"],
        "resolved": t["isResolved"],
        "outdated": t["isOutdated"],
        "resolved_by": (t.get("resolvedBy") or {}).get("login", ""),
        "line": t["line"],
        "start_line": t["startLine"],
        "side": t["diffSide"],
        "start_side": t["startDiffSide"],
        "original_line": t["originalLine"],
        "original_start_line": t["originalStartLine"],
        "comments": comments,
    }


def _verdict(reviews: list[dict], viewer: str) -> Verdict | None:
    """The viewer's verdict on the pull request: the state of their latest approval or change request and the commit
    it reviewed. None when they gave neither, or when GitHub dismissed the latest one. Takes the approvals, change
    requests and dismissed reviews, oldest first."""
    latest = next((r for r in reversed(reviews) if _author(r) == viewer), None)
    if not latest or latest["state"] == "DISMISSED":
        return None
    return Verdict(latest["state"], (latest["commit"] or {}).get("oid", ""))


def fetch(ref: PullRef) -> dict:
    """Every review thread, review and conversation comment of the pull request, plus the viewer's pending review
    and verdict, and the pull request's status now: a merge or a close does not move the head, so the page, built
    per head, learns them here."""
    data = graphql(READ, **asdict(ref), after=None)
    pr = data["repository"]["pullRequest"]
    threads = list(pr["reviewThreads"]["nodes"])
    page = pr["reviewThreads"]["pageInfo"]
    while page["hasNextPage"]:
        more = graphql(MORE_THREADS, **asdict(ref), after=page["endCursor"])["repository"]["pullRequest"][
            "reviewThreads"
        ]
        threads.extend(more["nodes"])
        page = more["pageInfo"]
    viewer = data["viewer"]["login"]
    pending_id = _pending_id(pr, viewer)
    reviews = [
        {
            "id": r["id"],
            "author": _author(r),
            "state": r["state"],
            "body": r["body"] or "",
            "html": r.get("bodyHTML") or "",
            "created": r["submittedAt"] or r["createdAt"],
            "url": r["url"],
        }
        for r in pr["reviews"]["nodes"]
        if r["state"] != "PENDING"
    ]
    comments = [
        {
            "id": c["id"],
            "author": _author(c),
            "body": c["body"],
            "html": c.get("bodyHTML") or "",
            "created": c["createdAt"],
            "url": c["url"],
        }
        for c in pr["comments"]["nodes"]
    ]
    return {
        "viewer": viewer,
        "pr_id": pr["id"],
        "head": pr["headRefOid"],
        "body_html": pr.get("bodyHTML") or "",
        "status": github.status_of(pr["state"], pr["isDraft"]) or "",
        "pending": {"id": pending_id} if pending_id else None,
        "verdict": asdict(verdict) if (verdict := _verdict(pr["verdicts"]["nodes"], viewer)) else None,
        "threads": [th for t in threads if (th := _thread(t, pending_id))],
        "reviews": reviews,
        "comments": comments,
    }


def _target(ref: PullRef) -> Target:
    data = graphql(
        """
query($owner: String!, $repo: String!, $number: Int!) {
  viewer { login }
  repository(owner: $owner, name: $repo) { pullRequest(number: $number) {
    id headRefOid
    %s
    %s
  } }
}"""
        % (PENDING, VERDICTS),
        **asdict(ref),
    )
    pr = data["repository"]["pullRequest"]
    viewer = data["viewer"]["login"]
    return Target(pr["id"], pr["headRefOid"], _pending_id(pr, viewer), _verdict(pr["verdicts"]["nodes"], viewer))


def _pending_id(pr: dict, viewer: str) -> str | None:
    return next((r["id"] for r in pr["pending"]["nodes"] if _author(r) == viewer), None)


def add_thread(
    ref: PullRef,
    *,
    path: str,
    body: str,
    line: int,
    side: Side,
    start_line: int | None = None,
    start_side: Side | None = None,
    mode: Mode = "review",
) -> dict:
    """Comment on a line or a range of lines."""
    target = _target(ref)
    thread = {"path": path, "body": body, "line": line, "side": side}
    if start_line is not None and start_line != line:
        thread.update(startLine=start_line, startSide=start_side or side)
    if mode == "single":
        graphql(
            """
mutation($pr: ID!, $oid: GitObjectID!, $threads: [DraftPullRequestReviewThread]) {
  addPullRequestReview(input: {pullRequestId: $pr, commitOID: $oid, event: COMMENT, threads: $threads}) { pullRequestReview { id } }
}""",
            pr=target.pr_id,
            oid=target.head,
            threads=[thread],
        )
        return {"posted": True}
    if target.pending:
        graphql(
            """
mutation($review: ID!, $path: String!, $body: String!, $line: Int!, $side: DiffSide!, $startLine: Int, $startSide: DiffSide) {
  addPullRequestReviewThread(input: {pullRequestReviewId: $review, path: $path, body: $body, line: $line, side: $side,
                                     startLine: $startLine, startSide: $startSide}) { thread { id } }
}""",
            review=target.pending,
            path=path,
            body=body,
            line=line,
            side=side,
            startLine=thread.get("startLine"),
            startSide=thread.get("startSide"),
        )
        return {"pending": target.pending}
    data = graphql(
        """
mutation($pr: ID!, $oid: GitObjectID!, $threads: [DraftPullRequestReviewThread]) {
  addPullRequestReview(input: {pullRequestId: $pr, commitOID: $oid, threads: $threads}) { pullRequestReview { id } }
}""",
        pr=target.pr_id,
        oid=target.head,
        threads=[thread],
    )
    return {"pending": data["addPullRequestReview"]["pullRequestReview"]["id"]}


def reply(ref: PullRef, *, thread_id: str, body: str, mode: Mode = "review") -> dict:
    """Reply in a thread, into the pending review when there is one and mode is "review", else posted at once."""
    pending = _target(ref).pending if mode == "review" else None
    graphql(
        """
mutation($thread: ID!, $body: String!, $review: ID) {
  addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: $thread, body: $body, pullRequestReviewId: $review}) { comment { id } }
}""",
        thread=thread_id,
        body=body,
        review=pending,
    )
    return {"pending": pending}


def resolve(thread_id: str, resolved: bool) -> dict:
    name = "resolveReviewThread" if resolved else "unresolveReviewThread"
    graphql("mutation($id: ID!) { %s(input: {threadId: $id}) { thread { id isResolved } } }" % name, id=thread_id)
    return {"resolved": resolved}


def delete_comment(comment_id: str) -> dict:
    graphql(
        "mutation($id: ID!) { deletePullRequestReviewComment(input: {id: $id}) { pullRequestReview { id } } }",
        id=comment_id,
    )
    return {"deleted": comment_id}


# The verdict each review event gives, and why the event is refused when the viewer gave that verdict on the head
# commit already.
REPEATS: dict[Event, tuple[VerdictState, str]] = {
    "APPROVE": ("APPROVED", "You approved this commit already."),
    "REQUEST_CHANGES": ("CHANGES_REQUESTED", "You requested changes on this commit already."),
}


def submit(ref: PullRef, *, event: Event, body: str = "") -> dict:
    """Submit the pending review, or a review without inline comments."""
    if event == "REQUEST_CHANGES" and not body.strip():
        raise ReviewRefused("Requesting changes needs a summary.")
    target = _target(ref)
    repeat = REPEATS.get(event)
    if repeat and target.verdict == Verdict(repeat[0], target.head):
        raise ReviewRefused(repeat[1])
    if target.pending:
        graphql(
            """
mutation($review: ID!, $event: PullRequestReviewEvent!, $body: String) {
  submitPullRequestReview(input: {pullRequestReviewId: $review, event: $event, body: $body}) { pullRequestReview { id state } }
}""",
            review=target.pending,
            event=event,
            body=body,
        )
    else:
        if event == "COMMENT" and not body.strip():
            raise ReviewRefused("A review without inline comments needs a summary.")
        graphql(
            """
mutation($pr: ID!, $oid: GitObjectID!, $event: PullRequestReviewEvent!, $body: String) {
  addPullRequestReview(input: {pullRequestId: $pr, commitOID: $oid, event: $event, body: $body}) { pullRequestReview { id state } }
}""",
            pr=target.pr_id,
            oid=target.head,
            event=event,
            body=body,
        )
    return {"submitted": event}


def discard(ref: PullRef) -> dict:
    pending = _target(ref).pending
    if pending:
        graphql(
            "mutation($id: ID!) { deletePullRequestReview(input: {pullRequestReviewId: $id}) { pullRequestReview { id } } }",
            id=pending,
        )
    return {"discarded": bool(pending)}


def add_conversation_comment(ref: PullRef, *, body: str) -> dict:
    pr_id = _target(ref).pr_id
    graphql(
        "mutation($pr: ID!, $body: String!) { addComment(input: {subjectId: $pr, body: $body}) { commentEdge { node { id } } } }",
        pr=pr_id,
        body=body,
    )
    return {"posted": True}


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(json.dumps(fetch(PullRef.parse(sys.argv[1])), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
