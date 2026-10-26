from pathlib import Path

import pytest

import build
import editors
import languages
import render
import settings
from editors import Editor


def test_folder_size_sums_every_file_and_is_zero_for_a_missing_folder(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"12345")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b").write_bytes(b"123")
    assert settings.folder_size(tmp_path) == 8
    assert settings.folder_size(tmp_path / "missing") == 0


@pytest.mark.parametrize(
    "n, text", [(0, "0 B"), (999, "999 B"), (1000, "1.0 kB"), (527_900_000, "527.9 MB"), (12_300_000_000, "12.3 GB")]
)
def test_human_size(n: int, text: str) -> None:
    assert settings.human_size(n) == text


def test_the_settings_name_every_section_editor_language_pattern_the_cache_and_the_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build, "CACHE", tmp_path)
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "p.html").write_bytes(b"x" * 2500)
    monkeypatch.setattr(
        editors, "installed", lambda: [Editor("cursor", "Cursor"), Editor("github", "GitHub", local=False)]
    )
    monkeypatch.setattr(render, "app_info", lambda: {"commit": "abc1234", "dirty": True, "built": ""})
    html = settings.settings_html()
    for heading in ("Keyboard shortcuts", "File types", "Folded lines", "Hidden files", "Editor", "Cache"):
        assert f"<h2>{heading}</h2>" in html
    assert '<li data-editor="cursor" data-installed>Cursor<span class="hint">installed</span></li>' in html
    assert '<li data-editor="vscode">VS Code<span class="hint">not installed</span></li>' in html
    for language in languages.Language:
        pattern = languages.support(language).test_name_pattern
        if pattern is not None:
            assert f"<tr><td>{languages.name(language)}</td><td><code>{pattern}</code></td></tr>" in html
    assert "<tr><td>Rust</td>" not in html
    assert "<code>_test$|^conftest$</code>" in html
    assert "<code>tests</code>" in html and "<code>test_</code>" in html and "<code>/src/test/</code>" in html
    assert f"<code>{tmp_path}</code>" in html
    assert "<td><code>pages/</code></td><td>the built pages</td><td>2.5 kB</td>" in html
    assert "<td><code>repos/</code></td><td>the bare clones that build the pages</td><td>0 B</td>" in html
    assert "commit <code>abc1234</code> with uncommitted changes · " in html


def test_the_settings_omit_the_commit_when_git_cannot_tell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render, "app_info", lambda: {"commit": "", "dirty": False, "built": ""})
    assert "commit" not in settings.settings_html().split('<p class="about')[1]
