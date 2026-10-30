#!/usr/bin/env python3
"""Render a classified diff plus PR metadata as one HTML page, and the theme every document shares.

Usage: render.py --splash   writes the desktop app's first page, web/index.html

The page's script is page/vendor/highlight.min.js, then shell/frame.js, then page/*.js in name order (hence the
numeric prefixes), and its stylesheet page/page.css. Both are inlined, so a page works from disk as well as from
the server, and never loads anything from the network. The theme (shell/theme.js and the palettes here) and
the tooltip (shell/tooltip.js) go into the head of every document the server sends.
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import commands
from github import Status

ROOT = Path(__file__).resolve().parent
PAGE_DIR = ROOT / "page"
VENDOR_DIR = PAGE_DIR / "vendor"
SHELL_DIR = ROOT / "shell"


def fill(template: str, **slots: str) -> str:
    """Fill a template's __NAME__ slots in one pass, so a value holding a slot's name is left as it is."""
    return re.sub("|".join(f"__{name}__" for name in slots), lambda m: slots[m.group(0)[2:-2]], template)


def mix(color: str, pct: int) -> str:
    return f"color-mix(in srgb, {color} {pct}%, transparent)"


def palette(
    *,
    canvas,
    panel,
    inset,
    text,
    text_secondary,
    text_muted,
    border,
    accent,
    accent_bg,
    tree_active_bg,
    tree_active_fg,
    add,
    dele,
    mod,
    ren,
    search_hit_bg,
    search_hit_fg,
    syntax,
):
    """The colour tokens of one theme, from its raw colours. Every tint a page needs is mixed here, so the
    stylesheets only ever name a token."""
    return dict(
        bg_canvas=canvas,
        bg_panel=panel,
        bg_inset=inset,
        bg_hover=inset,
        bg_hover_strong=border,
        text=text,
        text_secondary=text_secondary,
        text_muted=text_muted,
        text_on_fill=canvas,
        border=border,
        accent=accent,
        accent_hover=f"color-mix(in srgb, {accent} 85%, black)",
        accent_bg=accent_bg,
        tree_active_bg=tree_active_bg,
        tree_active_fg=tree_active_fg,
        row_target_bg=mix(accent, 22),
        row_selected_bg=mix(accent, 14),
        add_bg=mix(add, 16),
        add_fg=add,
        add_num=mix(add, 28),
        add_word=mix(add, 38),
        del_bg=mix(dele, 16),
        del_fg=dele,
        del_num=mix(dele, 28),
        del_word=mix(dele, 38),
        mod_bg=mix(mod, 22),
        mod_fg=mod,
        ren_bg=mix(ren, 22),
        ren_fg=ren,
        comment_pending_bg=mix(mod, 7),
        search_hit_bg=search_hit_bg,
        search_hit_fg=search_hit_fg,
        backdrop="rgb(0 0 0 / 45%)",
        **{f"syntax_{k}": v for k, v in syntax.items()},
    )


# Light takes its greys, git colours and font stack from the Claude desktop app's design tokens (its "cds" scale,
# text-git-added / text-git-removed, and the 20% mix it uses for diff backgrounds). Dark takes the colours of
# Flexoki dark (stephango.com/flexoki), and Gruvbox those of the Gruvbox dark palette (github.com/morhetz/gruvbox).
# Claude takes the desktop app's dark greys, moved up one step so that the canvas sits darker than the panels and the
# cards stand out; its border is one step lighter than the app's.
# In dark and Gruvbox, accent_bg and tree_active_bg mix the palette's blue over its canvas, at 16% and 34%. Claude keeps
# the app's own.
PALETTES = {
    "light": palette(
        canvas="#ffffff",
        panel="#f9f9f7",
        inset="#f0efec",
        text="#20201f",
        text_secondary="#6d6b67",
        text_muted="#898781",
        border="#e1e0d9",
        accent="#2f6feb",
        accent_bg="#e8f0fe",
        tree_active_bg="#cddcfb",
        tree_active_fg="#2f6feb",
        add="#1e9e3c",
        dele="#cd2054",
        mod="#9a5b00",
        ren="#6d3fc7",
        search_hit_bg="#ffe066",
        search_hit_fg="#20201f",
        syntax=dict(
            kw="#c2410c", str_="#3f7d2a", cmt="#898781", num="#9a6a10", fn="#20201f", cls="#8a5a00", builtin="#c2410c"
        ),
    ),
    "gruvbox": palette(
        canvas="#282828",
        panel="#1d2021",
        inset="#32302f",
        text="#ebdbb2",
        text_secondary="#bdae93",
        text_muted="#928374",
        border="#3c3836",
        accent="#83a598",
        accent_bg="#373c3a",
        tree_active_bg="#47524e",
        tree_active_fg="#83a598",
        add="#b8bb26",
        dele="#fb4934",
        mod="#fabd2f",
        ren="#d3869b",
        search_hit_bg="#fabd2f",
        search_hit_fg="#282828",
        syntax=dict(
            kw="#fb4934", str_="#b8bb26", cmt="#928374", num="#d3869b", fn="#ebdbb2", cls="#fabd2f", builtin="#fe8019"
        ),
    ),
    "claude": palette(
        canvas="#151515",
        panel="#20201f",
        inset="#2c2c2a",
        text="#d2d1c7",
        text_secondary="#a5a49a",
        text_muted="#898781",
        border="#363634",
        accent="#6ea0ff",
        accent_bg="#1a2742",
        tree_active_bg="#2a4474",
        tree_active_fg="#6ea0ff",
        add="#32d74b",
        dele="#ff2c56",
        mod="#f0b45a",
        ren="#c4a6ff",
        search_hit_bg="#ffe066",
        search_hit_fg="#1c1f26",
        syntax=dict(
            kw="#f29b7a", str_="#9fcf8a", cmt="#898781", num="#e5c07b", fn="#d2d1c7", cls="#e5c07b", builtin="#f29b7a"
        ),
    ),
    "dark": palette(
        canvas="#100f0f",
        panel="#1c1b1a",
        inset="#282726",
        text="#cecdc3",
        text_secondary="#b7b5ac",
        text_muted="#878580",
        border="#343331",
        accent="#4385be",
        accent_bg="#18222b",
        tree_active_bg="#21374a",
        tree_active_fg="#4385be",
        add="#879a39",
        dele="#d14d41",
        mod="#d0a215",
        ren="#8b7ec8",
        search_hit_bg="#d0a215",
        search_hit_fg="#100f0f",
        syntax=dict(
            kw="#879a39", str_="#3aa99f", cmt="#878580", num="#8b7ec8", fn="#da702c", cls="#d0a215", builtin="#ce5d97"
        ),
    ),
}


def vars_block(p: dict) -> str:
    return "; ".join(f"--{k.rstrip('_').replace('_', '-')}: {v}" for k, v in p.items()) + ";"


def theme_css() -> str:
    light, gruvbox, claude, dark = (vars_block(PALETTES[k]) for k in ("light", "gruvbox", "claude", "dark"))
    return f"""
:root {{ {claude} }}
:root[data-theme="light"] {{ {light} }}
:root[data-theme="gruvbox"] {{ {gruvbox} }}
:root[data-theme="dark"] {{ {dark} }}
"""


SPLASH = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>differ</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; padding-top: 28px; height: 100vh; display: grid; place-items: center; font: 14px -apple-system, BlinkMacSystemFont, sans-serif; color: __TEXT__; background: __BG__; }
</style>
</head>
<body>Starting the review server…</body>
</html>
"""


def splash_html() -> str:
    """The desktop app's first page, shown until the server answers. It loads before any server document, so
    its two colours come from the Claude palette at write time: `render.py --splash` writes it to web/index.html."""
    claude = PALETTES["claude"]
    return fill(SPLASH, TEXT=claude["text_secondary"], BG=claude["bg_canvas"])


# The icon of each pull request status, as the inner paths of a 24×24 stroked SVG, and the palette colour it takes.
PR_ICONS: dict[Status, tuple[str, str]] = {
    "open": (
        '<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7M6 9v12"/>',
        "add-fg",
    ),
    "draft": (
        '<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M18 6V5M18 11v-1M6 9v12"/>',
        "text-muted",
    ),
    "merged": (
        '<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M6 21V9a9 9 0 0 0 9 9"/>',
        "ren-fg",
    ),
    "closed": (
        '<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M6 9v12M21 3l-6 6M21 9l-6-6M18 11.5V15"/>',
        "del-fg",
    ),
}


def pr_icon(status: str) -> str:
    """The status icon as HTML, coloured by the .pr-icon rules of the theme head. Empty for an unknown status."""
    if status not in PR_ICONS:
        return ""
    label = status.capitalize()
    return f'<span class="pr-icon st-{status}" role="img" aria-label="{label}" title="{label}"></span>'


ReasonKind = Literal[
    "x-circle", "clock", "check-circle", "warning", "comment", "eye", "pencil", "person", "person-plus", "check"
]
# The icon of each kind of reason why a pull request sits in its section, as the inner paths of a 24×24 stroked SVG.
REASON_ICONS: dict[ReasonKind, str] = {
    "x-circle": '<circle cx="12" cy="12" r="9"/><path d="m15 9-6 6M9 9l6 6"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    "warning": '<path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4M12 17h.01"/>',
    "comment": '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
    "eye": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "pencil": '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    "person": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "person-plus": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/><path d="M19 4v4M21 6h-4"/>',
    "check": '<path d="m5 12 5 5 9-10"/>',
}


def reason_icon(kind: ReasonKind) -> str:
    """The icon of a kind of reason as HTML, in the colour of the text around it."""
    return f'<span class="reason-icon k-{kind}" aria-hidden="true"></span>'


def icon_url(paths: str) -> str:
    """A 24×24 stroked SVG of the paths, as a data URL."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="black" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round">{paths}</svg>'
    )
    return "data:image/svg+xml," + urllib.parse.quote(svg)


def icon_css() -> str:
    """Draw each status icon and each reason icon as a mask over its colour, so any document can show one with a
    single span."""
    mask = (
        "background: currentColor; -webkit-mask: var(--icon) center / contain no-repeat; "
        "mask: var(--icon) center / contain no-repeat; }"
    )
    rules = [
        ".pr-icon { display: inline-block; flex: none; width: 14px; height: 14px; vertical-align: -2px; " + mask,
        ".reason-icon { display: inline-block; flex: none; width: 12px; height: 12px; " + mask,
    ]
    for status, (paths, color) in PR_ICONS.items():
        rules.append(f'.pr-icon.st-{status} {{ --icon: url("{icon_url(paths)}"); color: var(--{color}); }}')
    for kind, paths in REASON_ICONS.items():
        rules.append(f'.reason-icon.k-{kind} {{ --icon: url("{icon_url(paths)}"); }}')
    return "\n".join(rules)


# A filled circle with a cross cut out of it, the shape of WebKit's own clear button.
SEARCH_CLEAR_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><mask id="m"><rect width="24" height="24" '
    'fill="white"/><path d="M15 9l-6 6M9 9l6 6" stroke="black" stroke-width="2" stroke-linecap="round"/></mask>'
    '<circle cx="12" cy="12" r="10" mask="url(#m)"/></svg>'
)


def search_clear_css() -> str:
    """Draw the clear button of every search field as a mask over a text token. WebKit's own button is grey
    on any theme, so it almost disappears on a dark background."""
    url = "data:image/svg+xml," + urllib.parse.quote(SEARCH_CLEAR_ICON)
    return (
        'input[type="search"]::-webkit-search-cancel-button { -webkit-appearance: none; appearance: none; '
        "width: 14px; height: 14px; margin: 0 -3px 0 6px; cursor: pointer; background: var(--text-muted); "
        f'-webkit-mask: url("{url}") center / contain no-repeat; mask: url("{url}") center / contain no-repeat; }}\n'
        'input[type="search"]::-webkit-search-cancel-button:hover { background: var(--text-secondary); }'
    )


def theme_head() -> str:
    """The theme variables, the status and reason icons, the base stylesheet, the search clear button and the scripts that
    apply the saved theme and draw the tooltip, for the head of a document so it never flashes the default theme.
    The base stylesheet comes after the icons, so a component can recolour the icon it holds."""
    css = "\n".join([theme_css(), icon_css(), (SHELL_DIR / "base.css").read_text(), search_clear_css()])
    js = "\n".join((SHELL_DIR / name).read_text() for name in ("theme.js", "tooltip.js"))
    return f"<style>{css}</style>\n<script>{js}</script>"


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<meta name="pr-status" content="__STATUS__">
<meta name="pr-author" content="__AUTHOR__">
__THEME__
<style>
__CSS__</style>
</head>
<body>
<div id="side"></div>
<div id="main"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
__JS__</script>
</body>
</html>
"""


TITLE = re.compile(rb"<title>#\d+ (.*?)</title>", re.S)
STATUS = re.compile(rb'<meta name="pr-status" content="(\w*)">')
AUTHOR = re.compile(rb'<meta name="pr-author" content="([^"]*)">')


def read_head(page: bytes) -> tuple[str | None, str, str]:
    """The pull request title, status and author a page from render_page names in its head: no title, an empty
    status or an empty author, when it names none."""
    head = page[:4096]
    title, status, author = TITLE.search(head), STATUS.search(head), AUTHOR.search(head)
    return (
        html.unescape(title[1].decode(errors="replace")) if title else None,
        status[1].decode() if status else "",
        html.unescape(author[1].decode(errors="replace")) if author else "",
    )


def page_scripts() -> list[Path]:
    """The scripts a review page inlines, in order: highlight.js, the page side of the shell, then page/*.js in name
    order."""
    return [VENDOR_DIR / "highlight.min.js", SHELL_DIR / "frame.js", *sorted(PAGE_DIR.glob("*.js"))]


def sources() -> list[Path]:
    """Every file whose content ends up in a page."""
    return [
        ROOT / "render.py",
        PAGE_DIR / "page.css",
        SHELL_DIR / "base.css",
        SHELL_DIR / "theme.js",
        SHELL_DIR / "tooltip.js",
        *page_scripts(),
    ]


def app_info() -> dict:
    """The commit of this repository the app runs from, and whether it has uncommitted changes; unknown when git
    can't tell."""

    def git(*args: str) -> str:
        try:
            r = commands.call(["git", "-C", str(ROOT), *args], timeout=10)
        except commands.CommandError:
            return ""
        return "" if r.returncode else r.stdout.decode(errors="replace").strip()

    own = git("rev-parse", "--show-toplevel") == str(ROOT)
    return {
        "commit": git("rev-parse", "--short", "HEAD") if own else "",
        "dirty": own and bool(git("--no-optional-locks", "status", "--porcelain")),
        "built": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def render_page(classified: dict, pr: dict) -> str:
    """The review page of a pull request: its classified diff, and the pull request as build.py describes it."""
    data = {**classified, "pr": pr, "app": app_info()}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = f"#{pr['number']} {pr['title']}"
    js = "\n".join(f"// ---- {path.relative_to(ROOT)}\n{path.read_text()}" for path in page_scripts())
    return fill(
        TEMPLATE,
        TITLE=html.escape(title),
        STATUS=pr["status"],
        AUTHOR=html.escape(pr["author"]),
        THEME=theme_head(),
        CSS=(PAGE_DIR / "page.css").read_text(),
        JS=js,
        DATA=payload,
    )


def main() -> None:
    if sys.argv[1:] != ["--splash"]:
        sys.exit(__doc__)
    (ROOT / "web" / "index.html").write_text(splash_html())


if __name__ == "__main__":
    main()
