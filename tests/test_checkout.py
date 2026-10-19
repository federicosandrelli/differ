import json
import threading
from pathlib import Path

import pytest

import checkout
from commands import CommandError
from github import PullRef
from helpers import GIT_ENV, Files, commit_tree, git

FAKE_GH = Path(__file__).resolve().parent / "e2e" / "fakegh"
REF = PullRef("o", "r", 7)
OTHER = PullRef("o", "r", 8)
FORK = PullRef("o", "r", 9)
BASE = {"a.txt": "one\n", "b.txt": "two\n"}
CHECKOUT = ["pr", "checkout", "7", "--repo", "o/r"]


class FakeGitHub:
    """The repository o/r behind the fake gh: a bare origin with BASE on main, a pusher that pushes to it as the pull
    requests' authors would, and the pull requests' heads."""

    def __init__(self, root: Path) -> None:
        self.state = root / "state"
        self.state.mkdir(parents=True)
        origin, self.pusher = root / "origin.git", root / "pusher"
        origin.mkdir()
        git(origin, "init", "-q", "--bare", "-b", "main")
        self.pusher.mkdir()
        git(self.pusher, "init", "-q", "-b", "main")
        git(self.pusher, "remote", "add", "origin", str(origin))
        (self.state / "repo.txt").write_text(str(origin))
        commit_tree(self.pusher, BASE, "base")
        git(self.pusher, "push", "-q", "origin", "main")
        self.branches: dict[int, str] = {}
        self.heads: dict[int, str] = {}

    def pr(self, number: int, branch: str, fork: bool = False) -> None:
        meta = {"headRefName": branch, "isCrossRepository": fork}
        (self.state / f"pr{number}.json").write_text(json.dumps(meta))
        self.branches[number] = branch
        git(self.pusher, "checkout", "-q", "-b", branch, "main")

    def commit(self, number: int, files: Files, amend: bool = False) -> str:
        """Commit the files on the pull request's branch, or amend its last commit, and push it, with --force when
        amending. A pull request from a fork pushes to refs/pull/<n>/head only. Returns the new head."""
        branch = self.branches[number]
        git(self.pusher, "checkout", "-q", branch)
        head = commit_tree(self.pusher, files, f"#{number}", amend=amend)
        meta = json.loads((self.state / f"pr{number}.json").read_text())
        refs = [f"HEAD:refs/pull/{number}/head"] + ([] if meta["isCrossRepository"] else [f"HEAD:refs/heads/{branch}"])
        git(self.pusher, "push", "-q", *(["--force"] if amend else []), "origin", *refs)
        self.heads[number] = head
        return head

    def fail(self, *commands: str) -> None:
        (self.state / "fail.txt").write_text("".join(f"{command}\n" for command in commands))

    def calls(self) -> list[list[str]]:
        log = self.state / "calls.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def clear_calls(self) -> None:
        (self.state / "calls.log").unlink(missing_ok=True)


@pytest.fixture
def gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(name, GIT_ENV[name])
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", GIT_ENV["GIT_CONFIG_GLOBAL"])
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("PATH", f"{FAKE_GH}:{GIT_ENV['PATH']}")
    gh = FakeGitHub(tmp_path / "github")
    monkeypatch.setenv("FAKE_GH_STATE", str(gh.state))
    gh.pr(7, "feature")
    gh.pr(8, "other")
    return gh


@pytest.fixture
def checkouts(gh: FakeGitHub, tmp_path: Path) -> checkout.Checkouts:
    return checkout.Checkouts(tmp_path / "checkouts", head=lambda ref: gh.heads[ref.number])


def at_head(checkouts: checkout.Checkouts, ref: PullRef = REF) -> Path:
    with checkouts.held(ref) as clone:
        return clone.at_head()


def head_of(folder: Path, rev: str = "HEAD") -> str:
    return git(folder, "rev-parse", rev).strip()


def branch_of(folder: Path) -> str:
    return git(folder, "branch", "--show-current").strip()


def test_the_first_at_head_clones_the_repository_and_checks_out_the_pull_request_at_its_head(
    gh: FakeGitHub, checkouts: checkout.Checkouts, tmp_path: Path
) -> None:
    head = gh.commit(7, {**BASE, "a.txt": "feature\n"})
    assert not (tmp_path / "checkouts").exists()
    folder = at_head(checkouts)
    assert folder == checkouts.folder(REF) == tmp_path / "checkouts/o/r"
    assert branch_of(folder) == "feature" and head_of(folder) == head
    assert not folder.with_name("r~partial").exists()


def test_a_clone_that_fails_to_set_its_default_leaves_no_clone_and_the_next_call_starts_over(
    gh: FakeGitHub, checkouts: checkout.Checkouts
) -> None:
    gh.commit(7, BASE)
    gh.fail("repo set-default")
    with pytest.raises(CommandError, match="injected failure of repo set-default"):
        at_head(checkouts)
    assert not checkouts.folder(REF).exists()
    gh.fail()
    folder = at_head(checkouts)
    assert [args[:2] for args in gh.calls()].count(["repo", "clone"]) == 2
    assert branch_of(folder) == "feature" and not folder.with_name("r~partial").exists()


def test_two_calls_at_once_clone_the_repository_once(gh: FakeGitHub, checkouts: checkout.Checkouts) -> None:
    gh.commit(7, BASE)
    folders: list[Path] = []
    threads = [threading.Thread(target=lambda: folders.append(at_head(checkouts))) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [args[:2] for args in gh.calls()].count(["repo", "clone"]) == 1
    assert folders == [checkouts.folder(REF)] * 2


def test_a_warm_up_and_a_call_at_once_clone_the_repository_once(gh: FakeGitHub, checkouts: checkout.Checkouts) -> None:
    gh.commit(7, BASE)
    folders: list[Path] = []
    threads = [
        threading.Thread(target=checkouts.warm, args=(REF,)),
        threading.Thread(target=lambda: folders.append(at_head(checkouts))),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [args[:2] for args in gh.calls()].count(["repo", "clone"]) == 1
    assert folders == [checkouts.folder(REF)]


def test_a_force_push_moves_the_branch_and_keeps_the_uncommitted_change_and_the_old_head_in_the_reflog(
    gh: FakeGitHub, checkouts: checkout.Checkouts
) -> None:
    old = gh.commit(7, {**BASE, "a.txt": "first\n"})
    folder = at_head(checkouts)
    new = gh.commit(7, {**BASE, "a.txt": "amended\n"}, amend=True)
    (folder / "b.txt").write_text("mine\n")
    at_head(checkouts)
    assert branch_of(folder) == "feature" and head_of(folder) == new
    assert (folder / "b.txt").read_text() == "mine\n"
    assert old in git(folder, "log", "-g", "--format=%H", "refs/heads/feature").split()
    assert head_of(folder, "@{upstream}") == new


def test_a_force_push_over_an_unpushed_commit_answers_gh_s_error_and_keeps_the_branch(
    gh: FakeGitHub, checkouts: checkout.Checkouts
) -> None:
    gh.commit(7, BASE)
    folder = at_head(checkouts)
    mine = commit_tree(folder, {**BASE, "b.txt": "mine\n"}, "mine")
    gh.commit(7, {**BASE, "a.txt": "amended\n"}, amend=True)
    with pytest.raises(CommandError, match="Not possible to fast-forward"):
        at_head(checkouts)
    assert branch_of(folder) == "feature" and head_of(folder, "refs/heads/feature") == mine


def test_a_force_push_moves_a_branch_with_its_local_commit_pushed_to_another_branch(
    gh: FakeGitHub, checkouts: checkout.Checkouts
) -> None:
    gh.commit(7, BASE)
    folder = at_head(checkouts)
    commit_tree(folder, {**BASE, "b.txt": "mine\n"}, "mine")
    git(folder, "push", "-q", "origin", "HEAD:refs/heads/elsewhere")
    new = gh.commit(7, {**BASE, "a.txt": "amended\n"}, amend=True)
    at_head(checkouts)
    assert branch_of(folder) == "feature" and head_of(folder) == new


def test_a_force_push_moves_a_branch_at_the_head_from_before_the_oldest_of_several_reflog_entries(
    gh: FakeGitHub, checkouts: checkout.Checkouts
) -> None:
    old = gh.commit(7, {**BASE, "a.txt": "first\n"})
    folder = at_head(checkouts)
    amended = gh.commit(7, {**BASE, "a.txt": "amended\n"}, amend=True)
    git(folder, "fetch", "-q", "origin")
    new = gh.commit(7, {**BASE, "a.txt": "three\n"})
    git(folder, "fetch", "-q", "origin")
    assert git(folder, "log", "-g", "--format=%H", "refs/remotes/origin/feature").split() == [new, amended]
    assert head_of(folder) == old
    at_head(checkouts)
    assert head_of(folder) == new


def test_a_force_push_of_a_fork_s_pull_request_answers_gh_s_error_and_keeps_the_branch(
    gh: FakeGitHub, checkouts: checkout.Checkouts
) -> None:
    gh.pr(9, "fix", fork=True)
    old = gh.commit(9, {**BASE, "a.txt": "first\n"})
    git(gh.pusher, "checkout", "-q", "-b", "unrelated", "main")
    commit_tree(gh.pusher, {**BASE, "c.txt": "unrelated\n"}, "unrelated")
    git(gh.pusher, "push", "-q", "origin", f"{old}:refs/heads/keep", "HEAD:refs/heads/fix")
    folder = at_head(checkouts, FORK)
    assert branch_of(folder) == "fix" and head_of(folder) == old
    gh.commit(9, {**BASE, "a.txt": "amended\n"}, amend=True)
    with pytest.raises(CommandError, match="Not possible to fast-forward"):
        at_head(checkouts, FORK)
    assert branch_of(folder) == "fix" and head_of(folder) == old


def test_untracked_files_on_another_pull_request_s_branch_let_the_checkout_go_on(
    gh: FakeGitHub, checkouts: checkout.Checkouts
) -> None:
    head = gh.commit(7, BASE)
    gh.commit(8, {**BASE, "a.txt": "other\n"})
    folder = at_head(checkouts, OTHER)
    (folder / "notes.txt").write_text("mine\n")
    at_head(checkouts)
    assert branch_of(folder) == "feature" and head_of(folder) == head
    assert (folder / "notes.txt").read_text() == "mine\n"


def test_a_force_push_that_adds_a_file_the_clone_holds_untracked_answers_git_s_error_and_keeps_the_file(
    gh: FakeGitHub, checkouts: checkout.Checkouts
) -> None:
    old = gh.commit(7, {**BASE, "a.txt": "first\n"})
    folder = at_head(checkouts)
    gh.commit(7, {**BASE, "a.txt": "amended\n", "new.txt": "theirs\n"}, amend=True)
    (folder / "new.txt").write_text("mine\n")
    with pytest.raises(CommandError, match="untracked working tree files would be overwritten"):
        at_head(checkouts)
    assert (folder / "new.txt").read_text() == "mine\n" and head_of(folder) == old


def test_a_push_that_would_overwrite_an_uncommitted_change_answers_gh_s_error_and_keeps_the_change(
    gh: FakeGitHub, checkouts: checkout.Checkouts
) -> None:
    old = gh.commit(7, BASE)
    folder = at_head(checkouts)
    gh.commit(7, {**BASE, "b.txt": "theirs\n"})
    (folder / "b.txt").write_text("mine\n")
    with pytest.raises(CommandError, match="would be overwritten by merge"):
        at_head(checkouts)
    assert (folder / "b.txt").read_text() == "mine\n" and head_of(folder) == old


def test_a_failed_first_checkout_answers_gh_s_error(gh: FakeGitHub, checkouts: checkout.Checkouts) -> None:
    gh.commit(7, BASE)
    gh.fail("pr checkout")
    with pytest.raises(CommandError, match="injected failure of pr checkout"):
        at_head(checkouts)
    assert branch_of(checkouts.folder(REF)) == "main"


@pytest.mark.parametrize("path", ["notes.txt", "b.txt"], ids=["untracked files", "a tracked change"])
def test_the_checkout_goes_on_with_untracked_files_or_changes_on_the_pull_request_s_branch(
    gh: FakeGitHub, checkouts: checkout.Checkouts, path: str
) -> None:
    gh.commit(7, BASE)
    folder = at_head(checkouts)
    new = gh.commit(7, {**BASE, "a.txt": "three\n"})
    (folder / path).write_text("mine\n")
    at_head(checkouts)
    assert head_of(folder) == new and (folder / path).read_text() == "mine\n"


@pytest.mark.parametrize("pull_request", [True, False], ids=["another pull request", "no pull request"])
def test_uncommitted_changes_on_another_branch_refuse_the_checkout(
    gh: FakeGitHub, checkouts: checkout.Checkouts, pull_request: bool
) -> None:
    gh.commit(7, BASE)
    gh.commit(8, {**BASE, "a.txt": "other\n"})
    if pull_request:
        folder, branch = at_head(checkouts, OTHER), "other"
    else:
        folder, branch = at_head(checkouts), "mine"
        git(folder, "checkout", "-q", "-b", branch)
    (folder / "b.txt").write_text("mine\n")
    before = head_of(folder)
    gh.clear_calls()
    with pytest.raises(checkout.Refused) as info:
        at_head(checkouts)
    assert str(info.value) == f"The clone at {folder} has uncommitted changes on {branch}. Commit or stash them first."
    assert branch_of(folder) == branch and head_of(folder) == before
    assert CHECKOUT not in gh.calls()


def test_uncommitted_changes_on_a_detached_head_refuse_the_checkout(
    gh: FakeGitHub, checkouts: checkout.Checkouts
) -> None:
    gh.commit(7, BASE)
    folder = at_head(checkouts)
    git(folder, "checkout", "-q", "--detach")
    (folder / "b.txt").write_text("mine\n")
    before = head_of(folder)
    gh.commit(7, {**BASE, "a.txt": "three\n"})
    with pytest.raises(checkout.Refused) as info:
        at_head(checkouts)
    message = f"The clone at {folder} has uncommitted changes on a detached HEAD. Commit or stash them first."
    assert str(info.value) == message
    assert branch_of(folder) == "" and head_of(folder) == before


@pytest.mark.parametrize(
    ("ref", "pushed", "fast"),
    [(REF, False, True), (REF, True, False), (OTHER, False, False), (FORK, False, False)],
    ids=[
        "same pull request at its head",
        "new push",
        "another pull request",
        "a fork's pull request on the same branch name",
    ],
)
def test_the_pull_request_checked_out_last_at_its_head_needs_no_checkout(
    gh: FakeGitHub, checkouts: checkout.Checkouts, ref: PullRef, pushed: bool, fast: bool
) -> None:
    head = gh.commit(7, BASE)
    gh.commit(8, {**BASE, "a.txt": "other\n"})
    if ref == FORK:
        (gh.state / "pr9.json").write_text(json.dumps({"headRefName": "feature", "isCrossRepository": True}))
        git(gh.pusher, "push", "-q", "origin", f"{head}:refs/pull/9/head")
        gh.heads[9] = head
    at_head(checkouts)
    if pushed:
        gh.commit(7, {**BASE, "a.txt": "three\n"})
    gh.clear_calls()
    folder = at_head(checkouts, ref)
    assert (not any(args[:2] == ["pr", "checkout"] for args in gh.calls())) is fast
    assert head_of(folder) == gh.heads[ref.number]


def test_after_warm_at_head_runs_no_clone_and_no_head_branch_lookup(
    gh: FakeGitHub, checkouts: checkout.Checkouts
) -> None:
    gh.commit(7, BASE)
    checkouts.warm(REF)
    assert checkouts.folder(REF).is_dir()
    gh.clear_calls()
    at_head(checkouts)
    assert gh.calls() == [CHECKOUT]
