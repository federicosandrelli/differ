"""The start page's sections: the viewer's own pull requests, other people's, and the ones opened today that neither
shows. GitHub's last answer, sorted into sections, is kept in board.json in the cache."""

from __future__ import annotations

import html
import itertools
import json
import os
import threading
import time
from datetime import UTC, date, datetime
from typing import NamedTuple, TypedDict

import build
import followed
import github
import recent
import render
from github import PullRef

PATH = build.CACHE / "board.json"
REFRESH_SECONDS = 30
FOLLOWED_QUERY = "is:pr is:open archived:false draft:false -author:@me sort:updated-desc"
MERGED_QUERY = "is:pr is:merged -author:@me sort:updated-desc"
FAILED = ("FAILURE", "ERROR")
RUNNING = ("PENDING", "EXPECTED")
SHOWN = 5
NONE_HTML = '<span class="hint none">None</span>'


class Column(NamedTuple):
    """A column of the start page: its label, its sections as (key, subtitle) pairs, the subtitle empty when the column
    has one section, its share of the row's width, whether its rows are quiet, and whether it is folded: drawn under
    the row, closed until the reader opens it."""

    label: str
    sections: tuple[tuple[str, str], ...]
    share: int = 1
    quiet: bool = False
    folded: bool = False


GROUPS = (
    (
        "Your pull requests",
        (
            Column("Drafts", (("draft", ""),), share=3),
            Column(
                "In review",
                (("action", "Needs your action"), ("ci", "Waiting on CI"), ("review", "Waiting on review")),
                share=7,
            ),
            Column("Merged today", (("merged", ""),), quiet=True, folded=True),
        ),
    ),
    (
        "Other people's pull requests",
        (
            Column("Need a reviewer", (("unreviewed", ""),)),
            Column(
                "Being reviewed",
                (("reviewing", "Waiting on your review"), ("reviewed", "By you"), ("others", "By someone else")),
            ),
            Column("Merged today", (("merged_others", ""),), quiet=True, folded=True),
        ),
    ),
)
CHEVRON_SVG = (
    '<svg class="chev" viewBox="0 0 16 16" width="18" height="18" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" '
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
)
GEAR_SVG = (
    '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-widt'
    'h="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.6'
    "5 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 "
    "1-4 0v-.09a1.65 1.65 0 0 0-1.08-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0"
    " .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 "
    "2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 "
    "1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0"
    ' 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>'
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
    asked about, the rows of each section, newest first, and the keys of the full sections (see `full`)."""

    viewer: str
    fetched: float
    repos: list[str]
    sections: dict[str, list[Row]]
    full: list[str]


def queries(repos: list[str], merged_since: date) -> list[str]:
    """The searches the sections read, in this order: the viewer's open pull requests, the viewer's pull requests
    merged on or after the date, the pull requests that ask for the viewer's review, the ones the viewer takes part
    in, the ones the viewer reviewed, and, when there are followed repositories, their open pull requests and the ones
    other people merged there on or after the date."""
    found = [
        "is:pr is:open archived:false author:@me sort:updated-desc",
        f"is:pr is:merged author:@me merged:>={merged_since.isoformat()} sort:updated-desc",
        "is:pr is:open archived:false review-requested:@me sort:updated-desc",
        "is:pr is:open archived:false involves:@me -author:@me sort:updated-desc",
        "is:pr is:open archived:false reviewed-by:@me -author:@me sort:updated-desc",
    ]
    if repos:
        scope = "".join(f" repo:{repo}" for repo in repos)
        found.append(FOLLOWED_QUERY + scope)
        found.append(f"{MERGED_QUERY} merged:>={merged_since.isoformat()}{scope}")
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
    sections: dict[str, list[Row]] = {
        key: [] for _, columns in GROUPS for column in columns for key, _ in column.sections
    }
    open_mine, merged, asked, involved, reviewed, *rest = found
    followed_open, merged_others = rest or ([], [])
    for pr in open_mine:
        key, reason, tone = own(pr, viewer)
        sections[key].append(row(pr, reason, tone))
    for pr in merged:
        sections["merged"].append(row(pr, "", ""))
    asked_urls, reviewed_urls = {pr["url"] for pr in asked}, {pr["url"] for pr in reviewed}
    followed_repos = set(repos)
    seen: set[str] = set()
    for pr in itertools.chain(asked, involved, reviewed, followed_open):
        if pr["url"] in seen or login(pr) == viewer:
            continue
        seen.add(pr["url"])
        repo = pr["repository"]["nameWithOwner"].lower()
        if placed := other(pr, viewer, pr["url"] in asked_urls, pr["url"] in reviewed_urls, repo in followed_repos):
            key, reason, tone = placed
            sections[key].append(row(pr, reason, tone))
    for pr in merged_others:
        if pr["url"] not in seen and login(pr) != viewer:
            sections["merged_others"].append(row(pr, "", ""))
    for rows in sections.values():
        rows.sort(key=lambda r: r["at"], reverse=True)
    return sections


def full(found: list[list[dict]]) -> list[str]:
    """The keys of the sections that may lack rows: those fed by a search that found as many pull requests as
    `github.search_prs` reads. `found` holds the answers to `queries`, in its order."""
    open_mine, merged, asked, involved, reviewed, *rest = found
    followed_open, merged_others = rest or ([], [])
    fed = (
        (open_mine, ("draft", "action", "ci", "review")),
        (merged, ("merged",)),
        (asked, ("reviewing",)),
        (involved, ("reviewing", "reviewed", "unreviewed", "others")),
        (reviewed, ("reviewing", "reviewed")),
        (followed_open, ("unreviewed", "others")),
        (merged_others, ("merged_others",)),
    )
    return sorted({key for answer, keys in fed if len(answer) >= github.SEARCH_LIMIT for key in keys})


def load() -> Board:
    try:
        data = json.loads(PATH.read_text())
        return Board(
            viewer=data["viewer"],
            fetched=data["fetched"],
            repos=data["repos"],
            sections=data["sections"],
            full=data.get("full", []),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return Board(viewer="", fetched=0.0, repos=[], sections={}, full=[])


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
    followed repositories. The merged sections hold the pull requests merged today, by GitHub's date (UTC). When
    GitHub can't answer, the saved sections stay. A refresh that starts while another runs waits for it, and then
    finds its answer fresh."""
    with refreshing:
        repos = followed.load()
        saved = load()
        if saved["repos"] == repos and time.time() - saved["fetched"] < REFRESH_SECONDS:
            return
        answer = github.search_prs(queries(repos, datetime.now(UTC).date()))
        if answer is None:
            return
        viewer, found = answer
        save(
            Board(
                viewer=viewer,
                fetched=time.time(),
                repos=repos,
                sections=classify(viewer, found, repos),
                full=full(found),
            )
        )


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


def today_start() -> float:
    """Midnight today, local time, in seconds since the epoch."""
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def opened_rows(shown: set[PullRef]) -> list[Row]:
    """The pull requests opened today, newest first, less the ones the sections show."""
    since = today_start()
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
        if p["opened"] >= since and recent.ref_of(p) not in shown
    ]


def count_html(rows: int, full: bool) -> str:
    """A count of rows, with a plus when `full` says a search may have found more than it read."""
    return f'<span class="count">{rows}{"+" if full else ""}</span>'


def card_html(r: Row, viewer: str, quiet: bool, hidden: bool) -> str:
    """A pull request as a card: its title, then, for the viewer's own, its status icon and `owner/repo#number`, or,
    for someone else's, the author's login and the repository's name, then the reason and the age. A quiet card sits
    on the canvas, the others on the panel."""
    ref = ref_of(r)
    if r["author"] == viewer:
        who = f'{render.pr_icon(r["status"])}<span class="ref">{html.escape(str(ref))}</span>'
    else:
        who = f'<span class="by">{html.escape(r["author"] or "ghost")}</span><span class="ref">{html.escape(r["repo"])}</span>'
    why = f'<span class="why {r["tone"]}">{html.escape(r["reason"])}</span>' if r["reason"] else ""
    return (
        f'<a class="card{"" if quiet else " panel"} pr" href="{html.escape(ref.path)}"{" hidden" if hidden else ""}>'
        f'<span class="t">{html.escape(r["title"])}</span><span class="m">{who}<span class="spacer"></span>{why}'
        f'<span class="age">{html.escape(ago(r["at"]))}</span></span></a>'
    )


def section_html(key: str, subtitle: str, rows: list[Row], full: bool, viewer: str, quiet: bool) -> str:
    """A section: its subtitle and count when it has a subtitle, then its cards, all but the first SHOWN hidden,
    and a "Show N more" button when some are hidden, or None when it has no rows."""
    head = f"<h4>{html.escape(subtitle)}{count_html(len(rows), full)}</h4>" if subtitle else ""
    cards = "".join(card_html(r, viewer, quiet, i >= SHOWN) for i, r in enumerate(rows))
    more = f'<button class="ghost more">Show {len(rows) - SHOWN} more</button>' if len(rows) > SHOWN else ""
    return f'<section class="sub" data-key="{key}">{head}{cards or NONE_HTML}{more}</section>'


def footer_html(board: Board) -> str:
    """The start page's footer: the viewer's login and when GitHub last answered, both left out until GitHub has
    answered, then a button that opens the Settings."""
    known = (
        f'<span>Signed in as <span class="login">{html.escape(board["viewer"])}</span></span>'
        f"<span>Updated from GitHub {html.escape(ago(board['fetched']))}</span>"
        if board["viewer"]
        else ""
    )
    return f'<footer class="start hint">{known}<button class="ghost" data-settings>Settings <kbd>⌘,</kbd></button></footer>'


def column_html(column: Column, board: Board) -> str:
    """A column: its label and the count of the rows of its sections, with a plus when one of them is full, then its
    sections. A folded column is a `details` keyed by its first section, closed, with a chevron in its summary."""
    lists = [
        (key, subtitle, board["sections"].get(key) or [], key in board["full"]) for key, subtitle in column.sections
    ]
    total = sum(len(rows) for _, _, rows, _ in lists)
    full = any(full for _, _, _, full in lists)
    head = f"<h3>{html.escape(column.label)}{count_html(total, full)}</h3>"
    body = "".join(section_html(*section, board["viewer"], column.quiet) for section in lists)
    classes = f'class="column{" quiet" if column.quiet else ""}"'
    if column.folded:
        return (
            f'<details class="folded" data-key="{column.sections[0][0]}"><summary>{CHEVRON_SVG}{head}</summary>'
            f"<div {classes}>{body}</div></details>"
        )
    return f"<div {classes}>{head}{body}</div>"


def name_of(r: Row) -> str:
    return f"{r['owner']}/{r['repo']}"


def repos_html(board: Board, on: list[str]) -> str:
    """The strip above the groups: a toggle per followed repository, pressed when `on` names it, with the count of
    its cards outside the folded columns, and a plus when a full section outside them has some of these cards, then a gear that opens the followed repositories in the Settings. A toggle shows the
    repository's name, or `owner/repo` when another followed repository has the same name. Without followed
    repositories, a hint and a button to follow one."""
    if not board["repos"]:
        return (
            '<div class="repos" role="group" aria-label="Repositories"><span class="hint">Other people\'s pull '
            "requests come from the repositories you follow.</span>"
            '<button class="ghost" data-settings="repositories">Follow a repository</button></div>'
        )
    counted = [key for _, columns in GROUPS for c in columns if not c.folded for key, _ in c.sections]
    short = [name.split("/", 1)[1] for name in board["repos"]]
    toggles = []
    for name, repo in zip(board["repos"], short, strict=True):
        cards = [(key, r) for key in counted for r in board["sections"].get(key) or [] if name_of(r) == name]
        label, title = (name, "") if short.count(repo) > 1 else (repo, f' title="{html.escape(name)}"')
        pressed = ' class="on" aria-pressed="true"' if name in on else ' aria-pressed="false"'
        toggles.append(
            f'<button{pressed} data-repo="{html.escape(name)}"{title}>{html.escape(label)}'
            f"{count_html(len(cards), any(key in board['full'] for key, _ in cards))}</button>"
        )
    return (
        '<div class="repos" role="group" aria-label="Repositories"><span class="hint">Repositories</span>'
        f'{"".join(toggles)}<button class="ghost" data-settings="repositories" title="Followed repositories" '
        f'aria-label="Followed repositories">{GEAR_SVG}</button></div>'
    )


def sections_html(on: list[str] | None = None) -> str:
    """The start page's board, once GitHub has answered: the strip of followed repositories, a group for the viewer's
    pull requests and one for other people's, each a row of columns, their widths in the shares the columns name, with
    its folded columns under the row; then the pull requests opened today and missing from every section, then the
    footer. The board shows the rows of the followed repositories that `on` names, or every row when `on` names no
    followed repository. A full section keeps its plus while the board shows only some repositories when it still
    has rows."""
    board = load()
    chosen = [name for name in board["repos"] if name in (on or [])]

    def kept(rows: list[Row]) -> list[Row]:
        return [r for r in rows if not chosen or name_of(r) in chosen]

    sections = {key: kept(rows) for key, rows in board["sections"].items()}
    shown = {ref_of(r) for rows in board["sections"].values() for r in rows}
    filtered: Board = {
        **board,
        "sections": sections,
        "full": [key for key in board["full"] if not chosen or sections.get(key)],
    }
    strip, groups = "", []
    if board["sections"]:
        strip = repos_html(board, chosen)
        for name, columns in GROUPS:
            row = [c for c in columns if not c.folded]
            split = " ".join(f"{c.share}fr" for c in row)
            parts = "".join(column_html(c, filtered) for c in row)
            folded = "".join(column_html(c, filtered) for c in columns if c.folded)
            groups.append(
                f'<section class="group"><h2>{html.escape(name)}</h2>'
                f'<div class="columns" style="--split: {split}">{parts}</div>{folded}</section>'
            )
    if opened := kept(opened_rows(shown)):
        groups.append(
            f'<section class="group opened"><h2>Recently opened<span class="count">{len(opened)}</span></h2>'
            f'<div class="list card">{"".join(row_html(r, board["viewer"]) for r in opened)}</div></section>'
        )
    return strip + ("".join(groups) or '<div class="empty">No pull requests yet.</div>') + footer_html(board)
