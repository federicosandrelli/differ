"""Parse the diff of a pull request and classify every change run."""

from __future__ import annotations

import ast
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

TOKEN = re.compile(r"[\w.]")
IDENT = re.compile(r"^[\w.]+$")
HAS_LETTER = re.compile(r"[A-Za-z]")

RunClass = Literal["substantive", "import", "rename", "whitespace", "moved"]
Side = Literal["old", "new"]
# A rename: the name before and the name after.
Pair = tuple[str, str]
# A changed line: its number on its own side, and its text.
Line = tuple[int, str]
# A line of a hunk: its tag, its numbers on the old and new side, and its text.
HunkLine = (
    tuple[Literal[" "], int, int, str] | tuple[Literal["-"], int, None, str] | tuple[Literal["+"], None, int, str]
)


@dataclass(frozen=True)
class Diff:
    """What classify reads: the diff (`git diff -M -U3` from the base to the head), the content of every changed
    file at the base and at the head by path, on the sides where it exists, and the paths .gitattributes marks
    linguist-generated."""

    patch: str
    old: dict[str, bytes]
    new: dict[str, bytes]
    generated: set[str]


@dataclass(frozen=True)
class Place:
    """A line of a file: on the new side for added lines, on the old side for removed ones."""

    path: str
    line: int


@dataclass
class Run:
    """A maximal block of changed lines of a hunk: the removed ones, then the added ones."""

    index: int
    removed: list[Line]
    added: list[Line]
    cls: RunClass = "substantive"
    rename: Pair | None = None
    # Whether the run defines its rename's new name (a class, a def, an annotation or an assignment).
    origin: bool = False
    # For a run with as many removed as added lines: whether each removed line becomes its added line by the renames.
    pair_rename: list[bool] = field(default_factory=list)
    # Where a moved block went, or where it came from.
    moved_to: Place | None = None

    @property
    def size(self) -> int:
        return len(self.removed) + len(self.added)

    @property
    def noise(self) -> bool:
        """Whether the page folds the run: anything but a substantive change, or the rename that defines the name."""
        return self.cls != "substantive" and not (self.cls == "rename" and self.origin)


@dataclass
class Hunk:
    old_start: int
    new_start: int
    lines: list[HunkLine] = field(default_factory=list)


@dataclass
class FileDiff:
    old_path: str | None
    new_path: str | None
    status: Literal["A", "M", "D", "R"]
    binary: bool = False
    similarity: int | None = None
    mode_changed: bool = False
    hunks: list[Hunk] = field(default_factory=list)
    runs: list[Run] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


@dataclass(frozen=True)
class Symbol:
    """A class, a function, or a call at module level that spans several lines, in a Python file. It starts on
    `start` (at its first decorator), its body on `body`, and it ends on `end`."""

    name: str
    kind: Literal["class", "def", "call"]
    start: int
    body: int
    end: int


@dataclass(frozen=True)
class Origin:
    """Where a rename is defined."""

    path: str
    line: int
    side: Side
    text: str


@dataclass(frozen=True)
class ClassifiedFile:
    """A changed file with its runs classified, and what the page shows of it besides the diff: its content on one
    side, and the symbols and import lines of a Python file."""

    diff: FileDiff
    content: str
    content_side: Side
    symbols: list[Symbol]
    import_lines: set[int]
    old_import_lines: set[int]


def parse_patch(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None
    old_no = new_no = 0
    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            m = re.match(r"diff --git a/(.*) b/(.*)$", raw)
            assert m
            current = FileDiff(old_path=m.group(1), new_path=m.group(2), status="M")
            files.append(current)
            hunk = None
            continue
        if current is None:
            continue
        if raw.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)", raw)
            assert m
            old_no, new_no = int(m.group(1)), int(m.group(2))
            hunk = Hunk(old_start=old_no, new_start=new_no)
            current.hunks.append(hunk)
            continue
        if hunk is None:
            # The file's header, up to its first hunk.
            if raw.startswith("new file mode"):
                current.status = "A"
                current.old_path = None
            elif raw.startswith("deleted file mode"):
                current.status = "D"
                current.new_path = None
            elif raw.startswith("similarity index"):
                current.status = "R"
                current.similarity = int(raw.removeprefix("similarity index ").rstrip("%"))
            elif raw.startswith("Binary files "):
                current.binary = True
            elif raw.startswith("old mode"):
                current.mode_changed = True
            continue
        if raw.startswith("\\ No newline"):
            continue
        tag, body = raw[:1], raw[1:]
        if tag == " " or raw == "":
            hunk.lines.append((" ", old_no, new_no, body))
            old_no += 1
            new_no += 1
        elif tag == "-":
            hunk.lines.append(("-", old_no, None, body))
            old_no += 1
        elif tag == "+":
            hunk.lines.append(("+", None, new_no, body))
            new_no += 1
    return files


def build_runs(fd: FileDiff) -> None:
    index = 0
    for hunk in fd.hunks:
        i = 0
        lines = hunk.lines
        while i < len(lines):
            if lines[i][0] == " ":
                i += 1
                continue
            j = i
            removed: list[Line] = []
            added: list[Line] = []
            while j < len(lines) and lines[j][0] != " ":
                line = lines[j]
                if line[0] == "-":
                    removed.append((line[1], line[3]))
                else:
                    added.append((line[2], line[3]))
                j += 1
            fd.runs.append(Run(index=index, removed=removed, added=added))
            index += 1
            i = j


# ---------------------------------------------------------------- python structure


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

    def visit(node: ast.AST, module_level: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Import | ast.ImportFrom):
                imports.update(range(child.lineno, last_line(child) + 1))
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                kind = "class" if isinstance(child, ast.ClassDef) else "def"
                symbols.append(Symbol(child.name, kind, first_line(child), first_line(child.body[0]), last_line(child)))
                visit(child, module_level=False)
            elif isinstance(child, ast.If | ast.Try | ast.With | ast.For | ast.While):
                visit(child, module_level)
            elif (
                module_level
                and isinstance(child, ast.Expr | ast.Assign | ast.AnnAssign | ast.AugAssign)
                and isinstance(child.value, ast.Call)
                and last_line(child) != child.lineno
            ):
                callee = ast.unparse(child.value.func).rsplit(".", 1)[-1]
                symbols.append(Symbol(callee, "call", child.lineno, child.lineno + 1, last_line(child)))

    visit(tree, module_level=True)
    return imports, symbols


# ---------------------------------------------------------------- renames


def token_diff(old: str, new: str) -> Pair | None:
    """The differing span of two lines, widened to token boundaries, or None when it is not identifier-like."""
    if old == new:
        return None
    p = 0
    limit = min(len(old), len(new))
    while p < limit and old[p] == new[p]:
        p += 1
    s = 0
    while s < limit - p and old[len(old) - 1 - s] == new[len(new) - 1 - s]:
        s += 1
    while p > 0 and TOKEN.match(old[p - 1]):
        p -= 1
    oe, ne = len(old) - s, len(new) - s
    while oe < len(old) and TOKEN.match(old[oe]):
        oe += 1
        ne += 1
    a, b = old[p:oe], new[p:ne]
    if not a or not b or a == b or not IDENT.match(a) or not IDENT.match(b):
        return None
    if not HAS_LETTER.search(a) or not HAS_LETTER.search(b):
        return None
    return strip_common_segments(a, b)


def strip_common_segments(a: str, b: str) -> Pair:
    """Drop the dotted segments two paths share at either end, keeping at least one segment on each side."""
    sa, sb = a.split("."), b.split(".")
    while len(sa) > 1 and len(sb) > 1 and sa[0] == sb[0]:
        sa.pop(0)
        sb.pop(0)
    while len(sa) > 1 and len(sb) > 1 and sa[-1] == sb[-1]:
        sa.pop()
        sb.pop()
    return ".".join(sa), ".".join(sb)


def module_path(path: str) -> str:
    return re.sub(r"\.py$", "", path).replace("/", ".")


def collect_renames(files: list[FileDiff]) -> dict[Pair, str | None]:
    """Every rename of the pull request, with the path of the renamed file it follows, if any. A rename is a name
    that changes the same way in at least two runs, or the module or file name of a renamed file."""
    runs: Counter[Pair] = Counter()
    of_files: dict[Pair, str] = {}
    for fd in files:
        if fd.status == "R" and fd.old_path and fd.new_path:
            of_files[(module_path(fd.old_path), module_path(fd.new_path))] = fd.path
            ob, nb = Path(fd.old_path).stem, Path(fd.new_path).stem
            if ob != nb:
                of_files[(ob, nb)] = fd.path
        for run in fd.runs:
            if len(run.removed) == len(run.added):
                pairs = (token_diff(o, n) for (_, o), (_, n) in zip(run.removed, run.added, strict=True))
                runs.update({pair for pair in pairs if pair})
    return {pair: of_files.get(pair) for pair, n in runs.items() if n >= 2 or pair in of_files} | of_files


def apply_renames(line: str, renames: list[Pair]) -> str:
    for a, b in renames:
        line = re.sub(r"(?<!\w)" + re.escape(a) + r"(?!\w)", b, line)
    return line


# A definition is a class, a def, an annotation, or an assignment with spaces round the equals sign.
# A keyword argument (name=value) has none, so it does not match.
DEF_RE = r"^\s*(?:class|async def|def)\s+{name}\b|^\s*{name}(?::|\s=\s)"


def is_definition(line: str, name: str) -> bool:
    short = name.rsplit(".", 1)[-1]
    return re.match(DEF_RE.format(name=re.escape(short)), line) is not None


# ---------------------------------------------------------------- classification


def classify_run(run: Run, old_imports: set[int], new_imports: set[int], renames: list[Pair]) -> None:
    """Classify the run, with the renames applied longest name first."""
    removed = [t for _, t in run.removed]
    added = [t for _, t in run.added]
    changed = removed + added
    if all(not t.strip() for t in changed):
        run.cls = "whitespace"
        return
    paired = len(removed) == len(added)
    if paired and all(re.sub(r"\s+", "", o) == re.sub(r"\s+", "", n) for o, n in zip(removed, added, strict=True)):
        run.cls = "whitespace"
        return
    if changed and all(no in old_imports for no, _ in run.removed) and all(no in new_imports for no, _ in run.added):
        run.cls = "import"
        return
    if paired:
        flags = [apply_renames(o, renames) == n for o, n in zip(removed, added, strict=True)]
        run.pair_rename = flags
        if flags and all(flags):
            # A line that renames the name more than once differs in a span wider than one token, so the
            # rename is found by the names it holds instead.
            pairs: Counter[Pair] = Counter()
            for o, n in zip(removed, added, strict=True):
                pair = token_diff(o, n)
                if pair in renames:
                    pairs[pair] += 1
                else:
                    pairs.update(p for p in renames if re.search(r"(?<!\w)" + re.escape(p[0]) + r"(?!\w)", o))
            if pairs:
                run.rename = pairs.most_common(1)[0][0]
            run.cls = "rename"
            run.origin = any(
                is_definition(o, a) or is_definition(n, b)
                for a, b in pairs
                for o, n in zip(removed, added, strict=True)
            )
            return
    run.cls = "substantive"


def detect_moves(files: list[FileDiff]) -> None:
    """Mark as moved a block of at least 4 removed lines that comes back as a block of added lines, ignoring
    indentation, and link the two."""

    def key(lines: list[Line]) -> tuple[str, ...]:
        return tuple(t.strip() for _, t in lines if t.strip())

    added_index: dict[tuple[str, ...], list[tuple[int, int]]] = defaultdict(list)
    for fi, fd in enumerate(files):
        for run in fd.runs:
            if len(run.added) >= 4 and not run.removed:
                added_index[key(run.added)].append((fi, run.index))
    for fd in files:
        for run in fd.runs:
            if len(run.removed) >= 4 and not run.added:
                k = key(run.removed)
                if k in added_index:
                    tfi, tri = added_index[k][0]
                    target = files[tfi]
                    run.cls = "moved"
                    run.moved_to = Place(target.path, target.runs[tri].added[0][0])
                    target.runs[tri].cls = "moved"
                    target.runs[tri].moved_to = Place(fd.path, run.removed[0][0])


TOP_LEVEL_TEST_DIRS = {"tests", "test", "e2e", "spec", "cypress", "playwright"}
TEST_DIRS = {"__tests__", "__snapshots__", "test_helpers"}
JS_EXT = r"(?:js|jsx|mjs|cjs|ts|tsx|mts|cts)"
TEST_NAME = re.compile(
    r"^test_|^conftest\.py$"
    r"|_test\.(?:py|go|rb|dart|exs)$|_spec\.rb$"
    rf"|\.(?:test|spec|cy)\.{JS_EXT}$|-test\.{JS_EXT}$"
    r"|\wTests?\.(?:java|kt|cs|swift|php)$|[a-z0-9]IT\.(?:java|kt)$"
)


def file_kind(path: str) -> Literal["docs", "test", "implementation"]:
    if path.startswith((".claude/", "docs/")) or path.endswith(".md"):
        return "docs"
    *dirs, name = path.split("/")
    if (
        (dirs and dirs[0] in TOP_LEVEL_TEST_DIRS)
        or any(d in TEST_DIRS or d.endswith("Tests") for d in dirs)
        or "/src/test/" in f"/{path}"
        or TEST_NAME.search(name)
    ):
        return "test"
    return "implementation"


GENERATED_NAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "poetry.lock",
    "uv.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "Gemfile.lock",
    "composer.lock",
    "go.sum",
}
GENERATED_SUFFIXES = (".min.js", ".min.css", ".pb.go", "_pb2.py", "_pb2.pyi", "_pb2_grpc.py")
GENERATED_MARKER = re.compile(
    r"@generated|do not (hand[- ])?edit|(file|code) (is|was|has been) (auto(matically)?[- ]?)?generated"
    r"|auto(matically)?[- ]?generated (by|from|at|on|with|using)\b",
    re.I,
)


def is_generated(path: str, source: str, marked: set[str]) -> bool:
    """Generated by a tool: marked linguist-generated, a lock file, or headed by a generator's warning."""
    name = path.rsplit("/", 1)[-1]
    if path in marked or name in GENERATED_NAMES or name.endswith(GENERATED_SUFFIXES):
        return True
    return any(GENERATED_MARKER.search(line) for line in source.splitlines()[:5])


LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".rb": "ruby",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".lua": "lua",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".sql": "sql",
    ".json": "json",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "ini",
    ".ini": "ini",
    ".cfg": "ini",
    ".css": "css",
    ".scss": "scss",
    ".html": "xml",
    ".xml": "xml",
    ".svg": "xml",
    ".md": "markdown",
    ".diff": "diff",
    ".patch": "diff",
    ".graphql": "graphql",
    ".r": "r",
    ".pl": "perl",
}
SPECIAL_FILES = {"Makefile": "makefile", "Dockerfile": "dockerfile"}


def language(path: str) -> str:
    """The highlight.js language name for a path, or "text"."""
    name = path.rsplit("/", 1)[-1]
    suffix = name[name.rfind(".") :].lower() if "." in name else ""
    return SPECIAL_FILES.get(name) or LANGUAGES.get(suffix, "text")


def text(content: bytes | None) -> str:
    """A file's content as text, or empty for a binary file or a file that is not there."""
    if content is None or b"\0" in content[:8192]:
        return ""
    return content.decode("utf-8", errors="replace")


def binary_sizes(diff: Diff, fd: FileDiff) -> dict[Side, int | None] | None:
    """A binary file's size in bytes on each side, None on a side without the file; None for a text file."""
    if not fd.binary:
        return None
    old = diff.old.get(fd.old_path) if fd.old_path else None
    new = diff.new.get(fd.new_path) if fd.new_path else None
    return {"old": None if old is None else len(old), "new": None if new is None else len(new)}


def classify_files(diff: Diff, files: list[FileDiff], renames: dict[Pair, str | None]) -> list[ClassifiedFile]:
    """Classify every run of every file."""
    longest_first = sorted(renames, key=lambda p: -len(p[0]))
    classified = []
    for fd in files:
        old_src = text(diff.old.get(fd.old_path)) if fd.old_path else ""
        new_src = text(diff.new.get(fd.new_path)) if fd.new_path else ""
        content, content_side = (new_src, "new") if fd.new_path else (old_src, "old")
        if fd.path.endswith(".py"):
            old_imports, _ = python_structure(old_src)
            imports, symbols = python_structure(content)
        else:
            old_imports, imports, symbols = set(), set(), []
        for run in fd.runs:
            classify_run(run, old_imports, imports if content_side == "new" else old_imports, longest_first)
        classified.append(ClassifiedFile(fd, content, content_side, symbols, imports, old_imports))
    return classified


def find_origins(files: list[ClassifiedFile], renames: dict[Pair, str | None]) -> dict[Pair, Origin | None]:
    """Where each rename is defined: the renamed file it follows, or the first class, def, annotation or assignment
    of the new name, with implementation files searched before tests."""
    origins: dict[Pair, Origin | None] = dict.fromkeys(renames)
    for fd in sorted((f.diff for f in files), key=lambda fd: file_kind(fd.path) == "test"):
        for pair, renamed_file in renames.items():
            if renamed_file == fd.path and origins[pair] is None:
                origins[pair] = Origin(fd.path, 1, "new", f"renamed from {fd.old_path}")
        for run in fd.runs:
            for (a, b), origin in origins.items():
                if origin is not None:
                    continue
                for no, line in run.added:
                    if is_definition(line, b):
                        origins[(a, b)] = Origin(fd.path, no, "new", line.strip())
                        break
                else:
                    for no, line in run.removed:
                        if is_definition(line, a):
                            origins[(a, b)] = Origin(fd.path, no, "old", line.strip())
                            break
    return origins


def file_json(f: ClassifiedFile, diff: Diff) -> dict:
    """The file as the page reads it."""
    fd = f.diff
    return {
        "path": fd.path,
        "old_path": fd.old_path,
        "new_path": fd.new_path,
        "status": fd.status,
        "similarity": fd.similarity,
        "mode_changed": fd.mode_changed,
        "kind": file_kind(fd.path),
        "generated": is_generated(fd.path, f.content, diff.generated),
        "lang": language(fd.path),
        "content": f.content,
        "content_side": f.content_side,
        "binary": binary_sizes(diff, fd),
        "hunks": [{"old_start": h.old_start, "new_start": h.new_start, "lines": h.lines} for h in fd.hunks],
        "runs": [
            {
                "index": run.index,
                "class": run.cls,
                "removed": [no for no, _ in run.removed],
                "added": [no for no, _ in run.added],
                "rename": run.rename,
                "origin": run.origin,
                "pair_rename": run.pair_rename,
                "moved_to": asdict(run.moved_to) if run.moved_to else None,
            }
            for run in fd.runs
        ],
        "symbols": [asdict(s) for s in f.symbols],
        "import_lines": sorted(f.import_lines),
        "old_import_lines": sorted(f.old_import_lines),
        "stats": {
            "raw": sum(run.size for run in fd.runs),
            "substantive": sum(run.size for run in fd.runs if not run.noise),
            "additions": sum(len(run.added) for run in fd.runs),
            "deletions": sum(len(run.removed) for run in fd.runs),
        },
    }


def renames_json(files: list[FileDiff], origins: dict[Pair, Origin | None]) -> list[dict]:
    """Every rename applied in at least one run, most used first."""
    runs: Counter[Pair] = Counter()
    paths: defaultdict[Pair, set[str]] = defaultdict(set)
    for fd in files:
        for run in fd.runs:
            if run.rename:
                runs[run.rename] += 1
                paths[run.rename].add(fd.path)
    out = [
        {
            "from": a,
            "to": b,
            "runs": runs[(a, b)],
            "files": sorted(paths[(a, b)]),
            "origin": asdict(origin) if origin else None,
        }
        for (a, b), origin in origins.items()
        if runs[(a, b)]
    ]
    return sorted(out, key=lambda r: -r["runs"])


def classify(diff: Diff) -> dict:
    """The diff's files, the renames found across them, and the line totals by class."""
    files = parse_patch(diff.patch)
    for fd in files:
        build_runs(fd)
    renames = collect_renames(files)
    classified = classify_files(diff, files, renames)
    detect_moves(files)
    totals: Counter[RunClass] = Counter()
    for fd in files:
        for run in fd.runs:
            totals[run.cls] += run.size
    return {
        "files": [file_json(f, diff) for f in classified],
        "renames": renames_json(files, find_origins(classified, renames)),
        "totals": dict(totals),
    }
