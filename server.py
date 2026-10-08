#!/usr/bin/env python3
"""Serve review pages for any GitHub pull request, building each on first visit.

Usage: server.py [port]   (default 8765)

  /                                  pull requests waiting for your review, yours, and recent pages
  /<owner>/<repo>/pull/<number>      the review page, rebuilt when the head commit changes
  /open?pr=<url or owner/repo#n>     redirects to the page above
"""

from __future__ import annotations

import html
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import build
import render

PAGE = re.compile(r"^/([\w.-]+)/([\w.-]+)/pull/(\d+)/?$")
STATUS = re.compile(r"^/status/([\w.-]+)/([\w.-]+)/(\d+)$")
HEAD_TTL = 20
LIST_TTL = 60


class Job:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.state = "building"
        self.error = ""


jobs: dict[tuple[str, str, int], Job] = {}
heads: dict[tuple[str, str, int], tuple[float, str]] = {}
lists: dict[str, tuple[float, list | str]] = {}
lock = threading.Lock()


def current_head(key: tuple[str, str, int], fresh: bool = False) -> str:
    now = time.time()
    with lock:
        cached = heads.get(key)
    if cached and not fresh and now - cached[0] < HEAD_TTL:
        return cached[1]
    sha = build.head_sha(*key)
    with lock:
        heads[key] = (now, sha)
    return sha


def start_job(key: tuple[str, str, int]) -> Job:
    with lock:
        job = jobs.get(key)
        if job and job.state == "building":
            return job
        job = jobs[key] = Job()

    def work() -> None:
        try:
            build.build(f"{key[0]}/{key[1]}#{key[2]}", log=lambda line: job.log.append(line))
            job.state = "done"
        except Exception as exc:
            job.error = str(exc)
            job.state = "error"

    threading.Thread(target=work, daemon=True).start()
    return job


def search(query: str) -> list | str:
    """Open pull requests matching a gh search query, newest first, or the error text when gh fails."""
    now = time.time()
    with lock:
        cached = lists.get(query)
    if cached and now - cached[0] < LIST_TTL:
        return cached[1]
    try:
        out = subprocess.run(["gh", "search", "prs", *query.split(), "--state=open", "--limit", "40",
                              "--json", "number,title,repository,author,updatedAt,url,isDraft"],
                             capture_output=True, text=True, timeout=30)
        if out.returncode:
            result: list | str = out.stderr.strip() or f"gh search prs exited with {out.returncode}"
        else:
            result = sorted(json.loads(out.stdout), key=lambda r: r.get("updatedAt", ""), reverse=True)
    except subprocess.TimeoutExpired:
        result = "gh search prs took longer than 30 seconds"
    except json.JSONDecodeError as exc:
        result = f"gh search prs returned invalid JSON: {exc}"
    with lock:
        lists[query] = (now, result)
    return result


def recent_pages() -> list[dict]:
    root = build.CACHE / "pages"
    if not root.exists():
        return []
    pages = []
    for f in root.glob("*/*/*/*.html"):
        owner, repo, number = f.parts[-4], f.parts[-3], f.parts[-2]
        pages.append({"owner": owner, "repo": repo, "number": int(number), "mtime": f.stat().st_mtime})
    latest: dict[tuple, dict] = {}
    for p in sorted(pages, key=lambda p: p["mtime"]):
        latest[(p["owner"], p["repo"], p["number"])] = p
    return sorted(latest.values(), key=lambda p: -p["mtime"])[:15]


def ago(iso_or_ts) -> str:
    if isinstance(iso_or_ts, str):
        ts = time.mktime(time.strptime(iso_or_ts[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    else:
        ts = iso_or_ts
    s = max(0, int(time.time() - ts))
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{s // n}{unit} ago"
    return "just now"


SHELL = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>{themes}
:root {{ --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif; --mono: "SF Mono", ui-monospace, Menlo, monospace; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--fg); font: 14px/1.5 var(--sans); }}
a {{ color: var(--accent); text-decoration: none; }} a:hover {{ text-decoration: underline; }}
main {{ max-width: 920px; margin: 0 auto; padding: 32px 24px 64px; }}
h1 {{ font-size: 20px; margin: 0 0 4px; }} h2 {{ font-size: 13px; margin: 28px 0 8px; color: var(--fg3); text-transform: uppercase; letter-spacing: .05em; font-weight: 600; }}
.sub {{ color: var(--fg2); margin: 0 0 20px; }}
form {{ display: flex; gap: 8px; }}
input {{ flex: 1; font: 14px var(--sans); color: var(--fg); background: var(--bg2); border: 1px solid var(--line); border-radius: 8px; padding: 9px 12px; outline: none; }}
input:focus {{ border-color: var(--accent); }}
button {{ font: 14px var(--sans); color: #fff; background: var(--accent); border: 0; border-radius: 8px; padding: 0 16px; cursor: pointer; }}
.list {{ border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }}
.item {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2px 16px; padding: 10px 14px; border-top: 1px solid var(--line); color: var(--fg); }}
.item:first-child {{ border-top: 0; }} .item:hover {{ background: var(--bg2); text-decoration: none; }}
.item .t {{ font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.item .m {{ grid-column: 1; color: var(--fg3); font-size: 12px; font-family: var(--mono); }}
.item .w {{ grid-row: 1 / span 2; grid-column: 2; align-self: center; color: var(--fg3); font-size: 12px; white-space: nowrap; }}
.draft {{ font-size: 11px; color: var(--fg3); border: 1px solid var(--line); border-radius: 4px; padding: 0 4px; margin-left: 6px; font-weight: 400; }}
.empty {{ color: var(--fg3); padding: 12px 14px; }}
.log {{ font: 13px/1.7 var(--mono); color: var(--fg2); background: var(--bg2); border: 1px solid var(--line); border-radius: 10px; padding: 12px 16px; white-space: pre-wrap; }}
.log b {{ color: var(--fg); font-weight: 500; }} .err {{ color: var(--del-fg); }}
.themes {{ display: flex; justify-content: flex-end; gap: 2px; margin: 0 0 8px; }}
.themes button {{ font: 11px var(--sans); color: var(--fg2); background: none; border: 1px solid var(--line); border-radius: 6px; padding: 2px 8px; }}
.themes button:hover {{ background: var(--bg3); }}
.themes button.on {{ background: var(--accent-bg); border-color: var(--accent); color: var(--accent); }}
html.app body {{ padding-top: 28px; }}
</style>
<script>try {{ const t = localStorage.getItem('dr:theme') || 'dark'; if (t !== 'auto') document.documentElement.dataset.theme = t; }} catch (e) {{}}</script>
</head><body><main><div class="themes">
<button data-theme="auto">auto</button><button data-theme="light">light</button><button data-theme="sepia">sepia</button><button data-theme="dark">dark</button>
</div>{body}</main>
<script>
(function () {{
  const get = () => {{ try {{ return localStorage.getItem('dr:theme') || 'dark'; }} catch (e) {{ return 'dark'; }} }};
  const apply = (t) => {{
    if (t === 'auto') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
    for (const b of document.querySelectorAll('.themes button')) b.classList.toggle('on', b.dataset.theme === t);
    const win = window.__TAURI__ && window.__TAURI__.window;
    if (win) win.getCurrentWindow().setTheme(t === 'auto' ? null : t === 'dark' ? 'dark' : 'light').catch(() => {{}});
  }};
  for (const b of document.querySelectorAll('.themes button')) b.addEventListener('click', () => {{
    try {{ localStorage.setItem('dr:theme', b.dataset.theme); }} catch (e) {{}}
    apply(b.dataset.theme);
  }});
  apply(get());
}})();
</script></body></html>"""


def shell(title: str, body: str) -> bytes:
    return SHELL.format(title=html.escape(title), themes=render.theme_css(), body=body).encode()


def pr_item(owner: str, repo: str, number: int, title: str, meta: str, when: str, draft: bool = False) -> str:
    return (f'<a class="item" href="/{owner}/{repo}/pull/{number}"><span class="t">{html.escape(title)}'
            f'{"<span class=draft>draft</span>" if draft else ""}</span>'
            f'<span class="w">{html.escape(when)}</span><span class="m">{html.escape(meta)}</span></a>')


def search_items(rows: list | str) -> str:
    if isinstance(rows, str):
        return f'<div class="empty err">{html.escape(rows)}</div>'
    if not rows:
        return '<div class="empty">Nothing here.</div>'
    out = []
    for r in rows:
        owner, repo = r["repository"]["nameWithOwner"].split("/")
        out.append(pr_item(owner, repo, r["number"], r["title"],
                           f'{owner}/{repo}#{r["number"]} · {(r.get("author") or {}).get("login", "")}',
                           ago(r["updatedAt"]), r.get("isDraft", False)))
    return "".join(out)


def start_page() -> bytes:
    with ThreadPoolExecutor(2) as pool:
        review, mine = pool.map(search, ["--review-requested=@me", "--author=@me"])
    recent = recent_pages()
    recent_html = "".join(
        pr_item(p["owner"], p["repo"], p["number"], f'{p["owner"]}/{p["repo"]}#{p["number"]}', "built page",
                ago(p["mtime"])) for p in recent) or '<div class="empty">No pages built yet.</div>'
    body = f"""<h1>Pull request review</h1><p class="sub">Open any GitHub pull request, or pick one below.</p>
<form action="/open"><input name="pr" placeholder="https://github.com/owner/repo/pull/123 or owner/repo#123" autofocus><button>Open</button></form>
<h2>Waiting for your review</h2><div class="list">{search_items(review)}</div>
<h2>Yours</h2><div class="list">{search_items(mine)}</div>
<h2>Recently opened</h2><div class="list">{recent_html}</div>"""
    return shell("Pull request review", body)


def progress_page(key: tuple[str, str, int]) -> bytes:
    owner, repo, number = key
    body = f"""<h1>{owner}/{repo}#{number}</h1><p class="sub"><a href="/">← All pull requests</a></p>
<div class="log" id="log">Starting…</div>
<script>
async function poll() {{
  const r = await fetch('/status/{owner}/{repo}/{number}'); const s = await r.json();
  const el = document.getElementById('log');
  el.innerHTML = s.log.map(l => l.replace(/&/g,'&amp;').replace(/</g,'&lt;')).join('\\n') || 'Starting…';
  if (s.state === 'done') {{ location.reload(); return; }}
  if (s.state === 'error') {{ el.innerHTML += '\\n<span class="err">' + s.error.replace(/&/g,'&amp;').replace(/</g,'&lt;') + '</span>\\n\\n<a href="/{owner}/{repo}/pull/{number}?refresh=1">Try again</a>'; return; }}
  setTimeout(poll, 700);
}}
poll();
</script>"""
    return shell(f"Building {owner}/{repo}#{number}", body)


def error_page(message: str) -> bytes:
    return shell("Error", f'<h1>Could not open that</h1><p class="sub"><a href="/">← All pull requests</a></p>'
                          f'<div class="log err">{html.escape(message)}</div>')


class Handler(BaseHTTPRequestHandler):
    def send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        try:
            if url.path == "/":
                return self.send(200, start_page())
            if url.path == "/open":
                owner, repo, number = build.parse_ref((query.get("pr") or [""])[0])
                return self.redirect(f"/{owner}/{repo}/pull/{number}")
            if m := STATUS.match(url.path):
                with lock:
                    job = jobs.get((m.group(1), m.group(2), int(m.group(3))))
                data = {"state": job.state, "log": job.log, "error": job.error} if job else {"state": "none", "log": [], "error": ""}
                return self.send(200, json.dumps(data).encode(), "application/json")
            if m := PAGE.match(url.path):
                key = (m.group(1), m.group(2), int(m.group(3)))
                refresh = "refresh" in query
                head = current_head(key, fresh=refresh)
                page = build.page_path(*key, head)
                if refresh and page.exists():
                    page.unlink()
                    return self.redirect(url.path)
                if build.page_current(page):
                    return self.send(200, page.read_bytes())
                start_job(key)
                return self.send(200, progress_page(key))
            return self.send(404, error_page(f"No page at {url.path}"))
        except build.BuildError as exc:
            return self.send(400, error_page(str(exc)))

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")


def port_taken(port: int) -> str | None:
    """The address something already answers on, IPv4 or IPv6; browsers try either for localhost."""
    for family, host in ((socket.AF_INET6, "::1"), (socket.AF_INET, "127.0.0.1")):
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.3)
            if probe.connect_ex((host, port)) == 0:
                return host
    return None


def exit_with_parent() -> None:
    """Exit once the parent process is gone, checked once a second."""
    parent = os.getppid()
    while os.getppid() == parent:
        time.sleep(1)
    os._exit(0)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    if taken := port_taken(port):
        sys.exit(f"Port {port} is already in use on {taken}. Stop that process or pass another port: server.py <port>")
    if "--exit-with-parent" in sys.argv:
        threading.Thread(target=exit_with_parent, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Serving pull request reviews on http://localhost:{port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
