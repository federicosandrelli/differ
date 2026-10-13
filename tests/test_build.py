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
