import json

import render

PR = {"number": 3, "title": "Fix <b>&</b> things", "url": "https://github.com/o/r/pull/3", "repo": "o/r", "head": "abc"}


def test_fill_replaces_slots_in_one_pass() -> None:
    assert render.fill("__A__ and __B__", A="__B__", B="b") == "__B__ and b"


def test_render_page_inlines_data_scripts_and_theme() -> None:
    classified = {
        "files": [{"path": "x.html", "content": "<script></script>"}],
        "renames": [],
        "totals": {},
    }
    html = render.render_page(classified, {"pr": PR})
    assert "<title>#3 Fix &lt;b&gt;&amp;&lt;/b&gt; things</title>" in html
    payload = html.split('<script id="data" type="application/json">')[1].split("</script>")[0]
    assert "</" not in payload and "<\\/script>" in payload
    data = json.loads(payload)
    assert data["files"][0]["content"] == "<script></script>"
    assert data["annotations"] == {"pr": PR}
    assert set(data["app"]) == {"commit", "dirty", "built"}
    markers = [line for line in html.splitlines() if line.startswith("// ---- ")]
    assert markers[0] == "// ---- frame.js"
    assert markers[1:] == [f"// ---- {p.name}" for p in render.scripts()]
    assert markers[1:] == sorted(markers[1:])
    assert ':root[data-theme="dark"]' in html
    assert (render.PAGE_DIR / "page.css").read_text() in html


def test_sources_lists_every_file_a_page_inlines() -> None:
    names = {p.name for p in render.sources()}
    assert {"render.py", "page.css", "theme.js", "frame.js", "00-data.js", "90-events.js"} <= names
