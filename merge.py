#!/usr/bin/env python3
"""Read where a GitHub pull request stands for merging, and act on it, through `gh`.

Usage: merge.py <owner>/<repo>#<number>   prints the merge state as JSON

The merge state is what the author of a pull request needs to merge it: its checks on the head commit, the review
decision, whether GitHub can merge it, its auto-merge request, and the merge methods the repository allows. The
actions are merge, turning auto-merge on and off, close, reopen, mark ready for review, and re-run the failed jobs of
a workflow run.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict
from typing import Literal

import github
from github import PullRef, graphql

Method = Literal["SQUASH", "MERGE", "REBASE"]
CheckState = Literal["passed", "failed", "cancelled", "running", "queued", "skipped"]

READ = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    squashMergeAllowed mergeCommitAllowed rebaseMergeAllowed autoMergeAllowed deleteBranchOnMerge
    pullRequest(number: $number) {
      headRefOid title body state isDraft baseRefName headRefName headRepositoryOwner { login }
      mergeable mergeStateStatus reviewDecision
      autoMergeRequest { mergeMethod enabledAt enabledBy { login } }
      latestReviews(first: 100) { nodes { author { login } state } }
      commits(last: 1) { totalCount nodes { commit { statusCheckRollup { contexts(first: 100) { nodes {
        __typename
        ... on CheckRun {
          name status conclusion startedAt completedAt detailsUrl isRequired(pullRequestNumber: $number)
          checkSuite { workflowRun { databaseId workflow { name } } }
          steps(first: 100) { nodes { name conclusion } }
          annotations(first: 20) { nodes { message annotationLevel } }
        }
        ... on StatusContext { context state targetUrl createdAt isRequired(pullRequestNumber: $number) }
      } } } } } }
    }
  }
}
"""

# The words GitHub Actions annotates every failed job with. The failed state already says as much.
EXIT_NOTE = re.compile(r"^Process completed with exit code \d+\.$")

RUN_STATES: dict[str, CheckState] = {
    "SUCCESS": "passed",
    "FAILURE": "failed",
    "TIMED_OUT": "failed",
    "STARTUP_FAILURE": "failed",
    "ACTION_REQUIRED": "failed",
    "CANCELLED": "cancelled",
    "SKIPPED": "skipped",
    "NEUTRAL": "skipped",
    "STALE": "skipped",
}
STATUS_STATES: dict[str, CheckState] = {
    "SUCCESS": "passed",
    "FAILURE": "failed",
    "ERROR": "failed",
    "PENDING": "running",
    "EXPECTED": "queued",
}


def _check(node: dict) -> dict | None:
    """A check run or a commit status as the page shows it, or None for a node of another type."""
    if node.get("__typename") == "CheckRun":
        if node["status"] == "COMPLETED":
            state = RUN_STATES.get(node["conclusion"] or "", "skipped")
        else:
            state = "running" if node["status"] == "IN_PROGRESS" else "queued"
        run = ((node.get("checkSuite") or {}).get("workflowRun")) or {}
        steps = (node.get("steps") or {}).get("nodes") or []
        notes = [
            a["message"].strip()
            for a in (node.get("annotations") or {}).get("nodes") or []
            if a["annotationLevel"] == "FAILURE" and not EXIT_NOTE.match(a["message"].strip())
        ]
        return {
            "name": node["name"],
            "workflow": (run.get("workflow") or {}).get("name", ""),
            "state": state,
            "started": node["startedAt"],
            "completed": node["completedAt"],
            "url": node["detailsUrl"] or "",
            "required": node["isRequired"],
            "run": run.get("databaseId"),
            "step": next((s["name"] for s in steps if s["conclusion"] == "FAILURE"), ""),
            "notes": notes,
        }
    if node.get("__typename") == "StatusContext":
        return {
            "name": node["context"],
            "workflow": "",
            "state": STATUS_STATES.get(node["state"], "queued"),
            "started": node["createdAt"],
            "completed": None,
            "url": node["targetUrl"] or "",
            "required": node["isRequired"],
            "run": None,
            "step": "",
            "notes": [],
        }
    return None


def fetch(ref: PullRef) -> dict:
    """The merge state of the pull request now. `messages` holds the commit headline and body GitHub suggests for a
    squash and for a merge commit."""
    repo = graphql(READ, **asdict(ref))["repository"]
    pr = repo["pullRequest"]
    commits = pr["commits"]
    rollup = (commits["nodes"][0]["commit"]["statusCheckRollup"] or {}) if commits["nodes"] else {}
    checks = [c for n in (rollup.get("contexts") or {}).get("nodes") or [] if (c := _check(n))]
    reviews = pr["latestReviews"]["nodes"]
    allowed = {
        "SQUASH": repo["squashMergeAllowed"],
        "MERGE": repo["mergeCommitAllowed"],
        "REBASE": repo["rebaseMergeAllowed"],
    }
    auto = pr["autoMergeRequest"]
    head_owner = (pr["headRepositoryOwner"] or {}).get("login", ref.owner)
    return {
        "head": pr["headRefOid"],
        "status": github.status_of(pr["state"], pr["isDraft"]) or "",
        "base": pr["baseRefName"],
        "commits": commits["totalCount"],
        "mergeable": pr["mergeable"],
        "merge_state": pr["mergeStateStatus"],
        "review": pr["reviewDecision"] or "",
        "approvers": [_login(r) for r in reviews if r["state"] == "APPROVED"],
        "change_requesters": [_login(r) for r in reviews if r["state"] == "CHANGES_REQUESTED"],
        "methods": [m for m, ok in allowed.items() if ok],
        "auto_merge_allowed": repo["autoMergeAllowed"],
        "delete_branch": repo["deleteBranchOnMerge"],
        "auto_merge": {
            "method": auto["mergeMethod"],
            "by": _login(auto, "enabledBy"),
            "at": auto["enabledAt"],
        }
        if auto
        else None,
        "checks": checks,
        "messages": {
            "SQUASH": [f"{pr['title']} (#{ref.number})", pr["body"] or ""],
            "MERGE": [f"Merge pull request #{ref.number} from {head_owner}/{pr['headRefName']}", pr["title"]],
        },
    }


def _login(node: dict | None, key: str = "author") -> str:
    return ((node or {}).get(key) or {}).get("login", "")


def _pr_id(ref: PullRef) -> str:
    query = "query($owner: String!, $repo: String!, $number: Int!) { repository(owner: $owner, name: $repo) { pullRequest(number: $number) { id } } }"
    return graphql(query, **asdict(ref))["repository"]["pullRequest"]["id"]


def _message(method: Method, headline: str, body: str) -> dict:
    """The commit headline and body a merge sends: none for a rebase, which keeps the commits as they are, and none
    for an empty headline, which leaves GitHub's own."""
    if method == "REBASE" or not headline.strip():
        return {"headline": None, "body": None}
    return {"headline": headline.strip(), "body": body}


def merge(ref: PullRef, *, method: Method, head: str, headline: str = "", body: str = "") -> dict:
    """Merge the pull request now. GitHub refuses when its head is no longer `head`."""
    graphql(
        """
mutation($pr: ID!, $method: PullRequestMergeMethod!, $head: GitObjectID!, $headline: String, $body: String) {
  mergePullRequest(input: {pullRequestId: $pr, mergeMethod: $method, expectedHeadOid: $head,
                           commitHeadline: $headline, commitBody: $body}) { pullRequest { id state } }
}""",
        pr=_pr_id(ref),
        method=method,
        head=head,
        **_message(method, headline, body),
    )
    return {"merged": True}


def auto_merge(
    ref: PullRef, *, on: bool, method: Method = "SQUASH", head: str = "", headline: str = "", body: str = ""
) -> dict:
    """Turn auto-merge on, so GitHub merges the pull request once its requirements pass, or off. Turning it on
    fails when the head is no longer `head`."""
    if not on:
        graphql(
            "mutation($pr: ID!) { disablePullRequestAutoMerge(input: {pullRequestId: $pr}) { pullRequest { id } } }",
            pr=_pr_id(ref),
        )
        return {"auto_merge": False}
    graphql(
        """
mutation($pr: ID!, $method: PullRequestMergeMethod!, $head: GitObjectID!, $headline: String, $body: String) {
  enablePullRequestAutoMerge(input: {pullRequestId: $pr, mergeMethod: $method, expectedHeadOid: $head,
                                     commitHeadline: $headline, commitBody: $body}) { pullRequest { id } }
}""",
        pr=_pr_id(ref),
        method=method,
        head=head,
        **_message(method, headline, body),
    )
    return {"auto_merge": True}


# The mutation that moves a pull request to each status the page can ask for.
MOVES = {
    "close": "closePullRequest",
    "reopen": "reopenPullRequest",
    "ready": "markPullRequestReadyForReview",
}


def move(ref: PullRef, action: str) -> dict:
    """Close the pull request, reopen it, or mark it ready for review: an action of MOVES."""
    name = MOVES[action]
    graphql("mutation($pr: ID!) { %s(input: {pullRequestId: $pr}) { pullRequest { id } } }" % name, pr=_pr_id(ref))
    return {action: True}


def rerun_failed(ref: PullRef, run: int) -> dict:
    """Re-run the failed jobs of a GitHub Actions workflow run of the repository."""
    github.gh("api", "-X", "POST", f"repos/{ref.slug}/actions/runs/{run}/rerun-failed-jobs")
    return {"rerun": run}


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(json.dumps(fetch(PullRef.parse(sys.argv[1])), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
