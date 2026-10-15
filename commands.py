"""Run the commands the app relies on, git and gh, with a time limit, and say how one failed."""

from __future__ import annotations

import subprocess

TIMEOUT = 120
# A clone or a fetch downloads history, so it gets longer.
LONG_TIMEOUT = 600


class CommandError(Exception):
    """A command is not installed, did not finish in time, or failed; the message names it."""


def call(args: list[str], input: bytes | None = None, timeout: float = TIMEOUT) -> subprocess.CompletedProcess[bytes]:
    """Run the command and wait for it, whatever its exit code. Raises CommandError when it is not installed or
    does not finish in time."""
    try:
        return subprocess.run(args, capture_output=True, input=input, timeout=timeout)
    except FileNotFoundError:
        name = "The GitHub CLI (gh)" if args[0] == "gh" else args[0]
        raise CommandError(f"{name} is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise CommandError(f"`{shown(args)}` did not finish within {timeout:g} seconds") from None


def run(args: list[str], input: bytes | None = None, timeout: float = TIMEOUT) -> bytes:
    """The command's output. Raises CommandError as call does, and when the command fails."""
    r = call(args, input, timeout)
    if r.returncode:
        err = r.stderr.decode(errors="replace").strip() or r.stdout.decode(errors="replace").strip()
        raise CommandError(f"`{shown(args)}` failed: {err[:600]}")
    return r.stdout


def shown(args: list[str]) -> str:
    """The start of a command line, enough to name the command in a message."""
    return " ".join(args[:4])
