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


def test_queries_search_the_followed_repositories_in_one_query() -> None:
    assert len(board.queries([], date(2026, 10, 20))) == 5
    assert "merged:>=2026-10-20" in board.queries([], date(2026, 10, 20))[1]
    found = board.queries(["acme/tools", "acme/web"], date(2026, 10, 20))
    assert found[5:] == [board.FOLLOWED_QUERY + " repo:acme/tools repo:acme/web"]


@pytest.mark.parametrize(
    ("fields", "section", "reason", "tone"),
    [
        ({"ci": "FAILURE"}, "action", "CI failed", "bad"),
        ({"mergeable": "CONFLICTING", "ci": "ERROR"}, "action", "CI failed · conflicts", "bad"),
        ({"decision": "APPROVED"}, "action", "approved and green", "ok"),
        ({"decision": "APPROVED", "ci": None}, "action", "approved and green", "ok"),
        ({"decision": "APPROVED", "ci": "PENDING"}, "ci", "checks running", ""),
        ({"ci": "EXPECTED"}, "ci", "checks running", ""),
        ({"decision": "CHANGES_REQUESTED"}, "review", "changes requested", ""),
        ({"requested": ("ann", "core")}, "review", "review requested from ann, core", ""),
        ({}, "review", "no reviewer yet", ""),
        ({"draft": True, "mergeable": "CONFLICTING"}, "draft", "conflicts", "warn"),
        ({"draft": True, "ci": "FAILURE"}, "draft", "CI failed", "bad"),
    ],
)
def test_own_places_a_pull_request_by_its_checks_conflicts_and_reviews(
    fields: dict, section: str, reason: str, tone: str
) -> None:
    assert board.own(pr(**fields), ME) == (section, reason, tone)


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
    assert board.own(said, ME) == ("action", "3 comments from ann, bob", "warn")
    # A reply of the viewer's after them answers them all, in a resolved thread too.
    answered = dict(said)
    answered["reviewThreads"] = {
        "nodes": said["reviewThreads"]["nodes"] + [thread(by(ME, "2026-10-20T16:00:00Z"), resolved=True)]
    }
    assert board.own(answered, ME) == ("review", "changes requested", "")


def test_own_counts_a_thread_reply_once_and_a_review_with_text_of_its_own() -> None:
    replied = pr(
        reviews=(
            by("ann", "2026-10-20T11:00:00Z", state="COMMENTED", body=""),
            by("bob", "2026-10-20T12:00:00Z", state="COMMENTED", body="Some notes."),
        ),
        threads=(thread(by("ann", "2026-10-20T11:00:00Z")),),
    )
    assert board.own(replied, ME) == ("action", "2 comments from ann, bob", "warn")


def test_own_shows_approved_and_green_after_an_approval_since_the_last_push() -> None:
    approved = pr(decision="APPROVED", reviews=(by("bob", "2026-10-20T11:00:00Z", state="APPROVED", body=""),))
    assert board.own(approved, ME) == ("action", "approved and green", "ok")


def test_own_counts_a_head_commit_as_the_viewers_reply_only_when_the_viewer_made_it() -> None:
    teammate = pr(pushed="2026-10-20T12:00:00Z", committer="ann", comments=(by("bob", "2026-10-20T11:00:00Z"),))
    assert board.own(teammate, ME) == ("action", "1 comment from bob", "warn")


def test_own_names_a_deleted_account_ghost() -> None:
    gone = pr(comments=({"author": None, "createdAt": "2026-10-21T00:00:00Z"},))
    assert board.own(gone, ME) == ("action", "1 comment from ghost", "warn")


def test_own_names_one_comment_in_the_singular_and_drops_approved_and_green_for_it() -> None:
    one = pr(decision="APPROVED", comments=(by("ann", "2026-10-21T00:00:00Z"),))
    assert board.own(one, ME) == ("action", "1 comment from ann", "warn")


@pytest.mark.parametrize(
    ("fields", "asked", "followed", "placed"),
    [
        ({}, True, False, ("reviewing", "your review requested", "accent")),
        ({"requested": (ME,)}, False, False, ("reviewing", "your review requested", "accent")),
        (
            {"reviews": (by(ME, "2026-10-20T00:00:00Z", state="APPROVED"),)},
            False,
            False,
            ("reviewed", "you approved", ""),
        ),
        (
            {"reviews": (by(ME, "2026-10-20T00:00:00Z", state="CHANGES_REQUESTED"),)},
            False,
            False,
            ("reviewed", "you requested changes", ""),
        ),
        (
            {"threads": (thread(by(ME, "2026-10-20T00:00:00Z"), resolved=True),)},
            False,
            False,
            ("reviewed", "you commented", ""),
        ),
        ({}, False, False, None),
        ({}, False, True, ("unreviewed", "", "")),
        ({"draft": True}, False, True, None),
        (
            {"reviews": (by("bob", "2026-10-20T00:00:00Z", state="APPROVED"),)},
            False,
            True,
            ("others", "approved by bob", ""),
        ),
        (
            {"reviews": (by("bob", "2026-10-20T00:00:00Z", state="COMMENTED"),)},
            False,
            True,
            ("others", "reviewed by bob", ""),
        ),
        ({"requested": ("ann",)}, False, True, ("others", "review requested from ann", "")),
        (
            {"reviews": (by("dep[bot]", "2026-10-20T00:00:00Z", state="COMMENTED"),)},
            False,
            True,
            ("unreviewed", "", ""),
        ),
    ],
)
def test_other_places_someone_elses_pull_request(
    fields: dict, asked: bool, followed: bool, placed: tuple[str, str, str] | None
) -> None:
    assert board.other(pr(author="ann", **fields), ME, asked, False, followed) == placed


def test_other_keeps_a_pull_request_the_viewer_reviewed_long_ago_and_names_the_last_verdict() -> None:
    assert board.other(pr(author="ann"), ME, False, True, False) == ("reviewed", "you reviewed", "")
    approved_then_replied = pr(
        author="ann",
        reviews=(
            by(ME, "2026-10-20T00:00:00Z", state="APPROVED"),
            by(ME, "2026-10-20T01:00:00Z", state="COMMENTED"),
        ),
    )
    assert board.other(approved_then_replied, ME, False, False, False) == ("reviewed", "you approved", "")


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
    sections = board.classify(ME, [mine, merged, asked, involved, reviewed, followed_found], ["acme/tools"])
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


def save(sections: dict, viewer: str = ME) -> None:
    board.save(board.Board(viewer=viewer, fetched=time.time(), repos=[], sections=sections))


def test_html_leaves_out_empty_sections_and_groups_and_names_other_authors() -> None:
    assert board.sections_html() == '<div class="empty">No pull requests yet.</div>'
    save(board.classify(ME, [[pr(1, ci="FAILURE")], [], [pr(2, author="ann", repo="acme/tools")], [], []], []))
    page = board.sections_html()
    assert re.findall(r"<h2>(.*?)</h2>", page) == ["Your pull requests", "Other people&#x27;s pull requests"]
    assert re.findall(r'<section class="sub" data-key="(\w+)">', page) == ["action", "reviewing"]
    assert page.count('class="chev ghost open"') == 2
    mine, theirs = re.findall(r'<a class="item".*?</a>', page)
    assert 'href="/acme/widgets/pull/1"' in mine and '<span class="why bad">CI failed</span>' in mine
    assert "<span>me</span>" not in mine
    assert 'href="/acme/tools/pull/2"' in theirs and "<span>ann</span>" in theirs


def test_recently_opened_lists_what_no_section_shows_and_escapes_titles() -> None:
    save(board.classify(ME, [[pr(1)], [], [], [], []], []))
    recent.note_opened(PullRef("acme", "widgets", 1), "Shown above", "open", ME)
    recent.note_opened(PullRef("acme", "widgets", 9), "a <b>", "merged", "ann")
    page = board.sections_html()
    opened = page[page.index('<section class="group opened">') :]
    assert '<h2>Recently opened<span class="count">1</span></h2>' in opened
    assert 'href="/acme/widgets/pull/9"' in opened and "a &lt;b&gt;" in opened
    assert "pull/1" not in opened


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
