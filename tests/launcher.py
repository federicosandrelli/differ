"""The fake `gh` and `claude` as commands the system can start by name.

POSIX starts the scripts themselves by their shebang lines. Windows starts only executables by name, and a .cmd
wrapper would cut the multi-line arguments the server passes at their first line break, so there each fake becomes
an .exe: distlib's launcher, taken from the pip wheel that ensurepip bundles, followed by a shebang line naming this
interpreter and a zip archive that holds the script as __main__.py."""

from __future__ import annotations

import atexit
import ensurepip
import functools
import io
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

TESTS = Path(__file__).resolve().parent
FAKE_CLAUDE = TESTS / "fakeclaude"
FAKE_GH = TESTS / "e2e" / "fakegh"


@functools.cache
def _folder() -> Path:
    folder = Path(tempfile.mkdtemp(prefix="differ-tests-bin-"))
    atexit.register(shutil.rmtree, folder, ignore_errors=True)
    return folder


def _windows_executable(script: Path, name: str) -> Path:
    wheel = next((Path(ensurepip.__file__).parent / "_bundled").glob("pip-*.whl"))
    with zipfile.ZipFile(wheel) as pip:
        stub = pip.read("pip/_vendor/distlib/t64.exe")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("__main__.py", script.read_text(encoding="utf-8"))
    folder = _folder() / name
    folder.mkdir(exist_ok=True)
    path = folder / f"{name}.exe"
    # The base interpreter, not the virtual environment's python.exe, which starts the base one as a process of its own;
    # the fakes need nothing from the environment.
    base = sys._base_executable  # pyright: ignore[reportAttributeAccessIssue]
    path.write_bytes(stub + f'#!"{base}"\r\n'.encode() + archive.getvalue())
    return path


@functools.cache
def fake_claude() -> Path:
    """The fake `claude`, for DIFFER_CLAUDE."""
    return _windows_executable(FAKE_CLAUDE, "claude") if sys.platform == "win32" else FAKE_CLAUDE


@functools.cache
def fake_gh_folder() -> Path:
    """A folder that holds the fake `gh` and nothing else, for the front of the PATH."""
    return _windows_executable(FAKE_GH / "fake_gh.py", "gh").parent if sys.platform == "win32" else FAKE_GH


def started_pid(start: dict) -> int:
    """The pid of the process the server started, from a start the fake claude logged: on Windows that is the
    launcher, and the fake runs under it."""
    return start["ppid"] if sys.platform == "win32" else start["pid"]
