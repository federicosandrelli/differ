"""The start page's sections: the viewer's own pull requests, other people's, and the ones opened last that neither
shows. GitHub's last answer, sorted into sections, is kept in board.json in the cache."""

from __future__ import annotations

import html
import itertools
import json
import os
import threading
import time
from datetime import date, datetime, timedelta
from typing import TypedDict

import build
import followed
import github
import recent
import render
from github import PullRef

PATH = build.CACHE / "board.json"
MERGED_DAYS = 7
REFRESH_SECONDS = 30
FOLLOWED_QUERY = "is:pr is:open archived:false draft:false -author:@me sort:updated-desc"
FAILED = ("FAILURE", "ERROR")
RUNNING = ("PENDING", "EXPECTED")
GROUPS = (
    (
        "Your pull requests",
        (
            ("action", "Need your action"),
            ("ci", "Waiting on CI"),
            ("review", "Waiting on review"),
            ("draft", "Draft"),
            ("merged", "Recently merged"),
        ),
    ),
    (
        "Other people's pull requests",
        (
            ("reviewing", "You are reviewing: need your action"),
            ("reviewed", "You are reviewing: waiting on the author"),
            ("unreviewed", "Need a reviewer"),
            ("others", "Being reviewed by someone else"),
        ),
    ),
)
CHEVRON = (
    '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)
lock = threading.Lock()
refreshing = threading.Lock()


class Row(TypedDict):
    """A pull request as a section lists it: why it is there (`reason`, drawn in the colour `tone` names: `bad`,
    `warn`, `ok`, `accent` or none), and the time its age counts from, in seconds since the epoch."""

    owner: str
    repo: str
    number: int
    title: str
    status: str
    author: str
    reason: str
    tone: str
    at: float


class Board(TypedDict):
    """What board.json holds: the viewer's login, when GitHub last answered, the followed repositories it was
    asked about, and the rows of each section, newest first."""

    viewer: str
    fetched: float
    repos: list[str]
    sections: dict[str, list[Row]]


def queries(repos: list[str], merged_since: date) -> list[str]:
    """The searches the sections read, in this order: the viewer's open pull requests, the viewer's pull requests
    merged on or after the date, the pull requests that ask for the viewer's review, the ones the viewer takes part
    in, the ones the viewer reviewed, and, when there are followed repositories, their open pull requests."""
    found = [
        "is:pr is:open archived:false author:@me sort:updated-desc",
        f"is:pr is:merged author:@me merged:>={merged_since.isoformat()} sort:updated-desc",
        "is:pr is:open archived:false review-requested:@me sort:updated-desc",
        "is:pr is:open archived:false involves:@me -author:@me sort:updated-desc",
        "is:pr is:open archived:false reviewed-by:@me -author:@me sort:updated-desc",
    ]
    if repos:
        found.append(FOLLOWED_QUERY + "".join(f" repo:{repo}" for repo in repos))
    return found


def nodes(node: dict | None, key: str) -> list[dict]:
    return [n for n in ((node or {}).get(key) or {}).get("nodes") or [] if n]


def login(node: dict | None) -> str:
    return ((node or {}).get("author") or {}).get("login") or ""


def is_bot(node: dict) -> bool:
    author = node.get("author") or {}
    return author.get("__typename") == "Bot" or (author.get("login") or "").endswith("[bot]")


def ci_state(pr: dict) -> str:
    """The state of the checks on the head commit, or an empty string when it has none."""
    commits = nodes(pr, "commits")
    return ((commits[-1].get("commit") or {}).get("statusCheckRollup") or {}).get("state") or "" if commits else ""


def reviews(pr: dict) -> list[dict]:
    return [r for r in nodes(pr, "reviews") if r.get("state") != "PENDING" and r.get("submittedAt")]


def thread_comments(pr: dict, resolved: bool) -> list[dict]:
    """The comments of the review threads, those of resolved threads too when `resolved` is set."""
    return [c for t in nodes(pr, "reviewThreads") if resolved or not t.get("isResolved") for c in nodes(t, "comments")]


def last_activity(pr: dict, viewer: str) -> str:
    """When the viewer last reviewed, commented, replied in a thread or made the head commit, as GitHub's ISO time;
    empty when never. GitHub gives no push time, so the head commit counts from when it was made."""
    times = [c["createdAt"] for c in nodes(pr, "comments") + thread_comments(pr, True) if login(c) == viewer]
    times += [r["submittedAt"] for r in reviews(pr) if login(r) == viewer]
    commits = nodes(pr, "commits")
    head = (commits[-1].get("commit") or {}) if commits else {}
    if ((head.get("author") or {}).get("user") or {}).get("login") == viewer:
        times.append(head.get("committedDate") or "")
    return max(times, default="")


def says_something(review: dict) -> bool:
    """Whether a review asks the author to answer by itself: a change request, or a comment review with a text of its
    own. A review that only holds thread replies counts through its replies, and an approval through the review
    decision."""
    return review["state"] == "CHANGES_REQUESTED" or (review["state"] == "COMMENTED" and bool(review.get("body")))


def new_comments(pr: dict, viewer: str) -> list[str]:
    """The authors of what people other than the viewer said after the viewer's last activity, one per comment,
    oldest first: comments, replies in open threads, and the reviews that say something. Bots are left out."""
    since = last_activity(pr, viewer)
    said = [(c["createdAt"], c) for c in nodes(pr, "comments") + thread_comments(pr, False)]
    said += [(r["submittedAt"], r) for r in reviews(pr) if says_something(r)]
    return [
        login(n) for at, n in sorted(said, key=lambda s: s[0]) if at > since and login(n) != viewer and not is_bot(n)
    ]


def requested(pr: dict) -> list[str]:
    """The users and teams asked to review, by login or team slug."""
    asked = ((n.get("requestedReviewer") or {}) for n in nodes(pr, "reviewRequests"))
    return [r.get("login") or r.get("slug") or "" for r in asked if r.get("login") or r.get("slug")]


def names(logins: list[str]) -> str:
    """The logins, each once; a deleted account, which GitHub gives without a login, as "ghost", as GitHub does."""
    return ", ".join(dict.fromkeys(name or "ghost" for name in logins))


def own(pr: dict, viewer: str) -> tuple[str, str, str]:
    """The section of one of the viewer's open pull requests, and the reason and tone its row shows."""
    ci = ci_state(pr)
    conflicts = pr.get("mergeable") == "CONFLICTING"
    said = new_comments(pr, viewer)
    reasons = []
    if ci in FAILED:
        reasons.append(("CI failed", "bad"))
    if conflicts:
        reasons.append(("conflicts", "warn"))
    if said:
        reasons.append((f"{len(said)} comment{'' if len(said) == 1 else 's'} from {names(said)}", "warn"))
    if pr.get("reviewDecision") == "APPROVED" and ci in ("SUCCESS", "") and not conflicts and not said:
        reasons.append(("approved and green", "ok"))
    reason, tone = " · ".join(r for r, _ in reasons), reasons[0][1] if reasons else ""
    if pr.get("isDraft"):
        return "draft", reason, tone
    if reasons:
        return "action", reason, tone
    if ci in RUNNING:
        return "ci", "checks running", ""
    if pr.get("reviewDecision") == "CHANGES_REQUESTED":
        return "review", "changes requested", ""
    if asked := requested(pr):
        return "review", f"review requested from {names(asked)}", ""
    return "review", "no reviewer yet", ""


def other(
    pr: dict, viewer: str, asked_of_viewer: bool, reviewed_by_viewer: bool, followed_repo: bool
) -> tuple[str, str, str] | None:
    """The section of someone else's open pull request, and the reason and tone its row shows; None when it
    belongs in none. `reviewed_by_viewer` says GitHub found a review of the viewer's, which may be older than the
    reviews the pull request holds."""
    if asked_of_viewer or viewer in requested(pr):
        return "reviewing", "your review requested", "accent"
    mine = [r for r in reviews(pr) if login(r) == viewer]
    verdicts = [r["state"] for r in mine if r["state"] in ("APPROVED", "CHANGES_REQUESTED")]
    if verdicts:
        return "reviewed", "you approved" if verdicts[-1] == "APPROVED" else "you requested changes", ""
    if mine or any(login(c) == viewer for c in nodes(pr, "comments") + thread_comments(pr, True)):
        return "reviewed", "you commented", ""
    if reviewed_by_viewer:
        return "reviewed", "you reviewed", ""
    if not followed_repo or pr.get("isDraft"):
        return None
    people = [r for r in reviews(pr) if not is_bot(r) and login(r) != login(pr)]
    approvers = [login(r) for r in people if r["state"] == "APPROVED"]
    if approvers:
        return "others", f"approved by {names(approvers)}", ""
    if people:
        return "others", f"reviewed by {names([login(r) for r in people])}", ""
    if asked := requested(pr):
        return "others", f"review requested from {names(asked)}", ""
    return "unreviewed", "", ""


def seconds(iso: str | None) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() if iso else 0.0


def row(pr: dict, reason: str, tone: str) -> Row:
    owner, repo = pr["repository"]["nameWithOwner"].lower().split("/", 1)
    status = github.status_of(pr.get("state") or "", bool(pr.get("isDraft"))) or ""
    at = pr.get("mergedAt") if status == "merged" else pr.get("updatedAt")
    return Row(
        owner=owner,
        repo=repo,
        number=pr["number"],
        title=pr.get("title") or "",
        status=status,
        author=login(pr),
        reason=reason,
        tone=tone,
        at=seconds(at),
    )


def classify(viewer: str, found: list[list[dict]], repos: list[str]) -> dict[str, list[Row]]:
    """The rows of each section from the answers to `queries`, in its order, each section newest first. A pull
    request shows in one section only."""
    sections: dict[str, list[Row]] = {key: [] for _, subs in GROUPS for key, _ in subs}
    open_mine, merged, asked, involved, reviewed, *rest = found
    for pr in open_mine:
        key, reason, tone = own(pr, viewer)
        sections[key].append(row(pr, reason, tone))
    for pr in merged:
        sections["merged"].append(row(pr, "", ""))
    asked_urls, reviewed_urls = {pr["url"] for pr in asked}, {pr["url"] for pr in reviewed}
    followed_repos = set(repos)
    seen: set[str] = set()
    for pr in itertools.chain(asked, involved, reviewed, *rest):
        if pr["url"] in seen or login(pr) == viewer:
            continue
        seen.add(pr["url"])
        repo = pr["repository"]["nameWithOwner"].lower()
        if placed := other(pr, viewer, pr["url"] in asked_urls, pr["url"] in reviewed_urls, repo in followed_repos):
            key, reason, tone = placed
            sections[key].append(row(pr, reason, tone))
    for rows in sections.values():
        rows.sort(key=lambda r: r["at"], reverse=True)
    return sections


def load() -> Board:
    try:
        data = json.loads(PATH.read_text())
        return Board(viewer=data["viewer"], fetched=data["fetched"], repos=data["repos"], sections=data["sections"])
    except (OSError, ValueError, KeyError, TypeError):
        return Board(viewer="", fetched=0.0, repos=[], sections={})


def save(board: Board) -> None:
    """Replace board.json in one step: the board goes to board.json.tmp first, which then takes the place of
    board.json, so a crash mid-write leaves the old board."""
    with lock:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_name(PATH.name + ".tmp")
        tmp.write_text(json.dumps(board))
        os.replace(tmp, PATH)


def refresh() -> None:
    """Ask GitHub again and save the sections, unless GitHub answered less than REFRESH_SECONDS ago about the same
    followed repositories. When GitHub can't answer, the saved sections stay. A refresh that starts while another
    runs waits for it, and then finds its answer fresh."""
    with refreshing:
        repos = followed.load()
        saved = load()
        if saved["repos"] == repos and time.time() - saved["fetched"] < REFRESH_SECONDS:
            return
        answer = github.search_prs(queries(repos, date.today() - timedelta(days=MERGED_DAYS)))
        if answer is None:
            return
        viewer, found = answer
        save(Board(viewer=viewer, fetched=time.time(), repos=repos, sections=classify(viewer, found, repos)))


def ago(ts: float) -> str:
    s = max(0, int(time.time() - ts))
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{s // n}{unit} ago"
    return "just now"


def ref_of(r: Row) -> PullRef:
    return PullRef(r["owner"], r["repo"], r["number"])


def row_html(r: Row, viewer: str) -> str:
    ref = ref_of(r)
    by = f"<span>{html.escape(r['author'])}</span>" if r["author"] and r["author"] != viewer else ""
    why = f'<span class="why {r["tone"]}">{html.escape(r["reason"])}</span>' if r["reason"] else ""
    return (
        f'<a class="item" href="{html.escape(ref.path)}"><span class="t">{render.pr_icon(r["status"])}{html.escape(r["title"])}'
        f'</span><span class="w">{html.escape(ago(r["at"]))}</span>'
        f'<span class="m"><span class="ref">{html.escape(str(ref))}</span>{by}{why}</span></a>'
    )


def opened_rows(shown: set[PullRef]) -> list[Row]:
    """The pull requests opened last, newest first, less the ones the sections show."""
    return [
        Row(
            owner=p["owner"],
            repo=p["repo"],
            number=p["number"],
            title=p["title"],
            status=p["status"],
            author=p["author"],
            reason="",
            tone="",
            at=p["opened"],
        )
        for p in recent.load()["pages"]
        if recent.ref_of(p) not in shown
    ]


def sections_html() -> str:
    """The sections as the start page shows them: a group of sections for the viewer's pull requests, one for other
    people's, each section with a chevron that closes it, then the pull requests opened last that no section shows.
    An empty section or group is left out."""
    board = load()
    viewer, shown, groups = board["viewer"], set(), []
    for name, subs in GROUPS:
        parts = []
        for key, label in subs:
            rows = board["sections"].get(key) or []
            shown.update(ref_of(r) for r in rows)
            if rows:
                parts.append(
                    f'<section class="sub" data-key="{key}"><h3><button class="chev ghost open" '
                    f'aria-label="Close or open the section" title="Close or open the section" aria-expanded="true">'
                    f'{CHEVRON}</button>{html.escape(label)}<span class="count">{len(rows)}</span></h3>'
                    f'<div class="list card">{"".join(row_html(r, viewer) for r in rows)}</div></section>'
                )
        if parts:
            groups.append(f'<section class="group"><h2>{html.escape(name)}</h2>{"".join(parts)}</section>')
    if opened := opened_rows(shown):
        groups.append(
            f'<section class="group opened"><h2>Recently opened<span class="count">{len(opened)}</span></h2>'
            f'<div class="list card">{"".join(row_html(r, viewer) for r in opened)}</div></section>'
        )
    return "".join(groups) or '<div class="empty">No pull requests yet.</div>'
