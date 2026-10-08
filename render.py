#!/usr/bin/env python3
"""Render a classified diff plus PR metadata as one HTML page.

Usage: render.py <classified.json> <annotations.json> <out.html>
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def mix(color: str, pct: int) -> str:
    return f"color-mix(in srgb, {color} {pct}%, transparent)"


def shade(color: str, pct: int, toward: int) -> str:
    """Mix a hex colour pct% towards black (0) or white (255)."""
    channels = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(c + (toward - c) * pct / 100):02x}" for c in channels)


def palette(*, bg, bg2, bg3, fg, fg2, fg3, line, accent, accent_bg, sel, sel_fg, add, dele, mod, ren, mark, mark_fg, syntax, header_toward):
    return dict(
        fhead=shade(bg, 11, header_toward), bg=bg, bg2=bg2, bg3=bg3, fg=fg, fg2=fg2, fg3=fg3, line=line, accent=accent, accent_bg=accent_bg, sel=sel, sel_fg=sel_fg,
        add_bg=mix(add, 16), add_fg=add, add_num=mix(add, 28), add_word=mix(add, 38),
        del_bg=mix(dele, 16), del_fg=dele, del_num=mix(dele, 28), del_word=mix(dele, 38),
        mod_bg=mix(mod, 22), mod_fg=mod, ren_bg=mix(ren, 22), ren_fg=ren, mark=mark, mark_fg=mark_fg, **syntax,
    )


# Greys, git colours and the font stack follow the Claude desktop app's design tokens (its "cds" scale,
# text-git-added / text-git-removed, and the 20% mix it uses for diff backgrounds). The app has light and dark
# only; sepia keeps the same structure on warm greys.
PALETTES = {
    "light": palette(
        bg="#ffffff", bg2="#f9f9f7", bg3="#f0efec", fg="#20201f", fg2="#6d6b67", fg3="#898781", line="#e1e0d9",
        accent="#2f6feb", accent_bg="#e8f0fe", sel="#cddcfb", sel_fg="#2f6feb",
        add="#1e9e3c", dele="#cd2054", mod="#9a5b00", ren="#6d3fc7", mark="#ffe066", mark_fg="#20201f",
        header_toward=0,
        syntax=dict(kw="#c2410c", str_="#3f7d2a", cmt="#898781", num="#9a6a10", fn="#20201f", cls="#8a5a00", builtin="#c2410c"),
    ),
    "sepia": palette(
        bg="#f6efdc", bg2="#efe6cf", bg3="#e5d9bd", fg="#3b3328", fg2="#6b5f4e", fg3="#9a8c74", line="#dacdb0",
        accent="#3a5a9a", accent_bg="#dfe4ec", sel="#cfd9ec", sel_fg="#3a5a9a",
        add="#2f7d2a", dele="#b8324d", mod="#8a6410", ren="#6a4a8a", mark="#f2d25b", mark_fg="#3b3328",
        header_toward=0,
        syntax=dict(kw="#b2451a", str_="#4f7a2a", cmt="#9a8c74", num="#9a6a10", fn="#3b3328", cls="#8a5a00", builtin="#b2451a"),
    ),
    "dark": palette(
        bg="#1a1a19", bg2="#151515", bg3="#20201f", fg="#d2d1c7", fg2="#a5a49a", fg3="#898781", line="#2c2c2a",
        accent="#6ea0ff", accent_bg="#1a2742", sel="#2a4474", sel_fg="#6ea0ff",
        add="#32d74b", dele="#ff2c56", mod="#f0b45a", ren="#c4a6ff", mark="#ffe066", mark_fg="#1c1f26",
        header_toward=255,
        syntax=dict(kw="#f29b7a", str_="#9fcf8a", cmt="#898781", num="#e5c07b", fn="#d2d1c7", cls="#e5c07b", builtin="#f29b7a"),
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


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<style>
__THEMES__
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


def main() -> None:
    classified = json.loads(Path(sys.argv[1]).read_text())
    annotations = json.loads(Path(sys.argv[2]).read_text())
    data = {**classified, "annotations": annotations}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = f"#{annotations['pr']['number']} {annotations['pr']['title']}"
    html = (TEMPLATE.replace("__TITLE__", title).replace("__THEMES__", theme_css())
            .replace("__CSS__", (ROOT / "page.css").read_text()).replace("__JS__", (ROOT / "page.js").read_text())
            .replace("__DATA__", payload))
    Path(sys.argv[3]).write_text(html)
    print(f"wrote {sys.argv[3]} ({len(html) // 1024} KB)")


if __name__ == "__main__":
    main()
