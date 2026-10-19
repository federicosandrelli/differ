"""Open a pull request's files in the user's editor: the editors the app knows, and how the server opens a file in
each, on the repository's clone from checkout.py or on GitHub.

The module's interface is `Editor`, `known`, `installed`, `prepare` and `open_file`. Behind it, a launcher seam holds
one adapter per kind of editor: `_Code` for VS Code and Cursor and `_JetBrains` for the JetBrains IDEs, both `_Local`
editors that open the clone, and `_GitHub`, which opens the file on github.com. A local adapter finds its command line
tool, builds the command that opens a file, and says whether the editor is still busy with the last file it opened for
a folder. The adapters start and look up processes through `processes`, a `Processes`; tests replace it."""

from __future__ import annotations

import re
import sys
import threading
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import quote

import build
import checkout
import commands
from checkout import Refused
from github import PullRef

APP_FOLDERS = (Path("/Applications"), Path.home() / "Applications")
JETBRAINS_CONFIG = Path.home() / "Library/Application Support/JetBrains"


@dataclass(frozen=True)
class Editor:
    """An editor the page can open a file in. A local one opens the repository's clone on this computer; the others
    open the file on a website."""

    id: str
    name: str
    local: bool = True


class Launch(Protocol):
    """A process that `Processes.start` started."""

    pid: int

    def poll(self) -> int | None: ...


class Processes(Protocol):
    """The interface through which the adapters start processes and look them up."""

    def oldest(self, command: Path) -> int | None: ...

    def start(self, argv: list[str]) -> Launch: ...


class SystemProcesses:
    """The `Processes` adapter for this computer's processes."""

    def oldest(self, command: Path) -> int | None:
        """The oldest process that runs the command line tool at this path, or None. For a JetBrains IDE that is the
        IDE itself, since a launcher that finds the IDE running hands its request over and exits."""
        pattern = "^" + re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", str(command))
        pid = commands.call(["pgrep", "-o", "-f", pattern]).stdout.strip()
        return int(pid) if pid else None

    def start(self, argv: list[str]) -> Launch:
        """Start the command as `commands.start` does. Raises CommandError when it is not installed."""
        return commands.start(argv)


processes: Processes = SystemProcesses()


@dataclass(frozen=True)
class _Launcher(ABC):
    """An adapter at the launcher seam: how the server opens a file in one editor."""

    editor: Editor

    @abstractmethod
    def available(self) -> bool:
        """Whether the editor can open a file on this computer now."""

    @abstractmethod
    def open(self, ref: PullRef, path: PurePosixPath, line: int) -> None:
        """Open the file at the path, relative to the repository, at the line, on the pull request's head. Returns
        without waiting for the editor. Raises Refused and CommandError as `open_file` says."""


@dataclass(frozen=True)
class _Local(_Launcher):
    """An editor installed on this computer, which opens the repository's clone. The server starts the editor's
    command line tool by its full path inside the app bundle, since the desktop app starts the server with a PATH that
    may not hold the tool."""

    apps: tuple[str, ...]
    tool: str

    def available(self) -> bool:
        return self.find() is not None

    def find(self) -> Path | None:
        """The command line tool of the first of the editor's app bundles installed, or None."""
        for folder in APP_FOLDERS:
            for app in self.apps:
                if (path := folder / app / self.tool).exists():
                    return path
        return None

    def busy(self, folder: Path) -> bool:
        """Whether the editor is still opening the last file it opened for the folder, so that it would fail to open
        another one now."""
        return False

    def open(self, ref: PullRef, path: PurePosixPath, line: int) -> None:
        """Refuse, before the clone moves, while the editor is still opening the last file it opened for this
        repository; else move the clone to the pull request's head (`checkout.Clone.at_head` tells how) and start the
        editor on it in a new window."""
        command = self.find()
        if command is None:
            raise commands.CommandError(f"{self.editor.name} is no longer installed")
        with checkout.checkouts.held(ref) as clone:
            if self.busy(clone.folder):
                raise Refused(
                    "The editor is still opening the last file from this repository. Answer its window first."
                )
            clone.at_head()
            self.start(command, clone.folder, clone.folder / path, line)

    @abstractmethod
    def start(self, command: Path, folder: Path, file: Path, line: int) -> None:
        """Start the editor, through the tool at `command`, on the folder in a new window, at the line of the file.
        Returns without waiting for it. Raises CommandError when the tool is missing."""


class _Code(_Local):
    """The adapter for VS Code and Cursor."""

    def start(self, command: Path, folder: Path, file: Path, line: int) -> None:
        processes.start([str(command), "-n", str(folder), "-g", f"{file}:{line}"])


_launches: dict[tuple[str, Path], tuple[Path, Launch]] = {}


@dataclass(frozen=True)
class _JetBrains(_Local):
    """The adapter for a JetBrains IDE. Its configuration folders start with one of `configs`, followed by the
    version."""

    configs: tuple[str, ...]

    def busy(self, folder: Path) -> bool:
        """Whether the launch this IDE started last for the folder is still handing its request to an IDE that was
        already running. A JetBrains launcher becomes the IDE when none runs; otherwise it waits until the running IDE
        opens the project, which can wait on a dialog, and a second request for the same project meanwhile makes the
        IDE fail."""
        last = _launches.get((self.editor.id, folder))
        if last is None or last[1].poll() is not None:
            return False
        tool, launch = last
        return processes.oldest(tool) not in (None, launch.pid)

    def start(self, command: Path, folder: Path, file: Path, line: int) -> None:
        _launches[self.editor.id, folder] = (command, processes.start(self.argv(command, folder, file, line)))

    def argv(self, command: Path, folder: Path, file: Path, line: int) -> list[str]:
        """The JetBrains launcher has no option for a new window; the IDE's "Open project in" setting decides. It
        also takes about two seconds to hand its request to a running IDE, so a file of a project the IDE has open
        goes through the IDE's URL scheme instead, which opens it at once."""
        if self.has_open(folder) and processes.oldest(command):
            return ["open", f"{Path(self.tool).name}://open?file={quote(str(file))}&line={line}"]
        return [str(command), str(folder), "--line", str(line), str(file)]

    def has_open(self, folder: Path) -> bool:
        """Whether the IDE had the folder open as a project when it last saved its list of recent projects, which it
        keeps after it quits."""
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


@dataclass(frozen=True)
class _GitHub(_Launcher):
    """The file on github.com, at the pull request's head commit, in the default browser. It needs no clone, so it is
    always available."""

    def available(self) -> bool:
        return True

    def open(self, ref: PullRef, path: PurePosixPath, line: int) -> None:
        url = f"https://github.com/{ref.slug}/blob/{build.recent_head(ref)}/{quote(str(path))}#L{line}"
        processes.start(["open", url])


_LAUNCHERS: dict[str, _Launcher] = {
    a.editor.id: a
    for a in (
        _Code(Editor("vscode", "VS Code"), ("Visual Studio Code.app",), "Contents/Resources/app/bin/code"),
        _Code(Editor("cursor", "Cursor"), ("Cursor.app",), "Contents/Resources/app/bin/cursor"),
        _JetBrains(
            Editor("pycharm", "PyCharm"),
            ("PyCharm.app", "PyCharm CE.app", "PyCharm Professional Edition.app", "PyCharm Community Edition.app"),
            "Contents/MacOS/pycharm",
            ("PyCharm",),
        ),
        _JetBrains(
            Editor("idea", "IntelliJ IDEA"),
            ("IntelliJ IDEA.app", "IntelliJ IDEA CE.app", "IntelliJ IDEA Ultimate.app"),
            "Contents/MacOS/idea",
            ("IntelliJIdea", "IdeaIC"),
        ),
        _JetBrains(Editor("webstorm", "WebStorm"), ("WebStorm.app",), "Contents/MacOS/webstorm", ("WebStorm",)),
        _JetBrains(Editor("goland", "GoLand"), ("GoLand.app",), "Contents/MacOS/goland", ("GoLand",)),
        _GitHub(Editor("github", "GitHub", local=False)),
    )
}


def known() -> list[Editor]:
    """Every editor the app knows, in the order of the page's menu."""
    return [a.editor for a in _LAUNCHERS.values()]


def installed() -> list[Editor]:
    """The editors available on this computer: the local ones with an installed app bundle, and GitHub, in the order
    of `known`. Looks for the bundles on each call."""
    return [a.editor for a in _LAUNCHERS.values() if a.available()]


def prepare(ref: PullRef) -> None:
    """When a local editor is installed, clone the repository and read the pull request's branch in the background,
    so that the first open waits for neither."""
    if not any(e.local for e in installed()):
        return

    def run() -> None:
        try:
            checkout.checkouts.warm(ref)
        except commands.CommandError as exc:
            sys.stderr.write(f"preparing the clone of {ref.slug} failed: {exc}\n")

    threading.Thread(target=run, daemon=True).start()


def open_file(ref: PullRef, editor_id: str, path: str, line: int) -> None:
    """Open the file at the line, on the pull request's head, in the editor: a local editor opens the repository's
    clone in a new window, on the pull request's branch, and GitHub opens the file at the head commit in the browser.
    Raises ValueError for an editor that is not installed or a path outside the repository, before a local editor
    waits for the repository's lock; Refused, before the clone moves, while a local editor is still opening the last
    file it opened for this repository, or when `checkout.Clone.at_head` refuses; and CommandError when gh or git
    fails, or when the editor's tool is missing. Returns once the editor starts, without waiting for it."""
    launcher = _LAUNCHERS.get(editor_id)
    if not launcher or not launcher.available():
        names = ", ".join(repr(e.id) for e in installed()) or "none"
        raise ValueError(f"{editor_id!r} is not an installed editor (installed: {names})")
    relative = PurePosixPath(path)
    if not path or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{path!r} is not a path inside the repository")
    launcher.open(ref, relative, line)
