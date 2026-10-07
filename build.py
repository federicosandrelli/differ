#!/usr/bin/env python3
"""Build the review page for one GitHub pull request.

Usage: build.py <owner>/<repo>#<number> | <pull request URL>

Keeps its own bare clone of each repo, without file contents, in the cache; git fetches the files the diff
needs. Diffs the head against the merge base, as GitHub does.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
CACHE = Path(os.environ.get("DIFF_REVIEW_CACHE", Path.home() / ".cache" / "diff-review"))
PR_REF = re.compile(r"(?:https?://github\.com/)?([\w.-]+)/([\w.-]+?)(?:/pull/|/pulls/|#)(\d+)")

Log = Callable[[str], None]


class BuildError(Exception):
    pass


def parse_ref(text: str) -> tuple[str, str, int]:
    m = PR_REF.search(text.strip())
    if not m:
        raise BuildError(f"Not a pull request reference: {text!r}. Use owner/repo#123 or a pull request URL.")
    return m.group(1), m.group(2), int(m.group(3))


def run(args: list[str], cwd: Path | None = None) -> str:
    r = subprocess.run(args, cwd=cwd, capture_output=True)
    if r.returncode:
        err = r.stderr.decode(errors="replace").strip() or r.stdout.decode(errors="replace").strip()
        raise BuildError(f"`{' '.join(args[:4])}` failed: {err[:600]}")
    return r.stdout.decode(errors="replace")


def run_bytes(args: list[str]) -> bytes:
    r = subprocess.run(args, capture_output=True)
    if r.returncode:
        raise BuildError(f"`{' '.join(args[:4])}` failed: {r.stderr.decode(errors='replace').strip()[:600]}")
    return r.stdout


def pr_metadata(owner: str, repo: str, number: int) -> dict:
    fields = "number,title,body,author,url,baseRefOid,headRefOid,baseRefName,headRefName,commits,state,isDraft"
    return json.loads(run(["gh", "pr", "view", str(number), "--repo", f"{owner}/{repo}", "--json", fields]))


def head_sha(owner: str, repo: str, number: int) -> str:
    return run(["gh", "pr", "view", str(number), "--repo", f"{owner}/{repo}", "--json", "headRefOid",
                "--jq", ".headRefOid"]).strip()


def page_path(owner: str, repo: str, number: int, head: str) -> Path:
    return CACHE / "pages" / owner / repo / str(number) / f"{head[:12]}.html"


def ensure_repo(owner: str, repo: str, log: Log) -> Path:
    mirror = CACHE / "repos" / owner / f"{repo}.git"
    if not mirror.exists():
        log(f"Cloning {owner}/{repo} without file contents (first time for this repo)")
        mirror.parent.mkdir(parents=True, exist_ok=True)
        run(["gh", "repo", "clone", f"{owner}/{repo}", str(mirror), "--", "--bare", "--filter=blob:none", "--quiet"])
    else:
        log(f"Using the cached clone of {owner}/{repo}")
    return mirror


def has_commit(repo_dir: Path, sha: str) -> bool:
    return subprocess.run(["git", "-C", str(repo_dir), "cat-file", "-e", f"{sha}^{{commit}}"],
                          capture_output=True).returncode == 0


def fetch(repo_dir: Path, number: int, base: str, head: str, log: Log) -> None:
    if has_commit(repo_dir, base) and has_commit(repo_dir, head):
        return
    log("Fetching the pull request's commits")
    run(["git", "-C", str(repo_dir), "fetch", "--quiet", "--no-tags", "origin", f"pull/{number}/head", base])
    if not has_commit(repo_dir, head):
        run(["git", "-C", str(repo_dir), "fetch", "--quiet", "--no-tags", "origin", head])


def export(repo_dir: Path, base: str, head: str, out: Path, log: Log) -> None:
    git = ["git", "-C", str(repo_dir)]
    log("Computing the diff")
    (out / "diff.patch").write_bytes(run_bytes(git + ["diff", "-M", "-U3", base, head]))
    names = run(git + ["diff", "-M", "--name-status", base, head])
    (out / "names.txt").write_text(names)
    entries = [line.split("\t") for line in names.splitlines() if line]
    log(f"Reading {len(entries)} changed files")
    for parts in entries:
        status = parts[0]
        if status.startswith("R") or status.startswith("C"):
            old, new = parts[1], parts[2]
        elif status == "D":
            old, new = parts[1], None
        elif status == "A":
            old, new = None, parts[1]
        else:
            old, new = parts[1], parts[1]
        for side, sha, path in (("old", base, old), ("new", head, new)):
            if not path:
                continue
            target = out / side / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(run_bytes(git + ["show", f"{sha}:{path}"]))


def build(ref: str, log: Log = print) -> Path:
    owner, repo, number = parse_ref(ref)
    log(f"Reading {owner}/{repo}#{number} from GitHub")
    meta = pr_metadata(owner, repo, number)
    head = meta["headRefOid"]
    target = page_path(owner, repo, number, head)
    if target.exists():
        log("Already built for this head commit")
        return target

    repo_dir = ensure_repo(owner, repo, log)
    fetch(repo_dir, number, meta["baseRefOid"], head, log)
    merge_base = run(["git", "-C", str(repo_dir), "merge-base", meta["baseRefOid"], head]).strip()

    work = CACHE / "work" / owner / repo / str(number) / head[:12]
    if work.exists():
        subprocess.run(["rm", "-rf", str(work)], check=True)
    (work / "old").mkdir(parents=True)
    (work / "new").mkdir(parents=True)
    export(repo_dir, merge_base, head, work, log)

    annotations = {"pr": {
        "number": number, "title": meta["title"], "url": meta["url"], "repo": f"{owner}/{repo}",
        "base": merge_base, "head": head, "author": (meta.get("author") or {}).get("login", ""),
        "commits": len(meta.get("commits") or []), "body": meta.get("body") or "",
        "base_ref": meta.get("baseRefName"), "head_ref": meta.get("headRefName"),
        "state": meta.get("state"), "draft": meta.get("isDraft", False),
    }}
    (work / "annotations.json").write_text(json.dumps(annotations, indent=2, ensure_ascii=False) + "\n")

    log("Classifying changes")
    classified = run([sys.executable, str(ROOT / "classify.py"), str(work)])
    (work / "classified.json").write_text(classified)
    log("Rendering the page")
    target.parent.mkdir(parents=True, exist_ok=True)
    run([sys.executable, str(ROOT / "render.py"), str(work / "classified.json"), str(work / "annotations.json"),
         str(target)])
    log("Done")
    return target


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    try:
        print(build(sys.argv[1]))
    except BuildError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
