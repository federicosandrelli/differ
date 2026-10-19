"""Open a pull request's files in the user's editor: the editors the app knows, and the clone of each repository,
with file contents, that they open on the pull request's branch."""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import build
import commands
import github
from github import PullRef

APP_FOLDERS = (Path("/Applications"), Path.home() / "Applications")
JETBRAINS_CONFIG = Path.home() / "Library/Application Support/JetBrains"


def oldest(command: Path) -> int | None:
    """The oldest process that runs the command line tool at this path, or None. For a JetBrains IDE that is the IDE
    itself, since a launcher that finds the IDE running hands its request over and exits."""
    pattern = "^" + re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", str(command))
    pid = commands.call(["pgrep", "-o", "-f", pattern]).stdout.strip()
    return int(pid) if pid else None


@dataclass(frozen=True)
class Editor:
    """An editor the page can open a file in. The server starts its command line tool by its full path inside the
    app bundle, since the desktop app starts the server with a PATH that may not hold the tool."""

    id: str
    name: str
    apps: tuple[str, ...]
    tool: str
    configs: tuple[str, ...] = ()

    def command(self) -> Path | None:
        """The command line tool of the first of the editor's app bundles installed, or None."""
        for folder in APP_FOLDERS:
            for app in self.apps:
                if (path := folder / app / self.tool).exists():
                    return path
        return None

    def args(self, command: Path, folder: Path, file: Path, line: int) -> list[str]:
        """The command that opens the folder in a new window of the editor, at the line of the file. The JetBrains
        launcher has no option for a new window; the IDE's "Open project in" setting decides. It also takes about
        two seconds to hand its request to a running IDE, so a file of a project the IDE has open goes through the
        IDE's URL scheme instead, which opens it at once."""
        if not self.configs:
            return [str(command), "-n", str(folder), "-g", f"{file}:{line}"]
        if self.has_open(folder) and oldest(command):
            return ["open", f"{Path(self.tool).name}://open?file={quote(str(file))}&line={line}"]
        return [str(command), str(folder), "--line", str(line), str(file)]

    def has_open(self, folder: Path) -> bool:
        """Whether the JetBrains IDE had the folder open as a project when it last saved its list of recent projects,
        which it keeps after it quits. Its configuration folders start with one of `configs`, followed by the
        version."""
        for recent in JETBRAINS_CONFIG.glob("*/options/recentProjects.xml"):
            if not recent.parent.parent.name.startswith(self.configs):
                continue
            try:
                entries = ET.parse(recent).iter("entry")
            except (OSError, ET.ParseError):
                continue
            for entry in entries:
                key = entry.get("key", "").replace("$USER_HOME$", str(Path.home()))
                if key == str(folder) and entry.find(".//RecentProjectMetaInfo[@opened='true']") is not None:
                    return True
        return False


EDITORS = {
    e.id: e
    for e in (
        Editor("vscode", "VS Code", ("Visual Studio Code.app",), "Contents/Resources/app/bin/code"),
        Editor("cursor", "Cursor", ("Cursor.app",), "Contents/Resources/app/bin/cursor"),
        Editor(
            "pycharm",
            "PyCharm",
            ("PyCharm.app", "PyCharm CE.app", "PyCharm Professional Edition.app", "PyCharm Community Edition.app"),
            "Contents/MacOS/pycharm",
            configs=("PyCharm",),
        ),
        Editor(
            "idea",
            "IntelliJ IDEA",
            ("IntelliJ IDEA.app", "IntelliJ IDEA CE.app", "IntelliJ IDEA Ultimate.app"),
            "Contents/MacOS/idea",
            configs=("IntelliJIdea", "IdeaIC"),
        ),
        Editor("webstorm", "WebStorm", ("WebStorm.app",), "Contents/MacOS/webstorm", configs=("WebStorm",)),
        Editor("goland", "GoLand", ("GoLand.app",), "Contents/MacOS/goland", configs=("GoLand",)),
    )
}


class Refused(Exception):
    """The server won't open the file now; the message says why and what to do."""


def installed() -> list[Editor]:
    return [e for e in EDITORS.values() if e.command()]


def checkout_dir(ref: PullRef) -> Path:
    return build.CACHE / "checkouts" / ref.owner / ref.repo


locks: dict[str, threading.Lock] = {}
locks_lock = threading.Lock()


def repo_lock(ref: PullRef) -> threading.Lock:
    """The lock that keeps one clone or checkout at a time in the repository's clone."""
    with locks_lock:
        return locks.setdefault(ref.slug, threading.Lock())


launches: dict[str, tuple[Path, subprocess.Popen[bytes]]] = {}
opened: dict[str, tuple[int, str]] = {}


def still_opening(launch: tuple[Path, subprocess.Popen[bytes]] | None) -> bool:
    """Whether the editor started last for a repository is still handing its request to an editor that was already
    running. A JetBrains launcher becomes the IDE when none runs; otherwise it waits until the running IDE opens the
    project, which can wait on a dialog, and a second request for the same project meanwhile makes the IDE fail."""
    if launch is None or launch[1].poll() is not None:
        return False
    command, process = launch
    return oldest(command) not in (None, process.pid)


def clone(ref: PullRef) -> None:
    """Clone the repository, unless its clone exists. The clone goes to its folder only once it is complete, so a
    failed one starts over. A GitHub name never holds "~", so the temporary folder is never another repository's
    clone."""
    folder = checkout_dir(ref)
    if folder.exists():
        return
    partial = folder.with_name(f"{folder.name}~partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.parent.mkdir(parents=True, exist_ok=True)
    github.clone_with_files(ref, partial)
    partial.rename(folder)


@functools.cache
def head_branch(ref: PullRef) -> tuple[str, bool]:
    """The pull request's head branch and whether it lives in a fork, read from GitHub once, since neither changes."""
    return github.head_branch(ref)


def prepare(ref: PullRef) -> None:
    """When an editor is installed, clone the repository and read the pull request's branch in the background, so
    that the first open waits for neither."""
    if not installed():
        return

    def run() -> None:
        try:
            with repo_lock(ref):
                clone(ref)
            head_branch(ref)
        except commands.CommandError as exc:
            sys.stderr.write(f"preparing the clone of {ref.slug} failed: {exc}\n")

    threading.Thread(target=run, daemon=True).start()


def current_branch(folder: Path) -> str:
    return build.git(folder, "branch", "--show-current").decode().strip()


def up_to_date(ref: PullRef, folder: Path) -> bool:
    """Whether the clone is still on the branch the last open checked out for this pull request, at the pull
    request's head on GitHub, so that opening it needs no checkout."""
    number, branch = opened.get(ref.slug, (0, ""))
    return (
        number == ref.number
        and branch == current_branch(folder)
        and build.git(folder, "rev-parse", "HEAD").decode().strip() == build.recent_head(ref)
    )


def open_file(ref: PullRef, editor_id: str, path: str, line: int) -> None:
    """Open the repository's clone in a new window of the editor, on the pull request's branch, at the line of the
    file. Clones the repository the first time. Never loses uncommitted changes or commits missing from every remote.
    Raises ValueError for an editor that is not installed or a path outside the repository, Refused when the clone
    has uncommitted changes to tracked files on another pull request's branch or while the editor started last for
    the repository is still opening, and CommandError when gh or git fails.
    Returns once the editor starts, without waiting for it."""
    editor = EDITORS.get(editor_id)
    command = editor and editor.command()
    if not editor or not command:
        names = ", ".join(repr(e.id) for e in installed()) or "none"
        raise ValueError(f"{editor_id!r} is not an installed editor (installed: {names})")
    relative = PurePosixPath(path)
    if not path or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{path!r} is not a path inside the repository")
    folder = checkout_dir(ref)
    with repo_lock(ref):
        if still_opening(launches.get(ref.slug)):
            raise Refused("The editor is still opening the last file from this repository. Answer its window first.")
        clone(ref)
        if not up_to_date(ref, folder):
            check_out(ref, folder)
            opened[ref.slug] = (ref.number, current_branch(folder))
        launches[ref.slug] = (command, commands.start(editor.args(command, folder, folder / relative, line)))


def check_out(ref: PullRef, folder: Path) -> None:
    """Check out the pull request's branch in the clone; see open_file."""
    changed = build.git(folder, "status", "--porcelain", "--untracked-files=no").strip()
    if changed and github.branch_pull_request(folder) != ref.number:
        raise Refused(
            f"The clone at {folder} has uncommitted changes on {current_branch(folder) or 'a detached HEAD'}. "
            "Commit or stash them first."
        )
    branch, fork = head_branch(ref)
    try:
        github.checkout(ref, folder)
    except commands.CommandError:
        # A force-pushed branch can't fast-forward. When the local branch diverged from the remote one and holds
        # no commit missing from every remote and from every head the remote branch had, checkout -B moves it to
        # the new head; it refuses rather than overwrite an untracked file or an uncommitted change, and the
        # branch keeps its upstream.
        local, remote = f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"
        is_ancestor = ["git", "-C", str(folder), "merge-base", "--is-ancestor", local, remote]
        if fork or commands.call(is_ancestor).returncode != 1:
            raise
        heads = build.git(folder, "log", "-g", "--format=%H", remote).decode().split()
        # The reflog lists each new head; the head the clone started from is the old value of the oldest entry.
        first = commands.call(
            ["git", "-C", str(folder), "rev-parse", "--verify", "--quiet", f"{remote}@{{{len(heads)}}}"]
        )
        heads += first.stdout.decode().split()
        if int(build.git(folder, "rev-list", "--count", local, "--not", *heads, "--remotes")):
            raise
        build.git(folder, "checkout", "-B", branch, remote)
