#!/usr/bin/env python3
"""Render a classified diff plus PR metadata as one HTML page.

Usage: render.py <classified.json> <annotations.json> <out.html>

The page's script is shell/frame.js followed by page/*.js in name order (hence the numeric prefixes), and its
stylesheet page/page.css. Both are inlined, so a page works from disk as well as from the server. The theme
(shell/theme.js and the palettes here) goes into the head of every document the server sends.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAGE_DIR = ROOT / "page"
SHELL_DIR = ROOT / "shell"


def fill(template: str, **slots: str) -> str:
    """Fill a template's __NAME__ slots in one pass, so a value holding a slot's name is left as it is."""
    return re.sub("|".join(f"__{name}__" for name in slots), lambda m: slots[m.group(0)[2:-2]], template)


def mix(color: str, pct: int) -> str:
    return f"color-mix(in srgb, {color} {pct}%, transparent)"


def palette(
    *, bg, bg2, bg3, fg, fg2, fg3, line, accent, accent_bg, sel, sel_fg, add, dele, mod, ren, mark, mark_fg, syntax
):
    return dict(
        bg=bg,
        bg2=bg2,
        bg3=bg3,
        fg=fg,
        fg2=fg2,
        fg3=fg3,
        line=line,
        accent=accent,
        accent_bg=accent_bg,
        sel=sel,
        sel_fg=sel_fg,
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
        mark=mark,
        mark_fg=mark_fg,
        **syntax,
    )


# Greys, git colours and the font stack follow the Claude desktop app's design tokens (its "cds" scale,
# text-git-added / text-git-removed, and the 20% mix it uses for diff backgrounds). The app has light and dark
# only; sepia keeps the same structure on warm greys.
PALETTES = {
    "light": palette(
        bg="#ffffff",
        bg2="#f9f9f7",
        bg3="#f0efec",
        fg="#20201f",
        fg2="#6d6b67",
        fg3="#898781",
        line="#e1e0d9",
        accent="#2f6feb",
        accent_bg="#e8f0fe",
        sel="#cddcfb",
        sel_fg="#2f6feb",
        add="#1e9e3c",
        dele="#cd2054",
        mod="#9a5b00",
        ren="#6d3fc7",
        mark="#ffe066",
        mark_fg="#20201f",
        syntax=dict(
            kw="#c2410c", str_="#3f7d2a", cmt="#898781", num="#9a6a10", fn="#20201f", cls="#8a5a00", builtin="#c2410c"
        ),
    ),
    "sepia": palette(
        bg="#f6efdc",
        bg2="#efe6cf",
        bg3="#e5d9bd",
        fg="#3b3328",
        fg2="#6b5f4e",
        fg3="#9a8c74",
        line="#dacdb0",
        accent="#3a5a9a",
        accent_bg="#dfe4ec",
        sel="#cfd9ec",
        sel_fg="#3a5a9a",
        add="#2f7d2a",
        dele="#b8324d",
        mod="#8a6410",
        ren="#6a4a8a",
        mark="#f2d25b",
        mark_fg="#3b3328",
        syntax=dict(
            kw="#b2451a", str_="#4f7a2a", cmt="#9a8c74", num="#9a6a10", fn="#3b3328", cls="#8a5a00", builtin="#b2451a"
        ),
    ),
    "dark": palette(
        bg="#1a1a19",
        bg2="#151515",
        bg3="#20201f",
        fg="#d2d1c7",
        fg2="#a5a49a",
        fg3="#898781",
        line="#2c2c2a",
        accent="#6ea0ff",
        accent_bg="#1a2742",
        sel="#2a4474",
        sel_fg="#6ea0ff",
        add="#32d74b",
        dele="#ff2c56",
        mod="#f0b45a",
        ren="#c4a6ff",
        mark="#ffe066",
        mark_fg="#1c1f26",
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


def theme_head() -> str:
    """The theme variables and the script that applies the saved theme, for the head of a document so it
    never flashes the default theme."""
    return f"<style>{theme_css()}</style>\n<script>{(SHELL_DIR / 'theme.js').read_text()}</script>"


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
__THEME__
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
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


def frame_script() -> str:
    """The script that lets a page run inside the tabbed shell; the review page gets it with the other scripts."""
    return (SHELL_DIR / "frame.js").read_text()


def sources() -> list[Path]:
    """Every file whose content ends up in a page, so a page older than any of them is rebuilt."""
    return [ROOT / "render.py", PAGE_DIR / "page.css", SHELL_DIR / "theme.js", SHELL_DIR / "frame.js", *scripts()]


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


def render_page(classified: dict, annotations: dict) -> str:
    data = {**classified, "annotations": annotations, "app": app_info()}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = f"#{annotations['pr']['number']} {annotations['pr']['title']}"
    js = "\n".join(f"// ---- {path.name}\n{path.read_text()}" for path in [SHELL_DIR / "frame.js", *scripts()])
    return fill(
        TEMPLATE,
        TITLE=html.escape(title),
        THEME=theme_head(),
        CSS=(PAGE_DIR / "page.css").read_text(),
        JS=js,
        DATA=payload,
    )


def main() -> None:
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    html = render_page(json.loads(Path(sys.argv[1]).read_text()), json.loads(Path(sys.argv[2]).read_text()))
    Path(sys.argv[3]).write_text(html)
    print(f"wrote {sys.argv[3]} ({len(html) // 1024} KB)")


if __name__ == "__main__":
    main()
