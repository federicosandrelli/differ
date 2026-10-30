from collections import Counter

import pytest

import file_types
from file_types import Docs, FileType
from languages import NO_LANGUAGE, Language


def test_no_suffix_or_name_belongs_to_two_file_types() -> None:
    keys = Counter(key for t in FileType for key in (*file_types.support(t).suffixes, *file_types.support(t).names))
    assert [key for key, n in keys.items() if n > 1] == []


def test_every_language_has_a_file_type() -> None:
    assert set(Language) - {file_types.support(t).language for t in FileType} == set()


def test_no_file_type_with_a_language_is_documentation() -> None:
    assert [
        t for t in FileType if file_types.support(t).language and file_types.support(t).docs is not Docs.NEVER
    ] == []


def test_every_suffix_is_lower_case() -> None:
    suffixes = [s for t in FileType for s in file_types.support(t).suffixes]
    assert [s for s in suffixes if s != s.lower()] == []


@pytest.mark.parametrize(
    ("path", "file_type"),
    [
        ("a/b.py", FileType.PYTHON),
        ("x.TSX", FileType.TYPESCRIPT),
        ("lib/mod.mts", FileType.TYPESCRIPT),
        ("Makefile", FileType.MAKEFILE),
        ("sub/Dockerfile", FileType.DOCKERFILE),
        ("notes", FileType.OTHER),
        ("a.b/notes", FileType.OTHER),
        (".gitignore", FileType.OTHER),
        ("weird.unknown", FileType.OTHER),
    ],
)
def test_of(path: str, file_type: FileType) -> None:
    assert file_types.of(path) == file_type


def test_language_support_of_a_file_type_without_a_language() -> None:
    assert file_types.language_support_of("data.json") is NO_LANGUAGE


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
def test_highlight_name(path: str, lang: str) -> None:
    assert file_types.support_of(path).highlight == lang


def test_every_generated_suffix_is_lower_case_and_ends_with_a_suffix_of_its_type() -> None:
    for t in FileType:
        support = file_types.support(t)
        assert [s for s in support.generated_suffixes if s != s.lower() or not s.endswith(support.suffixes)] == []
