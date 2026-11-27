"""GitHub, reached through its command line client gh: how a pull request is named, and what the app asks GitHub.
Every gh command the app runs goes through here."""

from __future__ import annotations

import functools
import http.client
import json
import re
import tempfile
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import commands

OWNER = r"[A-Za-z0-9][A-Za-z0-9-]*"
REPO = r"(?!-)(?!\.+(?![\w.-]))[\w.-]+"
# The owner and repo of a pull request, for a pattern that adds its number as the group "number".
SLUG = rf"(?P<owner>{OWNER})/(?P<repo>{REPO})"
REFERENCE = re.compile(rf"(?:https?://github\.com/)?(?<![\w.-]){SLUG}(?:/pull/|/pulls/|#)(?P<number>\d+)")

# A link to a file of a repository: github.com/<owner>/<repo>/blob/<ref>/<path> or .../raw/..., or
# raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>. A ref may hold slashes, so the rest holds the ref and the path.
REPO_FILES = (
    re.compile(rf"^https://github\.com/{SLUG}/(?:blob|raw)/(?P<rest>[^?#]+)"),
    re.compile(rf"^https://raw\.githubusercontent\.com/{SLUG}/(?P<rest>[^?#]+)"),
)
# A file pasted into a description or a comment. GitHub sends one of a private repository only to a signed-in request,
# so gh's token goes with the request, and only to this host.
USER_ATTACHMENT = re.compile(
    r"https://github\.com(?P<path>/user-attachments/assets/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})"
)
# Where GitHub takes a file to paste into a description or a comment, as gh's --attach flag sends it. It answers with
# the file's USER_ATTACHMENT link, and with 404 to someone who can't write to the repository.
ATTACHMENTS = "https://uploads.github.com/user-attachments/assets"
# A ref of up to this many parts, such as feature/x/y, splits from the path in a link to a repository file.
REF_PARTS = 4

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
    from_fork: bool
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
    fields = (
        "title,body,author,url,baseRefOid,headRefOid,baseRefName,headRefName,isCrossRepository,commits,state,isDraft"
    )
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
        from_fork=meta["isCrossRepository"],
        commits=len(meta["commits"]),
        state=meta["state"],
        draft=meta["isDraft"],
    )


def head_sha(ref: PullRef) -> str:
    return gh("pr", "view", str(ref.number), "--repo", ref.slug, "--json", "headRefOid", "--jq", ".headRefOid").strip()


def viewer_login() -> str:
    """The login gh is signed in as, or an empty string when gh can't tell."""
    try:
        return gh("api", "user", "--jq", ".login").strip()
    except commands.CommandError:
        return ""


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


def pr_standings(refs: list[PullRef]) -> dict[PullRef, Standing]:
    """The standing of each pull request on GitHub now, from one query. A pull request GitHub can't return is
    missing; when gh fails, every one is missing. No pull requests asks GitHub nothing."""
    if not refs:
        return {}
    params, fields, variables = [], [], {}
    for i, ref in enumerate(refs):
        params.append(f"$o{i}: String!, $r{i}: String!, $n{i}: Int!")
        fields.append(
            f"p{i}: repository(owner: $o{i}, name: $r{i}) "
            f"{{ pullRequest(number: $n{i}) {{ state isDraft author {{ login }} }} }}"
        )
        variables |= {f"o{i}": ref.owner, f"r{i}": ref.repo, f"n{i}": ref.number}
    try:
        # gh exits with 1 when GitHub can't resolve one repository, but still prints the data of the others.
        _, _, out = ask(f"query({', '.join(params)}) {{ {' '.join(fields)} }}", variables, timeout=15)
    except commands.CommandError:
        return {}
    data = out.get("data") or {}
    standings = {}
    for i, ref in enumerate(refs):
        pr = (data.get(f"p{i}") or {}).get("pullRequest")
        if pr and (status := status_of(pr["state"], pr["isDraft"])):
            standings[ref] = Standing(status, (pr.get("author") or {}).get("login", ""))
    return standings


RECENT_PRS = (
    "query($q: String!) { search(query: $q, type: ISSUE, first: 100) "
    "{ nodes { ... on PullRequest { repository { nameWithOwner } } } } }"
)


def repos_of_recent_prs(since: date) -> list[str] | None:
    """The `owner/repo` names of the repositories where the viewer opened a pull request on or after the date, one
    per pull request, the newest first. None when gh fails or GitHub answers without a search."""
    q = f"is:pr author:@me created:>={since.isoformat()} sort:created-desc"
    try:
        _, _, out = ask(RECENT_PRS, {"q": q}, timeout=15)
    except commands.CommandError:
        return None
    search = (out.get("data") or {}).get("search")
    if search is None:
        return None
    names = (((n or {}).get("repository") or {}).get("nameWithOwner") for n in search.get("nodes") or [])
    return [name for name in names if name]


# The fields of a pull request that search_prs asks for.
PR_FIELDS = """
number title url isDraft state updatedAt mergedAt mergeable reviewDecision additions deletions
repository { nameWithOwner }
author { __typename login }
commits(last: 1) { nodes { commit { committedDate author { user { login } } statusCheckRollup { state } } } }
reviewRequests(first: 20) { nodes { requestedReviewer { ... on User { login } ... on Team { slug } } } }
reviews(last: 30) { nodes { author { __typename login } state submittedAt body } }
comments(last: 30) { nodes { author { __typename login } createdAt } }
reviewThreads(last: 20) { nodes { isResolved comments(last: 5) { nodes { author { __typename login } createdAt } } } }
linear: comments(first: 5) { nodes { body } }
"""
# How many pull requests search_prs reads from each search.
SEARCH_LIMIT = 50


def search_prs(queries: list[str], timeout: float = 30) -> tuple[str, list[list[dict]]] | None:
    """The viewer's login and, for each search query, the first SEARCH_LIMIT pull requests it finds, with PR_FIELDS. The
    searches run side by side, one request each, since GitHub gives up on one request that holds them all. None
    when gh fails or GitHub answers without the viewer or a search."""
    query = (
        "query($q: String!) { viewer { login } "
        f"search(query: $q, type: ISSUE, first: {SEARCH_LIMIT}) {{ nodes {{ ... on PullRequest {{ {PR_FIELDS} }} }} }} }}"
    )

    def one(q: str) -> dict:
        try:
            return ask(query, {"q": q}, timeout)[2].get("data") or {}
        except commands.CommandError:
            return {}

    with ThreadPoolExecutor(max_workers=max(1, len(queries))) as pool:
        answers = list(pool.map(one, queries))
    viewer = next(((a.get("viewer") or {}).get("login") for a in answers if a.get("viewer")), None)
    if not viewer or any(a.get("search") is None for a in answers):
        return None
    return viewer, [[n for n in a["search"].get("nodes") or [] if n and n.get("number")] for a in answers]


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


def repo_file(url: str) -> bytes:
    """The content of the repository file a link names (see REPO_FILES), read through the API with gh's sign-in, which
    a private repository needs. Raises ValueError for a link to anything else, and CommandError when GitHub has no
    such file or gh fails."""
    m = next((m for pattern in REPO_FILES if (m := pattern.match(url))), None)
    parts = urllib.parse.unquote(m["rest"]).split("/") if m else []
    if not m or len(parts) < 2 or any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"{url} is not a link to a file of a GitHub repository")
    stderr = ""
    for cut in range(1, min(len(parts), REF_PARTS + 1)):
        ref, path = "/".join(parts[:cut]), "/".join(parts[cut:])
        endpoint = f"repos/{m['owner']}/{m['repo']}/contents/{urllib.parse.quote(path)}?ref={urllib.parse.quote(ref)}"
        r = commands.call(["gh", "api", "-H", "Accept: application/vnd.github.raw", endpoint])
        if not r.returncode:
            return r.stdout
        stderr = r.stderr.decode(errors="replace").strip()
    raise commands.CommandError(f"GitHub has no file at {url}: {commands.tail(stderr)}")


def user_attachment(url: str) -> tuple[bytes, str]:
    """The content and the media type of the image a link to a pasted file names (see USER_ATTACHMENT). GitHub answers
    a signed-in request with a redirect to a short-lived address on another host, which gets the request without the
    token. Raises ValueError for any other link or for a file that is not an image, and CommandError when GitHub has
    no such file or does not answer."""
    if not (m := USER_ATTACHMENT.fullmatch(url)):
        raise ValueError(f"{url} is not a link to a file pasted on GitHub")
    token = commands.run(["gh", "auth", "token", "--hostname", "github.com"]).decode().strip()
    try:
        connection = http.client.HTTPSConnection("github.com", timeout=commands.TIMEOUT)
        try:
            connection.request("GET", m["path"], headers={"Authorization": f"Bearer {token}", "User-Agent": "differ"})
            answer = connection.getresponse()
            location = answer.getheader("Location") or ""
        finally:
            connection.close()
        if answer.status not in (301, 302, 303, 307, 308) or not location.startswith("https://"):
            raise commands.CommandError(f"GitHub has no file at {url}: HTTP {answer.status}")
        with urllib.request.urlopen(location, timeout=commands.TIMEOUT) as r:
            if not (ctype := r.headers.get_content_type()).startswith("image/"):
                raise ValueError(f"{url} is not an image but {ctype}")
            return r.read(), ctype
    except (OSError, http.client.HTTPException) as exc:
        raise commands.CommandError(f"GitHub did not send {url}: {exc}") from None


@functools.cache
def repository_id(slug: str) -> int:
    """The number GitHub's REST API gives the repository. Raises CommandError when gh fails or prints another
    answer."""
    answer = gh("api", f"repos/{slug}", "--jq", ".id").strip()
    if not answer.isdigit():
        raise commands.CommandError(f"GitHub gave {slug} no repository id: {answer[:200]!r}")
    return int(answer)


def attach(ref: PullRef, name: str, content_type: str, data: bytes) -> str:
    """Upload a file to the pull request's repository, as a paste into a comment box on GitHub does, and return its
    link (see USER_ATTACHMENT). Raises CommandError when GitHub refuses the file or gh fails."""
    query = urllib.parse.urlencode(
        {"name": name, "content_type": content_type, "repository_id": repository_id(ref.slug)}
    )
    with tempfile.TemporaryDirectory() as folder:
        # GitHub refuses a body without a Content-Length, and gh sends one only for a file it reads from disk.
        upload = Path(folder) / "upload"
        upload.write_bytes(data)
        r = commands.call(
            [
                "gh",
                "api",
                "-X",
                "POST",
                "-H",
                "Content-Type: application/octet-stream",
                "--input",
                str(upload),
                f"{ATTACHMENTS}?{query}",
            ]
        )
    stderr = r.stderr.decode(errors="replace").strip()
    if r.returncode and "HTTP 404" in stderr:
        raise commands.CommandError(f"GitHub takes images only from people who can write to {ref.slug}")
    if r.returncode:
        raise commands.CommandError(f"GitHub did not take the image: {commands.tail(stderr)}")
    try:
        url = json.loads(r.stdout).get("url")
    except (ValueError, AttributeError):
        url = None
    if not isinstance(url, str) or not USER_ATTACHMENT.fullmatch(url):
        raise commands.CommandError(f"GitHub took the image but answered without its link: {r.stdout[:200]!r}")
    return url
