import subprocess
from typing import Any

import pytest

import commands


def failing(stderr: bytes) -> Any:
    return lambda args, **kwargs: subprocess.CompletedProcess(args, 1, b"", stderr)


def test_a_failed_command_keeps_the_end_of_a_long_error(monkeypatch: pytest.MonkeyPatch) -> None:
    progress = "".join(f"remote: Counting objects: {n}%\n" for n in range(100))
    monkeypatch.setattr(commands.subprocess, "run", failing(f"{progress}fatal: bad object deadbeef\n".encode()))
    with pytest.raises(commands.CommandError) as info:
        commands.run(["git", "fetch", "origin", "deadbeef"])
    message = str(info.value)
    assert message.startswith("`git fetch origin deadbeef` failed: …")
    assert message.endswith("fatal: bad object deadbeef")
    assert len(message.split("failed: …", 1)[1]) == commands.ERROR_MAX


def test_a_failed_command_keeps_a_short_error_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands.subprocess, "run", failing(b"no pull requests found\n"))
    with pytest.raises(commands.CommandError, match=r"^`gh pr view 9` failed: no pull requests found$"):
        commands.run(["gh", "pr", "view", "9"])
