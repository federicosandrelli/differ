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
    title = html.unescape(m.group(1).decode(errors="replace")) if m else f"{owner}/{repo}#{number}"
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


def shell_file(name: str) -> str:
    return (render.SHELL_DIR / name).read_text()


def shell(title: str, body: str) -> bytes:
    """A plain document inside the tabbed shell: the start page, and the progress and error pages."""
    return render.fill(shell_file("document.html"), TITLE=html.escape(title), THEME=render.theme_head(),
                       FRAME=render.frame_script(), BODY=body).encode()


def tabs_page() -> bytes:
    """The tabbed shell itself; its code is shell/tabs.js, with shell/frame.js as the page side."""
    return render.fill(shell_file("tabs.html"), THEME=render.theme_head(), SCRIPT=shell_file("tabs.js")).encode()


def pr_item(p: dict) -> str:
    ref = f'{p["owner"]}/{p["repo"]}#{p["number"]}'
    return (f'<a class="item" href="/{p["owner"]}/{p["repo"]}/pull/{p["number"]}"><span class="t">{html.escape(p["title"])}</span>'
            f'<span class="w">{html.escape(ago(p["opened"]))}</span><span class="m">{html.escape(ref)}</span></a>')


def recent_items() -> str:
    return "".join(map(pr_item, recent_pages())) or '<div class="empty">No pull requests opened yet.</div>'


def start_page() -> bytes:
    """The start page; in the tabbed shell its list is fetched again each time its tab comes to the front."""
    return shell("Pull request review", render.fill(shell_file("start.html"), RECENT=recent_items()))


def progress_page(key: tuple[str, str, int]) -> bytes:
    owner, repo, number = key
    ref = f"{owner}/{repo}#{number}"
    body = render.fill(shell_file("progress.html"), REF=html.escape(ref), STATUS=f"/status/{owner}/{repo}/{number}",
                       RETRY=f"/{owner}/{repo}/pull/{number}?refresh=1")
    return shell(f"Building {ref}", body)


def error_page(message: str) -> bytes:
    return shell("Error", render.fill(shell_file("error.html"), MESSAGE=html.escape(message)))


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
