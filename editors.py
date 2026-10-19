"""Open a pull request's files in the user's editor: the editors the app knows, and how the server starts them on the
repository's clone from checkout.py."""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import checkout
import commands
from checkout import Refused
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


def installed() -> list[Editor]:
    return [e for e in EDITORS.values() if e.command()]


launches: dict[str, tuple[Path, subprocess.Popen[bytes]]] = {}


def still_opening(launch: tuple[Path, subprocess.Popen[bytes]] | None) -> bool:
    """Whether the editor started last for a repository is still handing its request to an editor that was already
    running. A JetBrains launcher becomes the IDE when none runs; otherwise it waits until the running IDE opens the
    project, which can wait on a dialog, and a second request for the same project meanwhile makes the IDE fail."""
    if launch is None or launch[1].poll() is not None:
        return False
    command, process = launch
    return oldest(command) not in (None, process.pid)


def prepare(ref: PullRef) -> None:
    """When an editor is installed, clone the repository and read the pull request's branch in the background, so
    that the first open waits for neither."""
    if not installed():
        return

    def run() -> None:
        try:
            checkout.checkouts.warm(ref)
        except commands.CommandError as exc:
            sys.stderr.write(f"preparing the clone of {ref.slug} failed: {exc}\n")

    threading.Thread(target=run, daemon=True).start()


def open_file(ref: PullRef, editor_id: str, path: str, line: int) -> None:
    """Open the repository's clone in a new window of the editor, on the pull request's branch, at the line of the
    file; `checkout.Clone.at_head` tells how the clone gets there. Raises ValueError for an editor that is not
    installed or a path outside the repository, Refused while the editor started last for the repository is still
    opening or when at_head refuses, and CommandError when gh or git fails.
    Returns once the editor starts, without waiting for it."""
    editor = EDITORS.get(editor_id)
    command = editor and editor.command()
    if not editor or not command:
        names = ", ".join(repr(e.id) for e in installed()) or "none"
        raise ValueError(f"{editor_id!r} is not an installed editor (installed: {names})")
    relative = PurePosixPath(path)
    if not path or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{path!r} is not a path inside the repository")
    with checkout.checkouts.held(ref) as clone:
        if still_opening(launches.get(ref.slug)):
            raise Refused("The editor is still opening the last file from this repository. Answer its window first.")
        folder = clone.at_head()
        launches[ref.slug] = (command, commands.start(editor.args(command, folder, folder / relative, line)))
