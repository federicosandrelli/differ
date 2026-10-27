"""The repositories the reader follows, as `owner/repo` names in followed.json in the cache."""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import date, timedelta

import build
import github
from github import OWNER, REPO

PATH = build.CACHE / "followed.json"
FIRST_DAYS = 7
NAME = re.compile(rf"^(?:https?://github\.com/)?(?P<owner>{OWNER})/(?P<repo>{REPO})$")
lock = threading.Lock()


class NotARepository(Exception):
    """The text names no repository."""


def parse(text: str) -> str:
    """The `owner/repo` name the text gives, in lower case, without a slash after it and without the `.git` a clone
    URL ends in. GitHub matches names case-insensitively. Raises NotARepository for other text."""
    m = NAME.match(text.strip().lower().removesuffix("/").removesuffix(".git"))
    if not m:
        raise NotARepository(f"Not a repository: {text.strip()!r}. Use owner/repo or the repository's URL.")
    return f"{m['owner']}/{m['repo']}"


def names(texts: list[str]) -> list[str]:
    """The names the texts give, each once, in their order. Raises NotARepository for a text that names no
    repository."""
    return list(dict.fromkeys(parse(text) for text in texts))


def read() -> list[str] | None:
    """The saved list; None before the first save, and empty when the file is broken."""
    try:
        data = json.loads(PATH.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return []
    return [r for r in data if isinstance(r, str)] if isinstance(data, list) else []


def write(repos: list[str]) -> None:
    """Replace followed.json with the list. The list goes to followed.json.tmp first, which then takes the place
    of followed.json, so a crash mid-write leaves the old list. Call it with the lock held."""
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_name(PATH.name + ".tmp")
    tmp.write_text(json.dumps(repos))
    os.replace(tmp, PATH)


def load() -> list[str]:
    """The followed repositories, in the order they were added. Before the first save, the repositories where the
    viewer opened a pull request in the last FIRST_DAYS days, the one with the newest pull request first, saved as
    the list, less any name that is no repository; empty, with nothing saved, when GitHub can't answer. A list
    saved while GitHub was answering wins over GitHub's."""
    if (saved := read()) is not None:
        return saved
    first = github.repos_of_recent_prs(date.today() - timedelta(days=FIRST_DAYS))
    if first is None:
        return []
    repos = []
    for name in first:
        try:
            repos.append(parse(name))
        except NotARepository:
            pass
    repos = list(dict.fromkeys(repos))
    with lock:
        if (saved := read()) is not None:
            return saved
        write(repos)
    return repos


def save(repos: list[str]) -> list[str]:
    """Replace the list with the names the texts give, each once, in their order. Raises NotARepository for a
    text that names no repository, and saves nothing then."""
    saved = names(repos)
    with lock:
        write(saved)
    return saved
