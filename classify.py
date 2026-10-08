#!/usr/bin/env python3
"""Parse a unified diff and classify every change run.

Usage: classify.py <input dir> > classified.json

The input directory holds diff.patch (git diff -M), names.txt (git diff -M --name-status),
and old/ and new/ trees with the full content of every touched file,
and an optional generated.txt listing the paths marked linguist-generated in .gitattributes.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

TOKEN = re.compile(r"[\w.]")
IDENT = re.compile(r"^[\w.]+$")
HAS_LETTER = re.compile(r"[A-Za-z]")
CONTEXT_RADIUS = 4


@dataclass
class Run:
    index: int
    removed: list[tuple[int, str]]
    added: list[tuple[int, str]]
    cls: str = "substantive"
    rename: tuple[str, str] | None = None
    origin: bool = False
    pair_rename: list[bool] = field(default_factory=list)
    anchor_new: int = 0
    moved_to: dict | None = None
    file_index: int = -1


@dataclass
class Hunk:
    old_start: int
    new_start: int
    header: str
    lines: list[tuple[str, int | None, int | None, str]] = field(default_factory=list)


@dataclass
class FileDiff:
    old_path: str | None
    new_path: str | None
    status: str
    similarity: int | None = None
    hunks: list[Hunk] = field(default_factory=list)
    runs: list[Run] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


def parse_patch(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None
    old_no = new_no = 0
    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            m = re.match(r'diff --git a/(.*) b/(.*)$', raw)
            current = FileDiff(old_path=m.group(1), new_path=m.group(2), status="M")
            files.append(current)
            hunk = None
            continue
        if current is None:
            continue
        if hunk is None or raw.startswith(("---", "+++", "similarity", "rename", "new file", "deleted file", "index ")):
            if raw.startswith("new file mode"):
                current.status = "A"
                current.old_path = None
            elif raw.startswith("deleted file mode"):
                current.status = "D"
                current.new_path = None
            elif raw.startswith("similarity index"):
                current.similarity = int(raw.split()[2].rstrip("%"))
                current.status = "R"
            if not raw.startswith("@@"):
                continue
        if raw.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$", raw)
            old_no, new_no = int(m.group(1)), int(m.group(3))
            hunk = Hunk(old_start=old_no, new_start=new_no, header=m.group(5))
            current.hunks.append(hunk)
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
            removed, added = [], []
            while j < len(lines) and lines[j][0] != " ":
                tag, o, n, text = lines[j]
                (removed if tag == "-" else added).append((o if tag == "-" else n, text))
                j += 1
            anchor = added[0][0] if added else (lines[j][2] if j < len(lines) else hunk.new_start)
            fd.runs.append(Run(index=index, removed=removed, added=added, anchor_new=anchor))
            index += 1
            i = j


# ---------------------------------------------------------------- python structure

def python_line_sets(source: str) -> tuple[set[int], set[int], list[dict]]:
    """Return (import lines, docstring lines, symbols) for a Python source."""
    imports: set[int] = set()
    docstrings: set[int] = set()
    symbols: list[dict] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return imports, docstrings, symbols

    def doc_lines(node):
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            docstrings.update(range(body[0].lineno, body[0].end_lineno + 1))

    doc_lines(tree)

    def visit(node, depth, parent_name):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                imports.update(range(child.lineno, child.end_lineno + 1))
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(child, ast.ClassDef) else "def"
                start = child.decorator_list[0].lineno if child.decorator_list else child.lineno
                name = f"{parent_name}.{child.name}" if parent_name else child.name
                symbols.append({"name": name, "short": child.name, "kind": kind, "start": start,
                                "end": child.end_lineno, "depth": depth})
                doc_lines(child)
                visit(child, depth + 1, name)
            elif isinstance(child, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                visit(child, depth, parent_name)
    visit(tree, 0, "")
    return imports, docstrings, symbols


# ---------------------------------------------------------------- renames

def token_diff(old: str, new: str) -> tuple[str, str] | None:
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


def strip_common_segments(a: str, b: str) -> tuple[str, str]:
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


def collect_renames(files: list[FileDiff]) -> dict[tuple[str, str], dict]:
    counts: Counter = Counter()
    run_counts: Counter = Counter()
    seeds: dict[tuple[str, str], str] = {}
    for fd in files:
        if fd.status == "R" and fd.old_path and fd.new_path:
            seeds[(module_path(fd.old_path), module_path(fd.new_path))] = "file rename"
            ob, nb = Path(fd.old_path).stem, Path(fd.new_path).stem
            if ob != nb:
                seeds[(ob, nb)] = "file rename"
        for run in fd.runs:
            if len(run.removed) != len(run.added):
                continue
            seen: set[tuple[str, str]] = set()
            for (_, o), (_, n) in zip(run.removed, run.added):
                pair = token_diff(o, n)
                if pair:
                    counts[pair] += 1
                    seen.add(pair)
            for pair in seen:
                run_counts[pair] += 1
    renames: dict[tuple[str, str], dict] = {}
    for pair, c in counts.items():
        if run_counts[pair] >= 2 or pair in seeds:
            renames[pair] = {"from": pair[0], "to": pair[1], "count": c, "seed": seeds.get(pair)}
    for pair, why in seeds.items():
        renames.setdefault(pair, {"from": pair[0], "to": pair[1], "count": 0, "seed": why})
    return renames


def apply_renames(line: str, renames: list[tuple[str, str]]) -> str:
    for a, b in renames:
        line = re.sub(r"(?<!\w)" + re.escape(a) + r"(?!\w)", b, line)
    return line


# A keyword argument (name=value, no spaces) is not a definition; an assignment (name = value) is.
DEF_RE = r"^\s*(?:class|async def|def)\s+{name}\b|^\s*{name}(?::|\s=\s)"


def is_definition(line: str, name: str) -> bool:
    short = name.rsplit(".", 1)[-1]
    return re.match(DEF_RE.format(name=re.escape(short)), line) is not None


# ---------------------------------------------------------------- classification

def classify_run(run: Run, side_sets: dict, renames: list[tuple[str, str]], rename_info: dict) -> None:
    removed = [t for _, t in run.removed]
    added = [t for _, t in run.added]
    changed = removed + added
    if all(not t.strip() for t in changed):
        run.cls = "whitespace"
        return
    paired = len(removed) == len(added)
    if paired and all(re.sub(r"\s+", "", o) == re.sub(r"\s+", "", n) for o, n in zip(removed, added)):
        run.cls = "whitespace"
        return
    old_imports, _ = side_sets["old"]
    new_imports, _ = side_sets["new"]
    if changed and all(no in old_imports for no, _ in run.removed) and all(no in new_imports for no, _ in run.added):
        run.cls = "import"
        return


    if paired:
        flags = [apply_renames(o, renames) == n for o, n in zip(removed, added)]
        run.pair_rename = flags
        if flags and all(flags):
            pairs = Counter()
            for o, n in zip(removed, added):
                pair = token_diff(o, n)
                if pair in rename_info:
                    pairs[pair] += 1
            if pairs:
                run.rename = pairs.most_common(1)[0][0]
            run.cls = "rename"
            run.origin = any(
                is_definition(o, a) or is_definition(n, b)
                for (a, b) in (list(pairs) or [])
                for o, n in zip(removed, added)
            )
            return
    run.cls = "substantive"


def detect_moves(files: list[FileDiff]) -> None:
    def key(lines):
        return tuple(t.strip() for _, t in lines if t.strip())
    added_index: dict[tuple, list[tuple[int, int]]] = defaultdict(list)
    for fi, fd in enumerate(files):
        for run in fd.runs:
            if len(run.added) >= 4 and not run.removed:
                added_index[key(run.added)].append((fi, run.index))
    for fi, fd in enumerate(files):
        for run in fd.runs:
            if len(run.removed) >= 4 and not run.added:
                k = key(run.removed)
                if k in added_index:
                    tfi, tri = added_index[k][0]
                    target = files[tfi]
                    run.cls = "moved"
                    run.moved_to = {"path": target.path, "line": target.runs[tri].added[0][0]}
                    target.runs[tri].cls = "moved"
                    target.runs[tri].moved_to = {"path": fd.path, "line": run.removed[0][0]}


def file_kind(path: str) -> str:
    if path.startswith((".claude/", "docs/")) or path.endswith(".md"):
        return "docs"
    parts = path.split("/")
    name = parts[-1]
    if parts[0] in ("tests", "e2e") or name.startswith("test_") or name == "conftest.py" or "test_helpers" in parts:
        return "test"
    return "implementation"


GENERATED_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "poetry.lock", "uv.lock", "Pipfile.lock",
    "Cargo.lock", "Gemfile.lock", "composer.lock", "go.sum",
}
GENERATED_SUFFIXES = (".min.js", ".min.css", ".pb.go", "_pb2.py", "_pb2.pyi", "_pb2_grpc.py")
GENERATED_MARKER = re.compile(
    r"@generated|do not (hand[- ])?edit|(file|code) (is|was|has been) (auto(matically)?[- ]?)?generated"
    r"|auto(matically)?[- ]?generated (by|from|at|on|with|using)\b", re.I)


def is_generated(path: str, source: str, marked: set[str]) -> bool:
    """Generated by a tool: marked linguist-generated, a lock file, or headed by a generator's warning."""
    name = path.rsplit("/", 1)[-1]
    if path in marked or name in GENERATED_NAMES or name.endswith(GENERATED_SUFFIXES):
        return True
    return any(GENERATED_MARKER.search(line) for line in source.splitlines()[:5])


def read_source(path: Path) -> str:
    """The file as text, or empty for a binary file, which the page shows without lines."""
    if not path.exists():
        return ""
    data = path.read_bytes()
    if b"\0" in data[:8192]:
        return ""
    return data.decode("utf-8", errors="replace")


def main() -> None:
    root = Path(sys.argv[1])
    files = parse_patch((root / "diff.patch").read_text())
    for fd in files:
        build_runs(fd)
    marked_file = root / "generated.txt"
    marked = set(marked_file.read_text().split("\n")) - {""} if marked_file.exists() else set()
    rename_info = collect_renames(files)
    ordered = sorted(rename_info, key=lambda p: -len(p[0]))

    out_files = []
    for fi, fd in enumerate(files):
        old_src = read_source(root / "old" / fd.old_path) if fd.old_path else ""
        new_src = read_source(root / "new" / fd.new_path) if fd.new_path else ""
        is_py = fd.path.endswith(".py")
        old_imports, old_docs, _ = python_line_sets(old_src) if is_py else (set(), set(), [])
        new_imports, new_docs, symbols = python_line_sets(new_src if fd.new_path else old_src) if is_py else (set(), set(), [])
        side_sets = {"old": (old_imports, old_docs), "new": (new_imports, new_docs)}
        for run in fd.runs:
            run.file_index = fi
            classify_run(run, side_sets, ordered, rename_info)
        out_files.append({"fd": fd, "symbols": symbols, "content": new_src if fd.new_path else old_src,
                          "content_side": "new" if fd.new_path else "old",
                          "import_lines": new_imports if fd.new_path else old_imports, "old_import_lines": old_imports})
    detect_moves(files)

    rename_usage: dict[tuple[str, str], dict] = {p: {"runs": 0, "files": set(), "origin": None} for p in rename_info}
    for entry in sorted(out_files, key=lambda e: file_kind(e["fd"].path) == "test"):
        fd = entry["fd"]
        if fd.status == "R":
            for pair in rename_info:
                if rename_info[pair]["seed"] and rename_usage[pair]["origin"] is None:
                    rename_usage[pair]["origin"] = {"path": fd.path, "line": 1, "text": f"renamed from {fd.old_path}"}
        for run in fd.runs:
            for (a, b), u in rename_usage.items():
                if u["origin"] is not None:
                    continue
                for no, text in run.added:
                    if is_definition(text, b):
                        u["origin"] = {"path": fd.path, "line": no, "text": text.strip()}
                        break
                else:
                    for no, text in run.removed:
                        if is_definition(text, a):
                            u["origin"] = {"path": fd.path, "line": run.anchor_new, "text": text.strip()}
                            break
    result_files = []
    for entry in out_files:
        fd: FileDiff = entry["fd"]
        by_class = Counter()
        raw = 0
        substantive = 0
        runs_json = []
        for run in fd.runs:
            n = len(run.removed) + len(run.added)
            raw += n
            by_class[run.cls] += n
            if run.cls == "substantive" or (run.cls == "rename" and run.origin):
                substantive += n
            if run.rename:
                u = rename_usage[run.rename]
                u["runs"] += 1
                u["files"].add(fd.path)
            runs_json.append({
                "index": run.index, "class": run.cls,
                "removed": [no for no, _ in run.removed], "added": [no for no, _ in run.added],
                "rename": list(run.rename) if run.rename else None, "origin": run.origin,
                "pair_rename": run.pair_rename, "anchor_new": run.anchor_new, "moved_to": run.moved_to,
            })
        changed_new = {no for run in fd.runs for no in (n for n, _ in run.added)}
        anchors = {run.anchor_new for run in fd.runs}
        for sym in entry["symbols"]:
            sym["changed"] = any(sym["start"] <= a <= sym["end"] for a in anchors | changed_new)
        hunks_json = [{"old_start": h.old_start, "new_start": h.new_start, "header": h.header,
                       "lines": [[t, o, n, x] for t, o, n, x in h.lines]} for h in fd.hunks]
        result_files.append({
            "path": fd.path, "old_path": fd.old_path, "new_path": fd.new_path, "status": fd.status,
            "similarity": fd.similarity, "kind": file_kind(fd.path),
            "generated": is_generated(fd.path, entry["content"], marked),
            "lang": "python" if fd.path.endswith(".py") else ("markdown" if fd.path.endswith(".md") else "text"),
            "content": entry["content"], "content_side": entry["content_side"],
            "hunks": hunks_json, "runs": runs_json, "symbols": entry["symbols"],
            "import_lines": sorted(entry["import_lines"]), "old_import_lines": sorted(entry["old_import_lines"]),
            "stats": {"raw": raw, "substantive": substantive, "by_class": dict(by_class),
                      "additions": sum(len(r.added) for r in fd.runs), "deletions": sum(len(r.removed) for r in fd.runs)},
        })

    renames_json = []
    for pair, info in rename_info.items():
        u = rename_usage[pair]
        if u["runs"] == 0:
            continue
        renames_json.append({**info, "runs": u["runs"], "files": sorted(u["files"]), "origin": u["origin"]})
    renames_json.sort(key=lambda r: -r["runs"])

    totals = Counter()
    for f in result_files:
        for k, v in f["stats"]["by_class"].items():
            totals[k] += v
    json.dump({"files": result_files, "renames": renames_json, "totals": dict(totals)}, sys.stdout)


if __name__ == "__main__":
    main()
