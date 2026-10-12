import json
import re
import time
from pathlib import Path
from typing import Any, cast

import pytest

import server


def handler(origin: str | None, ctype: str | None, port: int = 8765) -> server.Handler:
    h = server.Handler.__new__(server.Handler)
    h.server = cast(Any, type("FakeServer", (), {"server_address": ("127.0.0.1", port)})())
    headers = {k: v for k, v in (("Origin", origin), ("Content-Type", ctype)) if v is not None}
    h.headers = cast(Any, headers)
    return h


def test_refused_accepts_a_loopback_origin_with_json() -> None:
    assert handler("http://localhost:8765", "application/json").refused() is None
    assert handler("http://127.0.0.1:8765", "application/json; charset=utf-8").refused() is None


@pytest.mark.parametrize(
    ("origin", "ctype", "why"),
    [
        (None, "application/json", "Origin (missing) is not this server"),
        ("https://evil.example", "application/json", "Origin https://evil.example is not this server"),
        ("http://localhost:9999", "application/json", "Origin http://localhost:9999 is not this server"),
        ("http://localhost:8765", None, "Content-Type (missing) is not application/json"),
        ("http://localhost:8765", "text/plain", "Content-Type text/plain is not application/json"),
    ],
)
def test_refused_rejects_other_origins_and_content_types(origin: str | None, ctype: str | None, why: str) -> None:
    assert handler(origin, ctype).refused() == why


def test_page_key_lowercases_owner_and_repo() -> None:
    m = server.PAGE.match("/Owner/Repo/pull/12")
    assert m is not None
    assert server.page_key(m) == ("owner", "repo", 12)
    assert server.PAGE.match("/owner/repo/pull/12/files") is None


def test_ago() -> None:
    now = time.time()
    assert server.ago(now) == "just now"
    assert server.ago(now - 90) == "1m ago"
    assert server.ago(now - 3700) == "1h ago"
    assert server.ago(now - 2 * 86400) == "2d ago"


def test_note_opened_keeps_the_newest_ten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "RECENT", tmp_path / "recent.json")
    assert server.recent_pages() == []
    for n in range(12):
        server.note_opened(("o", "r", n), f"<title>#{n} Title &amp; {n}</title>".encode())
    server.note_opened(("o", "r", 3), b"<html>no title</html>")
    recent = server.recent_pages()
    assert [p["number"] for p in recent] == [3, 11, 10, 9, 8, 7, 6, 5, 4, 2]
    assert recent[0]["title"] == "o/r#3"
    assert recent[1]["title"] == "Title & 11"
    assert json.loads((tmp_path / "recent.json").read_text()) == recent


def test_recent_items_escapes_titles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "RECENT", tmp_path / "recent.json")
    assert "No pull requests opened yet" in server.recent_items()
    server.note_opened(("o", "r", 1), b"<title>#1 a &lt;b&gt;</title>")
    items = server.recent_items()
    assert 'href="/o/r/pull/1"' in items
    assert re.search(r'<span class="t">a &lt;b&gt;</span>', items)
