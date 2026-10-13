import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

import build


@pytest.mark.parametrize(
    "text",
    [
        "https://github.com/Owner/Repo/pull/12",
        "https://github.com/owner/repo/pull/12/files",
        "owner/repo#12",
        "  Owner/Repo#12\n",
        "see github.com/owner/repo/pull/12 please",
    ],
)
def test_parse_ref_accepts_urls_and_short_refs_lowercased(text: str) -> None:
    assert build.parse_ref(text) == ("owner", "repo", 12)


@pytest.mark.parametrize("text", ["", "owner/repo", "https://github.com/owner/repo", "12"])
def test_parse_ref_rejects_other_text(text: str) -> None:
    with pytest.raises(build.BuildError, match="Not a pull request reference"):
        build.parse_ref(text)


@pytest.mark.parametrize(
    "text",
    [
        "/../../pull/1",
        "/./x/pull/1",
        "/-x/y/pull/1",
        "a/..#1",
        "a/.#1",
        "a/-b#1",
        "a.b/c#1",
        "https://github.com/-x/y/pull/1",
    ],
)
def test_parse_ref_rejects_names_that_are_not_github_names(text: str) -> None:
    with pytest.raises(build.BuildError, match="Not a pull request reference"):
        build.parse_ref(text)


@pytest.mark.parametrize(
    ("text", "ref"),
    [
        ("octo-org/my.repo#7", ("octo-org", "my.repo", 7)),
        ("a/.b#1", ("a", ".b", 1)),
        ("a/..b#1", ("a", "..b", 1)),
        ("a/b.#1", ("a", "b.", 1)),
        ("a/b-#1", ("a", "b-", 1)),
    ],
)
def test_parse_ref_accepts_github_names(text: str, ref: tuple[str, str, int]) -> None:
    assert build.parse_ref(text) == ref


def test_changed_paths_reads_name_status() -> None:
    names = "M\tkept.py\nA\tnew.py\nD\told.py\nR095\tbefore.py\tafter.py\nC100\tsrc.py\tcopy.py\n\n"
    assert build.changed_paths(names) == [
        ("kept.py", "kept.py"),
        (None, "new.py"),
        ("old.py", None),
        ("before.py", "after.py"),
        ("src.py", "copy.py"),
    ]


def test_page_path_uses_the_cache_and_a_short_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "CACHE", tmp_path)
    assert build.page_path("owner", "repo", 7, "0123456789abcdef") == tmp_path / "pages/owner/repo/7/0123456789ab.html"


def test_page_current_and_latest_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "CACHE", tmp_path)
    assert build.latest_page("owner", "repo", 7) is None

    stale = build.page_path("owner", "repo", 7, "a" * 12)
    stale.parent.mkdir(parents=True)
    stale.write_text("old")
    os.utime(stale, (0, 0))
    assert build.page_current(stale) is False
    assert build.latest_page("owner", "repo", 7) is None

    fresh = build.page_path("owner", "repo", 7, "b" * 12)
    fresh.write_text("new")
    future = time.time() + 60
    os.utime(fresh, (future, future))
    assert build.page_current(fresh) is True
    assert build.latest_page("owner", "repo", 7) == fresh

    build.forget_pages("owner", "repo", 7)
    assert not stale.parent.exists()


def test_run_names_the_command_and_limit_when_it_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(build.subprocess, "run", hang)
    with pytest.raises(build.BuildError, match=r"`gh pr view 7` did not finish within 5 seconds"):
        build.run(["gh", "pr", "view", "7", "--repo", "o/r"], timeout=5)


def test_run_explains_a_missing_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise FileNotFoundError(2, "No such file or directory", args[0])

    monkeypatch.setattr(build.subprocess, "run", missing)
    with pytest.raises(build.BuildError, match=r"The GitHub CLI \(gh\) is not installed or not on PATH"):
        build.run(["gh", "pr", "view", "7"])
    with pytest.raises(build.BuildError, match="git is not installed or not on PATH"):
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

    monkeypatch.setattr(build.subprocess, "run", record)
    build.ensure_repo("o", "r", lambda line: None)
    build.fetch(tmp_path / "r.git", 7, "base", "head", lambda line: None)
    assert timeouts == {"clone": 600, "cat-file": 120, "fetch": 600}


def test_prune_deletes_the_work_directory_and_keeps_the_two_newest_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build, "CACHE", tmp_path)
    work = tmp_path / "work" / "owner" / "repo" / "7" / ("a" * 12)
    work.mkdir(parents=True)
    (work / "classified.json").write_text("{}")
    other_work = tmp_path / "work" / "owner" / "repo" / "8" / ("a" * 12)
    other_work.mkdir(parents=True)
    pages = []
    for i, head in enumerate("abc"):
        page = build.page_path("owner", "repo", 7, head * 12)
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(head)
        os.utime(page, (1000 + i, 1000 + i))
        pages.append(page)
    other_page = build.page_path("owner", "repo", 8, "d" * 12)
    other_page.parent.mkdir(parents=True)
    other_page.write_text("d")
    os.utime(other_page, (0, 0))

    build.prune("owner", "repo", 7)

    assert not work.parent.exists()
    assert other_work.exists()
    assert [p.exists() for p in pages] == [False, True, True]
    assert other_page.exists()


def test_prune_without_pages_or_work_is_a_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "CACHE", tmp_path)
    build.prune("owner", "repo", 7)
    assert not (tmp_path / "pages").exists()


def test_prune_all_deletes_old_pages_and_every_work_directory_but_keeps_repos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build, "CACHE", tmp_path)
    work = tmp_path / "work" / "owner" / "repo" / "7" / ("a" * 12)
    work.mkdir(parents=True)
    (work / "diff.patch").write_text("")
    mirror = tmp_path / "repos" / "owner" / "repo.git"
    mirror.mkdir(parents=True)
    (mirror / "HEAD").write_text("ref: refs/heads/main\n")
    os.utime(mirror / "HEAD", (0, 0))
    now = time.time()
    old = build.page_path("owner", "repo", 7, "a" * 12)
    old.parent.mkdir(parents=True)
    old.write_text("old")
    os.utime(old, (now - 31 * 86400, now - 31 * 86400))
    recent = build.page_path("owner", "repo", 7, "b" * 12)
    recent.write_text("recent")
    os.utime(recent, (now - 29 * 86400, now - 29 * 86400))
    older_elsewhere = build.page_path("other", "repo", 1, "c" * 12)
    older_elsewhere.parent.mkdir(parents=True)
    older_elsewhere.write_text("old")
    os.utime(older_elsewhere, (0, 0))

    build.prune_all()

    assert not (tmp_path / "work").exists()
    assert (mirror / "HEAD").exists()
    assert not old.exists()
    assert recent.exists()
    assert not older_elsewhere.exists()


def test_prune_all_on_an_empty_cache_is_a_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build, "CACHE", tmp_path / "missing")
    build.prune_all()
    assert not (tmp_path / "missing").exists()
