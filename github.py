"""GitHub, reached through its command line client gh: how a pull request is named, and what the app asks GitHub.
Every gh command the app runs goes through here."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import commands

OWNER = r"[A-Za-z0-9][A-Za-z0-9-]*"
REPO = r"(?!-)(?!\.+(?![\w.-]))[\w.-]+"
# The owner and repo of a pull request, for a pattern that adds its number as the group "number".
SLUG = rf"(?P<owner>{OWNER})/(?P<repo>{REPO})"
REFERENCE = re.compile(rf"(?:https?://github\.com/)?(?<![\w.-]){SLUG}(?:/pull/|/pulls/|#)(?P<number>\d+)")

Status = Literal["open", "draft", "merged", "closed"]
STATUSES: dict[str, Status] = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}


@dataclass(frozen=True)
class PullRef:
    """The name of one pull request. GitHub matches owner and repo names case-insensitively, so they are kept in
    lower case, and one pull request has one name."""

    owner: str
    repo: str
    number: int

    @classmethod
    def parse(cls, text: str) -> PullRef:
        """The pull request a URL or an owner/repo#123 reference names. Raises ValueError for other text."""
        m = REFERENCE.search(text.strip())
        if not m:
            raise ValueError(f"Not a pull request reference: {text!r}. Use owner/repo#123 or a pull request URL.")
        return cls.from_match(m)

    @classmethod
    def from_match(cls, m: re.Match[str]) -> PullRef:
        """The pull request a match of a pattern built on SLUG names."""
        return cls(m["owner"].lower(), m["repo"].lower(), int(m["number"]))

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def path(self) -> str:
        """Where this server serves the pull request's page."""
        return f"/{self.slug}/pull/{self.number}"

    def __str__(self) -> str:
        return f"{self.slug}#{self.number}"


@dataclass(frozen=True)
class PullRequest:
    """What GitHub says about a pull request now."""

    title: str
    url: str
    body: str
    author: str
    base: str
    head: str
    base_ref: str
    head_ref: str
    commits: int
    state: str
    draft: bool

    @property
    def status(self) -> Status | None:
        return status_of(self.state, self.draft)


def status_of(state: str, draft: bool) -> Status | None:
    """A pull request's status from its GitHub state: an open draft is a draft. None for a state the app has no
    status for."""
    return "draft" if draft and state == "OPEN" else STATUSES.get(state)


def gh(*args: str, timeout: float = commands.TIMEOUT, cwd: Path | None = None) -> str:
    return commands.run(["gh", *args], timeout=timeout, cwd=cwd).decode(errors="replace")


def pull_request(ref: PullRef) -> PullRequest:
    fields = "title,body,author,url,baseRefOid,headRefOid,baseRefName,headRefName,commits,state,isDraft"
    meta = json.loads(gh("pr", "view", str(ref.number), "--repo", ref.slug, "--json", fields))
    return PullRequest(
        title=meta["title"],
        url=meta["url"],
        body=meta["body"],
        author=(meta["author"] or {}).get("login", ""),
        base=meta["baseRefOid"],
        head=meta["headRefOid"],
        base_ref=meta["baseRefName"],
        head_ref=meta["headRefName"],
        commits=len(meta["commits"]),
        state=meta["state"],
        draft=meta["isDraft"],
    )


def head_sha(ref: PullRef) -> str:
    return gh("pr", "view", str(ref.number), "--repo", ref.slug, "--json", "headRefOid", "--jq", ".headRefOid").strip()


def clone(ref: PullRef, mirror: Path) -> None:
    """A bare clone of the pull request's repository without file contents; git fetches each file when it is read."""
    gh(
        "repo",
        "clone",
        ref.slug,
        str(mirror),
        "--",
        "--bare",
        "--filter=blob:none",
        "--quiet",
        timeout=commands.LONG_TIMEOUT,
    )


def auth_problem() -> str | None:
    """Why gh cannot reach GitHub now, or None when `gh auth status` succeeds."""
    try:
        r = commands.call(["gh", "auth", "status"], timeout=10)
    except commands.CommandError as exc:
        return f"{exc}."
    return "The GitHub CLI (gh) is not signed in." if r.returncode else None


def graphql(query: str, **variables: object) -> dict:
    """The data GitHub answers a GraphQL query with. Raises CommandError when gh fails or GitHub answers with
    errors."""
    returncode, stderr, out = ask(query, variables, timeout=60)
    errors = out.get("errors") or []
    if returncode or errors:
        messages = "; ".join(e["message"] for e in errors)[: commands.ERROR_MAX]
        raise commands.CommandError(messages or commands.tail(stderr) or f"gh exited with {returncode}")
    return out["data"]


@dataclass(frozen=True)
class Standing:
    """Where a pull request stands on GitHub now: its status, and the login of its author."""

    status: Status
    author: str


def pr_standings(refs: list[PullRef]) -> tuple[str, dict[PullRef, Standing]]:
    """The viewer's login and the standing of each pull request on GitHub now, from one query. A pull request
    GitHub can't return is missing; when gh fails, every one is missing and the login is empty."""
    params, fields, variables = [], ["viewer { login }"], {}
    for i, ref in enumerate(refs):
        params.append(f"$o{i}: String!, $r{i}: String!, $n{i}: Int!")
        fields.append(
            f"p{i}: repository(owner: $o{i}, name: $r{i}) "
            f"{{ pullRequest(number: $n{i}) {{ state isDraft author {{ login }} }} }}"
        )
        variables |= {f"o{i}": ref.owner, f"r{i}": ref.repo, f"n{i}": ref.number}
    signature = f"({', '.join(params)})" if params else ""
    try:
        # gh exits with 1 when GitHub can't resolve one repository, but still prints the data of the others.
        _, _, out = ask(f"query{signature} {{ {' '.join(fields)} }}", variables, timeout=15)
    except commands.CommandError:
        return "", {}
    data = out.get("data") or {}
    viewer = (data.get("viewer") or {}).get("login", "")
    standings = {}
    for i, ref in enumerate(refs):
        pr = (data.get(f"p{i}") or {}).get("pullRequest")
        if pr and (status := status_of(pr["state"], pr["isDraft"])):
            standings[ref] = Standing(status, (pr.get("author") or {}).get("login", ""))
    return viewer, standings


def ask(query: str, variables: dict[str, object], timeout: float) -> tuple[int, str, dict]:
    """Send a GraphQL query through gh: its exit code, its stderr, and the answer it printed (empty when it
    printed none)."""
    request = json.dumps({"query": query, "variables": variables}).encode()
    r = commands.call(["gh", "api", "graphql", "--input", "-"], request, timeout)
    try:
        out = json.loads(r.stdout)
    except ValueError:
        out = {}
    return r.returncode, r.stderr.decode(errors="replace").strip(), out
