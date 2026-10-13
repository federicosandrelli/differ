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
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import classify
import render

ROOT = Path(__file__).resolve().parent
CACHE = Path(os.environ.get("DIFF_REVIEW_CACHE", Path.home() / ".cache" / "diff-review"))
OWNER = r"[A-Za-z0-9][A-Za-z0-9-]*"
REPO = r"(?!-)(?!\.+(?![\w.-]))[\w.-]+"
PR_REF = re.compile(rf"(?:https?://github\.com/)?(?<![\w.-])({OWNER})/({REPO})(?:/pull/|/pulls/|#)(\d+)")

Log = Callable[[str], None]
TIMEOUT = 120
CLONE_TIMEOUT = 600
GH_MISSING = "The GitHub CLI (gh) is not installed or not on PATH"


class BuildError(Exception):
    pass


def parse_ref(text: str) -> tuple[str, str, int]:
    """GitHub matches owner and repo names case-insensitively, so they are lowercased and one PR has one cache path."""
    m = PR_REF.search(text.strip())
    if not m:
        raise BuildError(f"Not a pull request reference: {text!r}. Use owner/repo#123 or a pull request URL.")
    return m.group(1).lower(), m.group(2).lower(), int(m.group(3))


def spawn(
    args: list[str], input: bytes | str | None = None, text: bool = False, timeout: float = TIMEOUT
) -> subprocess.CompletedProcess:
    """Run the command and wait at most `timeout` seconds for it. A command that is not installed or does not
    finish in time raises a BuildError that names it."""
    try:
        return subprocess.run(args, capture_output=True, input=input, text=text, timeout=timeout)
    except FileNotFoundError:
        raise BuildError(GH_MISSING if args[0] == "gh" else f"{args[0]} is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise BuildError(f"`{' '.join(args[:4])}` did not finish within {timeout:g} seconds") from None


def run_bytes(args: list[str], input: bytes | None = None, timeout: float = TIMEOUT) -> bytes:
    r = spawn(args, input=input, timeout=timeout)
    if r.returncode:
        err = r.stderr.decode(errors="replace").strip() or r.stdout.decode(errors="replace").strip()
        raise BuildError(f"`{' '.join(args[:4])}` failed: {err[:600]}")
    return r.stdout


def run(args: list[str], timeout: float = TIMEOUT) -> str:
    return run_bytes(args, timeout=timeout).decode(errors="replace")


def pr_metadata(owner: str, repo: str, number: int) -> dict:
    fields = "number,title,body,author,url,baseRefOid,headRefOid,baseRefName,headRefName,commits,state,isDraft"
    return json.loads(run(["gh", "pr", "view", str(number), "--repo", f"{owner}/{repo}", "--json", fields]))


def pr_statuses(keys: list[tuple[str, str, int]]) -> dict[tuple[str, str, int], str]:
    """The status of each pull request on GitHub now, as a key of render.PR_ICONS, by owner, repo and number,
    from one query. A pull request GitHub can't return is missing, and every one is missing when gh fails."""
    if not keys:
        return {}
    params, fields, variables = [], [], {}
    for i, (owner, repo, number) in enumerate(keys):
        params.append(f"$o{i}: String!, $r{i}: String!, $n{i}: Int!")
        fields.append(
            f"p{i}: repository(owner: $o{i}, name: $r{i}) {{ pullRequest(number: $n{i}) {{ state isDraft }} }}"
        )
        variables |= {f"o{i}": owner, f"r{i}": repo, f"n{i}": number}
    query = f"query({', '.join(params)}) {{ {' '.join(fields)} }}"
    # gh exits with 1 when GitHub can't resolve one repository, but still prints the data of the others.
    try:
        r = subprocess.run(
            ["gh", "api", "graphql", "--input", "-"],
            input=json.dumps({"query": query, "variables": variables}),
            capture_output=True,
            text=True,
            timeout=15,
        )
        out = json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {}
    data = out.get("data") if isinstance(out, dict) else None
    statuses = {}
    for i, key in enumerate(keys):
        repo = data.get(f"p{i}") if isinstance(data, dict) else None
        pr = repo.get("pullRequest") if isinstance(repo, dict) else None
        if isinstance(pr, dict):
            statuses[key] = render.pr_status({"state": pr.get("state"), "draft": pr.get("isDraft")})
    return statuses


def head_sha(owner: str, repo: str, number: int) -> str:
    return run(
        ["gh", "pr", "view", str(number), "--repo", f"{owner}/{repo}", "--json", "headRefOid", "--jq", ".headRefOid"]
    ).strip()


def page_path(owner: str, repo: str, number: int, head: str) -> Path:
    return CACHE / "pages" / owner / repo / str(number) / f"{head[:12]}.html"


def sources() -> list[Path]:
    return [ROOT / "build.py", ROOT / "classify.py", *render.sources()]


def page_current(page: Path) -> bool:
    """Whether the page exists and was built after the last change to the code that builds pages."""
    return page.exists() and page.stat().st_mtime >= max(src.stat().st_mtime for src in sources())


def latest_page(owner: str, repo: str, number: int) -> Path | None:
    """The newest current page built for the pull request, whatever head commit it was built from."""
    pages = sorted(page_path(owner, repo, number, "x").parent.glob("*.html"), key=lambda p: p.stat().st_mtime)
    return next((p for p in reversed(pages) if page_current(p)), None)


def forget_pages(owner: str, repo: str, number: int) -> None:
    shutil.rmtree(page_path(owner, repo, number, "x").parent, ignore_errors=True)


def ensure_repo(owner: str, repo: str, log: Log) -> Path:
    mirror = CACHE / "repos" / owner / f"{repo}.git"
    if not mirror.exists():
        log(f"Cloning {owner}/{repo} without file contents (first time for this repo)")
        mirror.parent.mkdir(parents=True, exist_ok=True)
        run(
            ["gh", "repo", "clone", f"{owner}/{repo}", str(mirror), "--", "--bare", "--filter=blob:none", "--quiet"],
            timeout=CLONE_TIMEOUT,
        )
    else:
        log(f"Using the cached clone of {owner}/{repo}")
    return mirror


def has_commit(repo_dir: Path, sha: str) -> bool:
    return spawn(["git", "-C", str(repo_dir), "cat-file", "-e", f"{sha}^{{commit}}"]).returncode == 0


def fetch(repo_dir: Path, number: int, base: str, head: str, log: Log) -> None:
    if has_commit(repo_dir, base) and has_commit(repo_dir, head):
        return
    log("Fetching the pull request's commits")
    git = ["git", "-C", str(repo_dir)]
    run(git + ["fetch", "--quiet", "--no-tags", "origin", f"pull/{number}/head", base], timeout=CLONE_TIMEOUT)
    if not has_commit(repo_dir, head):
        run(git + ["fetch", "--quiet", "--no-tags", "origin", head], timeout=CLONE_TIMEOUT)


def read_blobs(repo_dir: Path, specs: list[str]) -> dict[str, bytes]:
    """The content of every `<commit>:<path>` spec, read in one git process; missing specs are left out."""
    out = run_bytes(
        ["git", "-C", str(repo_dir), "cat-file", "--batch", "--buffer"], input="".join(s + "\n" for s in specs).encode()
    )
    blobs: dict[str, bytes] = {}
    pos = 0
    for spec in specs:
        end = out.index(b"\n", pos)
        header = out[pos:end].decode(errors="replace").split(" ")
        pos = end + 1
        if header[-1] == "missing":
            continue
        size = int(header[2])
        blobs[spec] = out[pos : pos + size]
        pos += size + 1
    return blobs


def changed_paths(names: str) -> list[tuple[str | None, str | None]]:
    """(old path, new path) per entry of `git diff --name-status`; None on the side where the file does not exist."""
    pairs: list[tuple[str | None, str | None]] = []
    for line in names.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith(("R", "C")):
            pairs.append((parts[1], parts[2]))
        elif status == "D":
            pairs.append((parts[1], None))
        elif status == "A":
            pairs.append((None, parts[1]))
        else:
            pairs.append((parts[1], parts[1]))
    return pairs


def export(repo_dir: Path, base: str, head: str, out: Path, log: Log) -> None:
    git = ["git", "-C", str(repo_dir)]
    log("Computing the diff")
    (out / "diff.patch").write_bytes(run_bytes(git + ["diff", "-M", "-U3", base, head]))
    names = run(git + ["diff", "-M", "--name-status", base, head])
    (out / "names.txt").write_text(names)
    pairs = changed_paths(names)
    log(f"Reading {len(pairs)} changed files")
    wanted = [
        (side, path) for old, new in pairs for side, sha, path in (("old", base, old), ("new", head, new)) if path
    ]
    blobs = read_blobs(repo_dir, [f"{base if side == 'old' else head}:{path}" for side, path in wanted])
    for side, path in wanted:
        target = out / side / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blobs.get(f"{base if side == 'old' else head}:{path}", b""))
    paths = "".join(path + "\n" for old, new in pairs if (path := new or old))
    attrs = spawn(git + ["check-attr", f"--source={head}", "--stdin", "linguist-generated"], input=paths, text=True)
    generated = [
        line.rsplit(": ", 2)[0] for line in attrs.stdout.splitlines() if line.rsplit(": ", 1)[-1] in ("set", "true")
    ]
    (out / "generated.txt").write_text("".join(path + "\n" for path in generated))


def build(ref: str, log: Log = print) -> Path:
    owner, repo, number = parse_ref(ref)
    log(f"Reading {owner}/{repo}#{number} from GitHub")
    meta = pr_metadata(owner, repo, number)
    head = meta["headRefOid"]
    target = page_path(owner, repo, number, head)
    if page_current(target):
        log("Already built for this head commit")
        return target

    repo_dir = ensure_repo(owner, repo, log)
    fetch(repo_dir, number, meta["baseRefOid"], head, log)
    merge_base = run(["git", "-C", str(repo_dir), "merge-base", meta["baseRefOid"], head]).strip()

    work = CACHE / "work" / owner / repo / str(number) / head[:12]
    shutil.rmtree(work, ignore_errors=True)
    (work / "old").mkdir(parents=True)
    (work / "new").mkdir(parents=True)
    export(repo_dir, merge_base, head, work, log)

    annotations = {
        "pr": {
            "number": number,
            "title": meta["title"],
            "url": meta["url"],
            "repo": f"{owner}/{repo}",
            "base": merge_base,
            "head": head,
            "author": (meta.get("author") or {}).get("login", ""),
            "commits": len(meta.get("commits") or []),
            "body": meta.get("body") or "",
            "base_ref": meta.get("baseRefName"),
            "head_ref": meta.get("headRefName"),
            "state": meta.get("state"),
            "draft": meta.get("isDraft", False),
        }
    }
    (work / "annotations.json").write_text(json.dumps(annotations, indent=2, ensure_ascii=False) + "\n")

    log("Classifying changes")
    classified = classify.classify(work)
    (work / "classified.json").write_text(json.dumps(classified, ensure_ascii=False))
    log("Rendering the page")
    html = render.render_page(classified, annotations)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html)
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
