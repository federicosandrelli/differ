import json
import os
import re
import signal
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

import checkout
import commands
import editors
from github import PullRef
from test_server import request, response

REF = PullRef("o", "r", 7)
TOOLS = {
    "vscode": "Visual Studio Code.app/Contents/Resources/app/bin/code",
    "cursor": "Cursor.app/Contents/Resources/app/bin/cursor",
    "pycharm": "PyCharm CE.app/Contents/MacOS/pycharm",
    "webstorm": "WebStorm.app/Contents/MacOS/webstorm",
    "goland": "GoLand.app/Contents/MacOS/goland",
}


@dataclass
class FakeLaunch:
    pid: int
    is_running: bool

    def poll(self) -> int | None:
        return None if self.is_running else 0


class FakeProcesses:
    """Stands in for editors.SystemProcesses. `oldest_pid` is the process it finds for any tool, and `running` keeps
    the processes it starts running. It records the tools it looks up and the commands it starts."""

    def __init__(self) -> None:
        self.oldest_pid: int | None = 100
        self.running = False
        self.asked: list[Path] = []
        self.started: list[list[str]] = []
        self.launches: list[FakeLaunch] = []

    def oldest(self, command: Path) -> int | None:
        self.asked.append(command)
        return self.oldest_pid

    def start(self, argv: list[str]) -> FakeLaunch:
        self.started.append(argv)
        self.launches.append(FakeLaunch(1000 + len(self.launches), self.running))
        return self.launches[-1]


class StubCheckouts:
    """Stands in for checkout.Checkouts, with its clone in `folder`. `refusal` makes at_head refuse with that
    message, and `warm_error` makes warm fail with that one. It records the pull requests whose clone it checks out
    and warms."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.refusal, self.warm_error = "", ""
        self.checked_out: list[PullRef] = []
        self.warmed: list[PullRef] = []

    @contextmanager
    def held(self, ref: PullRef) -> Iterator[SimpleNamespace]:
        yield SimpleNamespace(folder=self.folder, at_head=lambda: self.at_head(ref))

    def at_head(self, ref: PullRef) -> Path:
        if self.refusal:
            raise checkout.Refused(self.refusal)
        self.checked_out.append(ref)
        return self.folder

    def warm(self, ref: PullRef) -> None:
        if self.warm_error:
            raise commands.CommandError(self.warm_error)
        self.warmed.append(ref)


def install(folder: Path, editor: str) -> Path:
    tool = folder / TOOLS[editor]
    tool.parent.mkdir(parents=True)
    tool.touch()
    return tool


@pytest.fixture
def clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StubCheckouts:
    stub = StubCheckouts(tmp_path / "checkouts/o/r")
    monkeypatch.setattr(checkout, "checkouts", stub)
    return stub


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clone: StubCheckouts) -> FakeProcesses:
    """VS Code and PyCharm installed in the first of the app folders; the second one is empty."""
    apps = tmp_path / "Applications"
    install(apps, "vscode")
    install(apps, "pycharm")
    monkeypatch.setattr(editors, "APP_FOLDERS", (apps, tmp_path / "More"))
    monkeypatch.setattr(editors, "JETBRAINS_CONFIG", tmp_path / "JetBrains")
    monkeypatch.setattr(editors, "_launches", {})
    fake = FakeProcesses()
    monkeypatch.setattr(editors, "processes", fake)
    return fake


@pytest.fixture
def at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(editors.threading, "Thread", lambda target, daemon: SimpleNamespace(start=target))


@pytest.mark.parametrize(
    ("editor", "args"),
    [
        ("vscode", ["-n", "{folder}", "-g", "{file}:3"]),
        ("cursor", ["-n", "{folder}", "-g", "{file}:3"]),
        ("pycharm", ["{folder}", "--line", "3", "{file}"]),
    ],
)
def test_an_open_starts_the_editor_on_the_clone_at_the_line_of_the_file(
    fake: FakeProcesses, clone: StubCheckouts, editor: str, args: list[str]
) -> None:
    tool = editors.APP_FOLDERS[0] / TOOLS[editor]
    if not tool.exists():
        tool = install(editors.APP_FOLDERS[1], editor)
    editors.open_file(REF, editor, "src/a.py", 3)
    folder = clone.folder
    assert fake.started == [[str(tool), *(a.format(folder=folder, file=folder / "src/a.py") for a in args)]]
    assert clone.checked_out == [REF]


@pytest.mark.parametrize(
    ("config", "key", "opened", "oldest", "by_url"),
    [
        ("PyCharmCE2026.2", "{folder}", "true", 100, True),
        ("PyCharmCE2026.2", "$USER_HOME$/checkouts/o/r", "true", 100, True),
        ("PyCharmCE2026.2", "{folder}", "false", 100, False),
        ("PyCharmCE2026.2", "{folder}-other", "true", 100, False),
        ("GoLand2026.2", "{folder}", "true", 100, False),
        ("PyCharmCE2026.2", "{folder}", "true", None, False),
        ("PyCharmCE2026.2", None, "true", 100, False),
        ("PyCharmCE2026.2", "unreadable", "true", 100, False),
    ],
    ids=[
        "project open",
        "project open under the home folder",
        "project closed",
        "another project open",
        "open in another IDE",
        "IDE not running",
        "broken list of recent projects",
        "unreadable list of recent projects",
    ],
)
def test_a_running_jetbrains_ide_with_the_project_open_gets_the_file_through_its_url_scheme(
    fake: FakeProcesses,
    clone: StubCheckouts,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: str,
    key: str | None,
    opened: str,
    oldest: int | None,
    by_url: bool,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    fake.oldest_pid = oldest
    recent = editors.JETBRAINS_CONFIG / config / "options/recentProjects.xml"
    recent.parent.mkdir(parents=True)
    if key == "unreadable":
        recent.mkdir()
    else:
        recent.write_text(
            "<application><component"
            if key is None
            else f'<application><component name="RecentProjectsManager"><option name="additionalInfo"><map>'
            f'<entry key="{key.format(folder=clone.folder)}"><value><RecentProjectMetaInfo opened="{opened}" />'
            f"</value></entry></map></option></component></application>"
        )
    editors.open_file(REF, "pycharm", "src/a b.py", 4)
    file = clone.folder / "src/a b.py"
    url = ["open", f"pycharm://open?file={clone.folder}/src/a%20b.py&line=4"]
    launcher = [str(editors.APP_FOLDERS[0] / TOOLS["pycharm"]), str(clone.folder), "--line", "4", str(file)]
    assert fake.started == [url if by_url else launcher]


def test_installed_lists_the_installed_editors_of_every_app_folder_in_the_order_of_known(
    fake: FakeProcesses,
) -> None:
    install(editors.APP_FOLDERS[1], "goland")
    install(editors.APP_FOLDERS[1], "cursor")
    assert [e.id for e in editors.known()] == ["vscode", "cursor", "pycharm", "idea", "webstorm", "goland"]
    assert [e.id for e in editors.installed()] == ["vscode", "cursor", "pycharm", "goland"]
    assert editors.installed()[1] == editors.Editor("cursor", "Cursor")


def test_an_open_runs_the_tool_of_the_first_app_folder_that_holds_the_editor(
    fake: FakeProcesses, clone: StubCheckouts
) -> None:
    other = editors.APP_FOLDERS[1] / "PyCharm.app/Contents/MacOS/pycharm"
    other.parent.mkdir(parents=True)
    other.touch()
    editors.open_file(REF, "pycharm", "a.py", 1)
    assert fake.started[0][0] == str(editors.APP_FOLDERS[0] / TOOLS["pycharm"])


def test_prepare_warms_the_clone(fake: FakeProcesses, clone: StubCheckouts, at_once: None) -> None:
    editors.prepare(REF)
    assert clone.warmed == [REF]


def test_prepare_does_nothing_without_an_installed_editor(
    fake: FakeProcesses, clone: StubCheckouts, at_once: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(editors, "APP_FOLDERS", ())
    editors.prepare(REF)
    assert clone.warmed == []


def test_prepare_logs_a_failed_warm_up(
    fake: FakeProcesses, clone: StubCheckouts, at_once: None, capsys: pytest.CaptureFixture[str]
) -> None:
    clone.warm_error = "`gh repo clone o/r` failed: no network"
    editors.prepare(REF)
    assert capsys.readouterr().err == "preparing the clone of o/r failed: `gh repo clone o/r` failed: no network\n"


@pytest.mark.parametrize(
    ("editor", "why"),
    [
        ("cursor", "'cursor' is not an installed editor (installed: 'vscode', 'pycharm')"),
        ("/bin/sh", "'/bin/sh' is not an installed editor (installed: 'vscode', 'pycharm')"),
    ],
)
def test_an_editor_outside_the_installed_ones_is_refused_before_the_repository_lock(
    fake: FakeProcesses, clone: StubCheckouts, monkeypatch: pytest.MonkeyPatch, editor: str, why: str
) -> None:
    monkeypatch.setattr(clone, "held", lambda ref: pytest.fail("the open waited for the repository's lock"))
    with pytest.raises(ValueError, match=f"^{re.escape(why)}$"):
        editors.open_file(REF, editor, "a.py", 1)
    assert fake.started == []


@pytest.mark.parametrize("path", ["", "/etc/passwd", "../other/a.py", "src/../../a.py"])
def test_a_path_outside_the_repository_is_refused_before_the_repository_lock(
    fake: FakeProcesses, clone: StubCheckouts, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setattr(clone, "held", lambda ref: pytest.fail("the open waited for the repository's lock"))
    with pytest.raises(ValueError, match="is not a path inside the repository"):
        editors.open_file(REF, "vscode", path, 1)
    assert fake.started == []


def test_an_open_waits_while_the_last_launcher_hands_its_request_to_a_running_editor(
    fake: FakeProcesses, clone: StubCheckouts
) -> None:
    fake.running, fake.oldest_pid = True, 200
    editors.open_file(REF, "pycharm", "a.py", 1)
    fake.asked.clear()
    with pytest.raises(editors.Refused, match="The editor is still opening"):
        editors.open_file(REF, "pycharm", "b.py", 1)
    assert fake.asked == [editors.APP_FOLDERS[0] / TOOLS["pycharm"]]
    assert clone.checked_out == [REF] and len(fake.started) == 1
    fake.launches[0].is_running = False
    editors.open_file(REF, "pycharm", "b.py", 1)
    assert clone.checked_out == [REF, REF] and len(fake.started) == 2


@pytest.mark.parametrize("ide_quit", [False, True], ids=["the launcher became the IDE", "the IDE quit"])
def test_an_open_goes_on_while_the_last_launcher_runs_but_hands_nothing_over(
    fake: FakeProcesses, clone: StubCheckouts, ide_quit: bool
) -> None:
    fake.running, fake.oldest_pid = True, None
    editors.open_file(REF, "pycharm", "a.py", 1)
    fake.oldest_pid = None if ide_quit else fake.launches[0].pid
    editors.open_file(REF, "pycharm", "b.py", 1)
    assert len(fake.started) == 2


def test_a_jetbrains_hand_over_in_progress_leaves_the_ide_free_to_open_another_folder(
    fake: FakeProcesses, clone: StubCheckouts, tmp_path: Path
) -> None:
    fake.running, fake.oldest_pid = True, 200
    editors.open_file(REF, "pycharm", "a.py", 1)
    clone.folder = tmp_path / "checkouts/o/other"
    editors.open_file(PullRef("o", "other", 1), "pycharm", "a.py", 1)
    assert len(fake.started) == 2


@pytest.mark.parametrize("other", ["vscode", "webstorm"])
def test_a_jetbrains_hand_over_in_progress_leaves_another_editor_free_to_open_the_same_folder(
    fake: FakeProcesses, clone: StubCheckouts, other: str
) -> None:
    if other == "webstorm":
        install(editors.APP_FOLDERS[0], "webstorm")
    fake.running, fake.oldest_pid = True, 200
    editors.open_file(REF, "pycharm", "a.py", 1)
    editors.open_file(REF, other, "b.py", 1)
    assert len(fake.started) == 2
    with pytest.raises(editors.Refused):
        editors.open_file(REF, "pycharm", "c.py", 1)


@pytest.fixture
def running_tool(tmp_path: Path) -> Iterator[tuple[Path, int]]:
    """Sleep run through a link at the same path as the tool but with "x" in place of the dot, then twice through a
    link at the tool's path, which holds a dot and a space; all three run until the test ends. Yields the tool and
    the pid of its first process. macOS kills a copy of sleep, so the tool is a link."""
    tool = tmp_path / "a b.app/sl.eep"
    other = tmp_path / "a bxapp/sl.eep"
    procs: list[subprocess.Popen[bytes]] = []
    try:
        for path in (other, tool):
            path.parent.mkdir()
            path.symlink_to("/bin/sleep")
        for path in (other, tool, tool):
            procs.append(subprocess.Popen([str(path), "30"]))
        yield tool, procs[1].pid
    finally:
        for p in procs:
            p.kill()
            p.wait()


def test_oldest_finds_the_process_that_runs_the_tool_at_this_exact_path(running_tool: tuple[Path, int]) -> None:
    tool, pid = running_tool
    assert editors.SystemProcesses().oldest(tool) == pid


def test_oldest_finds_no_process_with_the_tool_only_as_an_argument(tmp_path: Path) -> None:
    tool = tmp_path / "a b.app/sl.eep"
    with subprocess.Popen(["/bin/sh", "-c", "sleep 30; true", str(tool)], start_new_session=True) as p:
        try:
            assert editors.SystemProcesses().oldest(tool) is None
        finally:
            os.killpg(p.pid, signal.SIGKILL)


def test_a_started_process_runs_until_it_exits() -> None:
    launch = editors.SystemProcesses().start(["/bin/sleep", "30"])
    try:
        assert launch.poll() is None
    finally:
        subprocess.run(["kill", str(launch.pid)], check=True)
    for _ in range(100):
        if launch.poll() is not None:
            break
        subprocess.run(["sleep", "0.05"])
    assert launch.poll() is not None


def post_open(body: dict) -> tuple[int, dict]:
    h = request("POST", "/api/o/r/7/open", json.dumps(body).encode())
    h.do_POST()
    code, raw = response(h)
    return code, json.loads(raw)


def test_the_open_route_opens_the_file(fake: FakeProcesses) -> None:
    assert post_open({"editor": "vscode", "path": "a.py", "line": 4}) == (200, {})
    assert fake.started[0][-1].endswith("/o/r/a.py:4")


def test_the_open_route_answers_a_refusal_with_its_reason(fake: FakeProcesses, clone: StubCheckouts) -> None:
    clone.refusal = "The clone at /x has uncommitted changes on other. Commit or stash them first."
    code, body = post_open({"editor": "vscode", "path": "a.py", "line": 4})
    assert (code, body) == (400, {"error": clone.refusal})
    assert fake.started == []


def test_the_editors_route_lists_the_installed_editors_and_the_known_names_and_prepares_the_clone(
    fake: FakeProcesses, clone: StubCheckouts, at_once: None
) -> None:
    h = request("GET", "/api/o/r/7/editors")
    h.do_GET()
    code, raw = response(h)
    assert code == 200
    assert json.loads(raw) == {
        "installed": [{"id": "vscode", "name": "VS Code"}, {"id": "pycharm", "name": "PyCharm"}],
        "known": ["VS Code", "Cursor", "PyCharm", "IntelliJ IDEA", "WebStorm", "GoLand"],
    }
    assert clone.warmed == [REF]
