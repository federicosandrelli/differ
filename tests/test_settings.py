import re
from pathlib import Path

import pytest

import build
import classify
import editors
import file_types
import languages
import render
import settings


def test_folder_size_sums_every_file_and_is_zero_for_a_missing_folder(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"12345")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b").write_bytes(b"123")
    assert settings.folder_size(tmp_path) == 8
    assert settings.folder_size(tmp_path / "missing") == 0


@pytest.mark.parametrize(
    "n, text",
    [
        (0, "0 bytes"),
        (1, "1 byte"),
        (1023, "1023 bytes"),
        (1024, "1.0 KB"),
        (1_048_550, "1.0 MB"),
        (527_900_000, "503.4 MB"),
    ],
)
def test_human_size(n: int, text: str) -> None:
    assert settings.human_size(n) == text


def test_the_settings_show_the_editors_the_patterns_the_generated_rules_the_cache_sizes_and_the_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build, "CACHE", tmp_path)
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "p.html").write_bytes(b"x" * 2500)
    monkeypatch.setattr(render, "app_info", lambda: {"commit": "abc1234", "dirty": True, "built": ""})
    html = settings.settings_html()
    for editor in editors.known():
        assert editor.name in html
    for language in languages.Language:
        pattern = languages.support(language).test_name_pattern
        shown = re.search(rf"<td>{re.escape(languages.name(language))}</td><td><code>([^<]*)</code>", html)
        assert (shown and shown[1]) == (pattern or None), language
    for literal in (
        *sorted(classify.GENERATED_NAMES),
        *file_types.support(file_types.FileType.PYTHON).generated_suffixes,
    ):
        assert f"<code>{literal}</code>" in html
    for literal in (
        classify.TEST_NAME_PREFIX,
        classify.TEST_PATH_PART,
        *file_types.support(file_types.FileType.MARKDOWN).suffixes,
    ):
        assert f"<code>{literal}</code>" in html
    assert str(tmp_path) in html
    assert re.search(r"<code>pages/</code></td><td>[^<]*</td><td>2.4 KB<", html)
    assert re.search(r"<code>repos/</code></td><td>[^<]*</td><td>0 bytes<", html)
    assert f'href="{settings.REPOSITORY}/commit/abc1234"' in html and "with uncommitted changes" in html


def test_the_settings_omit_the_commit_when_git_cannot_tell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render, "app_info", lambda: {"commit": "", "dirty": False, "built": ""})
    assert "commit" not in settings.settings_html().split('<p class="about')[1]
