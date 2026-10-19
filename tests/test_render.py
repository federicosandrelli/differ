import json
import re
from pathlib import Path

import render

PR = {
    "number": 3,
    "title": "Fix <b>&</b> things",
    "url": "https://github.com/o/r/pull/3",
    "repo": "o/r",
    "head": "abc",
    "status": "open",
    "author": "ann",
}


def test_fill_replaces_slots_in_one_pass() -> None:
    assert render.fill("__A__ and __B__", A="__B__", B="b") == "__B__ and b"


def test_render_page_inlines_data_scripts_and_theme() -> None:
    classified = {
        "files": [{"path": "x.html", "content": "<script></script>"}],
        "renames": [],
    }
    html = render.render_page(classified, PR)
    assert "<title>#3 Fix &lt;b&gt;&amp;&lt;/b&gt; things</title>" in html
    payload = html.split('<script id="data" type="application/json">')[1].split("</script>")[0]
    assert "</" not in payload and "<\\/script>" in payload
    data = json.loads(payload)
    assert data["files"][0]["content"] == "<script></script>"
    assert data["pr"] == PR
    assert set(data["app"]) == {"commit", "dirty", "built"}
    markers = [line for line in html.splitlines() if line.startswith("// ---- ")]
    assert markers[:2] == ["// ---- page/vendor/highlight.min.js", "// ---- shell/frame.js"]
    assert markers[2:] == sorted(f"// ---- page/{p.name}" for p in render.PAGE_DIR.glob("*.js"))
    assert ':root[data-theme="light"]' in html
    assert (render.PAGE_DIR / "page.css").read_text() in html


def test_read_head_reads_back_the_title_status_and_author_render_page_writes() -> None:
    page = render.render_page({"files": [], "renames": []}, PR | {"status": "merged", "author": "a&b"})
    assert render.read_head(page.encode()) == ("Fix <b>&</b> things", "merged", "a&b")
    assert render.read_head(b"<html></html>") == (None, "", "")


def test_page_inlines_highlight_js_and_loads_nothing_from_the_network() -> None:
    html = render.render_page({"files": [], "renames": []}, PR)
    assert "<script src=" not in html
    assert "<link " not in html
    assert (render.VENDOR_DIR / "highlight.min.js").read_text() in html
    assert (render.VENDOR_DIR / "LICENSE").read_text().startswith("BSD 3-Clause License")


def test_sources_lists_every_file_a_page_inlines() -> None:
    assert set(render.page_scripts()) <= set(render.sources())
    names = {p.name for p in render.sources()}
    assert {
        "render.py",
        "page.css",
        "base.css",
        "theme.js",
        "tooltip.js",
        "highlight.min.js",
        "frame.js",
        "00-data.js",
        "90-events.js",
    } <= names


def test_splash_page_is_written_from_the_dark_palette() -> None:
    assert (render.ROOT / "web" / "index.html").read_text() == render.splash_html(), "run: python3 render.py --splash"


def test_every_token_is_used_and_every_used_token_is_defined() -> None:
    defined = {f"--{k.rstrip('_').replace('_', '-')}" for k in render.PALETTES["light"]}
    non_colour = {"--mono", "--sans", "--dim", "--d", "--titlebar", "--side", "--cols"}
    non_colour |= {"--radius-sm", "--radius", "--radius-lg", "--radius-pill", "--control-font", "--control-line"}
    used: set[str] = set()
    for path in styled_sources():
        used |= set(re.findall(r"var\((--[a-z0-9-]+)", path.read_text()))
    assert used - non_colour == defined


def test_colours_live_only_in_the_palettes() -> None:
    """A stylesheet or script names a token, never a colour; render.py mixes every tint."""
    hex_colour = r"#(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{3,8}\b"
    named = r"\b(white|black|red|green|blue|yellow|orange|purple|gr[ae]y)(?![\w-])"
    literal = re.compile(rf"{hex_colour}|rgba?\(|hsla?\(|color-mix\(|{named}")
    found = [
        f"{p}:{n}"
        for p in styled_sources()
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if literal.search(line)
    ]
    assert found == []


def styled_sources() -> list[Path]:
    return [
        render.PAGE_DIR / "page.css",
        *render.PAGE_DIR.glob("*.js"),
        *render.SHELL_DIR.glob("*.css"),
        *render.SHELL_DIR.glob("*.html"),
        *render.SHELL_DIR.glob("*.js"),
    ]
