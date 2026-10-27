"""The programming languages, and the import lines, symbols, test names, definitions and module names of each."""

from __future__ import annotations

import ast
import re
from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import Literal, NamedTuple, TypedDict


class Symbol(TypedDict):
    """A class, a function, or a call with arguments below its opening parenthesis, in a Python file. It starts on
    `start` (a class or a function at its first decorator, a call at its opening parenthesis), its body on `body`,
    and it ends on `end`."""

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


def last_line(node: ast.stmt | ast.expr) -> int:
    return node.end_lineno or node.lineno


def python_structure(source: str) -> tuple[set[int], list[Symbol]]:
    """Return (import line numbers, symbols) for a Python source; both empty when it does not parse. The symbols
    come in the order they start, an outer one before an inner one that starts on the same line."""
    imports: set[int] = set()
    symbols: list[Symbol] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return imports, symbols
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            imports.update(range(node.lineno, last_line(node) + 1))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            kind = "class" if isinstance(node, ast.ClassDef) else "def"
            symbols.append(
                Symbol(
                    name=node.name,
                    kind=kind,
                    start=first_line(node),
                    body=first_line(node.body[0]),
                    end=last_line(node),
                )
            )
        elif isinstance(node, ast.Call) and last_line(node) > last_line(node.func):
            start = last_line(node.func)
            callee = ast.unparse(node.func).rsplit(".", 1)[-1]
            symbols.append(Symbol(name=callee, kind="call", start=start, body=start + 1, end=last_line(node)))
    symbols.sort(key=lambda s: (s["start"], -s["end"]))
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


def name(language: Language) -> str:
    """The language's name as it is written."""
    match language:
        case Language.PYTHON:
            return "Python"
        case Language.JAVASCRIPT:
            return "JavaScript"
        case Language.TYPESCRIPT:
            return "TypeScript"
        case Language.JAVA:
            return "Java"
        case Language.KOTLIN:
            return "Kotlin"
        case Language.SWIFT:
            return "Swift"
        case Language.CSHARP:
            return "C#"
        case Language.PHP:
            return "PHP"
        case Language.GO:
            return "Go"
        case Language.RUBY:
            return "Ruby"
        case Language.DART:
            return "Dart"
        case Language.ELIXIR:
            return "Elixir"
        case Language.RUST:
            return "Rust"
        case Language.C:
            return "C"
        case Language.CPP:
            return "C++"
        case Language.LUA:
            return "Lua"
        case Language.SHELL:
            return "Shell"
        case Language.SQL:
            return "SQL"
        case Language.R:
            return "R"
        case Language.PERL:
            return "Perl"


JVM_TEST_NAME = r"\wTests?$|[a-z0-9]IT$"


class Structure(NamedTuple):
    import_lines: set[int]
    symbols: list[Symbol]


class LanguageSupport(ABC):
    """The import lines, symbols, test names, definitions and module names of one language."""

    @abstractmethod
    def structure(self, source: str) -> Structure:
        """The import lines and the symbols of a source."""

    @property
    @abstractmethod
    def test_name_pattern(self) -> str | None:
        """The regular expression a file name without its suffix matches when it names a test file, or None when
        the language names its test files by their folder or prefix only."""

    def is_test_name(self, stem: str) -> bool:
        """Whether a file name without its suffix names a test file."""
        return self.test_name_pattern is not None and re.search(self.test_name_pattern, stem) is not None

    @abstractmethod
    def is_definition(self, line: str, name: str) -> bool:
        """Whether the line defines the name."""

    @abstractmethod
    def module_name(self, path: str) -> str:
        """The dotted module name of the file at the path."""


class Python(LanguageSupport):
    def structure(self, source: str) -> Structure:
        return Structure(*python_structure(source))

    @property
    def test_name_pattern(self) -> str:
        return r"_test$|^conftest$"

    def is_definition(self, line: str, name: str) -> bool:
        return is_definition(line, name)

    def module_name(self, path: str) -> str:
        return re.sub(r"\.py$", "", path).replace("/", ".")


class Java(LanguageSupport):
    def structure(self, source: str) -> Structure:
        return Structure(java_imports(source), [])

    @property
    def test_name_pattern(self) -> str:
        return JVM_TEST_NAME

    def is_definition(self, line: str, name: str) -> bool:
        return is_definition(line, name)

    def module_name(self, path: str) -> str:
        return path.replace("/", ".")


class JavaScript(LanguageSupport):
    """JavaScript and TypeScript."""

    def structure(self, source: str) -> Structure:
        return Structure(js_imports(source), [])

    @property
    def test_name_pattern(self) -> str:
        return r"\.(?:test|spec|cy)$|-test$"

    def is_definition(self, line: str, name: str) -> bool:
        return is_definition(line, name)

    def module_name(self, path: str) -> str:
        return path.replace("/", ".")


class Plain(LanguageSupport):
    """A language without import lines or symbols. Its test files match `test_name_pattern`, if given."""

    def __init__(self, test_name_pattern: str | None) -> None:
        self._test_name_pattern = test_name_pattern

    def structure(self, source: str) -> Structure:
        return Structure(set(), [])

    @property
    def test_name_pattern(self) -> str | None:
        return self._test_name_pattern

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
