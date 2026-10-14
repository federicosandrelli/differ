"""One server for the whole session, backed by a fixture repo and a fake `gh`, and a page without the CDN."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from helpers import commit_tree, git, make_repo

E2E = Path(__file__).resolve().parent
ROOT = E2E.parent.parent

OLD = {
    "src/app.py": "import os\n\n\ndef old_name(x):\n    return x + 1\n\n\ndef main():\n    print(old_name(2))\n",
    "src/cli.py": "from app import old_name\n\nprint(old_name(1))\n",
    "package-lock.json": '{"v": 1}\n',
    "README.md": "# Widgets\n",
    "docs/notes.txt": "Widgets are small.\n",
}
NEW = {
    "src/app.py": (
        "import os\nimport sys\n\n\ndef new_name(x):\n    return x + 1\n\n\n"
        'def main():\n    print(new_name(2))\n    print("hello")\n'
    ),
    "src/cli.py": "from app import new_name\n\nprint(new_name(1))\n",
    "package-lock.json": '{"v": 2}\n',
    "README.md": "# Widgets\n\nNow with a greeting.\n",
    "notes/notes.txt": "Widgets are small.\n",
}
TITLE = "Rename old_name to new_name"
LONG = {"src/long.py": "".join(f"x{n} = {n}\n" for n in range(1, 151))}

MODEL_FIELDS = (
    "    id: int\n    customer: str\n    amount: float\n    currency: str\n    created: str\n    shipped: bool\n"
)
MODELS_OLD = {
    "src/models.py": f"from dataclasses import dataclass\n\n\nTAX_RATE = 0.2\nSHIPPING = 5.0\nDISCOUNT = 0.1\n\n\n@dataclass\nclass Order:\n{MODEL_FIELDS}    old_total: float\n",
    "src/report.py": "def report(order):\n    return order.old_total\n",
}
MODELS_NEW = {
    "src/models.py": f"from dataclasses import dataclass\n\n\nTAX_RATE = 0.2\nSHIPPING = 5.0\nDISCOUNT = 0.1\n\n\n@dataclass\nclass Order:\n{MODEL_FIELDS}    new_total: float\n",
    "src/report.py": "def report(order):\n    return order.new_total\n",
}

CONFIG_FIELDS = "".join(f"    field_{n}: int\n" for n in range(1, 13))
CONFIG_ARGS = "".join(f"    arg_{n}={n},\n" for n in range(1, 13))
CONFIG_STEPS = "".join(f"    step_{n}()\n" for n in range(1, 13))
SERVICE_CALLS = {name: "".join(f"        {name}{n}()\n" for n in range(1, 13)) for name in ("a", "b")}
JOBS_FIELDS = "".join(f"    j{n} = {n}\n" for n in range(1, 13))
NEAR_LINES = "".join(f"y{n} = {n}\n" for n in range(2, 20))
CONFIG_OLD = {
    "src/config.py": f"class Config:\n{CONFIG_FIELDS}\n\napp = make_app(\n{CONFIG_ARGS})\n\n\ndef run():\n{CONFIG_STEPS}    return 0\n",
    "src/service.py": (
        f"class Service:\n    def m1(self):\n{SERVICE_CALLS['a']}        return 0\n\n"
        f'    def m2(self):\n{SERVICE_CALLS["b"]}        return 0\n\n    name = "service"\n'
    ),
    "src/jobs.py": f"class Jobs:\n{JOBS_FIELDS}\n    def run(self, x):\n        return x\n",
    "src/near.py": f"x = 1\n{NEAR_LINES}def f(a):\n    b = 1\n    return a\n",
}
CONFIG_NEW = {
    "src/config.py": (
        f"class Config:\n{CONFIG_FIELDS}    extra: bool\n\n\napp = make_app(\n{CONFIG_ARGS}    extra=True,\n)\n\n\n"
        f"def run():\n{CONFIG_STEPS}    return 1\n"
    ),
    "src/service.py": (
        f"class Service:\n    def m1(self):\n{SERVICE_CALLS['a']}        return 1\n\n"
        f'    def m2(self):\n{SERVICE_CALLS["b"]}        return 1\n\n    name = "service"\n    extra = True\n'
    ),
    "src/jobs.py": f"class Jobs:\n{JOBS_FIELDS}\n    def run(self, x, y):\n        return x\n",
    "src/near.py": f"x = 10\n{NEAR_LINES}def f(a):\n    b = 1\n    return a + b\n",
}


def pr_meta(number: int, base: str, head: str) -> dict:
    return {
        "number": number,
        "title": TITLE,
        "body": "Renames the helper and greets the user.",
        "author": {"login": "ann"},
        "url": f"https://github.com/acme/widgets/pull/{number}",
        "baseRefOid": base,
        "headRefOid": head,
        "baseRefName": "main",
        "headRefName": "greeting",
        "commits": [{"oid": head}],
        "state": "OPEN",
        "isDraft": False,
    }


def comment(cid: str, author: str, body: str) -> dict:
    return {
        "id": cid,
        "author": {"login": author},
        "body": body,
        "bodyHTML": "",
        "createdAt": "2026-10-01T10:00:00Z",
        "url": f"https://github.com/acme/widgets/pull/1#{cid}",
        "state": "SUBMITTED",
        "replyTo": None,
        "pullRequestReview": None,
    }


def thread(tid: str, line: int, body: str, author: str, resolved: bool = False) -> dict:
    return {
        "id": tid,
        "isResolved": resolved,
        "isOutdated": False,
        "path": "src/app.py",
        "line": line,
        "startLine": None,
        "diffSide": "RIGHT",
        "startDiffSide": None,
        "originalLine": line,
        "originalStartLine": None,
        "resolvedBy": {"login": "bob"} if resolved else None,
        "comments": {"nodes": [comment(f"{tid}-c1", author, body)]},
    }


def comments_state(head: str) -> dict:
    return {
        "head": head,
        "threads": [
            thread("T1", 11, "Say hi properly", "bob"),
            thread("T2", 6, "nit: spacing", "ann", resolved=True),
        ],
        "reviews": [
            {
                "id": "R1",
                "author": {"login": "bob"},
                "state": "APPROVED",
                "body": "LGTM",
                "bodyHTML": "",
                "createdAt": "2026-10-01T11:00:00Z",
                "submittedAt": "2026-10-01T11:00:00Z",
                "url": "https://github.com/acme/widgets/pull/1#R1",
            }
        ],
        "comments": [comment("K1", "ann", "Opening this for review.")],
    }


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if E2E in item.path.parents:
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(scope="session")
def gh_state(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The fake gh's files: the fixture repo with the pull request refs, the metadata and the comments."""
    root = tmp_path_factory.mktemp("e2e")
    repo, base, head = make_repo(root / "repo", OLD, NEW)
    state = root / "state"
    state.mkdir()
    (state / "repo.txt").write_text(str(repo))
    with_logo = commit_tree(repo, {**NEW, "src/logo.png": b"\x89PNG\0" * 1000}, "logo")
    models_base = commit_tree(repo, {**NEW, **MODELS_OLD}, "models base")
    models_head = commit_tree(repo, {**NEW, **MODELS_NEW}, "models head")
    with_long = commit_tree(repo, {**NEW, **LONG}, "long")
    config_base = commit_tree(repo, {**NEW, **CONFIG_OLD}, "config base")
    config_head = commit_tree(repo, {**NEW, **CONFIG_NEW}, "config head")
    for number, pr_base, pr_head in (
        (1, base, head),
        (2, base, head),
        (3, base, with_logo),
        (4, models_base, models_head),
        (5, head, with_long),
        (6, config_base, config_head),
    ):
        git(repo, "update-ref", f"refs/pull/{number}/head", pr_head)
        (state / f"pr{number}.json").write_text(json.dumps(pr_meta(number, pr_base, pr_head)))
    (state / "comments.json").write_text(json.dumps(comments_state(head)))
    (state / "mutations.log").write_text("")
    return state


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(scope="session")
def server_url(gh_state: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = free_port()
    env = {
        **os.environ,
        "PATH": f"{E2E / 'fakegh'}{os.pathsep}{os.environ['PATH']}",
        "DIFF_REVIEW_CACHE": str(tmp_path_factory.mktemp("cache")),
        "FAKE_GH_STATE": str(gh_state),
    }
    log = tmp_path_factory.mktemp("server") / "server.log"
    with log.open("w") as out:
        proc = subprocess.Popen([sys.executable, "server.py", str(port)], cwd=ROOT, env=env, stdout=out, stderr=out)
    url = f"http://localhost:{port}"
    deadline = time.time() + 15
    while True:
        try:
            urllib.request.urlopen(f"{url}/home", timeout=1).read()
            break
        except OSError:
            if proc.poll() is not None or time.time() > deadline:
                proc.kill()
                pytest.fail(f"server.py did not start:\n{log.read_text()}")
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait(timeout=5)
