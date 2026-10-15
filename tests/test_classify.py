from pathlib import Path

import pytest

import classify
from classify import FileDiff, Hunk, Run
from helpers import make_diff

OLD_APP = """import os


def old_name(x):
    return x + 1


def main():
    print(old_name(2))
"""
NEW_APP = """import os
import sys


def new_name(x):
    return x + 1


def main():
    print(new_name(2))
"""


def run_of(removed: list[str], added: list[str], start: int = 1) -> Run:
    return Run(
        index=0,
        removed=[(start + i, t) for i, t in enumerate(removed)],
        added=[(start + i, t) for i, t in enumerate(added)],
    )


# ---------------------------------------------------------------- parsing


def test_parse_patch_reads_statuses_and_line_numbers(tmp_path: Path) -> None:
    diff = make_diff(
        tmp_path,
        old={"keep.py": "a\nb\nc\n", "gone.txt": "bye\n", "before.py": "same\n"},
        new={"keep.py": "a\nB\nc\nd\n", "fresh.txt": "hi\n", "after.py": "same\n"},
    )
    files = {fd.path: fd for fd in classify.parse_patch(diff.patch)}
    assert files["keep.py"].status == "M"
    assert files["gone.txt"].status == "D" and files["gone.txt"].new_path is None
    assert files["fresh.txt"].status == "A" and files["fresh.txt"].old_path is None
    assert files["after.py"].status == "R" and files["after.py"].old_path == "before.py"
    (hunk,) = files["keep.py"].hunks
    assert hunk.old_start == 1 and hunk.new_start == 1
    assert hunk.lines == [
        (" ", 1, 1, "a"),
        ("-", 2, None, "b"),
        ("+", None, 2, "B"),
        (" ", 3, 3, "c"),
        ("+", None, 4, "d"),
    ]


def test_parse_patch_reads_changed_lines_that_look_like_file_headers(tmp_path: Path) -> None:
    diff = make_diff(tmp_path, old={"a.yml": "x: 1\n---\n-- note\ny: 2\n"}, new={"a.yml": "x: 1\n++ added\ny: 2\n"})
    ((hunk,),) = [fd.hunks for fd in classify.parse_patch(diff.patch)]
    assert hunk.lines == [
        (" ", 1, 1, "x: 1"),
        ("-", 2, None, "---"),
        ("-", 3, None, "-- note"),
        ("+", None, 2, "++ added"),
        (" ", 4, 3, "y: 2"),
    ]


def test_parse_patch_ends_lines_at_newlines_only(tmp_path: Path) -> None:
    diff = make_diff(
        tmp_path,
        old={"a.py": "a = 1\nb = 2\n", "b.txt": "one\r\ntwo\r\n"},
        new={"a.py": "a = 1\n\x0c\nb = 2\n", "b.txt": "one\r\nTWO\r\n"},
    )
    files = {fd.path: fd.hunks[0].lines for fd in classify.parse_patch(diff.patch)}
    assert files["a.py"] == [(" ", 1, 1, "a = 1"), ("+", None, 2, "\x0c"), (" ", 2, 3, "b = 2")]
    assert files["b.txt"] == [(" ", 1, 1, "one\r"), ("-", 2, None, "two\r"), ("+", None, 2, "TWO\r")]


def test_read_diff_reads_the_paths_git_quotes(tmp_path: Path) -> None:
    names = ["docs/café.md", 'say "hi".txt', "tab\there.txt", "back\\slash.txt", "Plan b/notes.md"]
    diff = make_diff(
        tmp_path,
        old={**dict.fromkeys(names, "a\n"), "old é.py": "x = 1\n"},
        new={
            **dict.fromkeys(names, "b\n"),
            "new é.py": "x = 1\n",
            ".gitattributes": "gen/*.py linguist-generated\n",
            "gen/schéma.py": "y = 2\n",
        },
    )
    files = {fd.path: fd for fd in classify.parse_patch(diff.patch)}
    assert set(files) == {*names, "new é.py", ".gitattributes", "gen/schéma.py"}
    assert all(files[name].old_path == name and diff.old[name] == b"a\n" and diff.new[name] == b"b\n" for name in names)
    assert files["new é.py"].old_path == "old é.py" and files["new é.py"].status == "R"
    assert diff.generated == {"gen/schéma.py"}


def test_read_diff_ignores_the_users_diff_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = {
        "diff.noprefix": "true",
        "diff.suppressBlankEmpty": "true",
        "diff.external": "echo",
        "color.ui": "always",
    }
    monkeypatch.setenv("GIT_CONFIG_COUNT", str(len(settings)))
    for i, (key, value) in enumerate(settings.items()):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{i}", key)
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{i}", value)
    diff = make_diff(tmp_path, old={"a.py": "a = 1\n\nb = 2\n"}, new={"a.py": "a = 1\n\nb = 3\n"})
    ((hunk,),) = [fd.hunks for fd in classify.parse_patch(diff.patch)]
    assert hunk.lines == [(" ", 1, 1, "a = 1"), (" ", 2, 2, ""), ("-", 3, None, "b = 2"), ("+", None, 3, "b = 3")]
    assert diff.new == {"a.py": b"a = 1\n\nb = 3\n"}


def test_parse_patch_keeps_a_binary_file_without_hunks(tmp_path: Path) -> None:
    diff = make_diff(tmp_path, old={"img.bin": b"\0\1\2"}, new={"img.bin": b"\0\1\3"})
    (fd,) = classify.parse_patch(diff.patch)
    assert fd.path == "img.bin" and fd.hunks == [] and fd.binary
    assert classify.decoded(diff.new["img.bin"]) == ""


def test_classify_gives_a_binary_file_its_size_on_each_side(tmp_path: Path) -> None:
    diff = make_diff(
        tmp_path,
        old={"changed.png": b"\0" * 10, "gone.png": b"\0\1" * 10, "notes.txt": "a\n"},
        new={"changed.png": b"\0" * 30, "added.png": b"\0\2\3\4" * 10, "notes.txt": "b\n"},
    )
    files = {f["path"]: f for f in classify.classify(diff)["files"]}
    assert files["changed.png"]["binary"] == {"old": 10, "new": 30}
    assert files["gone.png"]["binary"] == {"old": 20, "new": None}
    assert files["added.png"]["binary"] == {"old": None, "new": 40}
    assert files["notes.txt"]["binary"] is None


def test_binary_sizes_leaves_out_a_side_git_could_not_read() -> None:
    diff = classify.Diff(patch="", old={}, new={"a.png": b"\0" * 5}, generated=set())
    fd = FileDiff(old_path="missing.png", new_path="a.png", status="M", binary=True)
    assert classify.binary_sizes(diff, fd) == {"old": None, "new": 5}


def test_parse_patch_reads_the_similarity_of_a_rename(tmp_path: Path) -> None:
    blob = bytes(range(256)) * 4
    diff = make_diff(
        tmp_path,
        old={"pkg/aio/same.py": "x = 1\n", "a.bin": blob, "b.bin": blob, "keep.py": "a\n"},
        new={"pkg/same.py": "x = 1\n", "c.bin": blob, "d.bin": blob[:-1] + b"\0", "keep.py": "b\n"},
    )
    files = {fd.path: fd for fd in classify.parse_patch(diff.patch)}
    assert files["pkg/same.py"].similarity == 100 and files["pkg/same.py"].hunks == []
    assert files["c.bin"].similarity == 100
    changed = files["d.bin"]
    assert changed.status == "R" and changed.similarity is not None and changed.similarity < 100 and changed.hunks == []
    assert files["keep.py"].similarity is None


def test_parse_patch_notes_a_mode_change() -> None:
    patch = (
        "diff --git a/run.sh b/bin/run.sh\nold mode 100644\nnew mode 100755\n"
        "similarity index 100%\nrename from run.sh\nrename to bin/run.sh\n"
        "diff --git a/same.sh b/bin/same.sh\nsimilarity index 100%\nrename from same.sh\nrename to bin/same.sh\n"
    )
    files = {fd.path: fd for fd in classify.parse_patch(patch)}
    assert files["bin/run.sh"].similarity == 100 and files["bin/run.sh"].mode_changed
    assert files["bin/same.sh"].similarity == 100 and not files["bin/same.sh"].mode_changed


def test_parse_patch_makes_one_file_of_a_change_of_type() -> None:
    patch = (
        "diff --git a/run b/run\ndeleted file mode 100644\nindex 8b2fe54..0000000\n--- a/run\n+++ /dev/null\n"
        "@@ -1 +0,0 @@\n-echo hi\n"
        "diff --git a/run b/run\nnew file mode 120000\nindex 0000000..1de5659\n--- /dev/null\n+++ b/run\n"
        "@@ -0,0 +1 @@\n+target\n\\ No newline at end of file\n"
    )
    (fd,) = classify.parse_patch(patch)
    assert (fd.old_path, fd.new_path, fd.status, fd.mode_changed) == ("run", "run", "M", True)
    assert [h.lines for h in fd.hunks] == [[("-", 1, None, "echo hi")], [("+", None, 1, "target")]]


def test_build_runs_groups_changed_lines_between_context() -> None:
    fd = FileDiff(old_path="f", new_path="f", status="M")
    fd.hunks.append(
        Hunk(
            old_start=1,
            new_start=1,
            lines=[
                (" ", 1, 1, "ctx"),
                ("-", 2, None, "old"),
                ("+", None, 2, "new"),
                (" ", 3, 3, "ctx"),
                ("+", None, 4, "more"),
            ],
        )
    )
    classify.build_runs(fd)
    assert [(r.index, r.removed, r.added) for r in fd.runs] == [
        (0, [(2, "old")], [(2, "new")]),
        (1, [], [(4, "more")]),
    ]


# ---------------------------------------------------------------- python structure


def test_python_structure_finds_imports_and_symbols() -> None:
    source = """import os
from x import (
    a,
    b,
)


class C:
    def m(self):
        pass


@decorator
def f():
    if True:
        def inner():
            pass
"""
    imports, symbols = classify.python_structure(source)
    assert imports == {1, 2, 3, 4, 5}
    assert [(s["name"], s["kind"], s["start"]) for s in symbols] == [
        ("C", "class", 8),
        ("m", "def", 9),
        ("f", "def", 13),
        ("inner", "def", 16),
    ]


def test_python_structure_finds_the_multi_line_calls_at_module_level() -> None:
    source = """settings = Settings(
    debug=True,
)
app.run(
    port=80,
)
short = Settings(debug=False)
NAMES = [
    "a",
]


def f():
    g(
        1,
    )
"""
    _, symbols = classify.python_structure(source)
    assert [(s["name"], s["kind"], s["start"], s["body"], s["end"]) for s in symbols] == [
        ("Settings", "call", 1, 2, 3),
        ("run", "call", 4, 5, 6),
        ("f", "def", 13, 14, 16),
    ]


def test_python_structure_starts_the_body_after_the_decorators_and_the_signature() -> None:
    source = """@decorator
def f(
    a,
):
    \"\"\"Docstring.\"\"\"
    return a
"""
    _, symbols = classify.python_structure(source)
    assert [(s["start"], s["body"]) for s in symbols] == [(1, 5)]


def test_python_structure_starts_a_class_body_at_the_decorator_of_its_first_method() -> None:
    source = """class C:
    @property
    def a(self):
        return 1
"""
    _, symbols = classify.python_structure(source)
    assert [(s["name"], s["start"], s["body"]) for s in symbols] == [("C", 1, 2), ("a", 2, 4)]


def test_python_structure_is_empty_for_a_file_that_does_not_parse() -> None:
    assert classify.python_structure("def (") == (set(), [])


# ---------------------------------------------------------------- renames


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("foo(old_name)", "foo(new_name)", ("old_name", "new_name")),
        ("same", "same", None),
        ("x = 1", "x = 2", None),
        ("a.b.c()", "a.d.c()", ("b", "d")),
        ("old(1) + old(2)", "new(1) + new(2)", None),
    ],
)
def test_token_diff(old: str, new: str, expected: tuple[str, str] | None) -> None:
    assert classify.token_diff(old, new) == expected


def test_collect_renames_needs_two_runs_or_a_file_rename() -> None:
    twice = FileDiff(old_path="a.py", new_path="a.py", status="M")
    twice.runs = [run_of(["x = old()"], ["x = new()"]), run_of(["old()"], ["new()"], start=5)]
    once = FileDiff(old_path="b.py", new_path="b.py", status="M")
    once.runs = [run_of(["y = lone()"], ["y = alone()"])]
    moved = FileDiff(old_path="pkg/before.py", new_path="pkg/after.py", status="R")
    renames = classify.collect_renames([twice, once, moved])
    assert renames == {
        ("old", "new"): None,
        ("pkg.before", "pkg.after"): "pkg/after.py",
        ("before", "after"): "pkg/after.py",
    }


def test_a_rename_that_follows_a_renamed_file_has_that_file_as_its_origin(tmp_path: Path) -> None:
    diff = make_diff(
        tmp_path,
        old={"a/one.py": "x = 1\n", "b/two.py": "y = 2\n", "f.py": "one.f()\n", "g.py": "two.g()\n"},
        new={"a/uno.py": "x = 1\n", "b/dos.py": "y = 2\n", "f.py": "uno.f()\n", "g.py": "dos.g()\n"},
    )
    origins = {(r["from"], r["to"]): r["origin"] for r in classify.classify(diff)["renames"]}
    assert origins[("one", "uno")] == {"path": "a/uno.py", "line": 1, "side": "new", "text": "renamed from a/one.py"}
    assert origins[("two", "dos")] == {"path": "b/dos.py", "line": 1, "side": "new", "text": "renamed from b/two.py"}


def test_apply_renames_respects_word_boundaries() -> None:
    assert classify.apply_renames("foo foobar foo.bar", [("foo", "bar")]) == "bar foobar bar.bar"


@pytest.mark.parametrize(
    ("line", "name", "expected"),
    [
        ("def foo(x):", "foo", True),
        ("class foo:", "foo", True),
        ("foo: int = 1", "foo", True),
        ("foo = 1", "foo", True),
        ("    foo = 1", "mod.foo", True),
        ("bar(foo=1)", "foo", False),
        ("foo_bar = 1", "foo", False),
    ],
)
def test_is_definition(line: str, name: str, expected: bool) -> None:
    assert classify.is_definition(line, name) is expected


# ---------------------------------------------------------------- classification


def test_classify_run_whitespace() -> None:
    run = run_of(["  x = 1", ""], ["x = 1", "   "])
    classify.classify_run(run, set(), set(), [])
    assert run.cls == "whitespace"


def test_classify_run_import() -> None:
    run = run_of(["import os"], ["import os", "import sys"])
    classify.classify_run(run, {1}, {1, 2}, [])
    assert run.cls == "import"
    other = run_of([], ["import sys"], start=2)
    classify.classify_run(other, {1}, {1}, [])
    assert other.cls == "substantive"


def test_classify_run_rename_marks_the_definition_as_origin() -> None:
    renames = [("old", "new")]
    definition = run_of(["def old(x):"], ["def new(x):"])
    classify.classify_run(definition, set(), set(), renames)
    assert (definition.cls, definition.rename, definition.origin) == ("rename", ("old", "new"), True)
    use = run_of(["    return old(1)"], ["    return new(1)"])
    classify.classify_run(use, set(), set(), renames)
    assert (use.cls, use.rename, use.origin, use.pair_rename) == ("rename", ("old", "new"), False, [True])


def test_classify_run_names_a_rename_applied_twice_on_one_line() -> None:
    renames = [("old", "new")]
    run = run_of(["old(old)"], ["new(new)"])
    classify.classify_run(run, set(), set(), renames)
    assert (run.cls, run.rename) == ("rename", ("old", "new"))


def test_classify_run_substantive() -> None:
    run = run_of(["return 1"], ["return 2"])
    classify.classify_run(run, set(), set(), [("a", "b")])
    assert run.cls == "substantive" and run.pair_rename == [False]


def test_link_moves_links_a_block_removed_here_and_added_there() -> None:
    block = ["def f():", "    a = 1", "    b = 2", "    return a + b"]
    src = FileDiff(old_path="src.py", new_path="src.py", status="M")
    src.runs = [run_of(block, [], start=10)]
    dst = FileDiff(old_path="dst.py", new_path="dst.py", status="M")
    dst.runs = [run_of([], ["  " + line for line in block], start=20)]
    classify.link_moves([src, dst])
    assert src.runs[0].moved_to == {"path": "dst.py", "line": 20}
    assert dst.runs[0].moved_to == {"path": "src.py", "line": 10}
    classify.classify_run(src.runs[0], set(), set(), [])
    assert src.runs[0].cls == "moved"


def test_link_moves_counts_only_lines_that_are_not_blank() -> None:
    src = FileDiff(old_path="src.py", new_path="src.py", status="M")
    src.runs = [run_of(["a", "", "b", "", "c"], []), run_of(["", "", "", ""], [], start=20)]
    dst = FileDiff(old_path="dst.py", new_path="dst.py", status="M")
    dst.runs = [run_of([], ["a", "", "b", "", "c"]), run_of([], ["", "", "", ""], start=20)]
    classify.link_moves([src, dst])
    assert [r.moved_to for r in src.runs + dst.runs] == [None, None, None, None]


def test_blank_lines_removed_here_and_added_there_are_whitespace(tmp_path: Path) -> None:
    diff = make_diff(
        tmp_path,
        old={"a.py": "x = 1\n\n\n\n\ny = 2\n", "b.py": "p = 1\nq = 2\n"},
        new={"a.py": "x = 1\ny = 2\n", "b.py": "p = 1\n\n\n\n\nq = 2\n"},
    )
    runs = [(f["path"], r["class"], r["moved_to"]) for f in classify.classify(diff)["files"] for r in f["runs"]]
    assert runs == [("a.py", "whitespace", None), ("b.py", "whitespace", None)]


# ---------------------------------------------------------------- files


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("README.md", "docs"),
        ("docs/plan.txt", "docs"),
        (".claude/settings.json", "docs"),
        ("tests/test_x.py", "test"),
        ("pkg/test_x.py", "test"),
        ("pkg/conftest.py", "test"),
        ("e2e/flow.spec.ts", "test"),
        ("pkg/module.py", "implementation"),
        ("src/utils/__tests__/ImmutableObject-test.js", "test"),
        ("app/components/__tests__/Selector.spec.tsx", "test"),
        ("app/Button.test.ts", "test"),
        ("app/__snapshots__/Button.test.ts.snap", "test"),
        ("test/browser/run.js", "test"),
        ("cypress/support/commands.js", "test"),
        ("web/login.cy.ts", "test"),
        ("playwright/login.ts", "test"),
        ("pkg/server_test.go", "test"),
        ("pkg/module_test.py", "test"),
        ("core/src/test/java/app/Parser.java", "test"),
        ("core/src/main/java/app/ParserTest.java", "test"),
        ("core/src/main/kotlin/app/ParserTests.kt", "test"),
        ("core/src/main/java/app/ParserIT.java", "test"),
        ("spec/models/user_spec.rb", "test"),
        ("lib/user_test.rb", "test"),
        ("App.Tests/ParserTests.cs", "test"),
        ("App/ParserTests.cs", "test"),
        ("AppTests/Parser.swift", "test"),
        ("Sources/App/ParserTests.swift", "test"),
        ("src/ParserTest.php", "test"),
        ("lib/parser_test.dart", "test"),
        ("lib/parser_test.exs", "test"),
        ("src/Contest.java", "implementation"),
        ("src/latest.py", "implementation"),
        ("src/GIT.java", "implementation"),
        ("src/ab-test.yaml", "implementation"),
        ("api/openapi.spec.json", "implementation"),
        ("src/attest.go", "implementation"),
        ("src/Contests/Entry.cs", "implementation"),
        ("lib/test/helper.js", "implementation"),
        ("src/main/java/org/junit/Test.java", "implementation"),
        ("packages/playwright/src/index.ts", "implementation"),
    ],
)
def test_file_kind(path: str, kind: str) -> None:
    assert classify.file_kind(path) == kind


@pytest.mark.parametrize(
    ("path", "source", "marked", "expected"),
    [
        ("package-lock.json", "{}", set(), True),
        ("dist/app.min.js", "x", set(), True),
        ("proto/a_pb2.py", "x", set(), True),
        ("gen/schema.py", "# This file was automatically generated by tool.\n", set(), True),
        ("gen/schema.py", "# @generated\n", set(), True),
        ("gen/schema.py", "# DO NOT EDIT\n", set(), True),
        ("gen/schema.py", "x\n" * 5 + "# @generated\n", set(), False),
        ("gen/schema.py", "x\n", {"gen/schema.py"}, True),
        ("src/app.py", "import os\n", set(), False),
    ],
)
def test_is_generated(path: str, source: str, marked: set[str], expected: bool) -> None:
    assert classify.is_generated(path, source, marked) is expected


@pytest.mark.parametrize(
    ("path", "lang"),
    [
        ("a/b.py", "python"),
        ("x.TSX", "typescript"),
        ("Makefile", "makefile"),
        ("Dockerfile", "dockerfile"),
        ("page.html", "xml"),
        ("notes", "text"),
        ("weird.unknown", "text"),
    ],
)
def test_language(path: str, lang: str) -> None:
    assert classify.language(path) == lang


def test_classify_hides_a_file_as_noise_only_when_none_of_its_changes_shows(tmp_path: Path) -> None:
    blob = bytes(range(256)) * 4
    diff = make_diff(
        tmp_path,
        old={"moved.py": "x = 1\n", "imports.py": "import os\n\nx = 1\n", "img.bin": blob, "real.py": "x = 1\n"},
        new={
            "pkg/moved.py": "x = 1\n",
            "imports.py": "import os\nimport sys\n\nx = 1\n",
            "img.bin": blob[:-1] + b"\0",
            "real.py": "x = 2\n",
            "package-lock.json": "{}\n",
        },
    )
    files = {f["path"]: f["noise_only"] for f in classify.classify(diff)["files"]}
    assert files == {
        "pkg/moved.py": True,
        "imports.py": True,
        "img.bin": False,
        "real.py": False,
        "package-lock.json": False,
    }

    chmod = FileDiff(old_path="run.sh", new_path="bin/run.sh", status="R", similarity=100, mode_changed=True)
    assert not classify.ClassifiedFile(chmod, "", "new", [], set(), set(), generated=False).noise_only


def test_classify_a_small_pull_request(tmp_path: Path) -> None:
    diff = make_diff(
        tmp_path,
        old={
            ".gitattributes": "gen.py linguist-generated=true\n",
            "app.py": OLD_APP,
            "cli.py": "from app import old_name\n\nprint(old_name(1))\n",
            "gen.py": "VALUE = 1\n",
            "package-lock.json": '{"v": 1}\n',
            "gone.txt": "bye\n",
            "notes.txt": "Widgets are small.\n",
        },
        new={
            ".gitattributes": "gen.py linguist-generated=true\n",
            "app.py": NEW_APP,
            "cli.py": "from app import new_name\n\nprint(new_name(1))\n",
            "gen.py": "VALUE = 2\n",
            "package-lock.json": '{"v": 2}\n',
            "README.md": "# Hello\n",
            "docs/notes.txt": "Widgets are small.\n",
        },
    )
    result = classify.classify(diff)
    files = {f["path"]: f for f in result["files"]}

    assert [(r["class"], r["noise"]) for r in files["app.py"]["runs"]] == [
        ("import", True),
        ("rename", False),
        ("rename", True),
    ]
    assert files["app.py"]["runs"][1]["origin"] is True
    assert files["app.py"]["stats"] == {"additions": 3, "deletions": 2}
    assert files["app.py"]["import_lines"] == [1, 2] and files["app.py"]["old_import_lines"] == [1]
    assert [s["name"] for s in files["app.py"]["symbols"]] == ["new_name", "main"]

    assert [r["class"] for r in files["cli.py"]["runs"]] == ["import", "rename"]
    assert files["gen.py"]["generated"] is True and files["gen.py"]["runs"][0]["class"] == "substantive"
    assert files["package-lock.json"]["generated"] is True and files["package-lock.json"]["lang"] == "json"
    assert files["gone.txt"]["status"] == "D" and files["gone.txt"]["content_side"] == "old"
    assert files["gone.txt"]["content"] == "bye\n"
    assert files["README.md"]["status"] == "A" and files["README.md"]["kind"] == "docs"
    noise_only = {path for path, f in files.items() if f["noise_only"]}
    assert noise_only == {"cli.py", "docs/notes.txt"}

    (rename,) = result["renames"]
    assert rename["from"] == "old_name" and rename["to"] == "new_name"
    assert rename["runs"] == 3 and rename["files"] == ["app.py", "cli.py"]
    assert rename["origin"] == {"path": "app.py", "line": 5, "side": "new", "text": "def new_name(x):"}
