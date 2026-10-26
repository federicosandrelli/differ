"""The programming languages, and the import lines, symbols, test names, definitions and module names of each."""

from __future__ import annotations

import ast
import re
from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import Literal, NamedTuple, TypedDict


class Symbol(TypedDict):
    """A class, a function, or a call statement that spans several lines, in a Python file. It starts on `start`
    (at its first decorator), its body on `body`, and it ends on `end`."""

    name: str
    kind: Literal["class", "def", "call"]
    start: int
    body: int
    end: int


# ---------------------------------------------------------------- structure


def first_line(node: ast.stmt) -> int:
    """Return the line a statement starts on, counting its decorators."""
    decorators = getattr(node, "decorator_list", None)
    return decorators[0].lineno if decorators else node.lineno


def last_line(node: ast.stmt) -> int:
    return node.end_lineno or node.lineno


def python_structure(source: str) -> tuple[set[int], list[Symbol]]:
    """Return (import line numbers, symbols) for a Python source; both empty when it does not parse."""
    imports: set[int] = set()
    symbols: list[Symbol] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return imports, symbols

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Import | ast.ImportFrom):
                imports.update(range(child.lineno, last_line(child) + 1))
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                kind = "class" if isinstance(child, ast.ClassDef) else "def"
                symbols.append(
                    Symbol(
                        name=child.name,
                        kind=kind,
                        start=first_line(child),
                        body=first_line(child.body[0]),
                        end=last_line(child),
                    )
                )
                visit(child)
            elif (
                isinstance(child, ast.Expr | ast.Assign | ast.AnnAssign | ast.AugAssign)
                and isinstance(child.value, ast.Call)
                and last_line(child) != child.lineno
            ):
                callee = ast.unparse(child.value.func).rsplit(".", 1)[-1]
                symbols.append(
                    Symbol(name=callee, kind="call", start=child.lineno, body=child.lineno + 1, end=last_line(child))
                )
            elif isinstance(child, ast.stmt | ast.excepthandler | ast.match_case):
                visit(child)

    visit(tree)
    return imports, symbols


JAVA_IMPORT_RE = re.compile(r"import(?:\s+static)?(?:\s+[\w.*]+)?\s*;?")
JAVA_IMPORT_TAIL_RE = re.compile(r"[\w.*]+\s*;?")
JAVA_PACKAGE_RE = re.compile(r"package\s+[\w.\s]+;")
COMMENT_OR_STRING_RE = re.compile(r"""//|/\*|'(?:[^'\\]|\\.)*'?|"(?:[^"\\]|\\.)*"?""")


def java_code(line: str, in_comment: bool) -> tuple[str, bool]:
    """Return a Java or JavaScript line with its comments removed, and whether a block comment is still open at its
    end. Comment markers inside a quoted string stay."""
    code = []
    i = 0
    while i < len(line):
        if in_comment:
            end = line.find("*/", i)
            if end < 0:
                break
            i, in_comment = end + 2, False
            continue
        m = COMMENT_OR_STRING_RE.search(line, i)
        if m is None:
            code.append(line[i:])
            break
        if m[0] == "//":
            code.append(line[i : m.start()])
            break
        if m[0] == "/*":
            code.append(line[i : m.start()] + " ")
            i, in_comment = m.end(), True
        else:
            code.append(line[i : m.end()])
            i = m.end()
    return "".join(code).strip(), in_comment


def java_imports(source: str) -> set[int]:
    """Return the import line numbers of a Java source: the import statements in the header before the first
    type declaration, where only comments, blank lines and the package statement may sit between them."""
    imports: set[int] = set()
    in_comment = in_import = False
    for no, line in enumerate(source.removeprefix("﻿").split("\n"), 1):
        code, in_comment = java_code(line, in_comment)
        if not code:
            continue
        if in_import and JAVA_IMPORT_TAIL_RE.fullmatch(code):
            imports.add(no)
            in_import = not code.endswith(";")
        elif JAVA_IMPORT_RE.fullmatch(code):
            imports.add(no)
            in_import = not code.endswith(";")
        elif JAVA_PACKAGE_RE.fullmatch(code):
            in_import = False
        else:
            break
    return imports


JS_IMPORT_RE = re.compile(r"""import(?![\w$])[\w$\s,*{}]*(['"])[^'"]*\1\s*;?""")
JS_IMPORT_HEAD_RE = re.compile(r"import(?![\w$])[\w$\s,*{}]*")
JS_IMPORT_MIDDLE_RE = re.compile(r"[\w$\s,*{}]+")
JS_IMPORT_TAIL_RE = re.compile(r"""[\w$\s,*{}]*(?<![\w$])from\s*(['"])[^'"]*\1\s*;?""")
JS_LINE_BREAK_RE = re.compile("[\r\u2028\u2029]")
JS_DIRECTIVE_RE = re.compile(r"""(['"])[\w\s-]*\1\s*;?""")
JS_IMPORT_CONTINUATION_RE = re.compile(r"with(?![\w$])|;")


def js_imports(source: str) -> set[int]:
    """Return the import line numbers of a JavaScript or TypeScript source: the import statements in the header before
    the first other statement, where only comments, blank lines, a shebang and directives such as "use client" may sit
    between them, and comments may also sit inside a statement. A statement counts only once its module path closes
    it, and only when the next line does not continue it. The scan stops at a line break that git does not count."""
    imports: set[int] = set()
    statement: list[int] = []
    closed: list[int] = []
    in_comment = False
    for no, line in enumerate(source.removeprefix("\ufeff").replace("\ufeff", " ").split("\n"), 1):
        if JS_LINE_BREAK_RE.search(line.removesuffix("\r")):
            imports.difference_update(closed)
            break
        if no == 1 and line.startswith("#!"):
            continue
        code, in_comment = java_code(line, in_comment)
        if not code:
            if statement and line.strip():
                statement.append(no)
            continue
        if JS_IMPORT_RE.fullmatch(code) or (statement and JS_IMPORT_TAIL_RE.fullmatch(code)):
            imports.update(statement, [no])
            closed = [] if code.endswith(";") else [*statement, no]
            statement = []
        elif (JS_IMPORT_MIDDLE_RE if statement else JS_IMPORT_HEAD_RE).fullmatch(code):
            statement.append(no)
        elif not statement and JS_DIRECTIVE_RE.fullmatch(code):
            continue
        else:
            if not statement and JS_IMPORT_CONTINUATION_RE.match(code):
                imports.difference_update(closed)
            break
    return imports


# A definition is a class, a def, an annotation, or an assignment with spaces round the equals sign.
# A keyword argument (name=value) has none, so it does not match.
DEF_RE = r"^\s*(?:class|async def|def)\s+{name}\b|^\s*{name}(?::|\s=\s)"


def is_definition(line: str, name: str) -> bool:
    short = name.rsplit(".", 1)[-1]
    return re.match(DEF_RE.format(name=re.escape(short)), line) is not None


# ---------------------------------------------------------------- languages


class Language(Enum):
    PYTHON = auto()
    JAVASCRIPT = auto()
    TYPESCRIPT = auto()
    JAVA = auto()
    KOTLIN = auto()
    SWIFT = auto()
    CSHARP = auto()
    PHP = auto()
    GO = auto()
    RUBY = auto()
    DART = auto()
    ELIXIR = auto()
    RUST = auto()
    C = auto()
    CPP = auto()
    LUA = auto()
    SHELL = auto()
    SQL = auto()
    R = auto()
    PERL = auto()


JVM_TEST_NAME = r"\wTests?$|[a-z0-9]IT$"


class Structure(NamedTuple):
    import_lines: set[int]
    symbols: list[Symbol]


class LanguageSupport(ABC):
    """The import lines, symbols, test names, definitions and module names of one language."""

    @abstractmethod
    def structure(self, source: str) -> Structure:
        """The import lines and the symbols of a source."""

    @abstractmethod
    def is_test_name(self, stem: str) -> bool:
        """Whether a file name without its suffix names a test file."""

    @abstractmethod
    def is_definition(self, line: str, name: str) -> bool:
        """Whether the line defines the name."""

    @abstractmethod
    def module_name(self, path: str) -> str:
        """The dotted module name of the file at the path."""


class Python(LanguageSupport):
    def structure(self, source: str) -> Structure:
        return Structure(*python_structure(source))

    def is_test_name(self, stem: str) -> bool:
        return re.search(r"_test$|^conftest$", stem) is not None

    def is_definition(self, line: str, name: str) -> bool:
        return is_definition(line, name)

    def module_name(self, path: str) -> str:
        return re.sub(r"\.py$", "", path).replace("/", ".")


class Java(LanguageSupport):
    def structure(self, source: str) -> Structure:
        return Structure(java_imports(source), [])

    def is_test_name(self, stem: str) -> bool:
        return re.search(JVM_TEST_NAME, stem) is not None

    def is_definition(self, line: str, name: str) -> bool:
        return is_definition(line, name)

    def module_name(self, path: str) -> str:
        return path.replace("/", ".")


class JavaScript(LanguageSupport):
    """JavaScript and TypeScript."""

    def structure(self, source: str) -> Structure:
        return Structure(js_imports(source), [])

    def is_test_name(self, stem: str) -> bool:
        return re.search(r"\.(?:test|spec|cy)$|-test$", stem) is not None

    def is_definition(self, line: str, name: str) -> bool:
        return is_definition(line, name)

    def module_name(self, path: str) -> str:
        return path.replace("/", ".")


class Plain(LanguageSupport):
    """A language without import lines or symbols. Its test files match `test_name`, if given."""

    def __init__(self, test_name: str | None) -> None:
        self.test_name = test_name

    def structure(self, source: str) -> Structure:
        return Structure(set(), [])

    def is_test_name(self, stem: str) -> bool:
        return self.test_name is not None and re.search(self.test_name, stem) is not None

    def is_definition(self, line: str, name: str) -> bool:
        return is_definition(line, name)

    def module_name(self, path: str) -> str:
        return path.replace("/", ".")


NO_LANGUAGE = Plain(None)


def support(language: Language) -> LanguageSupport:
    match language:
        case Language.PYTHON:
            return Python()
        case Language.JAVASCRIPT | Language.TYPESCRIPT:
            return JavaScript()
        case Language.JAVA:
            return Java()
        case Language.KOTLIN:
            return Plain(JVM_TEST_NAME)
        case Language.SWIFT | Language.CSHARP | Language.PHP:
            return Plain(r"\wTests?$")
        case Language.GO | Language.DART | Language.ELIXIR:
            return Plain(r"_test$")
        case Language.RUBY:
            return Plain(r"_test$|_spec$")
        case (
            Language.RUST
            | Language.C
            | Language.CPP
            | Language.LUA
            | Language.SHELL
            | Language.SQL
            | Language.R
            | Language.PERL
        ):
            return Plain(None)
