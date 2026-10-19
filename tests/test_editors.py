import json
import re
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import build
import commands
import editors
from github import PullRef
from test_server import request, response

REF = PullRef("o", "r", 7)
STATUS = ["status", "--porcelain", "--untracked-files=no"]
HEAD_BRANCH = ["gh", "pr", "view", "7", "--repo", "o/r", "--json", "headRefName,isCrossRepository"]
BRANCH_PR = ["gh", "pr", "view", "--json", "number", "--jq", ".number"]
IS_ANCESTOR = ["merge-base", "--is-ancestor", "refs/heads/feature", "refs/remotes/origin/feature"]
REFLOG = ["log", "-g", "--format=%H", "refs/remotes/origin/feature"]
CHECKOUT = ["gh", "pr", "checkout", "7", "--repo", "o/r"]
CURRENT = ["branch", "--show-current"]
HEAD_SHA = ["gh", "pr", "view", "7", "--repo", "o/r", "--json", "headRefOid", "--jq", ".headRefOid"]


class FakeLaunch:
    def __init__(self, args: list[str], running: bool) -> None:
        self.args, self.pid, self.running = args, 100, running

    def poll(self) -> int | None:
        return None if self.running else 0


class FakeCommands:
    """Stands in for git, gh and pgrep, and records the commands it runs and the editors it starts. `first` is the
    head the clone wrote without a reflog entry; `own` counts the commits of the local branch that no remote has;
    `running` keeps the started editors running, `pids` are the processes pgrep finds, oldest first, and `github_head` is the pull
    request's head on GitHub."""

    def __init__(self) -> None:
        self.status, self.branch, self.fork, self.own = "", "feature", False, 0
        self.branch_pr: int | None = 7
        self.ancestor, self.reflog, self.first = 1, ["new"], "old"
        self.checkout_error, self.move_error, self.set_default_error, self.clone_seconds = "", "", "", 0.0
        self.ran: list[tuple[list[str], Path | None]] = []
        self.started: list[list[str]] = []
        self.running, self.pids, self.github_head = False, [100], "new"

    def run(self, args: list[str], cwd: Path | None = None, **kwargs: Any) -> subprocess.CompletedProcess:
        self.ran.append((args, cwd))
        code, out, err = 0, "", ""
        if args[:3] == ["gh", "repo", "clone"]:
            time.sleep(self.clone_seconds)
            Path(args[4]).mkdir(parents=True)
        elif args[3:] == STATUS:
            out = "".join(line for line in self.status.splitlines(keepends=True) if not line.startswith("??"))
        elif args[3:] == ["branch", "--show-current"]:
            out = self.branch + "\n"
        elif args[:3] == ["gh", "repo", "set-default"] and self.set_default_error:
            code, err = 1, self.set_default_error
        elif args[3:] == IS_ANCESTOR:
            code = self.ancestor
        elif args[3:6] == ["rev-parse", "--verify", "--quiet"]:
            if args[6] == f"refs/remotes/origin/feature@{{{len(self.reflog)}}}":
                out = self.first + "\n"
            else:
                code = 128
        elif args[3:] == ["rev-parse", "HEAD"]:
            out = "new\n"
        elif args[3:] == REFLOG:
            out = "".join(f"{commit}\n" for commit in self.reflog)
        elif args[3:5] == ["rev-list", "--count"]:
            out = f"{self.own}\n"
        elif args[3:5] == ["checkout", "-B"] and self.move_error:
            code, err = 1, self.move_error
        elif args[-1] == HEAD_BRANCH[-1]:
            out = json.dumps({"headRefName": "feature", "isCrossRepository": self.fork})
        elif args == BRANCH_PR:
            code, out = (1, "") if self.branch_pr is None else (0, f"{self.branch_pr}\n")
        elif args == CHECKOUT and self.checkout_error:
            code, err = 1, self.checkout_error
        elif args[-1] == HEAD_SHA[-1]:
            out = self.github_head + "\n"
        elif args[:3] == ["pgrep", "-o", "-f"]:
            out = f"{self.pids[0]}\n" if self.pids else ""
        return subprocess.CompletedProcess(args, code, out.encode(), err.encode())

    def popen(self, args: list[str], **kwargs: Any) -> FakeLaunch:
        self.started.append(args)
        return FakeLaunch(args, self.running)

    def commands(self) -> list[list[str]]:
        return [args for args, _ in self.ran]

    def git(self, *args: str) -> list[str]:
        return ["git", "-C", str(editors.checkout_dir(REF)), *args]


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeCommands:
    apps = tmp_path / "Applications"
    for tool in ("Visual Studio Code.app/Contents/Resources/app/bin/code", "PyCharm CE.app/Contents/MacOS/pycharm"):
        (apps / tool).parent.mkdir(parents=True)
        (apps / tool).touch()
    monkeypatch.setattr(editors, "APP_FOLDERS", (apps, tmp_path / "missing"))
    monkeypatch.setattr(editors, "JETBRAINS_CONFIG", tmp_path / "JetBrains")
    monkeypatch.setattr(build, "CACHE", tmp_path / "cache")
    for name in ("launches", "opened"):
        monkeypatch.setattr(editors, name, {})
    for name in ("heads", "head_checked", "head_read"):
        monkeypatch.setattr(build, name, {})
    editors.head_branch.cache_clear()
    fake = FakeCommands()
    monkeypatch.setattr(commands.subprocess, "run", fake.run)
    monkeypatch.setattr(commands.subprocess, "Popen", fake.popen)
    return fake


def test_the_first_open_clones_the_repository_then_checks_out_the_pull_request_and_starts_the_editor(
    fake: FakeCommands,
) -> None:
    folder = editors.checkout_dir(REF)
    partial = folder.with_name("r~partial")
    editors.open_file(REF, "vscode", "src/a.py", 12)
    assert fake.ran == [
        (["gh", "repo", "clone", "o/r", str(partial), "--", "--quiet"], None),
        (["gh", "repo", "set-default", "o/r"], partial),
        (fake.git(*STATUS), None),
        (HEAD_BRANCH, None),
        (CHECKOUT, folder),
        (fake.git(*CURRENT), None),
    ]
    assert folder.is_dir() and not partial.exists()
    code = editors.APP_FOLDERS[0] / "Visual Studio Code.app/Contents/Resources/app/bin/code"
    assert fake.started == [[str(code), "-n", str(folder), "-g", f"{folder}/src/a.py:12"]]


def test_a_later_open_checks_out_again_without_cloning(fake: FakeCommands) -> None:
    editors.open_file(REF, "pycharm", "a.py", 3)
    fake.ran.clear()
    fake.github_head = "newer"
    editors.open_file(REF, "pycharm", "a.py", 3)
    assert fake.commands()[-3:] == [fake.git(*STATUS), CHECKOUT, fake.git(*CURRENT)]
    assert not any(args[:3] == ["gh", "repo", "clone"] for args in fake.commands())
    folder = editors.checkout_dir(REF)
    pycharm = editors.APP_FOLDERS[0] / "PyCharm CE.app/Contents/MacOS/pycharm"
    assert fake.started[-1] == [str(pycharm), str(folder), "--line", "3", f"{folder}/a.py"]


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
    fake: FakeCommands, config: str, opened: str, pids: list[int], by_url: bool
) -> None:
    fake.pids = pids
    recent = editors.JETBRAINS_CONFIG / config / "options/recentProjects.xml"
    recent.parent.mkdir(parents=True)
    folder = editors.checkout_dir(REF)
    key = str(folder).replace(str(Path.home()), "$USER_HOME$")
    recent.write_text(
        f'<application><component name="RecentProjectsManager"><option name="additionalInfo"><map>'
        f'<entry key="{key}"><value><RecentProjectMetaInfo opened="{opened}" /></value></entry>'
        f"</map></option></component></application>"
    )
    editors.open_file(REF, "pycharm", "src/a b.py", 4)
    url = ["open", f"pycharm://open?file={folder}/src/a%20b.py&line=4"]
    assert (fake.started[-1] == url) is by_url


def test_a_clone_that_fails_at_any_step_leaves_no_clone_and_the_next_open_starts_over(fake: FakeCommands) -> None:
    folder = editors.checkout_dir(REF)
    fake.set_default_error = "failed to set default"
    with pytest.raises(commands.CommandError, match="failed to set default"):
        editors.open_file(REF, "vscode", "a.py", 1)
    assert not folder.exists() and folder.with_name("r~partial").is_dir()
    fake.set_default_error = ""
    fake.ran.clear()
    editors.open_file(REF, "vscode", "a.py", 1)
    assert fake.commands()[:2] == [
        ["gh", "repo", "clone", "o/r", str(folder.with_name("r~partial")), "--", "--quiet"],
        ["gh", "repo", "set-default", "o/r"],
    ]
    assert folder.is_dir() and len(fake.started) == 1


def test_a_force_pushed_branch_moves_to_the_new_head_when_it_holds_no_commit_of_its_own(fake: FakeCommands) -> None:
    fake.checkout_error, fake.status = "fatal: Not possible to fast-forward, aborting.", " M a.py\n"
    editors.open_file(REF, "vscode", "a.py", 1)
    assert fake.commands()[-7:-1] == [
        CHECKOUT,
        fake.git(*IS_ANCESTOR),
        fake.git(*REFLOG),
        fake.git("rev-parse", "--verify", "--quiet", "refs/remotes/origin/feature@{1}"),
        fake.git("rev-list", "--count", "refs/heads/feature", "--not", "new", "old", "--remotes"),
        fake.git("checkout", "-B", "feature", "refs/remotes/origin/feature"),
    ]
    assert len(fake.started) == 1


@pytest.mark.parametrize(
    ("number", "github_head", "fast"),
    [(7, "new", True), (7, "newer", False), (8, "new", False)],
    ids=["same pull request at its head", "new push", "another pull request"],
)
def test_an_open_of_the_pull_request_opened_last_at_its_head_runs_no_checkout(
    fake: FakeCommands, number: int, github_head: str, fast: bool
) -> None:
    editors.open_file(REF, "vscode", "a.py", 1)
    fake.ran.clear()
    fake.github_head = github_head
    editors.open_file(PullRef("o", "r", number), "vscode", "b.py", 2)
    assert (not any(args[:3] == ["gh", "pr", "checkout"] for args in fake.commands())) is fast
    assert len(fake.started) == 2


def test_the_fast_path_asks_github_for_the_head_only_when_the_server_has_not_read_it_lately(
    fake: FakeCommands,
) -> None:
    editors.open_file(REF, "vscode", "a.py", 1)
    fake.ran.clear()
    editors.open_file(REF, "vscode", "a.py", 1)
    assert fake.commands() == [fake.git(*CURRENT), fake.git("rev-parse", "HEAD"), HEAD_SHA]
    fake.ran.clear()
    editors.open_file(REF, "vscode", "a.py", 1)
    assert fake.commands() == [fake.git(*CURRENT), fake.git("rev-parse", "HEAD")]


def test_a_head_check_that_has_not_read_the_head_yet_does_not_make_the_old_head_recent(fake: FakeCommands) -> None:
    build.heads[REF], build.head_checked[REF] = "old", time.time()
    assert build.recent_head(REF) == "new"


def test_prepare_clones_the_repository_and_reads_the_branch_so_the_first_open_runs_neither(
    fake: FakeCommands, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(editors.threading, "Thread", lambda target, daemon: SimpleNamespace(start=target))
    editors.prepare(REF)
    assert fake.commands()[1:] == [["gh", "repo", "set-default", "o/r"], HEAD_BRANCH]
    fake.ran.clear()
    editors.open_file(REF, "vscode", "a.py", 1)
    assert fake.commands() == [fake.git(*STATUS), CHECKOUT, fake.git(*CURRENT)]


def test_prepare_does_nothing_without_an_installed_editor(fake: FakeCommands, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(editors.threading, "Thread", lambda target, daemon: SimpleNamespace(start=target))
    monkeypatch.setattr(editors, "APP_FOLDERS", ())
    editors.prepare(REF)
    assert fake.ran == []


@pytest.mark.parametrize(
    ("fork", "ancestor", "own"),
    [(True, 1, 0), (False, 0, 0), (False, 128, 0), (False, 1, 1)],
    ids=["fork", "local branch behind", "missing branch", "local commits"],
)
def test_a_failed_checkout_that_can_not_move_safely_answers_gh_s_error(
    fake: FakeCommands, fork: bool, ancestor: int, own: int
) -> None:
    fake.fork, fake.ancestor, fake.own = fork, ancestor, own
    fake.checkout_error = "fatal: Not possible to fast-forward, aborting."
    with pytest.raises(commands.CommandError, match="Not possible to fast-forward"):
        editors.open_file(REF, "vscode", "a.py", 1)
    assert fake.git("checkout", "-B", "feature", "refs/remotes/origin/feature") not in fake.commands()
    assert fake.started == []


def test_a_move_that_would_overwrite_a_file_answers_git_s_error(fake: FakeCommands) -> None:
    fake.checkout_error = "fatal: Not possible to fast-forward, aborting."
    fake.move_error = "error: The following untracked working tree files would be overwritten by checkout"
    with pytest.raises(commands.CommandError, match="untracked working tree files would be overwritten"):
        editors.open_file(REF, "vscode", "a.py", 1)
    assert fake.started == []


@pytest.mark.parametrize(
    ("status", "branch_pr", "asks_branch_pr"),
    [("?? .idea/workspace.xml\n?? notes.txt\n", 8, False), (" M a.py\n", 7, True)],
    ids=["untracked files", "changes on the pull request's branch"],
)
def test_the_checkout_goes_on_with_untracked_files_or_changes_on_the_pull_requests_branch(
    fake: FakeCommands, status: str, branch_pr: int, asks_branch_pr: bool
) -> None:
    fake.status, fake.branch_pr = status, branch_pr
    editors.open_file(REF, "vscode", "a.py", 1)
    assert ((BRANCH_PR, editors.checkout_dir(REF)) in fake.ran) == asks_branch_pr
    assert fake.commands()[-2] == CHECKOUT
    assert len(fake.started) == 1


@pytest.mark.parametrize("branch_pr", [8, None], ids=["another pull request", "no pull request"])
def test_uncommitted_changes_on_another_branch_refuse_the_checkout(fake: FakeCommands, branch_pr: int | None) -> None:
    fake.status, fake.branch, fake.branch_pr = " M a.py\n?? notes.txt\n", "other", branch_pr
    with pytest.raises(editors.Refused) as info:
        editors.open_file(REF, "vscode", "a.py", 1)
    folder = editors.checkout_dir(REF)
    assert str(info.value) == f"The clone at {folder} has uncommitted changes on other. Commit or stash them first."
    assert CHECKOUT not in fake.commands()
    assert fake.started == []


@pytest.mark.parametrize(
    ("editor", "why"),
    [
        ("cursor", "'cursor' is not an installed editor (installed: 'vscode', 'pycharm')"),
        ("/bin/sh", "'/bin/sh' is not an installed editor (installed: 'vscode', 'pycharm')"),
    ],
)
def test_an_editor_outside_the_installed_ones_is_refused(fake: FakeCommands, editor: str, why: str) -> None:
    with pytest.raises(ValueError, match=f"^{re.escape(why)}$"):
        editors.open_file(REF, editor, "a.py", 1)
    assert fake.ran == [] and fake.started == []


@pytest.mark.parametrize("path", ["", "/etc/passwd", "../other/a.py", "src/../../a.py"])
def test_a_path_outside_the_repository_is_refused(fake: FakeCommands, path: str) -> None:
    with pytest.raises(ValueError, match="is not a path inside the repository"):
        editors.open_file(REF, "vscode", path, 1)
    assert fake.ran == [] and fake.started == []


def test_an_open_waits_while_the_last_launcher_hands_its_request_to_a_running_editor(fake: FakeCommands) -> None:
    fake.running, fake.pids = True, [200, 100]
    editors.open_file(REF, "pycharm", "a.py", 1)
    fake.ran.clear()
    with pytest.raises(editors.Refused, match="The editor is still opening"):
        editors.open_file(REF, "pycharm", "b.py", 1)
    pycharm = editors.APP_FOLDERS[0] / "PyCharm CE.app/Contents/MacOS/pycharm"
    assert fake.commands() == [["pgrep", "-o", "-f", "^" + str(pycharm).replace(".", "\\.")]]
    assert len(fake.started) == 1
    fake.pids = [100, 300]
    editors.open_file(REF, "pycharm", "b.py", 1)
    assert len(fake.started) == 2


def test_two_opens_at_once_clone_the_repository_once(fake: FakeCommands) -> None:
    fake.clone_seconds = 0.2
    threads = [threading.Thread(target=editors.open_file, args=(REF, "vscode", "a.py", 1)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [args[:3] for args in fake.commands()].count(["gh", "repo", "clone"]) == 1
    assert len(fake.started) == 2


def post_open(body: dict) -> tuple[int, dict]:
    h = request("POST", "/api/o/r/7/open", json.dumps(body).encode())
    h.do_POST()
    code, raw = response(h)
    return code, json.loads(raw)


def test_the_open_route_opens_the_file(fake: FakeCommands) -> None:
    assert post_open({"editor": "vscode", "path": "a.py", "line": 4}) == (200, {})
    assert fake.started[0][-1].endswith("/o/r/a.py:4")


def test_the_open_route_answers_a_refusal_with_its_reason(fake: FakeCommands) -> None:
    fake.status, fake.branch, fake.branch_pr = " M a.py\n", "other", 8
    code, body = post_open({"editor": "vscode", "path": "a.py", "line": 4})
    assert code == 400 and body["error"].endswith("has uncommitted changes on other. Commit or stash them first.")


def test_the_editors_route_lists_the_installed_editors_and_the_known_names_and_prepares_the_clone(
    fake: FakeCommands, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(editors.threading, "Thread", lambda target, daemon: SimpleNamespace(start=target))
    h = request("GET", "/api/o/r/7/editors")
    h.do_GET()
    code, raw = response(h)
    assert code == 200
    assert json.loads(raw) == {
        "installed": [{"id": "vscode", "name": "VS Code"}, {"id": "pycharm", "name": "PyCharm"}],
        "known": ["VS Code", "Cursor", "PyCharm", "IntelliJ IDEA", "WebStorm", "GoLand"],
    }
    assert editors.checkout_dir(REF).is_dir()


def test_start_runs_the_command_detached(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict] = []
    monkeypatch.setattr(commands.subprocess, "Popen", lambda args, **kwargs: seen.append(kwargs))
    commands.start(["tool"])
    assert seen[0]["start_new_session"] is True
    assert seen[0]["stdin"] == seen[0]["stdout"] == seen[0]["stderr"] == subprocess.DEVNULL


def test_start_names_a_missing_command(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(args: list[str], **kwargs: Any) -> None:
        raise FileNotFoundError(args[0])

    monkeypatch.setattr(commands.subprocess, "Popen", missing)
    with pytest.raises(commands.CommandError, match="^/x/code is not installed$"):
        commands.start(["/x/code"])
