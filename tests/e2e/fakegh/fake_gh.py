"""A stand-in for the GitHub CLI, answering the commands the app runs from files in FAKE_GH_STATE.

Each call adds its arguments to calls.log, as a JSON list on a line of its own. A call fails, with exit code 1, when
its arguments start with the words of a line of fail.txt.

`gh auth status` reports a signed-in user.
`gh pr view <n> --repo o/r --json <fields> [--jq .field]` prints pr<n>.json, or one field of it.
`gh pr view --json number --jq .number` prints the <n> of a pr<n>.json with the branch checked out in the working
folder as its `headRefName`, or fails when it finds none.
`gh pr checkout <n> --repo o/r` checks out the pull request's branch in the working folder with the git commands
gh 2.86.0 runs, and fails with git's error when one fails.
`gh repo clone o/r <dest> [-- --bare ...]` clones the repo named in repo.txt, bare when asked; `gh repo set-default`
does nothing.
`gh api -H <accept> repos/o/r/contents/<path>?ref=<ref>` prints the file at that ref of the repo named in repo.txt,
or fails when it has none.
`gh api graphql --input -` answers a search with the nodes search.json lists for its query, or none; a merge state
query from merge<n>.json, over a merge state without checks; any other read query from comments.json, with the
status of pr<n>.json. A mutation waits while hold.txt exists, then fails when fail.txt lists it by then, else is
appended to mutations.log. A review posted with threads and a reply add their comments to comments.json, so the page
sees what it sent, and the answer holds the new comment's id. A merge, a close, a reopen or a mark ready moves the
status in pr<n>.json, and turning auto-merge on or off changes merge<n>.json.
`gh api -X POST <endpoint>` does nothing.
"""

import json
import os
import subprocess
import sys
import time
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path


def state_dir() -> Path:
    return Path(os.environ["FAKE_GH_STATE"])


def pr_meta(number: str) -> dict:
    return json.loads((state_dir() / f"pr{number}.json").read_text())


def pr_view(args: list[str]) -> None:
    meta = pr_meta(args[0])
    if "--jq" in args:
        print(meta[args[args.index("--jq") + 1].lstrip(".")])
    else:
        print(json.dumps(meta))


def pr_file(number: object) -> Path:
    return state_dir() / f"pr{number}.json"


def merge_state(number: object) -> dict:
    """The repository and pull request fields of a merge state query: a clean, open pull request without checks,
    with the fields merge<n>.json gives over them."""
    path = state_dir() / f"merge{number}.json"
    given = json.loads(path.read_text()) if path.exists() else {}
    meta = json.loads(pr_file(number).read_text())
    pr = {
        "headRefOid": meta["headRefOid"],
        "title": meta["title"],
        "body": meta["body"],
        "state": meta["state"],
        "isDraft": meta["isDraft"],
        "baseRefName": meta["baseRefName"],
        "headRefName": meta["headRefName"],
        "headRepositoryOwner": {"login": "acme"},
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "reviewDecision": None,
        "autoMergeRequest": None,
        "latestReviews": {"nodes": []},
        "commits": {"totalCount": 1, "nodes": [{"commit": {"statusCheckRollup": None}}]},
    } | given.get("pullRequest", {})
    repo = {
        "squashMergeAllowed": True,
        "mergeCommitAllowed": True,
        "rebaseMergeAllowed": True,
        "autoMergeAllowed": True,
        "deleteBranchOnMerge": True,
    } | given.get("repository", {})
    return repo | {"pullRequest": pr}


# The status fields each mutation that moves a pull request sets in its pr<n>.json.
MOVES = {
    "mergePullRequest(": {"state": "MERGED"},
    "closePullRequest(": {"state": "CLOSED"},
    "reopenPullRequest(": {"state": "OPEN"},
    "markPullRequestReadyForReview(": {"isDraft": False},
}


def move(query: str, variables: dict) -> None:
    """Move the pull request the mutation names to the status it asks for, and turn its auto-merge on or off."""
    number = str(variables.get("pr", "")).removeprefix("PR_")
    for name, fields in MOVES.items():
        if name in query:
            path = pr_file(number)
            path.write_text(json.dumps(json.loads(path.read_text()) | fields))
    if "PullRequestAutoMerge(" in query:
        path = state_dir() / f"merge{number}.json"
        given = json.loads(path.read_text()) if path.exists() else {}
        auto = None
        if "enablePullRequestAutoMerge(" in query:
            auto = {
                "mergeMethod": variables["method"],
                "enabledAt": datetime.now(UTC).isoformat(),
                "enabledBy": {"login": "tester"},
            }
        given.setdefault("pullRequest", {})["autoMergeRequest"] = auto
        path.write_text(json.dumps(given))


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def git_or_fail(*args: str) -> str:
    r = git(*args)
    if r.returncode:
        sys.exit(r.stderr.strip() or r.stdout.strip())
    return r.stdout


def branch_pr_view() -> None:
    branch = git_or_fail("branch", "--show-current").strip()
    for path in state_dir().glob("pr*.json"):
        if branch and json.loads(path.read_text())["headRefName"] == branch:
            print(path.stem[2:])
            return
    sys.exit(f'no pull requests found for branch "{branch}"')


def pr_checkout(args: list[str]) -> None:
    meta = pr_meta(args[0])
    branch = meta["headRefName"]
    if not meta["isCrossRepository"]:
        remote = f"refs/remotes/origin/{branch}"
        git_or_fail("fetch", "origin", f"+refs/heads/{branch}:{remote}", "--no-tags")
        if git("show-ref", "--verify", "--", f"refs/heads/{branch}").returncode == 0:
            git_or_fail("checkout", branch)
            git_or_fail("merge", "--ff-only", remote)
        else:
            git_or_fail("checkout", "-b", branch, "--track", f"origin/{branch}")
        return
    pull = f"refs/pull/{args[0]}/head"
    if git("branch", "--show-current").stdout.strip() == branch:
        git_or_fail("fetch", "origin", pull, "--no-tags")
        git_or_fail("merge", "--ff-only", "FETCH_HEAD")
    else:
        git_or_fail("fetch", "origin", f"{pull}:{branch}", "--no-tags")
        git_or_fail("checkout", branch)


def repo_clone(args: list[str]) -> None:
    source = (state_dir() / "repo.txt").read_text().strip()
    bare = ["--bare"] if "--bare" in args else []
    subprocess.run(["git", "clone", *bare, "--quiet", source, args[1]], check=True)


def contents(endpoint: str) -> None:
    path, ref = urllib.parse.urlsplit(endpoint).path.split("/contents/", 1)[1], endpoint.split("?ref=", 1)[1]
    source = (state_dir() / "repo.txt").read_text().strip()
    r = subprocess.run(
        ["git", "-C", source, "show", f"{urllib.parse.unquote(ref)}:{urllib.parse.unquote(path)}"], capture_output=True
    )
    if r.returncode:
        sys.exit("gh: Not Found (HTTP 404)")
    sys.stdout.buffer.write(r.stdout)


def thread_node(tid: str, draft: dict) -> dict:
    return {
        "id": tid,
        "isResolved": False,
        "isOutdated": False,
        "path": draft["path"],
        "line": draft["line"],
        "startLine": draft.get("startLine"),
        "diffSide": draft["side"],
        "startDiffSide": draft.get("startSide"),
        "originalLine": draft["line"],
        "originalStartLine": draft.get("startLine"),
        "resolvedBy": None,
        "comments": {"nodes": [comment_node(f"{tid}-c1", draft["body"])]},
    }


def comment_node(cid: str, body: str) -> dict:
    return {
        "id": cid,
        "author": {"login": "tester"},
        "body": body,
        "bodyHTML": "",
        "createdAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "url": f"https://github.com/acme/widgets/pull/1#{cid}",
        "state": "SUBMITTED",
        "replyTo": None,
        "pullRequestReview": None,
    }


def mutate(args: list[str], request: dict) -> None:
    """Answer a mutation. A held-<pid> file marks it while it runs."""
    query, variables = request["query"], request.get("variables") or {}
    marker = state_dir() / f"held-{os.getpid()}"
    marker.write_text("")
    try:
        while (state_dir() / "hold.txt").exists():
            time.sleep(0.05)
        fail_if_listed(args)
        comments_file = state_dir() / "comments.json"
        state = json.loads(comments_file.read_text())
        with (state_dir() / "mutations.log").open("a") as log:
            log.write(json.dumps(request) + "\n")
        data: dict = {}
        move(query, variables)
        if "addPullRequestReview(" in query:
            added = [
                thread_node(f"T{len(state['threads']) + 1 + i}", d)
                for i, d in enumerate(variables.get("threads") or [])
            ]
            state["threads"].extend(added)
            comments_file.write_text(json.dumps(state))
            review = {"id": "R_new", "comments": {"nodes": [{"id": t["comments"]["nodes"][0]["id"]} for t in added]}}
            data = {"addPullRequestReview": {"pullRequestReview": review}}
        if "addPullRequestReviewThreadReply(" in query:
            thread = next(t for t in state["threads"] if t["id"] == variables["thread"])
            node = comment_node(f"{thread['id']}-c{len(thread['comments']['nodes']) + 1}", variables["body"])
            thread["comments"]["nodes"].append(node)
            comments_file.write_text(json.dumps(state))
            data = {"addPullRequestReviewThreadReply": {"comment": {"id": node["id"]}}}
        print(json.dumps({"data": data}))
    finally:
        marker.unlink()


def graphql(args: list[str]) -> None:
    request = json.loads(sys.stdin.read())
    query, variables = request["query"], request.get("variables") or {}
    if "search(" in query:
        searches = state_dir() / "search.json"
        found = json.loads(searches.read_text()) if searches.exists() else {}
        print(json.dumps({"data": {"viewer": {"login": "tester"}, "search": {"nodes": found.get(variables["q"], [])}}}))
        return
    if query.lstrip().startswith("mutation"):
        mutate(args, request)
        return
    number = variables.get("number", 1)
    if "mergeStateStatus" in query:
        print(json.dumps({"data": {"repository": merge_state(number)}}))
        return
    state = json.loads((state_dir() / "comments.json").read_text())
    meta = json.loads(pr_file(number).read_text()) if pr_file(number).exists() else {"state": "OPEN", "isDraft": False}
    pr = {
        "id": f"PR_{number}",
        "headRefOid": state["head"],
        "bodyHTML": "",
        "state": meta["state"],
        "isDraft": meta["isDraft"],
        "reviews": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": state["reviews"]},
        "pending": {"nodes": [r for r in state["reviews"] if r["state"] == "PENDING"]},
        "verdicts": {
            "nodes": [r for r in state["reviews"] if r["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")]
        },
        "reviewThreads": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": state["threads"]},
        "comments": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": state["comments"]},
    }
    print(json.dumps({"data": {"viewer": {"login": "tester"}, "repository": {"pullRequest": pr}}}))


def log_and_fail(args: list[str]) -> None:
    with (state_dir() / "calls.log").open("a") as log:
        log.write(json.dumps(args) + "\n")
    fail_if_listed(args)


def fail_if_listed(args: list[str]) -> None:
    failures = state_dir() / "fail.txt"
    lines = failures.read_text().splitlines() if failures.exists() else []
    for line in lines:
        if line.split() and args[: len(line.split())] == line.split():
            sys.exit(f"fake gh: injected failure of {' '.join(args)}")


def main() -> None:
    args = sys.argv[1:]
    log_and_fail(args)
    if args[:2] == ["auth", "status"]:
        print("Logged in to github.com account tester")
    elif args[:3] == ["pr", "view", "--json"]:
        branch_pr_view()
    elif args[:2] == ["pr", "view"]:
        pr_view(args[2:])
    elif args[:2] == ["pr", "checkout"]:
        pr_checkout(args[2:])
    elif args[:2] == ["repo", "clone"]:
        repo_clone(args[2:])
    elif args[:2] == ["repo", "set-default"]:
        pass
    elif args[:3] == ["api", "-X", "POST"]:
        pass
    elif args[:2] == ["api", "graphql"]:
        graphql(args)
    elif args[:3] == ["api", "-H", "Accept: application/vnd.github.raw"]:
        contents(args[3])
    else:
        sys.exit(f"fake gh: unsupported command {' '.join(args)}")


if __name__ == "__main__":
    main()
