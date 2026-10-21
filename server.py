#!/usr/bin/env python3
"""Serve review pages for any GitHub pull request, building each on first visit.

Usage: server.py [port] [--log <path>] [--exit-with-parent]   (default port 8765)

  --log <path>        append the server's stderr to this file; a file past 5 MB starts over at start-up
  --exit-with-parent  exit once the parent process is gone

  /                                  the tabbed shell: the start page as a home tab, and a tab per open page
  /home                              the start page: a search bar and the pull requests opened last, in
                                     three sections of up to twenty: yours, other people's, and merged
  /recent                            the list of pull requests opened last, as an HTML fragment, with each
                                     status and author fetched from GitHub
  /<owner>/<repo>/pull/<number>      the review page: the newest built one at once, rebuilt in the
                                     background when the head commit has moved
  /open?pr=<url or owner/repo#n>     redirects to the page above
                                     (the page, /open and /status refuse a request a browser marks
                                     Sec-Fetch-Site: cross-site, so no web page can start a clone or a build)
  /image?src=<link>                  the image a link to a file of a GitHub repository names, read with gh, since
                                     GitHub sends a file of a private repository only to a signed-in browser
                                     (refused cross-site, as the page is)
  /api/<owner>/<repo>/<number>/comments   GET: the review threads, reviews and conversation comments
  /api/<owner>/<repo>/<number>/editors    GET: the editors installed (GitHub always is), and the names of all the editors
                                     the app knows
  /api/<owner>/<repo>/<number>/<action>   POST with a JSON body: comment, reply, resolve, delete, submit, discard,
                                     conversation, open (opens a file in an editor, on the pull request's branch)
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
import sys
import threading
import time
import traceback
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, TextIO, get_args
from urllib.parse import parse_qs, urlparse

import build
import commands
import comments
import editors
import github
import recent
import render
from github import SLUG, PullRef

PAGE = re.compile(rf"^/{SLUG}/pull/(?P<number>\d+)/?$")
STATUS = re.compile(rf"^/status/{SLUG}/(?P<number>\d+)$")
API = re.compile(rf"^/api/{SLUG}/(?P<number>\d+)/(?P<action>\w+)$")
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]")
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
}
# An SVG file can hold scripts, and the reader may open an image's address on its own.
IMAGE_POLICY = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
GH_TTL = 60
LOG_MAX = 5 * 1024 * 1024


gh_checked: tuple[float, str | None] = (0.0, None)
lock = threading.Lock()


def gh_problem() -> str | None:
    """Why gh cannot reach GitHub, or None. The answer from the last check serves for a minute, so signing in
    shows on the start page within a minute, without a restart."""
    global gh_checked
    with lock:
        checked, problem = gh_checked
    if time.time() - checked < GH_TTL:
        return problem
    problem = github.auth_problem()
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


def shell_file(name: str) -> str:
    return (render.SHELL_DIR / name).read_text()


def shell(title: str, body: str) -> bytes:
    """A plain document inside the tabbed shell: the start page, and the progress and error pages."""
    return render.fill(
        shell_file("document.html"),
        TITLE=html.escape(title),
        THEME=render.theme_head(),
        FRAME=shell_file("frame.js"),
        BODY=body,
    ).encode()


def tabs_page() -> bytes:
    """The tabbed shell itself; its code is shell/tabs.js, with shell/frame.js as the page side."""
    return render.fill(shell_file("tabs.html"), THEME=render.theme_head(), SCRIPT=shell_file("tabs.js")).encode()


def start_page() -> bytes:
    """The start page, with the statuses and authors saved last. Its list, with each status and author fetched
    from GitHub, is fetched again when the page loads and each time its tab comes to the front."""
    return shell(
        "I beg to differ", render.fill(shell_file("start.html"), BANNER=gh_banner(), RECENT=recent.items_html())
    )


def progress_page(ref: PullRef) -> bytes:
    body = render.fill(
        shell_file("progress.html"),
        REF=html.escape(str(ref)),
        STATUS=f"/status/{ref.slug}/{ref.number}",
        RETRY=f"{ref.path}?refresh=1",
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

    def send_image(self, body: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("Content-Security-Policy", IMAGE_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
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
                recent.refresh()
                return self.send(200, recent.items_html().encode())
            if url.path in ("/open", "/image") or STATUS.match(url.path) or PAGE.match(url.path):
                if why := self.cross_site():
                    return self.send(403, error_page(f"Forbidden: {why}"))
            if url.path == "/open":
                try:
                    return self.redirect(PullRef.parse((query.get("pr") or [""])[0]).path)
                except ValueError as exc:
                    return self.send(400, error_page(str(exc)))
            if url.path == "/image":
                src = (query.get("src") or [""])[0]
                if not (ctype := IMAGE_TYPES.get(Path(urlparse(src).path).suffix.lower())):
                    return self.send(404, error_page(f"{src} is not an image"))
                try:
                    return self.send_image(github.repo_file(src), ctype)
                except ValueError as exc:
                    return self.send(404, error_page(str(exc)))
            if m := STATUS.match(url.path):
                job = build.job_of(PullRef.from_match(m))
                return self.send_json(asdict(job) if job else {"state": "none", "log": [], "error": ""})
            if m := PAGE.match(url.path):
                if url.path != url.path.lower():
                    return self.redirect(url.path.lower() + (f"?{url.query}" if url.query else ""))
                ref = PullRef.from_match(m)
                if "refresh" in query:
                    build.refresh(ref)
                    return self.redirect(url.path)
                editors.prepare(ref)
                if page := build.serve(ref):
                    body = page.read_bytes()
                    recent.note_opened(ref, *render.read_head(body))
                    return self.send(200, body)
                return self.send(200, progress_page(ref))
            if (m := API.match(url.path)) and m["action"] == "comments":
                return self.send_json(comments.fetch(PullRef.from_match(m)))
            if (m := API.match(url.path)) and m["action"] == "editors":
                return self.send_json(
                    {
                        "installed": [{"id": e.id, "name": e.name, "local": e.local} for e in editors.installed()],
                        "known": [e.name for e in editors.known()],
                    }
                )
            return self.send(404, error_page(f"No page at {url.path}"))
        except commands.CommandError as exc:
            return self.send_message(502, str(exc), as_json=API.match(url.path) is not None)
        except Exception as exc:
            return self.send_failure(exc, as_json=API.match(url.path) is not None)

    def send_json(self, data: dict, code: int = 200) -> None:
        self.send(code, json.dumps(data).encode(), "application/json")

    def send_message(self, code: int, message: str, as_json: bool) -> None:
        """Answer with a message for the reader: as JSON to the page's API calls, else as the error page."""
        if as_json:
            self.send_json({"error": message}, code)
        else:
            self.send(code, error_page(message))

    def cross_site(self) -> str | None:
        """Why this GET is refused, or None: a page on another site must not start a clone or a build with the
        user's gh identity, which a GET to a page path or to /open does, nor read a build's progress at /status, nor
        load a file of a private repository through /image.

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
        try:
            self.send_message(500, f"Unexpected error: {type(exc).__name__}: {exc}", as_json)
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
        ref, action = PullRef.from_match(m), m["action"]
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("the request is not a JSON object")
            if action == "comment":
                start_side = body.get("start_side")
                result = comments.add_thread(
                    ref,
                    path=body["path"],
                    body=body["body"],
                    line=line_number(body["line"]),
                    side=one_of(body.get("side", "RIGHT"), comments.Side),
                    start_line=None if body.get("start_line") is None else line_number(body["start_line"]),
                    start_side=None if start_side is None else one_of(start_side, comments.Side),
                    mode=one_of(body.get("mode", "review"), comments.Mode),
                )
            elif action == "reply":
                mode = one_of(body.get("mode", "review"), comments.Mode)
                result = comments.reply(ref, thread_id=body["thread"], body=body["body"], mode=mode)
            elif action == "resolve":
                result = comments.resolve(body["thread"], bool(body.get("resolved", True)))
            elif action == "delete":
                result = comments.delete_comment(body["comment"])
            elif action == "submit":
                result = comments.submit(ref, event=one_of(body["event"], comments.Event), body=body.get("body", ""))
            elif action == "discard":
                result = comments.discard(ref)
            elif action == "conversation":
                result = comments.add_conversation_comment(ref, body=body["body"])
            elif action == "open":
                editors.open_file(ref, str(body["editor"]), str(body["path"]), line_number(body["line"]))
                result = {}
            else:
                return self.send_json({"error": f"Unknown action {action}"}, 404)
            return self.send_json(result)
        except (KeyError, ValueError) as exc:
            return self.send_json({"error": f"Bad request: {exc}"}, 400)
        except (comments.ReviewRefused, editors.Refused) as exc:
            return self.send_json({"error": str(exc)}, 400)
        except commands.CommandError as exc:
            return self.send_json({"error": str(exc)}, 502)
        except Exception as exc:
            return self.send_failure(exc, as_json=True)

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write(f"{self.address_string()} {format % args}\n")


def one_of(value: object, kind: object) -> Any:
    """The value of a request's field when the Literal type allows it; else a ValueError, which answers 400."""
    if value not in get_args(kind):
        raise ValueError(f"{value!r} is not one of {', '.join(map(repr, get_args(kind)))}")
    return value


def line_number(value: object) -> int:
    """The line number a request's field names; else a ValueError, which answers 400."""
    if type(value) is not int or value < 1:
        raise ValueError(f"{value!r} is not a line number")
    return value


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
