"""A stand-in for the GitHub CLI, answering the four commands the app runs from files in FAKE_GH_STATE.

`gh auth status` reports a signed-in user.
`gh pr view <n> --repo o/r --json <fields> [--jq .field]` prints pr<n>.json, or one field of it.
`gh repo clone o/r <dest> -- --bare ...` clones the repo named in repo.txt.
`gh api graphql --input -` answers a read query from comments.json; a mutation is appended to mutations.log,
and a review posted with threads adds them to comments.json, so the page sees what it sent.
"""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

STATE = Path(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].startswith("/") else None


def state_dir() -> Path:
    import os

    return Path(os.environ["FAKE_GH_STATE"])


def pr_view(args: list[str]) -> None:
    meta = json.loads((state_dir() / f"pr{args[0]}.json").read_text())
    if "--jq" in args:
        print(meta[args[args.index("--jq") + 1].lstrip(".")])
    else:
        print(json.dumps(meta))


def repo_clone(args: list[str]) -> None:
    source = (state_dir() / "repo.txt").read_text().strip()
    subprocess.run(["git", "clone", "--bare", "--quiet", source, args[1]], check=True)


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
    pending_only = "states: [PENDING]" in query
    pr = {
        "id": "PR_1",
        "headRefOid": state["head"],
        "bodyHTML": "",
        "reviews": {"nodes": [r for r in state["reviews"] if not pending_only or r["state"] == "PENDING"]},
        "reviewThreads": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": state["threads"]},
        "comments": {"nodes": state["comments"]},
    }
    print(json.dumps({"data": {"viewer": {"login": "tester"}, "repository": {"pullRequest": pr}}}))


def main() -> None:
    args = sys.argv[1:]
    if args[:2] == ["auth", "status"]:
        print("Logged in to github.com account tester")
    elif args[:2] == ["pr", "view"]:
        pr_view(args[2:])
    elif args[:2] == ["repo", "clone"]:
        repo_clone(args[2:])
    elif args[:2] == ["api", "graphql"]:
        graphql()
    else:
        sys.exit(f"fake gh: unsupported command {' '.join(args)}")


if __name__ == "__main__":
    main()
