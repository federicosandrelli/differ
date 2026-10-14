#!/usr/bin/env python3
"""Read and write the review comments of one GitHub pull request through `gh api graphql`.

Usage: comments.py <owner>/<repo>#<number>   prints the comments as JSON

Inline comments are grouped in review threads. A new comment goes into the viewer's pending review when there
is one (and starts one otherwise), or is posted at once as a review of its own, which is what GitHub's
"Add single comment" button does.
"""

from __future__ import annotations

import json
import subprocess
import sys

import build


class CommentError(Exception):
    pass


def graphql(query: str, **variables) -> dict:
    try:
        r = subprocess.run(
            ["gh", "api", "graphql", "--input", "-"],
            input=json.dumps({"query": query, "variables": variables}),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        raise CommentError(build.GH_MISSING) from None
    except subprocess.TimeoutExpired:
        raise CommentError("`gh api graphql` did not finish within 60 seconds") from None
    try:
        out = json.loads(r.stdout) if r.stdout.strip() else {}
    except json.JSONDecodeError:
        out = {}
    errors = out.get("errors") or []
    if r.returncode or errors:
        detail = "; ".join(e.get("message", "") for e in errors) or r.stderr.strip() or f"gh exited with {r.returncode}"
        raise CommentError(detail[:600])
    return out.get("data") or {}


THREAD_FIELDS = """
  id isResolved isOutdated path line startLine diffSide startDiffSide originalLine originalStartLine
  resolvedBy { login }
  comments(first: 100) { nodes { id author { login } body bodyHTML createdAt url state replyTo { id } pullRequestReview { id state } } }
"""

# The approvals, change requests and dismissed reviews, newest last, that _verdict reads.
VERDICTS = "verdicts: reviews(last: 100, states: [APPROVED, CHANGES_REQUESTED, DISMISSED]) { nodes { author { login } state commit { oid } } }"

READ = """
query($owner: String!, $repo: String!, $number: Int!, $after: String) {
  viewer { login }
  repository(owner: $owner, name: $repo) { pullRequest(number: $number) {
    id headRefOid bodyHTML
    reviewThreads(first: 100, after: $after) { pageInfo { hasNextPage endCursor } nodes { %s } }
    reviews(first: 100) { nodes { id author { login } state body bodyHTML createdAt submittedAt url } }
    %s
    comments(first: 100) { nodes { id author { login } body bodyHTML createdAt url } }
  } }
}
""" % (THREAD_FIELDS, VERDICTS)

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


def _verdict(reviews: list[dict], viewer: str) -> dict | None:
    """The viewer's verdict on the pull request: the state of their latest approval or change request and the commit
    it reviewed. None when they gave neither, or when GitHub dismissed the latest one. Takes the approvals, change
    requests and dismissed reviews, oldest first."""
    latest = next((r for r in reversed(reviews) if _author(r) == viewer), None)
    if not latest or latest["state"] == "DISMISSED":
        return None
    return {"state": latest["state"], "commit": (latest.get("commit") or {}).get("oid", "")}


def fetch(owner: str, repo: str, number: int) -> dict:
    """Every review thread, review and conversation comment of the pull request, plus the viewer's pending review
    and verdict."""
    data = graphql(READ, owner=owner, repo=repo, number=number, after=None)
    pr = data["repository"]["pullRequest"]
    threads = list(pr["reviewThreads"]["nodes"])
    page = pr["reviewThreads"]["pageInfo"]
    while page["hasNextPage"]:
        more = graphql(MORE_THREADS, owner=owner, repo=repo, number=number, after=page["endCursor"])["repository"][
            "pullRequest"
        ]["reviewThreads"]
        threads.extend(more["nodes"])
        page = more["pageInfo"]
    viewer = data["viewer"]["login"]
    pending = next((r for r in pr["reviews"]["nodes"] if r["state"] == "PENDING" and _author(r) == viewer), None)
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
    pending_id = pending["id"] if pending else None
    return {
        "viewer": viewer,
        "pr_id": pr["id"],
        "head": pr["headRefOid"],
        "body_html": pr.get("bodyHTML") or "",
        "pending": {"id": pending_id} if pending else None,
        "verdict": _verdict(pr["verdicts"]["nodes"], viewer),
        "threads": [th for t in threads if (th := _thread(t, pending_id))],
        "reviews": reviews,
        "comments": comments,
    }


def _pending_review(owner: str, repo: str, number: int) -> tuple[str, str, str | None, dict | None]:
    """The pull request's node id and head commit, the id of the viewer's pending review if there is one, and the
    viewer's verdict."""
    data = graphql(
        """
query($owner: String!, $repo: String!, $number: Int!) {
  viewer { login }
  repository(owner: $owner, name: $repo) { pullRequest(number: $number) {
    id headRefOid reviews(first: 10, states: [PENDING]) { nodes { id author { login } } }
    %s
  } }
}"""
        % VERDICTS,
        owner=owner,
        repo=repo,
        number=number,
    )
    pr = data["repository"]["pullRequest"]
    viewer = data["viewer"]["login"]
    pending = next((r["id"] for r in pr["reviews"]["nodes"] if _author(r) == viewer), None)
    return pr["id"], pr["headRefOid"], pending, _verdict(pr["verdicts"]["nodes"], viewer)


def add_thread(
    owner: str,
    repo: str,
    number: int,
    *,
    path: str,
    body: str,
    line: int,
    side: str,
    start_line: int | None = None,
    start_side: str | None = None,
    mode: str = "review",
) -> dict:
    """Comment on a line or a range of lines. mode "review" adds to the pending review (starting one when needed);
    mode "single" posts the comment at once."""
    pr_id, head, pending, _ = _pending_review(owner, repo, number)
    thread = {"path": path, "body": body, "line": line, "side": side}
    if start_line is not None and start_line != line:
        thread.update(startLine=start_line, startSide=start_side or side)
    if mode == "single":
        graphql(
            """
mutation($pr: ID!, $oid: GitObjectID!, $threads: [DraftPullRequestReviewThread]) {
  addPullRequestReview(input: {pullRequestId: $pr, commitOID: $oid, event: COMMENT, threads: $threads}) { pullRequestReview { id } }
}""",
            pr=pr_id,
            oid=head,
            threads=[thread],
        )
        return {"posted": True}
    if pending:
        graphql(
            """
mutation($review: ID!, $path: String!, $body: String!, $line: Int!, $side: DiffSide!, $startLine: Int, $startSide: DiffSide) {
  addPullRequestReviewThread(input: {pullRequestReviewId: $review, path: $path, body: $body, line: $line, side: $side,
                                     startLine: $startLine, startSide: $startSide}) { thread { id } }
}""",
            review=pending,
            path=path,
            body=body,
            line=line,
            side=side,
            startLine=thread.get("startLine"),
            startSide=thread.get("startSide"),
        )
        return {"pending": pending}
    data = graphql(
        """
mutation($pr: ID!, $oid: GitObjectID!, $threads: [DraftPullRequestReviewThread]) {
  addPullRequestReview(input: {pullRequestId: $pr, commitOID: $oid, threads: $threads}) { pullRequestReview { id } }
}""",
        pr=pr_id,
        oid=head,
        threads=[thread],
    )
    return {"pending": data["addPullRequestReview"]["pullRequestReview"]["id"]}


def reply(owner: str, repo: str, number: int, *, thread_id: str, body: str, mode: str = "review") -> dict:
    """Reply in a thread, into the pending review when there is one and mode is "review", else posted at once."""
    pending = _pending_review(owner, repo, number)[2] if mode == "review" else None
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
REPEATS = {
    "APPROVE": ("APPROVED", "You approved this commit already."),
    "REQUEST_CHANGES": ("CHANGES_REQUESTED", "You requested changes on this commit already."),
}


def submit(owner: str, repo: str, number: int, *, event: str, body: str = "") -> dict:
    """Submit the pending review, or a review without inline comments, as APPROVE, REQUEST_CHANGES or COMMENT."""
    if event not in ("APPROVE", "REQUEST_CHANGES", "COMMENT"):
        raise CommentError(f"Unknown review event {event!r}")
    if event == "REQUEST_CHANGES" and not body.strip():
        raise CommentError("Requesting changes needs a summary.")
    pr_id, head, pending, verdict = _pending_review(owner, repo, number)
    repeat = REPEATS.get(event)
    if repeat and verdict == {"state": repeat[0], "commit": head}:
        raise CommentError(repeat[1])
    if pending:
        graphql(
            """
mutation($review: ID!, $event: PullRequestReviewEvent!, $body: String) {
  submitPullRequestReview(input: {pullRequestReviewId: $review, event: $event, body: $body}) { pullRequestReview { id state } }
}""",
            review=pending,
            event=event,
            body=body,
        )
    else:
        if event == "COMMENT" and not body.strip():
            raise CommentError("A review without inline comments needs a summary.")
        graphql(
            """
mutation($pr: ID!, $oid: GitObjectID!, $event: PullRequestReviewEvent!, $body: String) {
  addPullRequestReview(input: {pullRequestId: $pr, commitOID: $oid, event: $event, body: $body}) { pullRequestReview { id state } }
}""",
            pr=pr_id,
            oid=head,
            event=event,
            body=body,
        )
    return {"submitted": event}


def discard(owner: str, repo: str, number: int) -> dict:
    pending = _pending_review(owner, repo, number)[2]
    if pending:
        graphql(
            "mutation($id: ID!) { deletePullRequestReview(input: {pullRequestReviewId: $id}) { pullRequestReview { id } } }",
            id=pending,
        )
    return {"discarded": bool(pending)}


def add_conversation_comment(owner: str, repo: str, number: int, *, body: str) -> dict:
    pr_id = _pending_review(owner, repo, number)[0]
    graphql(
        "mutation($pr: ID!, $body: String!) { addComment(input: {subjectId: $pr, body: $body}) { commentEdge { node { id } } } }",
        pr=pr_id,
        body=body,
    )
    return {"posted": True}


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    owner, repo, number = build.parse_ref(sys.argv[1])
    print(json.dumps(fetch(owner, repo, number), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
