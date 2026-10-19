"""Run the commands the app relies on, git and gh, with a time limit, and say how one failed."""

from __future__ import annotations

import subprocess
from pathlib import Path

TIMEOUT = 120
# A clone or a fetch downloads history, so it gets longer.
LONG_TIMEOUT = 600
ERROR_MAX = 600


class CommandError(Exception):
    """A command is not installed, did not finish in time, or failed; the message names it."""


def call(
    args: list[str], input: bytes | None = None, timeout: float = TIMEOUT, cwd: Path | None = None
) -> subprocess.CompletedProcess[bytes]:
    """Run the command in cwd (by default this process's directory) and wait for it, whatever its exit code. Raises
    CommandError when it is not installed or does not finish in time."""
    try:
        return subprocess.run(args, capture_output=True, input=input, timeout=timeout, cwd=cwd)
    except FileNotFoundError:
        name = "The GitHub CLI (gh)" if args[0] == "gh" else args[0]
        raise CommandError(f"{name} is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise CommandError(f"`{shown(args)}` did not finish within {timeout:g} seconds") from None


def run(args: list[str], input: bytes | None = None, timeout: float = TIMEOUT, cwd: Path | None = None) -> bytes:
    """The command's output. Raises CommandError as call does, and when the command fails."""
    r = call(args, input, timeout, cwd)
    if r.returncode:
        err = r.stderr.decode(errors="replace").strip() or r.stdout.decode(errors="replace").strip()
        raise CommandError(f"`{shown(args)}` failed: {tail(err)}")
    return r.stdout


def start(args: list[str]) -> None:
    """Start the command without waiting for it, in a session of its own, so it keeps running after this process
    exits. Raises CommandError when it is not installed."""
    try:
        subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
    except FileNotFoundError:
        raise CommandError(f"{args[0]} is not installed") from None


def shown(args: list[str]) -> str:
    """The start of a command line, enough to name the command in a message."""
    return " ".join(args[:4])


def tail(text: str) -> str:
    """The last ERROR_MAX characters of the text, after an ellipsis when the text is longer. A command prints its
    error last, after any progress or warnings."""
    return text if len(text) <= ERROR_MAX else "…" + text[-ERROR_MAX:]
