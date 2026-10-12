import os
import time
from pathlib import Path

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
