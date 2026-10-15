#!/usr/bin/env python3
"""Serve review pages for any GitHub pull request, building each on first visit.

Usage: server.py [port] [--log <path>] [--exit-with-parent]   (default port 8765)

  --log <path>        append the server's stderr to this file; a file past 5 MB starts over at start-up
  --exit-with-parent  exit once the parent process is gone

  /                                  the tabbed shell: the start page as a home tab, and a tab per open page
  /home                              the start page: a search bar and the ten pull requests opened last
  /recent                            the list of pull requests opened last, as an HTML fragment, with each
                                     status fetched from GitHub
  /<owner>/<repo>/pull/<number>      the review page: the newest built one at once, rebuilt in the
                                     background when the head commit has moved
  /open?pr=<url or owner/repo#n>     redirects to the page above
                                     (the page, /open and /status refuse a request a browser marks
                                     Sec-Fetch-Site: cross-site, so no web page can start a clone or a build)
  /api/<owner>/<repo>/<number>/comments   GET: the review threads, reviews and conversation comments
  /api/<owner>/<repo>/<number>/<action>   POST with a JSON body: comment, reply, resolve, delete, submit, discard, conversation
                                     (only from a page this server served: the Origin must be this server
                                     on a loopback host, and the Content-Type application/json)

Every request must name this server on a loopback host in its Host header; any other Host gets a 403.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TextIO
from urllib.parse import parse_qs, urlparse

import build
import comments
import render

PAGE = re.compile(rf"^/({build.OWNER})/({build.REPO})/pull/(\d+)/?$")
STATUS = re.compile(rf"^/status/({build.OWNER})/({build.REPO})/(\d+)$")
API = re.compile(rf"^/api/({build.OWNER})/({build.REPO})/(\d+)/(\w+)$")
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]")
TITLE = re.compile(rb"<title>#\d+ (.*?)</title>", re.S)
PR_STATUS = re.compile(rb'<meta name="pr-status" content="(\w*)">')
HEAD_TTL = 20
RECENT = build.CACHE / "recent.json"
RECENT_MAX = 10
GH_TTL = 60
GH_TIMEOUT = 10
LOG_MAX = 5 * 1024 * 1024


class Job:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.state = "building"
        self.error = ""


jobs: dict[tuple[str, str, int], Job] = {}
heads: dict[tuple[str, str, int], tuple[float, str]] = {}
gh_checked: tuple[float, str | None] = (0.0, None)
lock = threading.Lock()
refreshing = threading.Lock()


def page_key(m: re.Match) -> tuple[str, str, int]:
    """The owner, repo and number a route names, with owner and repo lowercased as build.parse_ref does."""
    return m.group(1).lower(), m.group(2).lower(), int(m.group(3))


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
    """The pull requests opened last, newest first: owner, repo, number, title, status and the time of the last
    open."""
    try:
        return json.loads(RECENT.read_text())
    except (OSError, ValueError):
        return []


def note_opened(key: tuple[str, str, int], page: bytes) -> None:
    """Move the pull request to the top of the recently opened list, with the title and status of the page just
    served."""
    owner, repo, number = key
    m = TITLE.search(page[:4096])
    title = html.unescape(m.group(1).decode(errors="replace")) if m else f"{owner}/{repo}#{number}"
    s = PR_STATUS.search(page[:4096])
    status = s.group(1).decode() if s else ""
    with lock:
        rest = [p for p in recent_pages() if (p["owner"], p["repo"], p["number"]) != key]
        entry = {
            "owner": owner,
            "repo": repo,
            "number": number,
            "title": title,
            "status": status,
            "opened": time.time(),
        }
        save_recent([entry, *rest][:RECENT_MAX])


def save_recent(pages: list[dict]) -> None:
    """Replace recent.json in one step, so a reader sees the old list or the new one and never part of one. The
    list goes to recent.json.tmp first and is renamed over recent.json, so a crash mid-write leaves the old list."""
    RECENT.parent.mkdir(parents=True, exist_ok=True)
    tmp = RECENT.with_name(RECENT.name + ".tmp")
    tmp.write_text(json.dumps(pages))
    os.replace(tmp, RECENT)


def refresh_statuses() -> None:
    """Save the status GitHub reports now for each pull request in the recently opened list. A pull request
    GitHub can't return keeps the status saved before. While one refresh runs, another returns at once."""
    if not refreshing.acquire(blocking=False):
        return
    try:
        live = build.pr_statuses([(p["owner"], p["repo"], p["number"]) for p in recent_pages()])
        with lock:
            pages = recent_pages()
            updated = [
                p | {"status": live.get((p["owner"], p["repo"], p["number"]), p.get("status", ""))} for p in pages
            ]
            if updated != pages:
                save_recent(updated)
    finally:
        refreshing.release()


def check_gh() -> str | None:
    """Why gh cannot reach GitHub now, or None when `gh auth status` succeeds."""
    try:
        r = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=GH_TIMEOUT)
    except FileNotFoundError:
        return "The GitHub CLI (gh) is not installed."
    except subprocess.TimeoutExpired:
        return f"`gh auth status` gave no answer in {GH_TIMEOUT} seconds."
    if r.returncode != 0:
        return "The GitHub CLI (gh) is not signed in."
    return None


def gh_problem() -> str | None:
    """Why gh cannot reach GitHub, or None. The answer from the last check serves for a minute, so signing in
    shows on the start page within a minute, without a restart."""
    global gh_checked
    with lock:
        checked, problem = gh_checked
    if time.time() - checked < GH_TTL:
        return problem
    problem = check_gh()
    with lock:
        gh_checked = (time.time(), problem)
    return problem


def gh_banner() -> str:
    """The notice above the start page's search bar when gh cannot reach GitHub, or an empty string."""
    problem = gh_problem()
    if problem is None:
        return ""
    return (
        f'<div class="banner">{html.escape(problem)} Run <code>gh auth login</code> in a terminal, '
        "then reload this page.</div>"
    )


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
    return render.fill(
        shell_file("document.html"),
        TITLE=html.escape(title),
        THEME=render.theme_head(),
        FRAME=render.frame_script(),
        BODY=body,
    ).encode()


def tabs_page() -> bytes:
    """The tabbed shell itself; its code is shell/tabs.js, with shell/frame.js as the page side."""
    return render.fill(shell_file("tabs.html"), THEME=render.theme_head(), SCRIPT=shell_file("tabs.js")).encode()


def pr_item(p: dict) -> str:
    ref = f"{p['owner']}/{p['repo']}#{p['number']}"
    return (
        f'<a class="item" href="/{p["owner"]}/{p["repo"]}/pull/{p["number"]}"><span class="t">{render.pr_icon(p.get("status", ""))}{html.escape(p["title"])}</span>'
        f'<span class="w">{html.escape(ago(p["opened"]))}</span><span class="m">{html.escape(ref)}</span></a>'
    )


def recent_items() -> str:
    return "".join(map(pr_item, recent_pages())) or '<div class="empty">No pull requests opened yet.</div>'


def start_page() -> bytes:
    """The start page, with the statuses saved last. Its list, with each status fetched from GitHub, is fetched
    again when the page loads and each time its tab comes to the front."""
    return shell("I beg to differ", render.fill(shell_file("start.html"), BANNER=gh_banner(), RECENT=recent_items()))


def progress_page(key: tuple[str, str, int]) -> bytes:
    owner, repo, number = key
    ref = f"{owner}/{repo}#{number}"
    body = render.fill(
        shell_file("progress.html"),
        REF=html.escape(ref),
        STATUS=f"/status/{owner}/{repo}/{number}",
        RETRY=f"/{owner}/{repo}/pull/{number}?refresh=1",
    )
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

    def port(self) -> int | None:
        address = self.server.server_address
        return address[1] if isinstance(address, tuple) else None

    def wrong_host(self) -> str | None:
        """Why this request is refused, or None: the Host must be this server on a loopback host.

        A browser sends the name it resolved, so a site whose name DNS points at this server (DNS rebinding)
        cannot read a page or act with the user's gh identity through it.
        """
        host = self.headers.get("Host")
        if host not in {f"{name}:{self.port()}" for name in LOOPBACK_HOSTS}:
            return f"Host {host or '(missing)'} is not this server"
        return None

    def do_GET(self) -> None:
        if why := self.wrong_host():
            return self.send(403, error_page(why))
        url = urlparse(self.path)
        query = parse_qs(url.query)
        try:
            if url.path == "/":
                return self.send(200, tabs_page())
            if url.path == "/home":
                return self.send(200, start_page())
            if url.path == "/recent":
                refresh_statuses()
                return self.send(200, recent_items().encode())
            if url.path == "/open" or STATUS.match(url.path) or PAGE.match(url.path):
                if why := self.cross_site():
                    return self.send(403, error_page(f"Forbidden: {why}"))
            if url.path == "/open":
                owner, repo, number = build.parse_ref((query.get("pr") or [""])[0])
                return self.redirect(f"/{owner}/{repo}/pull/{number}")
            if m := STATUS.match(url.path):
                with lock:
                    job = jobs.get(page_key(m))
                data = (
                    {"state": job.state, "log": job.log, "error": job.error}
                    if job
                    else {"state": "none", "log": [], "error": ""}
                )
                return self.send(200, json.dumps(data).encode(), "application/json")
            if m := PAGE.match(url.path):
                if url.path != url.path.lower():
                    return self.redirect(url.path.lower() + (f"?{url.query}" if url.query else ""))
                key = page_key(m)
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
                return self.send_json(comments.fetch(*page_key(m)))
            return self.send(404, error_page(f"No page at {url.path}"))
        except build.BuildError as exc:
            return self.send(400, error_page(str(exc)))
        except comments.CommentError as exc:
            return self.send_json({"error": str(exc)}, 502)
        except Exception as exc:
            return self.send_failure(exc, as_json=API.match(url.path) is not None)

    def send_json(self, data: dict, code: int = 200) -> None:
        self.send(code, json.dumps(data).encode(), "application/json")

    def cross_site(self) -> str | None:
        """Why this GET is refused, or None: a page on another site must not start a clone or a build with the
        user's gh identity, which a GET to a page path or to /open does, nor read a build's progress at /status.

        Browsers send Sec-Fetch-Site with every request: same-origin, same-site, none (a typed address or a
        bookmark) or cross-site. A request without the header (an old browser, curl) passes.
        """
        if self.headers.get("Sec-Fetch-Site", "").strip().lower() == "cross-site":
            return "the request comes from another site (Sec-Fetch-Site: cross-site)"
        return None

    def send_failure(self, exc: Exception, as_json: bool) -> None:
        """Answer 500 for an error no route expected, with its traceback on stderr. A client that has already
        gone away gets no answer."""
        traceback.print_exc(file=sys.stderr)
        message = f"Unexpected error: {type(exc).__name__}: {exc}"
        try:
            if as_json:
                self.send_json({"error": message}, 500)
            else:
                self.send(500, error_page(message))
        except OSError:
            pass

    def refused(self) -> str | None:
        """Why this POST is refused, or None: a page from another origin must not act with the user's gh identity.

        Browsers send an Origin header with every POST, so the Origin must be this server on a loopback host.
        The Content-Type must be application/json, which a cross-origin page can send only after a preflight
        this server never answers.
        """
        origin = self.headers.get("Origin")
        if origin not in {f"http://{host}:{self.port()}" for host in LOOPBACK_HOSTS}:
            return f"Origin {origin or '(missing)'} is not this server"
        ctype = self.headers.get("Content-Type", "")
        if ctype.split(";")[0].strip().lower() != "application/json":
            return f"Content-Type {ctype or '(missing)'} is not application/json"
        return None

    def do_POST(self) -> None:
        if why := self.wrong_host():
            return self.send(403, error_page(why))
        m = API.match(urlparse(self.path).path)
        if not m:
            return self.send(404, error_page(f"No page at {self.path}"))
        if why := self.refused():
            return self.send_json({"error": f"Forbidden: {why}"}, 403)
        (owner, repo, number), action = page_key(m), m.group(4)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if action == "comment":
                result = comments.add_thread(
                    owner,
                    repo,
                    number,
                    path=body["path"],
                    body=body["body"],
                    line=int(body["line"]),
                    side=body.get("side", "RIGHT"),
                    start_line=body.get("start_line"),
                    start_side=body.get("start_side"),
                    mode=body.get("mode", "review"),
                )
            elif action == "reply":
                result = comments.reply(
                    owner, repo, number, thread_id=body["thread"], body=body["body"], mode=body.get("mode", "review")
                )
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
        except Exception as exc:
            return self.send_failure(exc, as_json=True)

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write(f"{self.address_string()} {format % args}\n")


def port_taken(port: int) -> str | None:
    """The address something already answers on, IPv4 or IPv6; browsers try either for localhost. A family the
    host does not support has nothing answering on it."""
    for family, host in ((socket.AF_INET6, "::1"), (socket.AF_INET, "127.0.0.1")):
        try:
            probe = socket.socket(family, socket.SOCK_STREAM)
        except OSError:
            continue
        with probe:
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


def open_log(path: Path) -> TextIO:
    """The log file at path, opened for appending, or started over when it already holds more than LOG_MAX bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if path.exists() and path.stat().st_size > LOG_MAX else "a"
    return path.open(mode, buffering=1)


def log_to(path: Path) -> None:
    """Send this process's stderr, and the stderr of the gh and git commands it runs, to the file at path."""
    log = open_log(path)
    sys.stderr.flush()
    os.dup2(log.fileno(), sys.stderr.fileno())


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve review pages for any GitHub pull request.")
    parser.add_argument("port", nargs="?", type=int, default=8765)
    parser.add_argument("--log", type=Path, help="append the server's stderr to this file")
    parser.add_argument("--exit-with-parent", action="store_true", help="exit once the parent process is gone")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args(sys.argv[1:])
    if args.log:
        log_to(args.log)
    port = args.port
    if taken := port_taken(port):
        sys.exit(f"Port {port} is already in use on {taken}. Stop that process or pass another port: server.py <port>")
    if args.exit_with_parent:
        threading.Thread(target=exit_with_parent, daemon=True).start()
    build.prune_all()
    if problem := gh_problem():
        sys.stderr.write(f"{problem} Run `gh auth login`; the start page checks again once a minute.\n")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Serving pull request reviews on http://localhost:{port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
