"""The body of the settings modal: how the app reads a pull request and what it keeps on this computer, as an HTML
fragment the shell shows. The static text is shell/settings.html; this module fills in what the code decides."""

from __future__ import annotations

import html
from pathlib import Path

import build
import classify
import editors
import file_types
import languages
import render

CACHE_FOLDERS = (
    ("repos", "the bare clones that build the pages"),
    ("checkouts", "the clones your editor opens"),
    ("pages", "the built pages"),
)


def folder_size(folder: Path) -> int:
    """The bytes of every file under the folder; 0 for a folder that does not exist. A file that goes away during
    the walk, as git's temporary files do while a clone runs, counts as 0."""
    total = 0
    for p in folder.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def human_size(n: int) -> str:
    """The size as the page shows a binary file's: bytes under a kilobyte, then KB, then MB."""
    if n < 1024:
        return f"{n} byte{'' if n == 1 else 's'}"
    kb = round(n / 1024, 1)
    return f"{kb:.1f} KB" if kb < 1024 else f"{n / 1024**2:.1f} MB"


def items(values: list[str]) -> str:
    return "".join(f"<li>{v}</li>" for v in values)


def code(value: str) -> str:
    return f"<code>{html.escape(value)}</code>"


def docs_rules() -> str:
    folders = " or ".join(code(d) for d in classify.DOCS_DIRS)
    suffixes = ", ".join(
        code(s) for t in file_types.FileType if file_types.support(t).docs for s in file_types.support(t).suffixes
    )
    return items([f"A path under {folders}.", f"A file with the suffix {suffixes}."])


def test_rules() -> str:
    top = ", ".join(code(d) for d in sorted(classify.TOP_LEVEL_TEST_DIRS))
    inner = ", ".join(code(d) for d in sorted(classify.TEST_DIRS))
    rows = "".join(
        f"<tr><td>{languages.name(language)}</td><td>{code(pattern)}</td></tr>"
        for language in languages.Language
        if (pattern := languages.support(language).test_name_pattern) is not None
    )
    return items(
        [
            f"A file in a top-level folder named {top}.",
            f"A folder named {inner}, or ending in {code(classify.TEST_DIR_SUFFIX)}, anywhere in the path.",
            f"{code(classify.TEST_PATH_PART)} in the path.",
            f"A name starting with {code(classify.TEST_NAME_PREFIX)}.",
            "A name without its suffix that matches the pattern of the file's language:"
            f'<table class="patterns">{rows}</table>',
        ]
    )


def editor_list() -> str:
    installed = {e.id for e in editors.installed()}
    return "".join(
        f'<li>{html.escape(e.name)}<span class="hint">{"installed" if e.id in installed else "not installed"}</span></li>'
        for e in editors.known()
    )


def cache_rows() -> str:
    return "".join(
        f"<tr><td>{code(name + '/')}</td><td>{what}</td><td>{human_size(folder_size(build.CACHE / name))}</td></tr>"
        for name, what in CACHE_FOLDERS
    )


def commit_line() -> str:
    info = render.app_info()
    if not info["commit"]:
        return ""
    return f"commit {code(info['commit'])}{' with uncommitted changes' if info['dirty'] else ''} · "


def settings_html() -> str:
    return render.fill(
        (render.SHELL_DIR / "settings.html").read_text(),
        DOCS_RULES=docs_rules(),
        TEST_RULES=test_rules(),
        EDITORS=editor_list(),
        CACHE=html.escape(str(build.CACHE)),
        CACHE_ROWS=cache_rows(),
        COMMIT=commit_line(),
    )
