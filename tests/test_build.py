import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

import build
import commands
from github import PullRef

REF = PullRef("owner", "repo", 7)


def test_page_path_uses_the_cache_and_a_short_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "CACHE", tmp_path)
    assert build.page_path(REF, "0123456789abcdef") == tmp_path / "pages/owner/repo/7/0123456789ab.html"


def test_page_current_and_latest_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "CACHE", tmp_path)
    assert build.latest_page(REF) is None

    stale = build.page_path(REF, "a" * 12)
    stale.parent.mkdir(parents=True)
    stale.write_text("old")
    os.utime(stale, (0, 0))
    assert build.page_current(stale) is False
    assert build.latest_page(REF) is None

    fresh = build.page_path(REF, "b" * 12)
    fresh.write_text("new")
    future = time.time() + 60
    os.utime(fresh, (future, future))
    assert build.page_current(fresh) is True
    assert build.latest_page(REF) == fresh

    build.forget_pages(REF)
    assert not stale.parent.exists()


def test_a_missing_git_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise FileNotFoundError(2, "No such file or directory", args[0])

    monkeypatch.setattr(commands.subprocess, "run", missing)
    with pytest.raises(commands.CommandError, match="git is not installed or not on PATH"):
        build.has_commit(Path("/nowhere"), "abc")


def test_every_subprocess_gets_a_timeout_and_clones_get_a_longer_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build, "CACHE", tmp_path)
    timeouts: dict[str, float] = {}

    def record(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        verb = next(a for a in args if a in ("clone", "cat-file", "fetch"))
        timeouts[verb] = kwargs["timeout"]
        return subprocess.CompletedProcess(args, 1 if verb == "cat-file" else 0, b"", b"")

    monkeypatch.setattr(commands.subprocess, "run", record)
    build.ensure_repo(PullRef("o", "r", 7), lambda line: None)
    build.fetch(tmp_path / "r.git", 7, "base", "head", lambda line: None)
    assert timeouts == {"clone": 600, "cat-file": 120, "fetch": 600}


def test_prune_keeps_the_two_newest_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "CACHE", tmp_path)
    pages = []
    for i, head in enumerate("abc"):
        page = build.page_path(REF, head * 12)
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(head)
        os.utime(page, (1000 + i, 1000 + i))
        pages.append(page)
    other_page = build.page_path(PullRef("owner", "repo", 8), "d" * 12)
    other_page.parent.mkdir(parents=True)
    other_page.write_text("d")
    os.utime(other_page, (0, 0))

    build.prune(REF)

    assert [p.exists() for p in pages] == [False, True, True]
    assert other_page.exists()


def test_mtime_of_a_missing_file_is_zero(tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text("x")
    os.utime(page, (1234, 1234))
    assert build.mtime(page) == 1234
    assert build.mtime(tmp_path / "gone.html") == 0.0


def test_prune_without_pages_or_work_is_a_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "CACHE", tmp_path)
    build.prune(REF)
    assert not (tmp_path / "pages").exists()


def test_prune_all_deletes_old_pages_but_keeps_repos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "CACHE", tmp_path)
    mirror = tmp_path / "repos" / "owner" / "repo.git"
    mirror.mkdir(parents=True)
    (mirror / "HEAD").write_text("ref: refs/heads/main\n")
    os.utime(mirror / "HEAD", (0, 0))
    now = time.time()
    old = build.page_path(REF, "a" * 12)
    old.parent.mkdir(parents=True)
    old.write_text("old")
    os.utime(old, (now - 31 * 86400, now - 31 * 86400))
    recent = build.page_path(REF, "b" * 12)
    recent.write_text("recent")
    os.utime(recent, (now - 29 * 86400, now - 29 * 86400))
    older_elsewhere = build.page_path(PullRef("other", "repo", 1), "c" * 12)
    older_elsewhere.parent.mkdir(parents=True)
    older_elsewhere.write_text("old")
    os.utime(older_elsewhere, (0, 0))

    build.prune_all()

    assert (mirror / "HEAD").exists()
    assert not old.exists()
    assert recent.exists()
    assert not older_elsewhere.exists()


def test_prune_all_on_an_empty_cache_is_a_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "CACHE", tmp_path / "missing")
    build.prune_all()
    assert not (tmp_path / "missing").exists()


def test_a_page_is_stale_once_any_module_that_shapes_it_changes() -> None:
    assert {"build.py", "classify.py", "github.py", "render.py"} <= {p.name for p in build.sources()}


class AtOnce:
    """A stand-in for threading.Thread that runs its target when started."""

    def __init__(self, target: Any, daemon: bool) -> None:
        self.target = target

    def start(self) -> None:
        self.target()


@pytest.fixture
def pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A cache with one current page for REF built from head "a", a GitHub whose head is in the dict, and the
    builds started, in the dict too."""
    monkeypatch.setattr(build, "CACHE", tmp_path)
    monkeypatch.setattr(build, "jobs", {})
    monkeypatch.setattr(build, "head_checked", {})
    monkeypatch.setattr(build, "heads", {})
    monkeypatch.setattr(build.threading, "Thread", AtOnce)
    state: dict[str, Any] = {"head": "a" * 40, "head_reads": 0, "started": []}

    def head_sha(ref: PullRef) -> str:
        state["head_reads"] += 1
        return state["head"]

    monkeypatch.setattr(build.github, "head_sha", head_sha)
    monkeypatch.setattr(build, "build", lambda ref, log: state["started"].append(ref))
    page = build.page_path(REF, "a" * 40)
    page.parent.mkdir(parents=True)
    page.write_text("page")
    future = time.time() + 60
    os.utime(page, (future, future))
    return state


def test_serve_reads_the_head_at_most_once_in_head_ttl(pages: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    assert build.serve(REF) == build.page_path(REF, "a" * 40)
    assert build.serve(REF) is not None
    assert pages["head_reads"] == 1 and pages["started"] == []
    monkeypatch.setitem(build.head_checked, REF, time.time() - build.HEAD_TTL)
    pages["head"] = "b" * 40
    assert build.serve(REF) == build.page_path(REF, "a" * 40)
    assert pages["head_reads"] == 2 and pages["started"] == [REF]


def test_serve_starts_the_first_build_of_a_pull_request(pages: dict[str, Any]) -> None:
    other = PullRef("owner", "repo", 8)
    assert build.serve(other) is None
    assert pages["started"] == [other] and pages["head_reads"] == 0


def test_a_build_already_running_is_not_started_again(pages: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        build.threading, "Thread", lambda target, daemon: type("Idle", (), {"start": lambda self: None})()
    )
    first = build.start(REF)
    assert build.start(REF) is first and build.job_of(REF) is first and first.state == "building"


def test_refresh_rebuilds_only_when_the_head_moved(pages: dict[str, Any]) -> None:
    build.refresh(REF)
    assert pages["started"] == [] and build.latest_page(REF) is not None
    pages["head"] = "b" * 40
    build.refresh(REF)
    assert pages["started"] == [REF] and build.latest_page(REF) is None


def test_refresh_without_a_current_page_builds_without_reading_the_head(
    pages: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(ref: PullRef) -> str:
        raise commands.CommandError("`gh pr view 8` failed: no pull requests found")

    monkeypatch.setattr(build.github, "head_sha", fail)
    other = PullRef("owner", "repo", 8)
    build.refresh(other)
    assert pages["started"] == [other]


def test_serve_returns_the_page_of_a_head_moved_back_to_an_older_commit(
    pages: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    old, new = build.page_path(REF, "a" * 40), build.page_path(REF, "b" * 40)

    def serve_after_head_ttl() -> Path | None:
        monkeypatch.setitem(build.head_checked, REF, time.time() - build.HEAD_TTL)
        return build.serve(REF)

    pages["head"] = "b" * 40
    serve_after_head_ttl()
    assert pages["started"] == [REF]
    new.write_text("new page")
    later = time.time() + 120
    os.utime(new, (later, later))
    assert build.serve(REF) == new

    pages["head"] = "a" * 40
    serve_after_head_ttl()
    assert build.serve(REF) == old
    build.refresh(REF)
    assert build.serve(REF) == old and pages["started"] == [REF]
