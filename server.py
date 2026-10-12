#!/usr/bin/env python3
"""Serve review pages for any GitHub pull request, building each on first visit.

Usage: server.py [port]   (default 8765)

  /                                  the tabbed shell: the start page as a home tab, and a tab per open page
  /home                              the start page: a search bar and the ten pull requests opened last
  /recent                            the list of pull requests opened last, as an HTML fragment
  /<owner>/<repo>/pull/<number>      the review page: the newest built one at once, rebuilt in the
                                     background when the head commit has moved
  /open?pr=<url or owner/repo#n>     redirects to the page above
  /api/<owner>/<repo>/<number>/comments   GET: the review threads, reviews and conversation comments
  /api/<owner>/<repo>/<number>/<action>   POST with a JSON body: comment, reply, resolve, delete, submit, discard, conversation
"""

from __future__ import annotations

import html
import json
import os
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import build
import comments
import render

PAGE = re.compile(r"^/([\w.-]+)/([\w.-]+)/pull/(\d+)/?$")
STATUS = re.compile(r"^/status/([\w.-]+)/([\w.-]+)/(\d+)$")
API = re.compile(r"^/api/([\w.-]+)/([\w.-]+)/(\d+)/(\w+)$")
TITLE = re.compile(rb"<title>#\d+ (.*?)</title>", re.S)
HEAD_TTL = 20
RECENT = build.CACHE / "recent.json"
RECENT_MAX = 10


class Job:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.state = "building"
        self.error = ""


jobs: dict[tuple[str, str, int], Job] = {}
heads: dict[tuple[str, str, int], tuple[float, str]] = {}
lock = threading.Lock()


def head_checked_recently(key: tuple[str, str, int]) -> bool:
    with lock:
        cached = heads.get(key)
    return bool(cached) and time.time() - cached[0] < HEAD_TTL


def current_head(key: tuple[str, str, int]) -> str:
    sha = build.head_sha(*key)
    with lock:
        heads[key] = (time.time(), sha)
    return sha


def rebuild_if_moved(key: tuple[str, str, int]) -> None:
    """Build the page for the head commit in the background when the one served was built from an older one."""
    if head_checked_recently(key):
        return
    with lock:
        heads[key] = (time.time(), "")

    def work() -> None:
        try:
            if not build.page_current(build.page_path(*key, current_head(key))):
                start_job(key)
        except build.BuildError as exc:
            sys.stderr.write(f"head check for {key} failed: {exc}\n")

    threading.Thread(target=work, daemon=True).start()


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


def recent_pages() -> list[dict]:
    """The pull requests opened last, newest first: owner, repo, number, title and the time of the last open."""
    try:
        return json.loads(RECENT.read_text())
    except (OSError, ValueError):
        return []


def note_opened(key: tuple[str, str, int], page: bytes) -> None:
    """Move the pull request to the top of the recently opened list, titled from the page just served."""
    owner, repo, number = key
    m = TITLE.search(page[:4096])
    title = m.group(1).decode(errors="replace") if m else f"{owner}/{repo}#{number}"
    with lock:
        rest = [p for p in recent_pages() if (p["owner"], p["repo"], p["number"]) != key]
        entry = {"owner": owner, "repo": repo, "number": number, "title": title, "opened": time.time()}
        RECENT.parent.mkdir(parents=True, exist_ok=True)
        RECENT.write_text(json.dumps([entry, *rest][:RECENT_MAX]))


def ago(ts: float) -> str:
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
h1 {{ font-size: 20px; margin: 0 0 4px; }}
.sub {{ color: var(--fg2); margin: 0 0 20px; }}
form {{ display: flex; gap: 8px; margin: 0 0 20px; }}
input {{ flex: 1; font: 14px var(--sans); color: var(--fg); background: var(--bg2); border: 1px solid var(--line); border-radius: 8px; padding: 9px 12px; outline: none; }}
input:focus {{ border-color: var(--accent); }}
button {{ font: 14px var(--sans); color: #fff; background: var(--accent); border: 0; border-radius: 8px; padding: 0 16px; cursor: pointer; }}
.list {{ border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }}
.item {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2px 16px; padding: 10px 14px; border-top: 1px solid var(--line); color: var(--fg); }}
.item:first-child {{ border-top: 0; }} .item:hover {{ background: var(--bg2); text-decoration: none; }}
.item .t {{ font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.item .m {{ grid-column: 1; color: var(--fg3); font-size: 12px; font-family: var(--mono); }}
.item .w {{ grid-row: 1 / span 2; grid-column: 2; align-self: center; color: var(--fg3); font-size: 12px; white-space: nowrap; }}
.empty {{ color: var(--fg3); padding: 12px 14px; }}
.log {{ font: 13px/1.7 var(--mono); color: var(--fg2); background: var(--bg2); border: 1px solid var(--line); border-radius: 10px; padding: 12px 16px; white-space: pre-wrap; }}
.log b {{ color: var(--fg); font-weight: 500; }} .err {{ color: var(--del-fg); }}
.themes {{ display: flex; justify-content: flex-end; gap: 2px; margin: 0 0 8px; }}
.themes button {{ font: 11px var(--sans); color: var(--fg2); background: none; border: 1px solid var(--line); border-radius: 6px; padding: 2px 8px; }}
.themes button:hover {{ background: var(--bg3); }}
.themes button.on {{ background: var(--accent-bg); border-color: var(--accent); color: var(--accent); }}
html.app body {{ padding-top: 28px; }}
</style>
{theme_boot}
</head><body><script>{frame}</script><main><div class="themes">
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
    return SHELL.format(title=html.escape(title), themes=render.theme_css(), theme_boot=render.THEME_BOOT, frame=render.frame_script(),
                        body=body).encode()


# The tabbed shell: a tab strip (the title bar strip in the desktop app) and one iframe per tab. The home tab holds
# the start page; a link to a page opens a tab for it, or brings the tab that has it to the front. The tabs survive
# a relaunch through localStorage. A hidden frame keeps its layout, so a page keeps its scroll position across switches.
TABS = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Pull requests</title>
<style>__THEMES__
:root { --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif; --mono: "SF Mono", ui-monospace, Menlo, monospace; }
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; }
body { background: var(--bg); color: var(--fg); font: 13px var(--sans); display: flex; flex-direction: column; overflow: hidden; }
#tabs { flex: none; display: flex; align-items: center; gap: 4px; height: 36px; padding: 0 8px; background: var(--bg2); border-bottom: 1px solid var(--line); overflow: hidden; user-select: none; -webkit-user-select: none; }
html.app #tabs { height: 40px; padding-left: 84px; }
.tab { display: inline-flex; align-items: center; gap: 6px; height: 26px; padding: 0 6px 0 10px; border-radius: 7px; color: var(--fg2); flex: 0 1 auto; min-width: 48px; max-width: 260px; cursor: default; }
.tab:hover { background: var(--bg3); }
.tab.on { background: var(--bg3); color: var(--fg); box-shadow: 0 0 0 1px var(--line); }
.tab.home { flex: none; padding: 0 8px; min-width: 0; }
.tab.home svg { display: block; }
.tab .label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12.5px; }
.tab .badge { flex: none; font: 11px/18px var(--mono); min-width: 18px; text-align: center; padding: 0 4px; border-radius: 4px; background: var(--bg); color: var(--fg2); border: 1px solid var(--line); }
.tab .close { flex: none; width: 16px; height: 16px; border: 0; border-radius: 4px; padding: 0; background: none; color: var(--fg3); font: 15px/16px var(--sans); cursor: pointer; visibility: hidden; }
.tab:hover .close, .tab.on .close { visibility: visible; }
.tab .close:hover { background: var(--line); color: var(--fg); }
#panes { flex: 1; position: relative; min-height: 0; }
#panes iframe { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; background: var(--bg); visibility: hidden; }
#panes iframe.on { visibility: visible; }
</style>
__THEME_BOOT__
</head><body><div id="tabs" data-tauri-drag-region></div><div id="panes"></div>
<script>
(function () {
  const APP = document.documentElement.classList.contains('app');
  const HOME_ICON = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path d="M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>';
  const bar = document.getElementById('tabs'), panes = document.getElementById('panes');
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  // A tab is keyed by its page's path; a page still being resolved (/open?pr=...) by its query until it reports.
  const keyOf = (url) => { const u = new URL(url, location.origin); const path = u.pathname.replace(/\/+$/, '').toLowerCase(); return path === '/open' ? path + u.search.toLowerCase() : path; };
  const home = { key: 'home', url: '/home', title: 'Pull requests', badge: 0, frame: null };
  let tabs = [], active = home;

  const lsGet = () => { try { return JSON.parse(localStorage.getItem('dr:tabs')) || {}; } catch (e) { return {}; } };
  const save = () => { try { localStorage.setItem('dr:tabs', JSON.stringify({ active: active.key, tabs: tabs.map(t => ({ url: t.url, title: t.title, badge: t.badge })) })); } catch (e) {} };

  function frameOf(tab) {
    if (!tab.frame) { tab.frame = document.createElement('iframe'); tab.frame.src = tab.url; panes.appendChild(tab.frame); }
    return tab.frame;
  }
  function draw() {
    const pill = (t) => `<div class="tab ${t === active ? 'on' : ''}" data-key="${esc(t.key)}" title="${esc(t.title)}"><span class="label">${esc(t.title)}</span>${t.badge ? `<span class="badge" title="${t.badge} unresolved">${t.badge}</span>` : ''}<button class="close" data-close="${esc(t.key)}" title="Close tab">×</button></div>`;
    bar.innerHTML = `<div class="tab home ${active === home ? 'on' : ''}" data-key="home" title="Pull requests">${HOME_ICON}</div>` + tabs.map(pill).join('');
    document.title = active.title;
  }
  function activate(tab) {
    active = tab;
    const frame = frameOf(tab);
    for (const f of panes.children) f.classList.toggle('on', f === frame);
    draw(); save();
    if (frame.contentWindow) { frame.contentWindow.postMessage({ type: 'shown' }, location.origin); frame.contentWindow.focus(); }
  }
  function open(url) {
    const key = keyOf(url);
    if (key === '' || key === '/home') return activate(home);
    let tab = tabs.find(t => t.key === key);
    if (!tab) { tab = { key, url, title: '…', badge: 0, frame: null }; tabs.push(tab); }
    activate(tab);
  }
  function close(tab) {
    const i = tabs.indexOf(tab);
    if (i < 0) return;
    tabs.splice(i, 1);
    if (tab.frame) tab.frame.remove();
    if (active === tab) activate(tabs[i] || tabs[i - 1] || home); else { draw(); save(); }
  }
  const byKey = (key) => key === 'home' ? home : tabs.find(t => t.key === key);
  // Cmd+Option+Left/Right steps through the home tab and the open tabs in strip order, wrapping at the ends.
  function step(dir) {
    const all = [home, ...tabs];
    activate(all[(all.indexOf(active) + dir + all.length) % all.length]);
  }
  window.addEventListener('keydown', (e) => {
    if (!e.metaKey || !e.altKey || e.ctrlKey || e.shiftKey || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return;
    e.preventDefault();
    step(e.key === 'ArrowLeft' ? -1 : 1);
  });

  bar.addEventListener('click', (e) => {
    const c = e.target.closest('[data-close]');
    if (c) { close(byKey(c.dataset.close)); return; }
    const t = e.target.closest('[data-key]');
    if (t) activate(byKey(t.dataset.key));
  });
  bar.addEventListener('auxclick', (e) => {
    const t = e.button === 1 && e.target.closest('.tab:not(.home)');
    if (t) close(byKey(t.dataset.key));
  });
  window.addEventListener('message', (e) => {
    if (e.origin !== location.origin || !e.data) return;
    const m = e.data;
    if (m.type === 'open') { open(m.url); return; }
    if (m.type === 'step') { step(m.dir === -1 ? -1 : 1); return; }
    if (m.type === 'external') { if (APP) location.href = m.url; else window.open(m.url, '_blank', 'noopener'); return; }
    const tab = tabs.find(t => t.frame && t.frame.contentWindow === e.source);
    if (!tab) return;
    if (m.type === 'badge') { tab.badge = m.n; draw(); save(); }
    if (m.type === 'page') {
      tab.url = m.url; tab.title = m.title;
      const key = keyOf(m.url), twin = tabs.find(t => t !== tab && t.key === key);
      if (twin) { close(tab); activate(twin); return; }
      tab.key = key; draw(); save();
    }
  });
  // The start page picks the theme; it arrives here as a storage event, and the native window follows.
  const applyTheme = (t) => {
    if (t === 'auto') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
    const win = window.__TAURI__ && window.__TAURI__.window;
    if (win) win.getCurrentWindow().setTheme(t === 'auto' ? null : t === 'dark' ? 'dark' : 'light').catch(() => {});
  };
  window.addEventListener('storage', (e) => { if (e.key === 'dr:theme') applyTheme(e.newValue || 'dark'); });

  const saved = lsGet();
  tabs = (saved.tabs || []).filter(t => t && t.url).map(t => ({ key: keyOf(t.url), url: t.url, title: t.title || '…', badge: t.badge || 0, frame: null }));
  activate(byKey(saved.active) || home);
})();
</script></body></html>"""


def tabs_page() -> bytes:
    return TABS.replace("__THEMES__", render.theme_css()).replace("__THEME_BOOT__", render.THEME_BOOT).encode()


def pr_item(p: dict) -> str:
    ref = f'{p["owner"]}/{p["repo"]}#{p["number"]}'
    return (f'<a class="item" href="/{p["owner"]}/{p["repo"]}/pull/{p["number"]}"><span class="t">{html.escape(p["title"])}</span>'
            f'<span class="w">{html.escape(ago(p["opened"]))}</span><span class="m">{html.escape(ref)}</span></a>')


def recent_items() -> str:
    return "".join(map(pr_item, recent_pages())) or '<div class="empty">No pull requests opened yet.</div>'


def start_page() -> bytes:
    """The start page; in the tabbed shell its list is fetched again each time its tab comes to the front."""
    return shell("Pull request review", f"""<h1>Pull request review</h1>
<form action="/open"><input name="pr" placeholder="https://github.com/owner/repo/pull/123 or owner/repo#123" autofocus><button>Open</button></form>
<div class="list" id="recent">{recent_items()}</div>
<script>
document.addEventListener('tab-shown', () => fetch('/recent').then(r => r.text()).then(h => {{ document.getElementById('recent').innerHTML = h; }}).catch(() => {{}}));
</script>""")


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
                return self.send(200, tabs_page())
            if url.path == "/home":
                return self.send(200, start_page())
            if url.path == "/recent":
                return self.send(200, recent_items().encode())
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
                if "refresh" in query:
                    if not build.page_current(build.page_path(*key, current_head(key))):
                        build.forget_pages(*key)
                        start_job(key)
                    return self.redirect(url.path)
                if page := build.latest_page(*key):
                    rebuild_if_moved(key)
                    body = page.read_bytes()
                    note_opened(key, body)
                    return self.send(200, body)
                start_job(key)
                return self.send(200, progress_page(key))
            if (m := API.match(url.path)) and m.group(4) == "comments":
                return self.send_json(comments.fetch(m.group(1), m.group(2), int(m.group(3))))
            return self.send(404, error_page(f"No page at {url.path}"))
        except build.BuildError as exc:
            return self.send(400, error_page(str(exc)))
        except comments.CommentError as exc:
            return self.send_json({"error": str(exc)}, 502)

    def send_json(self, data: dict, code: int = 200) -> None:
        self.send(code, json.dumps(data).encode(), "application/json")

    def do_POST(self) -> None:
        m = API.match(urlparse(self.path).path)
        if not m:
            return self.send(404, error_page(f"No page at {self.path}"))
        owner, repo, number, action = m.group(1), m.group(2), int(m.group(3)), m.group(4)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if action == "comment":
                result = comments.add_thread(owner, repo, number, path=body["path"], body=body["body"], line=int(body["line"]),
                                             side=body.get("side", "RIGHT"), start_line=body.get("start_line"),
                                             start_side=body.get("start_side"), mode=body.get("mode", "review"))
            elif action == "reply":
                result = comments.reply(owner, repo, number, thread_id=body["thread"], body=body["body"], mode=body.get("mode", "review"))
            elif action == "resolve":
                result = comments.resolve(body["thread"], bool(body.get("resolved", True)))
            elif action == "delete":
                result = comments.delete_comment(body["comment"])
            elif action == "submit":
                result = comments.submit(owner, repo, number, event=body["event"], body=body.get("body", ""))
            elif action == "discard":
                result = comments.discard(owner, repo, number)
            elif action == "conversation":
                result = comments.add_conversation_comment(owner, repo, number, body=body["body"])
            else:
                return self.send_json({"error": f"Unknown action {action}"}, 404)
            return self.send_json(result)
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            return self.send_json({"error": f"Bad request: {exc}"}, 400)
        except comments.CommentError as exc:
            return self.send_json({"error": str(exc)}, 502)

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
