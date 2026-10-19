"""Open a pull request's files in the user's editor: the editors the app knows, and the clone of each repository,
with file contents, that they open on the pull request's branch."""

from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import build
import commands
import github
from github import PullRef

APP_FOLDERS = (Path("/Applications"), Path.home() / "Applications")


@dataclass(frozen=True)
class Editor:
    """An editor the page can open a file in. The server starts its command line tool by its full path inside the
    app bundle, since the desktop app starts the server with a PATH that may not hold the tool."""

    id: str
    name: str
    apps: tuple[str, ...]
    tool: str
    jetbrains: bool = False

    def command(self) -> Path | None:
        """The command line tool of the first of the editor's app bundles installed, or None."""
        for folder in APP_FOLDERS:
            for app in self.apps:
                if (path := folder / app / self.tool).exists():
                    return path
        return None

    def args(self, command: Path, folder: Path, file: Path, line: int) -> list[str]:
        """The command that opens the folder in a new window of the editor, at the line of the file. The JetBrains
        launcher has no option for a new window; the IDE's "Open project in" setting decides."""
        if self.jetbrains:
            return [str(command), str(folder), "--line", str(line), str(file)]
        return [str(command), "-n", str(folder), "-g", f"{file}:{line}"]


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
            jetbrains=True,
        ),
        Editor(
            "idea",
            "IntelliJ IDEA",
            ("IntelliJ IDEA.app", "IntelliJ IDEA CE.app", "IntelliJ IDEA Ultimate.app"),
            "Contents/MacOS/idea",
            jetbrains=True,
        ),
        Editor("webstorm", "WebStorm", ("WebStorm.app",), "Contents/MacOS/webstorm", jetbrains=True),
        Editor("goland", "GoLand", ("GoLand.app",), "Contents/MacOS/goland", jetbrains=True),
    )
}


class Refused(Exception):
    """The clone can't switch to the pull request's branch; the message says why and what to do."""


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


def open_file(ref: PullRef, editor_id: str, path: str, line: int) -> None:
    """Open the repository's clone in a new window of the editor, on the pull request's branch, at the line of the
    file. Clones the repository the first time. Never loses uncommitted changes or commits missing from every remote.
    Raises ValueError for an editor that is not installed or a path outside the repository, Refused when the clone
    has uncommitted changes to tracked files on another pull request's branch, and CommandError when gh or git fails.
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
        if not folder.exists():
            # The clone goes to the final folder only once it is complete, so a failed one starts over. A GitHub name
            # never holds "~", so the temporary folder is never another repository's clone.
            partial = folder.with_name(f"{folder.name}~partial")
            shutil.rmtree(partial, ignore_errors=True)
            partial.parent.mkdir(parents=True, exist_ok=True)
            github.clone_with_files(ref, partial)
            partial.rename(folder)
        changed = build.git(folder, "status", "--porcelain", "--untracked-files=no").strip()
        if changed and github.branch_pull_request(folder) != ref.number:
            branch = build.git(folder, "branch", "--show-current").decode().strip()
            raise Refused(
                f"The clone at {folder} has uncommitted changes on {branch or 'a detached HEAD'}. "
                "Commit or stash them first."
            )
        branch, fork = github.head_branch(ref)
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
    commands.start(editor.args(command, folder, folder / relative, line))
