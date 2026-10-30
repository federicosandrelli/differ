"""The body of the settings modal: the followed repositories, how the app reads a pull request and what it keeps on
this computer, as an HTML fragment the shell shows. The static text is shell/settings.html; this module fills in
what the code decides and what the reader saved."""

from __future__ import annotations

import html
import json
from pathlib import Path

import build
import classify
import editors
import file_types
import followed
import languages
import render

REPOSITORY = "https://github.com/federicosandrelli/differ"
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
    def suffixes(docs: file_types.Docs) -> str:
        types = (file_types.support(t) for t in file_types.FileType)
        return ", ".join(code(suffix) for t in types if t.docs is docs for suffix in t.suffixes)

    folders = ", ".join(code(d) for d in sorted(classify.DOCS_DIRS))
    return items(
        [
            f"The suffix {suffixes(file_types.Docs.ALWAYS)}.",
            f"The suffix {suffixes(file_types.Docs.IN_DOCS_FOLDER)}, and a folder named {folders} in any case, or ending in "
            f"{code(classify.DOCS_DIR_SUFFIX)}, anywhere in the path.",
        ]
    )


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
            f"A top-level folder named {top}.",
            f"A folder named {inner}, or ending in {code(classify.TEST_DIR_SUFFIX)}, anywhere in the path.",
            f"{code(classify.TEST_PATH_PART)} in the path.",
            f"A name starting with {code(classify.TEST_NAME_PREFIX)}.",
            "A name without its suffix that matches the pattern of the file's language:"
            f'<table class="patterns">{rows}</table>',
        ]
    )


def generated_rules() -> str:
    names = ", ".join(code(n) for n in sorted(classify.GENERATED_NAMES))
    suffixes = sorted({s for t in file_types.FileType for s in file_types.support(t).generated_suffixes})
    return items(
        [
            f"The name {names}.",
            f"A name ending in {', '.join(code(s) for s in suffixes)}.",
            f"The {code('linguist-generated')} mark in {code('.gitattributes')}.",
            'A line near the top that says a tool wrote it, such as "DO NOT EDIT" or "@generated".',
        ]
    )


def editor_names() -> str:
    return ", ".join(html.escape(e.name) for e in editors.known())


def cache_rows() -> str:
    return "".join(
        f"<tr><td>{code(name + '/')}</td><td>{what}</td><td>{human_size(folder_size(build.CACHE / name))}</td></tr>"
        for name, what in CACHE_FOLDERS
    )


def commit_line() -> str:
    info = render.app_info()
    if not info["commit"]:
        return ""
    link = f'<a href="{REPOSITORY}/commit/{info["commit"]}" target="_blank">{code(info["commit"])}</a>'
    return f"commit {link}{' with uncommitted changes' if info['dirty'] else ''} · "


def settings_html() -> str:
    return render.fill(
        (render.SHELL_DIR / "settings.html").read_text(),
        FOLLOWED=html.escape(json.dumps(followed.load())),
        DOCS_RULES=docs_rules(),
        TEST_RULES=test_rules(),
        GENERATED_RULES=generated_rules(),
        EDITORS=editor_names(),
        CACHE=html.escape(str(build.CACHE)),
        CACHE_ROWS=cache_rows(),
        COMMIT=commit_line(),
    )
