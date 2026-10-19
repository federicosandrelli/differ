import json
import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import checkout
import commands
import editors
from github import PullRef
from test_server import request, response

REF = PullRef("o", "r", 7)


class FakeLaunch:
    def __init__(self, args: list[str], running: bool) -> None:
        self.args, self.pid, self.running = args, 100, running

    def poll(self) -> int | None:
        return None if self.running else 0


class FakeCommands:
    """Stands in for pgrep, and records the commands it runs and the editors it starts. `running` keeps the started
    editors running, and `pids` are the processes pgrep finds, oldest first."""

    def __init__(self) -> None:
        self.ran: list[list[str]] = []
        self.started: list[list[str]] = []
        self.running, self.pids = False, [100]

    def run(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        self.ran.append(args)
        out = f"{self.pids[0]}\n" if args[:3] == ["pgrep", "-o", "-f"] and self.pids else ""
        return subprocess.CompletedProcess(args, 0, out.encode(), b"")

    def popen(self, args: list[str], **kwargs: Any) -> FakeLaunch:
        self.started.append(args)
        return FakeLaunch(args, self.running)


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


@pytest.fixture
def clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StubCheckouts:
    stub = StubCheckouts(tmp_path / "checkouts/o/r")
    monkeypatch.setattr(checkout, "checkouts", stub)
    return stub


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clone: StubCheckouts) -> FakeCommands:
    apps = tmp_path / "Applications"
    for tool in ("Visual Studio Code.app/Contents/Resources/app/bin/code", "PyCharm CE.app/Contents/MacOS/pycharm"):
        (apps / tool).parent.mkdir(parents=True)
        (apps / tool).touch()
    monkeypatch.setattr(editors, "APP_FOLDERS", (apps, tmp_path / "missing"))
    monkeypatch.setattr(editors, "JETBRAINS_CONFIG", tmp_path / "JetBrains")
    monkeypatch.setattr(editors, "launches", {})
    fake = FakeCommands()
    monkeypatch.setattr(commands.subprocess, "run", fake.run)
    monkeypatch.setattr(commands.subprocess, "Popen", fake.popen)
    return fake


@pytest.fixture
def at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(editors.threading, "Thread", lambda target, daemon: SimpleNamespace(start=target))


@pytest.mark.parametrize(
    ("editor", "args"),
    [
        ("vscode", ["Visual Studio Code.app/Contents/Resources/app/bin/code", "-n", "{folder}", "-g", "{file}:3"]),
        ("pycharm", ["PyCharm CE.app/Contents/MacOS/pycharm", "{folder}", "--line", "3", "{file}"]),
    ],
)
def test_an_open_starts_the_editor_on_the_clone_at_the_line_of_the_file(
    fake: FakeCommands, clone: StubCheckouts, editor: str, args: list[str]
) -> None:
    editors.open_file(REF, editor, "src/a.py", 3)
    folder = clone.folder
    tool = str(editors.APP_FOLDERS[0] / args[0])
    assert fake.started == [[tool, *(a.format(folder=folder, file=folder / "src/a.py") for a in args[1:])]]
    assert clone.checked_out == [REF]


@pytest.mark.parametrize(
    ("config", "opened", "pids", "by_url"),
    [
        ("PyCharmCE2026.2", "true", [100], True),
        ("PyCharmCE2026.2", "false", [100], False),
        ("GoLand2026.2", "true", [100], False),
        ("PyCharmCE2026.2", "true", [], False),
    ],
    ids=["project open", "project closed", "open in another IDE", "IDE not running"],
)
def test_a_running_jetbrains_ide_with_the_project_open_gets_the_file_through_its_url_scheme(
    fake: FakeCommands, clone: StubCheckouts, config: str, opened: str, pids: list[int], by_url: bool
) -> None:
    fake.pids = pids
    recent = editors.JETBRAINS_CONFIG / config / "options/recentProjects.xml"
    recent.parent.mkdir(parents=True)
    key = str(clone.folder).replace(str(Path.home()), "$USER_HOME$")
    recent.write_text(
        f'<application><component name="RecentProjectsManager"><option name="additionalInfo"><map>'
        f'<entry key="{key}"><value><RecentProjectMetaInfo opened="{opened}" /></value></entry>'
        f"</map></option></component></application>"
    )
    editors.open_file(REF, "pycharm", "src/a b.py", 4)
    url = ["open", f"pycharm://open?file={clone.folder}/src/a%20b.py&line=4"]
    assert (fake.started[-1] == url) is by_url


def test_prepare_warms_the_clone(fake: FakeCommands, clone: StubCheckouts, at_once: None) -> None:
    editors.prepare(REF)
    assert clone.warmed == [REF]


def test_prepare_does_nothing_without_an_installed_editor(
    fake: FakeCommands, clone: StubCheckouts, at_once: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(editors, "APP_FOLDERS", ())
    editors.prepare(REF)
    assert clone.warmed == []


def test_prepare_logs_a_failed_warm_up(
    fake: FakeCommands, clone: StubCheckouts, at_once: None, capsys: pytest.CaptureFixture[str]
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
def test_an_editor_outside_the_installed_ones_is_refused(
    fake: FakeCommands, clone: StubCheckouts, editor: str, why: str
) -> None:
    with pytest.raises(ValueError, match=f"^{re.escape(why)}$"):
        editors.open_file(REF, editor, "a.py", 1)
    assert clone.checked_out == [] and fake.started == []


@pytest.mark.parametrize("path", ["", "/etc/passwd", "../other/a.py", "src/../../a.py"])
def test_a_path_outside_the_repository_is_refused(fake: FakeCommands, clone: StubCheckouts, path: str) -> None:
    with pytest.raises(ValueError, match="is not a path inside the repository"):
        editors.open_file(REF, "vscode", path, 1)
    assert clone.checked_out == [] and fake.started == []


def test_an_open_waits_while_the_last_launcher_hands_its_request_to_a_running_editor(
    fake: FakeCommands, clone: StubCheckouts
) -> None:
    fake.running, fake.pids = True, [200, 100]
    editors.open_file(REF, "pycharm", "a.py", 1)
    fake.ran.clear()
    with pytest.raises(editors.Refused, match="The editor is still opening"):
        editors.open_file(REF, "pycharm", "b.py", 1)
    pycharm = editors.APP_FOLDERS[0] / "PyCharm CE.app/Contents/MacOS/pycharm"
    assert fake.ran == [["pgrep", "-o", "-f", "^" + str(pycharm).replace(".", "\\.")]]
    assert clone.checked_out == [REF] and len(fake.started) == 1
    fake.pids = [100, 300]
    editors.open_file(REF, "pycharm", "b.py", 1)
    assert clone.checked_out == [REF, REF] and len(fake.started) == 2


def post_open(body: dict) -> tuple[int, dict]:
    h = request("POST", "/api/o/r/7/open", json.dumps(body).encode())
    h.do_POST()
    code, raw = response(h)
    return code, json.loads(raw)


def test_the_open_route_opens_the_file(fake: FakeCommands) -> None:
    assert post_open({"editor": "vscode", "path": "a.py", "line": 4}) == (200, {})
    assert fake.started[0][-1].endswith("/o/r/a.py:4")


def test_the_open_route_answers_a_refusal_with_its_reason(fake: FakeCommands, clone: StubCheckouts) -> None:
    clone.refusal = "The clone at /x has uncommitted changes on other. Commit or stash them first."
    code, body = post_open({"editor": "vscode", "path": "a.py", "line": 4})
    assert (code, body) == (400, {"error": clone.refusal})
    assert fake.started == []


def test_the_editors_route_lists_the_installed_editors_and_the_known_names_and_prepares_the_clone(
    fake: FakeCommands, clone: StubCheckouts, at_once: None
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
