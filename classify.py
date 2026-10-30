"""Parse the diff of a pull request and classify every change run."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypedDict

import file_types
from languages import LanguageSupport, Symbol

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
    """What classify reads: the diff (`git diff-tree -p -M -U3` from the base to the head), the content of every changed
    file at the base and at the head by path, on the sides where it exists, and the paths .gitattributes marks
    linguist-generated."""

    patch: str
    old: dict[str, bytes]
    new: dict[str, bytes]
    generated: set[str]


class Place(TypedDict):
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


class Origin(TypedDict):
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
    generated: bool

    @property
    def noise_only(self) -> bool:
        """Whether the page hides the file as noise: every run of it folds, or it is renamed or moved without a
        change, which git reports as 100% similarity and no mode change. A generated file is hidden as generated,
        and a changed binary file has no runs but has changed."""
        fd = self.diff
        if self.generated:
            return False
        if fd.runs:
            return all(run.noise for run in fd.runs)
        return fd.similarity == 100 and not fd.mode_changed


C_ESCAPES = {
    b"a": b"\a",
    b"b": b"\b",
    b"t": b"\t",
    b"n": b"\n",
    b"v": b"\v",
    b"f": b"\f",
    b"r": b"\r",
    b'"': b'"',
    b"\\": b"\\",
}


def unquoted(name: str) -> str:
    """A path as git prints it: as it is, or between double quotes with C escapes when it holds a tab, a quote, a
    backslash or a character outside ASCII, each byte of which git writes in octal."""
    if not name.startswith('"'):
        return name

    def unescape(m: re.Match[bytes]) -> bytes:
        code = m.group(1)
        return bytes([int(code, 8)]) if code.isdigit() else C_ESCAPES[code]

    return re.sub(rb"\\([0-7]{3}|.)", unescape, name[1:-1].encode()).decode(errors="replace")


def parse_patch(patch: str) -> list[FileDiff]:
    """The files of a diff that git printed, as git reads one: lines end at a newline only, and a file's
    paths come from its `diff --git` line, or from its rename lines when it is renamed."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None
    old_no = new_no = 0
    for raw in patch.split("\n"):
        if raw.startswith("diff --git "):
            # A file that keeps its name has it twice, so the first half of the line names it.
            names = raw.removeprefix("diff --git ")
            path = unquoted(names[: len(names) // 2])[2:]
            current = FileDiff(old_path=path, new_path=path, status="M")
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
                deleted = files[-2] if len(files) > 1 else None
                if deleted and deleted.status == "D" and deleted.old_path == current.new_path:
                    # git prints a change of type, such as a file that becomes a symlink, as a deletion and a
                    # creation of the path; they make one changed file.
                    files.pop()
                    current = deleted
                    current.status, current.new_path, current.mode_changed = "M", current.old_path, True
                else:
                    current.status = "A"
                    current.old_path = None
            elif raw.startswith("deleted file mode"):
                current.status = "D"
                current.new_path = None
            elif raw.startswith("similarity index"):
                current.status = "R"
                current.similarity = int(raw.removeprefix("similarity index ").rstrip("%"))
            elif raw.startswith("rename from "):
                current.old_path = unquoted(raw.removeprefix("rename from "))
            elif raw.startswith("rename to "):
                current.new_path = unquoted(raw.removeprefix("rename to "))
            elif raw.startswith("Binary files "):
                current.binary = True
            elif raw.startswith("old mode"):
                current.mode_changed = True
            continue
        if raw.startswith("\\ No newline"):
            continue
        tag, body = raw[:1], raw[1:]
        if tag == " ":
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


def collect_renames(files: list[FileDiff]) -> dict[Pair, str | None]:
    """Every rename of the pull request, with the path of the renamed file it follows, if any. A rename is a name
    that changes the same way in at least two runs, or the module or file name of a renamed file."""
    runs: Counter[Pair] = Counter()
    of_files: dict[Pair, str] = {}
    for fd in files:
        if fd.status == "R" and fd.old_path and fd.new_path:
            old_module = file_types.language_support_of(fd.old_path).module_name(fd.old_path)
            new_module = file_types.language_support_of(fd.new_path).module_name(fd.new_path)
            of_files[(old_module, new_module)] = fd.path
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


# ---------------------------------------------------------------- classification


def classify_run(
    run: Run, old_imports: set[int], new_imports: set[int], renames: list[Pair], language: LanguageSupport
) -> None:
    """Classify the run, with the renames applied longest name first. A run link_moves linked is moved."""
    if run.moved_to:
        run.cls = "moved"
        return
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
                language.is_definition(o, a) or language.is_definition(n, b)
                for a, b in pairs
                for o, n in zip(removed, added, strict=True)
            )
            return
    run.cls = "substantive"


def link_moves(files: list[FileDiff]) -> None:
    """Link a run that only removes lines to a run that only adds the same lines elsewhere, ignoring indentation
    and blank lines, when they hold at least 4 lines that are not blank: each run's moved_to names the other."""

    def key(lines: list[Line]) -> tuple[str, ...]:
        return tuple(t.strip() for _, t in lines if t.strip())

    added: dict[tuple[str, ...], tuple[FileDiff, Run]] = {}
    for fd in files:
        for run in fd.runs:
            if not run.removed and len(key(run.added)) >= 4:
                added.setdefault(key(run.added), (fd, run))
    for fd in files:
        for run in fd.runs:
            if not run.added and (target := added.get(key(run.removed))):
                target_file, target_run = target
                run.moved_to = Place(path=target_file.path, line=target_run.added[0][0])
                target_run.moved_to = Place(path=fd.path, line=run.removed[0][0])


DOCS_DIRS = {"docs", "doc", "Documentation"}
DOCS_DIR_SUFFIX = ".docc"
TOP_LEVEL_TEST_DIRS = {"tests", "test", "e2e", "spec", "cypress", "playwright"}
TEST_DIRS = {"__tests__", "__snapshots__", "test_helpers"}
TEST_DIR_SUFFIX = "Tests"
TEST_PATH_PART = "/src/test/"
TEST_NAME_PREFIX = "test_"


def file_kind(path: str) -> Literal["docs", "test", "implementation"]:
    *dirs, name = path.split("/")
    docs = file_types.support_of(path).docs
    in_docs_dir = any(d in DOCS_DIRS or d.endswith(DOCS_DIR_SUFFIX) for d in dirs)
    if docs is file_types.Docs.ALWAYS or (docs is file_types.Docs.IN_DOCS_FOLDER and in_docs_dir):
        return "docs"
    if (
        (dirs and dirs[0] in TOP_LEVEL_TEST_DIRS)
        or any(d in TEST_DIRS or d.endswith(TEST_DIR_SUFFIX) for d in dirs)
        or TEST_PATH_PART in f"/{path}"
        or name.startswith(TEST_NAME_PREFIX)
        or file_types.language_support_of(path).is_test_name(name.rsplit(".", 1)[0])
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
GENERATED_MARKER = re.compile(
    r"@generated|do not (hand[- ])?edit|(file|code) (is|was|has been) (auto(matically)?[- ]?)?generated"
    r"|auto(matically)?[- ]?generated (by|from|at|on|with|using)\b",
    re.I,
)


def is_generated(path: str, source: str, marked: set[str]) -> bool:
    """Generated by a tool: marked linguist-generated, a lock file, or headed by a generator's warning."""
    name = path.rsplit("/", 1)[-1]
    generated_suffixes = file_types.support_of(path).generated_suffixes
    if path in marked or name in GENERATED_NAMES or name.lower().endswith(generated_suffixes):
        return True
    return any(GENERATED_MARKER.search(line) for line in source.splitlines()[:5])


def decoded(content: bytes | None) -> str:
    """A file's content as text, or empty for a binary file or a file that is not there."""
    if content is None or b"\0" in content[:8192]:
        return ""
    return content.decode("utf-8", errors="replace")


def enclosing(symbols: list[Symbol], line: int) -> Symbol | None:
    """Return the innermost symbol with the line in its body, from its `body` line to its `end` line, or None when
    the line is in no symbol's body. The innermost symbol starts last, and of those it ends first."""
    around = [s for s in symbols if s["start"] < line and s["body"] <= line <= s["end"]]
    return max(around, key=lambda s: (s["start"], -s["end"]), default=None)


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
        old_src = decoded(diff.old.get(fd.old_path)) if fd.old_path else ""
        new_src = decoded(diff.new.get(fd.new_path)) if fd.new_path else ""
        content, content_side = (new_src, "new") if fd.new_path else (old_src, "old")
        language = file_types.language_support_of(fd.path)
        old_imports = language.structure(old_src).import_lines
        imports, symbols = language.structure(content)
        for run in fd.runs:
            classify_run(run, old_imports, imports, longest_first, language)
        generated = is_generated(fd.path, content, diff.generated)
        classified.append(ClassifiedFile(fd, content, content_side, symbols, imports, old_imports, generated))
    return classified


def find_origins(files: list[ClassifiedFile], renames: dict[Pair, str | None]) -> dict[Pair, Origin | None]:
    """Where each rename is defined: the renamed file it follows, or the first class, def, annotation or assignment
    of the new name, with implementation files searched before tests."""
    origins: dict[Pair, Origin | None] = dict.fromkeys(renames)
    for fd in sorted((f.diff for f in files), key=lambda fd: file_kind(fd.path) == "test"):
        for pair, renamed_file in renames.items():
            if renamed_file == fd.path and origins[pair] is None:
                origins[pair] = Origin(path=fd.path, line=1, side="new", text=f"renamed from {fd.old_path}")
        language = file_types.language_support_of(fd.path)
        for run in fd.runs:
            for (a, b), origin in origins.items():
                if origin is not None:
                    continue
                for no, line in run.added:
                    if language.is_definition(line, b):
                        origins[(a, b)] = Origin(path=fd.path, line=no, side="new", text=line.strip())
                        break
                else:
                    for no, line in run.removed:
                        if language.is_definition(line, a):
                            origins[(a, b)] = Origin(path=fd.path, line=no, side="old", text=line.strip())
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
        "kind": file_kind(fd.path),
        "generated": f.generated,
        "noise_only": f.noise_only,
        "lang": file_types.support_of(fd.path).highlight,
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
                "noise": run.noise,
                "moved_to": run.moved_to,
                "enclosing": enclosing(f.symbols, run.added[0][0]) if run.added else None,
            }
            for run in fd.runs
        ],
        "symbols": [s for s in f.symbols if s["kind"] != "call"],
        "import_lines": sorted(f.import_lines),
        "old_import_lines": sorted(f.old_import_lines),
        "stats": {
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
            "origin": origin,
        }
        for (a, b), origin in origins.items()
        if runs[(a, b)]
    ]
    return sorted(out, key=lambda r: -r["runs"])


def classify(diff: Diff) -> dict:
    """The diff's files and the renames found across them."""
    files = parse_patch(diff.patch)
    for fd in files:
        build_runs(fd)
    link_moves(files)
    renames = collect_renames(files)
    classified = classify_files(diff, files, renames)
    return {
        "files": [file_json(f, diff) for f in classified],
        "renames": renames_json(files, find_origins(classified, renames)),
    }
