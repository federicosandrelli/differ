"""The checks of the fields a client sends: a page action's JSON object (`actions.py`), or the arguments of a tool of
the differ MCP server (`mcp.py`). Each answers the field's value as its type, or raises a ValueError that says what is
wrong with it, which an action answers with 400 and a tool with an error for Claude."""

from __future__ import annotations

from typing import Any, get_args


def one_of(value: object, kind: object) -> Any:
    """The value of a field when the Literal type allows it; else a ValueError."""
    if value not in get_args(kind):
        raise ValueError(f"{value!r} is not one of {', '.join(map(repr, get_args(kind)))}")
    return value


def flag(value: object) -> bool:
    """The yes or no a field names; else a ValueError."""
    if type(value) is not bool:
        raise ValueError(f"{value!r} is not true or false")
    return value


def run_id(value: object) -> int:
    """The workflow run id a field names; else a ValueError."""
    if type(value) is not int or value <= 0:
        raise ValueError(f"{value!r} is not a workflow run id")
    return value


def line_number(value: object) -> int:
    """The line number a field names; else a ValueError."""
    if type(value) is not int or value < 1:
        raise ValueError(f"{value!r} is not a line number")
    return value


def count_of(value: object) -> int:
    """The count a field names; else a ValueError."""
    if type(value) is not int or value < 0:
        raise ValueError(f"{value!r} is not a count")
    return value


def text_of(value: object) -> str:
    """The text a field holds; else a ValueError."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{value!r} is not a text")
    return value


def lines_of(value: object) -> list[str]:
    """The lines of code a field holds; else a ValueError."""
    if not isinstance(value, list) or not all(isinstance(line, str) for line in value):
        raise ValueError(f"{value!r} is not a list of lines")
    return value
