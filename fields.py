"""The checks of the fields a client sends: a page action's JSON object (`actions.py`), or the arguments of a tool of
the differ MCP server (`mcp.py`). Each answers the field's value as its type, or raises Invalid, a ValueError that says
what is wrong with it, which an action answers with 400 and a tool with an error for Claude."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, get_args


class Invalid(ValueError):
    """A field that is missing, of another type, or out of its range."""


def field[T](fields: dict, name: str, check: Callable[[object], T]) -> T:
    """The value of the field with that name, as check gives it; else Invalid, which names the field."""
    if name not in fields:
        raise Invalid(f"{name} is missing")
    try:
        return check(fields[name])
    except Invalid as exc:
        raise Invalid(f"{name}: {exc}") from exc


def optional[T, D](fields: dict, name: str, check: Callable[[object], T], default: D) -> T | D:
    """The value of the field with that name, as check gives it, or default when it is missing or null."""
    return default if fields.get(name) is None else field(fields, name, check)


def one_of(value: object, kind: object) -> Any:
    """The value of a field when the Literal type allows it; else Invalid."""
    if value not in get_args(kind):
        raise Invalid(f"{value!r} is not one of {', '.join(map(repr, get_args(kind)))}")
    return value


def flag(value: object) -> bool:
    """The yes or no a field names; else Invalid."""
    if type(value) is not bool:
        raise Invalid(f"{value!r} is not true or false")
    return value


def run_id(value: object) -> int:
    """The workflow run id a field names; else Invalid."""
    if type(value) is not int or value <= 0:
        raise Invalid(f"{value!r} is not a workflow run id")
    return value


def line_number(value: object) -> int:
    """The line number a field names; else Invalid."""
    if type(value) is not int or value < 1:
        raise Invalid(f"{value!r} is not a line number")
    return value


def count_of(value: object) -> int:
    """The count a field names; else Invalid."""
    if type(value) is not int or value < 0:
        raise Invalid(f"{value!r} is not a count")
    return value


def text_of(value: object) -> str:
    """The text a field holds; else Invalid."""
    if not isinstance(value, str) or not value.strip():
        raise Invalid(f"{value!r} is not a text")
    return value


def lines_of(value: object) -> list[str]:
    """The lines of code a field holds; else Invalid."""
    if not isinstance(value, list) or not all(isinstance(line, str) for line in value):
        raise Invalid(f"{value!r} is not a list of lines")
    return value


def string_of(value: object) -> str:
    """The text a field holds, which may be empty; else Invalid."""
    if not isinstance(value, str):
        raise Invalid(f"{value!r} is not a string")
    return value
