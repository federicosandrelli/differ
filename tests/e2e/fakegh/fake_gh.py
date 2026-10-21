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
`gh api graphql --input -` answers a read query from comments.json; a mutation is appended to mutations.log,
and a review posted with threads adds them to comments.json, so the page sees what it sent.
"""

import json
import os
import subprocess
import sys
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
    now = datetime.now(UTC).isoformat(timespec="seconds")
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
        "comments": {
            "nodes": [
                {
                    "id": f"{tid}-c1",
                    "author": {"login": "tester"},
                    "body": draft["body"],
                    "bodyHTML": "",
                    "createdAt": now,
                    "url": f"https://github.com/acme/widgets/pull/1#{tid}",
                    "state": "SUBMITTED",
                    "replyTo": None,
                    "pullRequestReview": None,
                }
            ]
        },
    }


def graphql() -> None:
    request = json.loads(sys.stdin.read())
    query, variables = request["query"], request.get("variables") or {}
    comments_file = state_dir() / "comments.json"
    state = json.loads(comments_file.read_text())
    if query.lstrip().startswith("mutation"):
        with (state_dir() / "mutations.log").open("a") as log:
            log.write(json.dumps(request) + "\n")
        data: dict = {}
        if "addPullRequestReview(" in query:
            for draft in variables.get("threads") or []:
                state["threads"].append(thread_node(f"T{len(state['threads']) + 1}", draft))
            comments_file.write_text(json.dumps(state))
            data = {"addPullRequestReview": {"pullRequestReview": {"id": "R_new"}}}
        print(json.dumps({"data": data}))
        return
    pr = {
        "id": "PR_1",
        "headRefOid": state["head"],
        "bodyHTML": "",
        "state": "OPEN",
        "isDraft": False,
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
    elif args[:2] == ["api", "graphql"]:
        graphql()
    elif args[:3] == ["api", "-H", "Accept: application/vnd.github.raw"]:
        contents(args[3])
    else:
        sys.exit(f"fake gh: unsupported command {' '.join(args)}")


if __name__ == "__main__":
    main()
