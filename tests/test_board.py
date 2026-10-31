import json
import re
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import board
import recent
from github import PullRef

ME = "me"


@pytest.fixture(autouse=True)
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(board, "PATH", tmp_path / "board.json")
    monkeypatch.setattr(recent, "PATH", tmp_path / "recent.json")
    monkeypatch.setattr(board.followed, "load", lambda: [])
    return tmp_path


def by(login: str, at: str, **fields: Any) -> dict:
    return {"author": {"__typename": "Bot" if login.endswith("[bot]") else "User", "login": login}, **fields} | (
        {"createdAt": at} if "state" not in fields else {"submittedAt": at}
    )


def pr(
    number: int = 1,
    *,
    author: str = ME,
    repo: str = "acme/widgets",
    draft: bool = False,
    state: str = "OPEN",
    ci: str | None = "SUCCESS",
    pushed: str = "2026-10-20T10:00:00Z",
    committer: str | None = None,
    mergeable: str = "MERGEABLE",
    decision: str | None = None,
    requested: tuple[str, ...] = (),
    reviews: tuple[dict, ...] = (),
    comments: tuple[dict, ...] = (),
    threads: tuple[dict, ...] = (),
    updated: str = "2026-10-21T10:00:00Z",
    merged: str | None = None,
) -> dict:
    return {
        "number": number,
        "title": f"PR {number}",
        "url": f"https://github.com/{repo}/pull/{number}",
        "isDraft": draft,
        "state": state,
        "updatedAt": updated,
        "mergedAt": merged,
        "mergeable": mergeable,
        "reviewDecision": decision,
        "repository": {"nameWithOwner": repo},
        "author": {"__typename": "User", "login": author},
        "commits": {
            "nodes": [
                {
                    "commit": {
                        "committedDate": pushed,
                        "author": {"user": {"login": committer or author}},
                        "statusCheckRollup": ci and {"state": ci},
                    }
                }
            ]
        },
        "reviewRequests": {"nodes": [{"requestedReviewer": {"login": r}} for r in requested]},
        "reviews": {"nodes": list(reviews)},
        "comments": {"nodes": list(comments)},
        "reviewThreads": {"nodes": list(threads)},
    }


def thread(*said: dict, resolved: bool = False) -> dict:
    return {"isResolved": resolved, "comments": {"nodes": list(said)}}


def test_queries_search_the_followed_repositories_for_open_and_merged_pull_requests_only_when_there_are_some() -> None:
    assert len(board.queries([], date(2026, 10, 20))) == 5
    assert "merged:>=2026-10-20" in board.queries([], date(2026, 10, 20))[1]
    found = board.queries(["acme/tools", "acme/web"], date(2026, 10, 20))
    assert found[5:] == [
        board.FOLLOWED_QUERY + " repo:acme/tools repo:acme/web",
        board.MERGED_QUERY + " merged:>=2026-10-20 repo:acme/tools repo:acme/web",
    ]


def flat(placed: tuple[str, list[board.Reason]] | None) -> tuple[str, list[tuple]] | None:
    """A section and its reasons as (text, tone, kind) tuples, or None."""
    if placed is None:
        return None
    section, reasons = placed
    return section, [(r["text"], r["tone"], r["kind"]) for r in reasons]


@pytest.mark.parametrize(
    ("fields", "placed"),
    [
        ({"ci": "FAILURE"}, ("action", [("CI failed", "bad", "x-circle")])),
        (
            {"mergeable": "CONFLICTING", "ci": "ERROR"},
            ("action", [("CI failed", "bad", "x-circle"), ("Conflicts", "warn", "warning")]),
        ),
        ({"decision": "APPROVED"}, ("action", [("Approved and green", "ok", "check-circle")])),
        ({"decision": "APPROVED", "ci": None}, ("action", [("Approved and green", "ok", "check-circle")])),
        ({"decision": "APPROVED", "ci": "PENDING"}, ("ci", [("Checks running", "", "clock")])),
        ({"ci": "EXPECTED"}, ("ci", [("Checks running", "", "clock")])),
        ({"decision": "CHANGES_REQUESTED"}, ("review", [("Changes requested", "", "pencil")])),
        ({"requested": ("ann", "core")}, ("review", [("Review requested from ann, core", "", "person")])),
        ({}, ("review", [("No reviewer yet", "", "person-plus")])),
        ({"draft": True}, ("draft", [])),
        ({"draft": True, "mergeable": "CONFLICTING"}, ("draft", [("Conflicts", "warn", "warning")])),
        ({"draft": True, "ci": "FAILURE"}, ("draft", [("CI failed", "bad", "x-circle")])),
    ],
)
def test_own_places_a_pull_request_by_its_checks_conflicts_and_reviews(fields: dict, placed: tuple) -> None:
    assert flat(board.own(pr(**fields), ME)) == placed


def test_own_lists_every_reason_in_order_with_the_number_of_comments() -> None:
    late = pr(ci="FAILURE", mergeable="CONFLICTING", comments=(by("ann", "2026-10-21T00:00:00Z"),))
    assert flat(board.own(late, ME)) == (
        "action",
        [
            ("CI failed", "bad", "x-circle"),
            ("Conflicts", "warn", "warning"),
            ("1 comment from ann", "warn", "comment"),
        ],
    )


def test_own_counts_what_people_said_after_the_last_push_or_reply() -> None:
    said = pr(
        pushed="2026-10-20T10:00:00Z",
        decision="CHANGES_REQUESTED",
        comments=(by("ann", "2026-10-20T09:00:00Z"), by("ann", "2026-10-20T11:00:00Z")),
        reviews=(
            by("bob", "2026-10-20T12:00:00Z", state="CHANGES_REQUESTED"),
            by("cid", "2026-10-20T12:30:00Z", state="PENDING"),
        ),
        threads=(
            thread(by("ann", "2026-10-20T13:00:00Z")),
            thread(by("ann", "2026-10-20T14:00:00Z"), resolved=True),
            thread(by("ci-bot[bot]", "2026-10-20T15:00:00Z")),
        ),
    )
    assert flat(board.own(said, ME)) == ("action", [("3 comments from ann, bob", "warn", "comment")])
    # A reply of the viewer's after them answers them all, in a resolved thread too.
    answered = dict(said)
    answered["reviewThreads"] = {
        "nodes": said["reviewThreads"]["nodes"] + [thread(by(ME, "2026-10-20T16:00:00Z"), resolved=True)]
    }
    assert flat(board.own(answered, ME)) == ("review", [("Changes requested", "", "pencil")])


def test_own_counts_a_thread_reply_once_and_a_review_with_text_of_its_own() -> None:
    replied = pr(
        reviews=(
            by("ann", "2026-10-20T11:00:00Z", state="COMMENTED", body=""),
            by("bob", "2026-10-20T12:00:00Z", state="COMMENTED", body="Some notes."),
        ),
        threads=(thread(by("ann", "2026-10-20T11:00:00Z")),),
    )
    assert flat(board.own(replied, ME)) == ("action", [("2 comments from ann, bob", "warn", "comment")])


def test_own_shows_approved_and_green_after_an_approval_since_the_last_push() -> None:
    approved = pr(decision="APPROVED", reviews=(by("bob", "2026-10-20T11:00:00Z", state="APPROVED", body=""),))
    assert flat(board.own(approved, ME)) == ("action", [("Approved and green", "ok", "check-circle")])


def test_own_counts_a_head_commit_as_the_viewers_reply_only_when_the_viewer_made_it() -> None:
    teammate = pr(pushed="2026-10-20T12:00:00Z", committer="ann", comments=(by("bob", "2026-10-20T11:00:00Z"),))
    assert flat(board.own(teammate, ME)) == ("action", [("1 comment from bob", "warn", "comment")])


def test_own_names_a_deleted_account_ghost() -> None:
    gone = pr(comments=({"author": None, "createdAt": "2026-10-21T00:00:00Z"},))
    assert flat(board.own(gone, ME)) == ("action", [("1 comment from ghost", "warn", "comment")])


def test_own_names_one_comment_in_the_singular_and_drops_approved_and_green_for_it() -> None:
    one = pr(decision="APPROVED", comments=(by("ann", "2026-10-21T00:00:00Z"),))
    assert flat(board.own(one, ME)) == ("action", [("1 comment from ann", "warn", "comment")])


@pytest.mark.parametrize(
    ("fields", "asked", "followed", "placed"),
    [
        ({}, True, False, ("reviewing", [("Your review requested", "accent", "eye")])),
        ({"requested": (ME,)}, False, False, ("reviewing", [("Your review requested", "accent", "eye")])),
        (
            {"reviews": (by(ME, "2026-10-20T00:00:00Z", state="APPROVED"),)},
            False,
            False,
            ("reviewed", [("You approved", "", "check")]),
        ),
        (
            {"reviews": (by(ME, "2026-10-20T00:00:00Z", state="CHANGES_REQUESTED"),)},
            False,
            False,
            ("reviewed", [("You requested changes", "", "pencil")]),
        ),
        (
            {"threads": (thread(by(ME, "2026-10-20T00:00:00Z"), resolved=True),)},
            False,
            False,
            ("reviewed", [("You commented", "", "comment")]),
        ),
        ({}, False, False, None),
        ({}, False, True, ("unreviewed", [])),
        ({"draft": True}, False, True, None),
        (
            {"reviews": (by("bob", "2026-10-20T00:00:00Z", state="APPROVED"),)},
            False,
            True,
            ("others", [("Approved by bob", "", "check")]),
        ),
        (
            {"reviews": (by("bob", "2026-10-20T00:00:00Z", state="COMMENTED"),)},
            False,
            True,
            ("others", [("Reviewed by bob", "", "comment")]),
        ),
        ({"requested": ("ann",)}, False, True, ("others", [("Review requested from ann", "", "person")])),
        (
            {"reviews": (by("dep[bot]", "2026-10-20T00:00:00Z", state="COMMENTED"),)},
            False,
            True,
            ("unreviewed", []),
        ),
    ],
)
def test_other_places_someone_elses_pull_request(
    fields: dict, asked: bool, followed: bool, placed: tuple | None
) -> None:
    assert flat(board.other(pr(author="ann", **fields), ME, asked, False, followed)) == placed


def test_other_keeps_a_pull_request_the_viewer_reviewed_long_ago_and_names_the_last_verdict() -> None:
    assert flat(board.other(pr(author="ann"), ME, False, True, False)) == (
        "reviewed",
        [("You reviewed", "", "comment")],
    )
    approved_then_replied = pr(
        author="ann",
        reviews=(
            by(ME, "2026-10-20T00:00:00Z", state="APPROVED"),
            by(ME, "2026-10-20T01:00:00Z", state="COMMENTED"),
        ),
    )
    assert flat(board.other(approved_then_replied, ME, False, False, False)) == (
        "reviewed",
        [("You approved", "", "check")],
    )


def test_classify_puts_each_pull_request_in_one_section_newest_first() -> None:
    mine = [pr(1, updated="2026-10-20T00:00:00Z"), pr(2, updated="2026-10-22T00:00:00Z"), pr(3, draft=True)]
    merged = [pr(4, state="MERGED", merged="2026-10-21T00:00:00Z")]
    asked = [pr(5, author="ann")]
    involved = [pr(5, author="ann"), pr(6, author="ann", reviews=(by(ME, "2026-10-20T00:00:00Z", state="COMMENTED"),))]
    reviewed: list[dict] = []
    followed_found = [
        pr(7, author="bob", repo="Acme/Tools"),
        pr(8, author=ME, repo="acme/tools"),
        pr(9, author="bob", repo="other/x"),
    ]
    sections = board.classify(ME, [mine, merged, asked, involved, reviewed, followed_found, []], ["acme/tools"])
    numbers = {key: [r["number"] for r in rows] for key, rows in sections.items() if rows}
    assert numbers == {
        "review": [2, 1],
        "draft": [3],
        "merged": [4],
        "reviewing": [5],
        "reviewed": [6],
        "unreviewed": [7],
    }
    assert sections["merged"][0]["status"] == "merged" and sections["draft"][0]["status"] == "draft"
    assert sections["unreviewed"][0]["owner"] == "acme" and sections["unreviewed"][0]["repo"] == "tools"


def save(sections: dict, viewer: str = ME, full: tuple[str, ...] = (), repos: tuple[str, ...] = ()) -> None:
    board.save(board.Board(viewer=viewer, fetched=time.time(), repos=list(repos), sections=sections, full=list(full)))


def test_classify_lists_other_peoples_merges_newest_merge_first_and_leaves_out_the_viewers_own() -> None:
    merged_others = [
        pr(1, author="ann", state="MERGED", merged="2026-10-20T00:00:00Z", updated="2026-10-23T00:00:00Z"),
        pr(2, author="bob", state="MERGED", merged="2026-10-22T00:00:00Z", updated="2026-10-22T00:00:00Z"),
        pr(3, author=ME, state="MERGED", merged="2026-10-21T00:00:00Z"),
    ]
    sections = board.classify(ME, [[], [], [], [], [], [], merged_others], ["acme/widgets"])
    assert [r["number"] for r in sections["merged_others"]] == [2, 1]
    assert sections["merged_others"][1]["at"] == board.seconds("2026-10-20T00:00:00Z")


def sub(page: str, key: str) -> str:
    """The HTML of the list with the key."""
    return re.findall(rf'<section class="sub" data-key="{key}">.*?</section>', page)[0]


def column_counts(page: str) -> list[str]:
    return re.findall(r'<h3>[^<]*<span class="count">([^<]*)</span>', page)


def test_html_draws_every_column_counts_its_sections_and_names_other_authors() -> None:
    assert board.sections_html().startswith('<div class="empty">No pull requests yet.</div><footer')
    save(board.classify(ME, [[pr(1, ci="FAILURE"), pr(2)], [], [pr(3, author="ann", repo="acme/tools")], [], []], []))
    page = board.sections_html()
    assert re.findall(r'<section class="sub" data-key="(\w+)">', page) == [
        "draft",
        "action",
        "merged",
        "unreviewed",
        "reviewing",
        "merged_others",
    ]
    assert column_counts(page) == ["0", "2", "0", "0", "1", "0"]
    mine, _, theirs = re.findall(r'<a class="card.*?</a>', page)
    assert 'href="/acme/widgets/pull/1"' in mine and '<span class="reason tone-bad" title="CI failed">' in mine
    assert '<span class="ref">acme/widgets#1</span>' in mine and ">me<" not in mine
    assert 'href="/acme/tools/pull/3"' in theirs and '<span class="by">ann</span>' in theirs
    assert '<span class="ref">tools</span>' in theirs


def test_html_lists_a_column_most_urgent_section_first_and_each_section_newest_first() -> None:
    mine = [
        pr(1, updated="2026-10-24T00:00:00Z"),
        pr(2, ci="PENDING", updated="2026-10-25T00:00:00Z"),
        pr(3, ci="FAILURE", updated="2026-10-20T00:00:00Z"),
        pr(4, ci="FAILURE", updated="2026-10-21T00:00:00Z"),
    ]
    asked = [pr(5, author="ann", updated="2026-10-20T00:00:00Z")]
    involved = [
        pr(
            6, author="ann", reviews=(by(ME, "2026-10-20T00:00:00Z", state="APPROVED"),), updated="2026-10-21T00:00:00Z"
        ),
        pr(7, author="ann", requested=("bob",), updated="2026-10-24T00:00:00Z"),
    ]
    save(board.classify(ME, [mine, [], asked, involved, [], [], []], ["acme/widgets"]))
    page = board.sections_html()
    assert re.findall(r'href="/acme/widgets/pull/(\d+)"', sub(page, "action")) == ["4", "3", "2", "1"]
    assert re.findall(r'href="/acme/widgets/pull/(\d+)"', sub(page, "reviewing")) == ["5", "6", "7"]


@pytest.mark.parametrize("sections", [None, [], "rows"])
def test_load_treats_a_board_with_broken_sections_as_empty(paths: Path, sections: object) -> None:
    (paths / "board.json").write_text(json.dumps({"viewer": ME, "fetched": 0.0, "repos": [], "sections": sections}))
    assert board.load()["sections"] == {}


def test_load_treats_a_board_with_rows_without_reasons_as_empty(paths: Path) -> None:
    old_row = {"owner": "acme", "repo": "widgets", "number": 1, "title": "PR 1", "status": "open", "author": ME}
    old_row |= {"reason": "CI failed", "tone": "bad", "at": 0.0}
    old = {"viewer": ME, "fetched": time.time(), "repos": [], "sections": {"action": [old_row]}, "full": []}
    (paths / "board.json").write_text(json.dumps(old))
    assert board.load() == board.Board(viewer="", fetched=0.0, repos=[], sections={}, full=[])
    assert board.sections_html().startswith('<div class="empty">No pull requests yet.</div>')


def test_footer_names_the_viewer_and_the_last_answer_only_once_github_has_answered() -> None:
    footer = board.footer_html(board.load())
    assert "Signed in" not in footer and "Updated" not in footer
    save({}, viewer="ann")
    footer = board.footer_html(board.load())
    assert 'Signed in as <span class="login">ann</span>' in footer and "Updated from GitHub just now" in footer


def test_html_names_a_deleted_author_ghost() -> None:
    save(board.classify(ME, [[], [], [pr(1, author="")], [], []], []))
    assert '<span class="by">ghost</span>' in sub(board.sections_html(), "reviewing")


def test_html_keeps_an_empty_column_with_none() -> None:
    save(board.classify(ME, [[pr(1)], [], [], [], []], []))
    page = board.sections_html()
    for key in ("draft", "reviewing"):
        assert board.NONE_HTML in sub(page, key)
    assert board.NONE_HTML not in sub(page, "action")
    assert column_counts(page)[:2] == ["0", "1"]


def test_html_shows_the_first_rows_of_a_section_and_a_button_for_the_rest() -> None:
    save(board.classify(ME, [[pr(n, draft=True) for n in range(1, 9)], [], [], [], []], []))
    drafts = sub(board.sections_html(), "draft")
    assert drafts.count("<a class=") == 8 and drafts.count(" hidden>") == 3
    assert '<button class="ghost more">Show 3 more</button>' in drafts
    save(board.classify(ME, [[pr(n, draft=True) for n in range(1, 6)], [], [], [], []], []))
    assert "more</button>" not in sub(board.sections_html(), "draft")


def test_full_names_every_section_a_search_with_as_many_answers_as_it_reads_feeds() -> None:
    limit = board.github.SEARCH_LIMIT
    some, many = [pr(1)], [pr(n) for n in range(limit)]
    assert board.full([some, many, some, some, some]) == ["merged"]
    assert board.full([many, some, some, some, some, some, many]) == [
        "action",
        "ci",
        "draft",
        "merged_others",
        "review",
    ]
    assert board.full([some, some, some, some, some, many, some]) == ["others", "unreviewed"]
    assert board.full([some, some, some, some, many]) == ["reviewed", "reviewing"]


def test_html_splits_each_row_in_shares_and_folds_the_merged_column_under_it() -> None:
    save(board.classify(ME, [[pr(1)], [pr(2, state="MERGED", merged="2026-10-20T00:00:00Z")], [], [], []], []))
    page = board.sections_html()
    assert re.findall(r'<div class="columns" style="--split: ([^"]*)">', page) == ["3fr 7fr", "1fr 1fr"]
    mine, theirs = re.findall(r'<details class="folded" data-key="(\w+)"><summary><svg class="chev"', page)
    assert (mine, theirs) == ("merged", "merged_others")
    folded = page[page.index('<details class="folded" data-key="merged">') : page.index("</details>")]
    assert "<h3>Merged today" in folded and 'href="/acme/widgets/pull/2"' in folded
    assert folded.index("</summary>") < folded.index('<div class="column quiet">')
    assert column_counts(page) == ["0", "1", "1", "0", "0", "0"]


def test_html_adds_a_plus_to_the_count_of_a_column_with_a_full_section() -> None:
    save(board.classify(ME, [[pr(1), pr(2, ci="FAILURE")], [], [], [], []], []), full=("action",))
    page = board.sections_html()
    assert column_counts(page)[1] == "2+"


FOLLOWED = ("acme/api", "acme/web")


def save_two_repos(full: tuple[str, ...] = ()) -> None:
    """A board with draft 1 to 7 in acme/web, draft 8, review 9 and merged 11 in acme/api, and someone else's 10 in
    acme/tools, which the viewer does not follow."""
    drafts = [pr(n, draft=True, repo="acme/web") for n in range(1, 8)] + [pr(8, draft=True, repo="acme/api")]
    merged = pr(11, repo="acme/api", state="MERGED", merged="2026-10-22T00:00:00Z")
    found = [[*drafts, pr(9, repo="acme/api")], [merged], [pr(10, author="ann", repo="acme/tools")], [], []]
    save(board.classify(ME, found, list(FOLLOWED)), full=full, repos=FOLLOWED)


def toggles(page: str) -> dict[str, tuple[bool, str]]:
    """Each toggle of the strip: whether it is pressed, and its count."""
    found = re.findall(
        r'<button( class="on")? aria-pressed="\w+" data-repo="([^"]+)"[^>]*>[^<]*<span class="count">([^<]*)', page
    )
    return {repo: (bool(on), count) for on, repo, count in found}


def test_html_shows_the_rows_of_the_repositories_turned_on_and_counts_them() -> None:
    save_two_repos()
    page = board.sections_html(["acme/api"])
    assert re.findall(r'href="/acme/\w+/pull/(\d+)"', page) == ["8", "9", "11"]
    assert column_counts(page)[:2] == ["1", "1"]
    assert "more</button>" not in sub(page, "draft") and board.NONE_HTML in sub(page, "reviewing")
    page = board.sections_html(["acme/web", "acme/api"])
    assert column_counts(page)[:2] == ["8", "1"] and "Show 3 more" in sub(page, "draft")
    assert 'href="/acme/tools/pull/10"' not in page


def test_html_shows_every_row_when_no_followed_repository_is_on_and_drops_an_unfollowed_one() -> None:
    save_two_repos()
    for on in (None, [], ["acme/gone"]):
        page = board.sections_html(on)
        assert 'href="/acme/tools/pull/10"' in page and column_counts(page)[:2] == ["8", "1"]
        assert toggles(page) == {"acme/api": (False, "2"), "acme/web": (False, "7")}
    assert toggles(board.sections_html(["acme/gone", "acme/api"]))["acme/api"] == (True, "2")


def test_a_toggle_counts_its_cards_outside_the_merged_columns_with_a_plus_for_a_full_section() -> None:
    save_two_repos(full=("review", "merged"))
    page = board.sections_html(["acme/web"])
    assert toggles(page) == {"acme/api": (False, "2+"), "acme/web": (True, "7")}
    assert column_counts(page)[1:3] == ["0", "0"]
    assert toggles(board.sections_html(["acme/api", "acme/web"])) == {
        "acme/api": (True, "2+"),
        "acme/web": (True, "7"),
    }


def test_recently_opened_follows_the_repositories_turned_on_and_leaves_out_hidden_cards() -> None:
    save_two_repos()
    recent.note_opened(PullRef("acme", "web", 1), "In a section", "open", ME)
    recent.note_opened(PullRef("acme", "web", 20), "Web", "open", "ann")
    recent.note_opened(PullRef("acme", "api", 21), "Api", "open", "ann")
    page = board.sections_html(["acme/api"])
    opened = page[page.index('<section class="group opened">') :]
    assert re.findall(r'href="/acme/\w+/pull/(\d+)"', opened) == ["21"]


def test_recently_opened_lists_what_no_section_shows_and_escapes_titles() -> None:
    save(board.classify(ME, [[pr(1)], [], [], [], []], []))
    recent.note_opened(PullRef("acme", "widgets", 1), "Shown above", "open", ME)
    recent.note_opened(PullRef("acme", "widgets", 9), "a <b>", "merged", "ann")
    page = board.sections_html()
    opened = page[page.index('<section class="group opened">') :]
    assert '<h2>Recently opened<span class="count">1</span></h2>' in opened
    assert 'href="/acme/widgets/pull/9"' in opened and "a &lt;b&gt;" in opened
    assert "pull/1" not in opened


def test_recently_opened_leaves_out_the_pull_requests_opened_before_today() -> None:
    save(board.classify(ME, [[], [], [], [], []], []))
    recent.note_opened(PullRef("acme", "widgets", 1), "Today", "open", "ann")
    recent.note_opened(PullRef("acme", "widgets", 2), "Yesterday", "open", "ann")
    pages = recent.load()
    pages["pages"][0]["opened"] = board.today_start() - 1
    recent.save(pages)
    page = board.sections_html()
    assert "pull/1" in page and "pull/2" not in page


def answer(*found: list[dict]) -> tuple[str, list[list[dict]]]:
    return ME, [*found, [], [], [], [], []][:5]


def test_refresh_saves_the_sections_and_keeps_them_when_github_cannot_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(board.github, "search_prs", lambda queries: answer([pr(1)]))
    board.refresh()
    saved = board.load()
    assert saved["viewer"] == ME and [r["number"] for r in saved["sections"]["review"]] == [1]
    monkeypatch.setattr(board.github, "search_prs", lambda queries: None)
    monkeypatch.setattr(board, "REFRESH_SECONDS", 0)
    board.refresh()
    assert board.load()["sections"] == saved["sections"]


def test_refresh_waits_a_while_unless_the_followed_repositories_changed(
    paths: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[list[str]] = []
    monkeypatch.setattr(board.github, "search_prs", lambda queries: asked.append(queries) or answer())
    board.refresh()
    board.refresh()
    assert len(asked) == 1
    monkeypatch.setattr(board.followed, "load", lambda: ["acme/tools"])
    board.refresh()
    assert len(asked) == 2 and "repo:acme/tools" in asked[1][-1]
    assert json.loads((paths / "board.json").read_text())["repos"] == ["acme/tools"]


def test_a_refresh_that_starts_during_another_waits_and_uses_its_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = []
    started, release = threading.Event(), threading.Event()

    def slow(queries: list[str]) -> tuple[str, list[list[dict]]]:
        asked.append(queries)
        started.set()
        release.wait(5)
        return answer([pr(1)])

    monkeypatch.setattr(board.github, "search_prs", slow)
    first = threading.Thread(target=board.refresh)
    first.start()
    started.wait(5)
    second = threading.Thread(target=board.refresh)
    second.start()
    second.join(0.2)
    assert second.is_alive()
    release.set()
    first.join(5)
    second.join(5)
    assert len(asked) == 1 and [r["number"] for r in board.load()["sections"]["review"]] == [1]


def test_ago() -> None:
    now = time.time()
    assert board.ago(now) == "just now"
    assert board.ago(now - 90) == "1m ago"
    assert board.ago(now - 3700) == "1h ago"
    assert board.ago(now - 2 * 86400) == "2d ago"
