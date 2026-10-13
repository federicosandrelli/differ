#!/usr/bin/env python3
"""Render a classified diff plus PR metadata as one HTML page.

Usage: render.py <classified.json> <annotations.json> <out.html>
       render.py --splash

The page's script is page/vendor/highlight.min.js, then shell/frame.js, then page/*.js in name order (hence the
numeric prefixes), and its stylesheet page/page.css. Both are inlined, so a page works from disk as well as from
the server, and never loads anything from the network. The theme (shell/theme.js and the palettes here) goes
into the head of every document the server sends.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

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
        text_on_accent="#fff",
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
        **{f"syntax_{k}": v for k, v in syntax.items()},
    )


# Greys, git colours and the font stack follow the Claude desktop app's design tokens (its "cds" scale,
# text-git-added / text-git-removed, and the 20% mix it uses for diff backgrounds). The app has light and dark
# only; sepia keeps the same structure on warm greys.
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
    "sepia": palette(
        canvas="#f6efdc",
        panel="#efe6cf",
        inset="#e5d9bd",
        text="#3b3328",
        text_secondary="#6b5f4e",
        text_muted="#9a8c74",
        border="#dacdb0",
        accent="#3a5a9a",
        accent_bg="#dfe4ec",
        tree_active_bg="#cfd9ec",
        tree_active_fg="#3a5a9a",
        add="#2f7d2a",
        dele="#b8324d",
        mod="#8a6410",
        ren="#6a4a8a",
        search_hit_bg="#f2d25b",
        search_hit_fg="#3b3328",
        syntax=dict(
            kw="#b2451a", str_="#4f7a2a", cmt="#9a8c74", num="#9a6a10", fn="#3b3328", cls="#8a5a00", builtin="#b2451a"
        ),
    ),
    "dark": palette(
        canvas="#1a1a19",
        panel="#151515",
        inset="#20201f",
        text="#d2d1c7",
        text_secondary="#a5a49a",
        text_muted="#898781",
        border="#2c2c2a",
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
}


def vars_block(p: dict) -> str:
    return "; ".join(f"--{k.rstrip('_').replace('_', '-')}: {v}" for k, v in p.items()) + ";"


def theme_css() -> str:
    light, sepia, dark = (vars_block(PALETTES[k]) for k in ("light", "sepia", "dark"))
    return f"""
:root {{ {light} }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme]) {{ {dark} }} }}
:root[data-theme="light"] {{ {light} }}
:root[data-theme="sepia"] {{ {sepia} }}
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
    its two colours come from the dark palette at write time: `render.py --splash` writes it to web/index.html."""
    dark = PALETTES["dark"]
    return fill(SPLASH, TEXT=dark["text_secondary"], BG=dark["bg_canvas"])


# The icon of each pull request status, as the inner paths of a 24×24 stroked SVG, and the palette colour it takes.
PR_ICONS = {
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


def pr_status(pr: dict) -> str:
    """The pull request's status as a key of PR_ICONS: "draft" for an open draft, else its state in lower case.
    Empty for a state without an icon."""
    status = "draft" if pr.get("draft") and pr.get("state") == "OPEN" else (pr.get("state") or "").lower()
    return status if status in PR_ICONS else ""


def pr_icon(status: str) -> str:
    """The status icon as HTML, coloured by the .pr-icon rules of the theme head. Empty for an unknown status."""
    if status not in PR_ICONS:
        return ""
    label = status.capitalize()
    return f'<span class="pr-icon st-{status}" role="img" aria-label="{label}" title="{label}"></span>'


def pr_icon_css() -> str:
    """Draw each status icon as a mask over its colour, so any document can show one with a single span."""
    rules = [
        ".pr-icon { display: inline-block; flex: none; width: 14px; height: 14px; vertical-align: -2px; "
        "background: currentColor; -webkit-mask: var(--icon) center / contain no-repeat; "
        "mask: var(--icon) center / contain no-repeat; }"
    ]
    for status, (paths, color) in PR_ICONS.items():
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="black" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round">{paths}</svg>'
        )
        url = "data:image/svg+xml," + urllib.parse.quote(svg)
        rules.append(f'.pr-icon.st-{status} {{ --icon: url("{url}"); color: var(--{color}); }}')
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
    """The theme variables, the status icons, the search clear button and the script that applies the saved
    theme, for the head of a document so it never flashes the default theme."""
    css = "\n".join([theme_css(), pr_icon_css(), search_clear_css()])
    return f"<style>{css}</style>\n<script>{(SHELL_DIR / 'theme.js').read_text()}</script>"


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<meta name="pr-status" content="__STATUS__">
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


def scripts() -> list[Path]:
    return sorted(PAGE_DIR.glob("*.js"))


def vendored() -> list[Path]:
    """The third-party scripts a page inlines before its own."""
    return [VENDOR_DIR / "highlight.min.js"]


def frame_script() -> str:
    """The script that lets a page run inside the tabbed shell; the review page gets it with the other scripts."""
    return (SHELL_DIR / "frame.js").read_text()


def sources() -> list[Path]:
    """Every file whose content ends up in a page, so a page older than any of them is rebuilt."""
    return [
        ROOT / "render.py",
        PAGE_DIR / "page.css",
        SHELL_DIR / "theme.js",
        *vendored(),
        SHELL_DIR / "frame.js",
        *scripts(),
    ]


def app_info() -> dict:
    """The commit of this repository the page was rendered from, for the bug report a page writes."""

    def git(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    own = git("rev-parse", "--show-toplevel") == str(ROOT)
    return {
        "commit": git("rev-parse", "--short", "HEAD") if own else "",
        "dirty": own and bool(git("--no-optional-locks", "status", "--porcelain")),
        "built": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def script_name(path: Path) -> str:
    """The name on a script's marker line: the file name, or the path under page/ for a vendored script."""
    return str(path.relative_to(PAGE_DIR)) if path.is_relative_to(VENDOR_DIR) else path.name


def render_page(classified: dict, annotations: dict) -> str:
    data = {**classified, "annotations": annotations, "app": app_info()}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = f"#{annotations['pr']['number']} {annotations['pr']['title']}"
    js = "\n".join(
        f"// ---- {script_name(path)}\n{path.read_text()}" for path in [*vendored(), SHELL_DIR / "frame.js", *scripts()]
    )
    return fill(
        TEMPLATE,
        TITLE=html.escape(title),
        STATUS=pr_status(annotations["pr"]),
        THEME=theme_head(),
        CSS=(PAGE_DIR / "page.css").read_text(),
        JS=js,
        DATA=payload,
    )


def main() -> None:
    if sys.argv[1:] == ["--splash"]:
        (ROOT / "web" / "index.html").write_text(splash_html())
        return
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    html = render_page(json.loads(Path(sys.argv[1]).read_text()), json.loads(Path(sys.argv[2]).read_text()))
    Path(sys.argv[3]).write_text(html)
    print(f"wrote {sys.argv[3]} ({len(html) // 1024} KB)")


if __name__ == "__main__":
    main()
