#!/usr/bin/env python3
"""Serve review pages for any GitHub pull request, building each on first visit.

Usage: server.py [port] [--log <path>] [--exit-with-parent]   (default port 8765)

  --log <path>        append the server's stderr to this file; a file past 5 MB starts over at start-up
  --exit-with-parent  exit once the parent process is gone

  /                                  the tabbed shell: the start page as a home tab, and a tab per open page
  /home                              the start page: a search bar and the sections of pull requests, drawn
                                     from GitHub's last answer
  /board?repo=<owner/repo>&cached=1&force=1
                                     the sections of the start page, as an HTML fragment: your pull requests,
                                     other people's, and the ones opened today, after asking GitHub again (at
                                     most every 30 seconds, unless the followed repositories changed or force=1
                                     is given; with force=1, a 502 when GitHub can't answer). With repo, once per
                                     followed repository, only the rows of those repositories; with cached=1, from
                                     GitHub's last answer, without asking again (force=1 then does nothing)
  /settings                          the body of the settings modal, as an HTML fragment: the followed
                                     repositories, the shortcuts, the file type rules, the folds, the editors,
                                     the cache and the version
  /api/followed                      POST with a JSON body {"repos": [...]}: replaces the followed repositories
                                     with the names given, and answers the list saved (refused from another
                                     origin, as the actions below are)
  /<owner>/<repo>/pull/<number>      the review page: the newest built one at once, rebuilt in the
                                     background when the head commit has moved
  /open?pr=<url or owner/repo#n>     redirects to the page above
                                     (the page, /open and /status refuse a request a browser marks
                                     Sec-Fetch-Site: cross-site, so no web page can start a clone or a build)
  /image?src=<link>                  the image a link to a file of a GitHub repository names, read with gh, since
                                     GitHub sends a file of a private repository only to a signed-in browser
                                     (refused cross-site, as the page is)
  /api/link?pr=<url or owner/repo#n> the pull request's name, its repository, the path of its page, whether the
                                     repository is followed and, for a followed one, whether the pull request comes
                                     from a fork (null when unasked or when GitHub can't answer); a 400 for text
                                     that names no pull request (refused cross-site, as the page is)
  /api/<owner>/<repo>/<number>/comments   GET: the review threads, reviews and conversation comments (a merged or
                                     closed pull request loses its comments for Claude here)
  /api/<owner>/<repo>/<number>/viewed     GET: the paths of the files the viewer marked viewed on GitHub
  /api/<owner>/<repo>/<number>/editors    GET: the editors installed (GitHub always is), and the names of all the editors
                                     the app knows
  /api/<owner>/<repo>/<number>/merge      GET: the merge state: the checks, the review decision, whether GitHub can
                                     merge, the auto-merge request and the merge methods allowed
  /api/<owner>/<repo>/<number>/claude     GET: the comments for Claude, each placed on the head commit ?head= names,
                                     whether a Claude Code session listens, and where the embedded session stands
  /api/<owner>/<repo>/<number>/claude_thread?id=<id>   GET: one comment for Claude: its lines, the code it is on,
                                     every message of its thread and `seen`, the number of those messages
  /api/<owner>/<repo>/<number>/listen     a WebSocket: a Claude Code session listens for the comments for Claude,
                                     one session per pull request (refused from another origin)
  /api/<owner>/<repo>/<number>/terminal   a WebSocket: the page's terminal, relayed to the embedded session's
                                     pseudoterminal as binary frames; a text frame sets its size
  /api/<owner>/<repo>/<number>/mcp        POST: the differ MCP server for the embedded session, JSON-RPC with the
                                     pull request's token in the Authorization header
  /api/<owner>/<repo>/<number>/hook       POST: a Claude Code hook of the embedded session, which says whether
                                     Claude asks the reader a question, with the same token as the mcp endpoint
  /vendor/<name>                     a vendored file a page loads on demand: xterm.js and its stylesheet and addon
  /api/<owner>/<repo>/<number>/<action>   POST with a JSON body: a page action (actions.py), such as comment,
                                     merge or claude_open
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
import signal
import socket
import sys
import threading
import time
import traceback
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TextIO
from urllib.parse import parse_qs, urlparse

import actions
import board
import build
import checkout
import claude
import commands
import comments
import editors
import file_types
import followed
import github
import mcp
import merge
import recent
import render
import session
import settings
import websocket
from github import SLUG, PullRef

PAGE = re.compile(rf"^/{SLUG}/pull/(?P<number>\d+)/?$")
STATUS = re.compile(rf"^/status/{SLUG}/(?P<number>\d+)$")
API = re.compile(rf"^/api/{SLUG}/(?P<number>\d+)/(?P<action>\w+)$")
VENDOR = re.compile(r"^/vendor/(?P<name>[\w.-]+)$")
# The vendored files a page loads on demand, with their media types; everything else a page needs is inlined in it.
VENDOR_FILES = {"xterm.js": "text/javascript", "xterm.css": "text/css", "xterm-addon-fit.js": "text/javascript"}
FOLLOWED = "/api/followed"
LINK = "/api/link"
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]")
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
    """The notice under the start page's title and field when gh cannot reach GitHub, or an empty string."""
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
    return render.fill(
        shell_file("tabs.html"),
        THEME=render.theme_head(),
        DEFAULT_MODEL=session.DEFAULT_MODEL,
        SCRIPT=shell_file("tabs.js"),
    ).encode()


def start_page() -> bytes:
    """The start page, with the sections drawn from GitHub's last answer. The page fetches them again from /board when
    it loads and each time its tab comes to the front."""
    return shell(
        "I beg to differ", render.fill(shell_file("start.html"), BANNER=gh_banner(), BOARD=board.sections_html())
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
            if m := VENDOR.match(url.path):
                if not (ctype := VENDOR_FILES.get(m["name"])):
                    return self.send(404, error_page(f"No file at {url.path}"))
                return self.send(200, (render.VENDOR_DIR / m["name"]).read_bytes(), f"{ctype}; charset=utf-8")
            if url.path == "/board":
                if "cached" not in query:
                    threading.Thread(target=session.sweep, daemon=True).start()
                    statuses = threading.Thread(target=recent.refresh)
                    statuses.start()
                    answered = board.refresh(force="force" in query)
                    statuses.join()
                    if "force" in query and not answered:
                        return self.send(502, b"GitHub did not answer", "text/plain; charset=utf-8")
                return self.send(200, board.sections_html(repos_in(query)).encode())
            if url.path == "/settings":
                return self.send(200, settings.settings_html().encode())
            if url.path in ("/open", "/image", LINK) or STATUS.match(url.path) or PAGE.match(url.path):
                if why := self.cross_site():
                    return self.send(403, error_page(f"Forbidden: {why}"))
            if url.path == "/open":
                try:
                    return self.redirect(PullRef.parse((query.get("pr") or [""])[0]).path)
                except ValueError as exc:
                    return self.send(400, error_page(str(exc)))
            if url.path == LINK:
                try:
                    ref = PullRef.parse((query.get("pr") or [""])[0])
                except ValueError as exc:
                    return self.send_json({"error": str(exc)}, 400)
                is_followed, fork = ref.slug in followed.load(), None
                if is_followed:
                    try:
                        fork = checkout.branch_of(ref)[1]
                    except commands.CommandError:
                        pass
                return self.send_json(
                    {"name": str(ref), "repo": ref.slug, "path": ref.path, "followed": is_followed, "fork": fork}
                )
            if url.path == "/image":
                src = (query.get("src") or [""])[0]
                if not (ctype := file_types.support_of(urlparse(src).path).image_type):
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
                ref = PullRef.from_match(m)
                data = comments.fetch(ref)
                if data["status"] in claude.ENDED:
                    session.expire(ref)
                return self.send_json(data)
            if (m := API.match(url.path)) and m["action"] == "viewed":
                return self.send_json({"viewed": comments.viewed(PullRef.from_match(m))})
            if (m := API.match(url.path)) and m["action"] == "merge":
                return self.send_json(merge.fetch(PullRef.from_match(m)))
            if (m := API.match(url.path)) and m["action"] == "claude":
                ref = PullRef.from_match(m)
                data = claude.fetch(ref, (query.get("head") or [""])[0])
                return self.send_json({**data, "session": session.status(ref)})
            if (m := API.match(url.path)) and m["action"] == "claude_thread":
                if not (thread_id := (query.get("id") or [""])[0]):
                    return self.send_json({"error": "Bad request: claude_thread needs ?id=<thread id>"}, 400)
                try:
                    return self.send_json(claude.thread(PullRef.from_match(m), thread_id))
                except claude.NoComment as exc:
                    return self.send_json({"error": str(exc)}, 404)
            if (m := API.match(url.path)) and m["action"] == "listen":
                return self.listen(PullRef.from_match(m))
            if (m := API.match(url.path)) and m["action"] == "terminal":
                return self.terminal(PullRef.from_match(m))
            if (m := API.match(url.path)) and m["action"] == "mcp":
                self.send_response(405)
                self.send_header("Allow", "POST")
                self.send_header("Content-Length", "0")
                return self.end_headers()
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

    def origins(self) -> set[str]:
        """The origins of the pages this server serves."""
        return {f"http://{host}:{self.port()}" for host in LOOPBACK_HOSTS}

    def upgrade(self, from_page: bool) -> websocket.Connection | None:
        """Answer a WebSocket handshake and give the connection to the client, or answer why the request is refused
        and give None.

        A browser sends an Origin header with every handshake, so a page from another origin is refused, as it is
        for a POST. A handshake `from_page` must carry this server's Origin, as a POST must; one from a session's
        client, which sends none, may leave it out.
        """
        origin = self.headers.get("Origin")
        if (origin is not None or from_page) and origin not in self.origins():
            self.send_json({"error": f"Forbidden: Origin {origin or '(missing)'} is not this server"}, 403)
            return None
        key = self.headers.get("Sec-WebSocket-Key")
        if self.headers.get("Upgrade", "").lower() != "websocket" or not key:
            self.send_json({"error": "Bad request: not a WebSocket handshake"}, 400)
            return None
        # WebSocket clients expect an HTTP/1.1 status line, and the handler answers HTTP/1.0 by default.
        self.protocol_version = "HTTP/1.1"
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", websocket.accept_key(key))
        self.end_headers()
        self.close_connection = True
        return websocket.Connection(self.rfile, self.wfile)

    def listen(self, ref: PullRef) -> None:
        """Answer the WebSocket handshake of a Claude Code session that listens for the comments for Claude on the
        pull request, then hold the connection until either side closes it: claude sends each comment as a text
        frame, and this answers pings. A second session gets a close frame with the reason while one listens."""
        if not (ws := self.upgrade(from_page=False)):
            return

        def send_text(text: str) -> None:
            ws.send(websocket.TEXT, text.encode())

        try:
            claude.attach(ref, send_text)
        except claude.Busy as exc:
            ws.close(4000, str(exc))
            return
        try:
            for _ in ws.messages():
                pass
        finally:
            claude.detach(ref, send_text)

    def terminal(self, ref: PullRef) -> None:
        """Answer the WebSocket handshake of the page's terminal, then relay the pull request's session until either
        side closes the connection: what the session prints goes to the page as binary frames, what the reader types
        comes back the same way, and a text frame `{"cols": n, "rows": n}` sets the terminal's size. Only a page of
        this server may connect: typing into the session runs code."""
        if not (ws := self.upgrade(from_page=True)):
            return

        def send_bytes(data: bytes) -> None:
            ws.send(websocket.BINARY, data)

        session.attach(ref, send_bytes)
        try:
            for opcode, payload in ws.messages():
                if opcode == websocket.BINARY:
                    session.type_in(ref, payload)
                elif opcode == websocket.TEXT:
                    size = json.loads(payload)
                    session.resize(ref, int(size["cols"]), int(size["rows"]))
        except (OSError, ValueError, KeyError, TypeError):
            pass
        finally:
            session.detach(ref, send_bytes)

    def post_mcp(self, ref: PullRef) -> None:
        """Answer one JSON-RPC request of the pull request's session, which must carry the pull request's token."""
        if not mcp.authorized(ref, self.headers.get("Authorization")):
            return self.send_json({"error": "Forbidden: not this pull request's token"}, 403)
        try:
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError as exc:
            return self.send_json({"error": f"Bad request: {exc}"}, 400)
        code, body = mcp.handle(ref, request)
        if body is None:
            return self.send(code, b"", "application/json")
        return self.send_json(body, code)

    def post_hook(self, ref: PullRef) -> None:
        """Take a hook of the pull request's session, which must carry the pull request's token, and answer with an
        empty body, which Claude Code reads as a hook that lets the tool go on."""
        if not mcp.authorized(ref, self.headers.get("Authorization")):
            return self.send_json({"error": "Forbidden: not this pull request's token"}, 403)
        try:
            session.hook(ref, self.json_body())
        except ValueError as exc:
            return self.send_json({"error": f"Bad request: {exc}"}, 400)
        return self.send(200, b"", "application/json")

    def refused(self) -> str | None:
        """Why this POST is refused, or None: a page from another origin must not act with the user's gh identity.

        Browsers send an Origin header with every POST, so the Origin must be this server on a loopback host.
        The Content-Type must be application/json, which a cross-origin page can send only after a preflight
        this server never answers.
        """
        origin = self.headers.get("Origin")
        if origin not in self.origins():
            return f"Origin {origin or '(missing)'} is not this server"
        ctype = self.headers.get("Content-Type", "")
        if ctype.split(";")[0].strip().lower() != "application/json":
            return f"Content-Type {ctype or '(missing)'} is not application/json"
        return None

    def do_POST(self) -> None:
        if why := self.wrong_host():
            return self.send(403, error_page(why))
        path = urlparse(self.path).path
        if path == FOLLOWED:
            return self.post_followed()
        m = API.match(path)
        if not m:
            return self.send(404, error_page(f"No page at {self.path}"))
        if m["action"] == "mcp":
            return self.post_mcp(PullRef.from_match(m))
        if m["action"] == "hook":
            return self.post_hook(PullRef.from_match(m))
        if why := self.refused():
            return self.send_json({"error": f"Forbidden: {why}"}, 403)
        try:
            try:
                body = self.json_body()
            except ValueError as exc:
                return self.send_json({"error": f"Bad request: {exc}"}, 400)
            code, result = actions.answer(PullRef.from_match(m), m["action"], body, self.port() or 0)
            return self.send_json(result, code)
        except Exception as exc:
            return self.send_failure(exc, as_json=True)

    def json_body(self) -> dict:
        """The request's JSON object. Raises ValueError when the body is no JSON object."""
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if not isinstance(body, dict):
            raise ValueError("the request is not a JSON object")
        return body

    def post_followed(self) -> None:
        """Replace the followed repositories with the names the body's "repos" gives, and answer the names saved."""
        if why := self.refused():
            return self.send_json({"error": f"Forbidden: {why}"}, 403)
        try:
            repos = self.json_body()["repos"]
            if not isinstance(repos, list) or not all(isinstance(r, str) for r in repos):
                raise ValueError("repos is not a list of strings")
            return self.send_json({"repos": followed.save(repos)})
        except (KeyError, ValueError) as exc:
            return self.send_json({"error": f"Bad request: {exc}"}, 400)
        except followed.NotARepository as exc:
            return self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            return self.send_failure(exc, as_json=True)

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write(f"{self.address_string()} {format % args}\n")


def repos_in(query: dict[str, list[str]]) -> list[str]:
    """The repositories named by a query's `repo` parameters, in lower case, as `followed.json` names them."""
    return [name.strip().lower() for name in query.get("repo", [])]


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
    """Exit once the parent process is gone, checked once a second, after ending the Claude sessions."""
    parent = os.getppid()
    while os.getppid() == parent:
        time.sleep(1)
    session.stop_all()
    os._exit(0)


def exit_on_signal(_signum: int, _frame: object) -> None:
    """End the Claude sessions and exit: the desktop app, or the user, ended the server."""
    session.stop_all()
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
    signal.signal(signal.SIGTERM, exit_on_signal)
    signal.signal(signal.SIGINT, exit_on_signal)
    build.prune_all()
    claude.install_skills()
    session.reap_orphans()
    threading.Thread(target=session.sweep, daemon=True).start()
    if problem := gh_problem():
        sys.stderr.write(f"{problem} Run `gh auth login`; the start page checks again once a minute.\n")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Serving pull request reviews on http://localhost:{port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
