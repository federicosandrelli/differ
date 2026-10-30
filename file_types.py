"""The file types, found by a file's name, and what each one is."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

import languages


class FileType(Enum):
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
    JSON = auto()
    YAML = auto()
    TOML = auto()
    INI = auto()
    CSS = auto()
    SCSS = auto()
    HTML = auto()
    XML = auto()
    SVG = auto()
    MARKDOWN = auto()
    RESTRUCTUREDTEXT = auto()
    ASCIIDOC = auto()
    RDOC = auto()
    POD = auto()
    RD = auto()
    DOXYGEN = auto()
    TEXT = auto()
    DIFF = auto()
    GRAPHQL = auto()
    MAKEFILE = auto()
    DOCKERFILE = auto()
    PNG = auto()
    JPEG = auto()
    GIF = auto()
    WEBP = auto()
    AVIF = auto()
    BMP = auto()
    ICO = auto()
    OTHER = auto()


class Docs(Enum):
    """Where a file of a type is documentation: anywhere, only inside a documentation folder, or nowhere. A type
    that can hold code is never documentation."""

    ALWAYS = auto()
    IN_DOCS_FOLDER = auto()
    NEVER = auto()


@dataclass(frozen=True)
class FileTypeSupport:
    """How the app knows a file type, and what it does with one: `suffixes` (lower case) and `names` find the type,
    `highlight` is the highlight.js language name, `language` is its programming language or None, `docs` says where
    a file of the type is documentation, a name ending in one of `generated_suffixes` is generated, and `image_type` is
    the media type of an image."""

    suffixes: tuple[str, ...]
    names: tuple[str, ...]
    highlight: str
    language: languages.Language | None
    docs: Docs
    generated_suffixes: tuple[str, ...]
    image_type: str | None


def support(file_type: FileType) -> FileTypeSupport:
    match file_type:
        case FileType.PYTHON:
            return FileTypeSupport(
                suffixes=(".py", ".pyi"),
                names=(),
                highlight="python",
                language=languages.Language.PYTHON,
                docs=Docs.NEVER,
                generated_suffixes=("_pb2.py", "_pb2.pyi", "_pb2_grpc.py"),
                image_type=None,
            )
        case FileType.JAVASCRIPT:
            return FileTypeSupport(
                suffixes=(".js", ".mjs", ".cjs", ".jsx"),
                names=(),
                highlight="javascript",
                language=languages.Language.JAVASCRIPT,
                docs=Docs.NEVER,
                generated_suffixes=(".min.js",),
                image_type=None,
            )
        case FileType.TYPESCRIPT:
            return FileTypeSupport(
                suffixes=(".ts", ".tsx", ".mts", ".cts"),
                names=(),
                highlight="typescript",
                language=languages.Language.TYPESCRIPT,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.JAVA:
            return FileTypeSupport(
                suffixes=(".java",),
                names=(),
                highlight="java",
                language=languages.Language.JAVA,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.KOTLIN:
            return FileTypeSupport(
                suffixes=(".kt",),
                names=(),
                highlight="kotlin",
                language=languages.Language.KOTLIN,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.SWIFT:
            return FileTypeSupport(
                suffixes=(".swift",),
                names=(),
                highlight="swift",
                language=languages.Language.SWIFT,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.CSHARP:
            return FileTypeSupport(
                suffixes=(".cs",),
                names=(),
                highlight="csharp",
                language=languages.Language.CSHARP,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.PHP:
            return FileTypeSupport(
                suffixes=(".php",),
                names=(),
                highlight="php",
                language=languages.Language.PHP,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.GO:
            return FileTypeSupport(
                suffixes=(".go",),
                names=(),
                highlight="go",
                language=languages.Language.GO,
                docs=Docs.NEVER,
                generated_suffixes=(".pb.go",),
                image_type=None,
            )
        case FileType.RUBY:
            return FileTypeSupport(
                suffixes=(".rb",),
                names=(),
                highlight="ruby",
                language=languages.Language.RUBY,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.DART:
            return FileTypeSupport(
                suffixes=(".dart",),
                names=(),
                highlight="text",
                language=languages.Language.DART,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.ELIXIR:
            return FileTypeSupport(
                suffixes=(".ex", ".exs"),
                names=(),
                highlight="text",
                language=languages.Language.ELIXIR,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.RUST:
            return FileTypeSupport(
                suffixes=(".rs",),
                names=(),
                highlight="rust",
                language=languages.Language.RUST,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.C:
            return FileTypeSupport(
                suffixes=(".c", ".h"),
                names=(),
                highlight="c",
                language=languages.Language.C,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.CPP:
            return FileTypeSupport(
                suffixes=(".cpp", ".hpp"),
                names=(),
                highlight="cpp",
                language=languages.Language.CPP,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.LUA:
            return FileTypeSupport(
                suffixes=(".lua",),
                names=(),
                highlight="lua",
                language=languages.Language.LUA,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.SHELL:
            return FileTypeSupport(
                suffixes=(".sh", ".bash", ".zsh"),
                names=(),
                highlight="bash",
                language=languages.Language.SHELL,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.SQL:
            return FileTypeSupport(
                suffixes=(".sql",),
                names=(),
                highlight="sql",
                language=languages.Language.SQL,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.R:
            return FileTypeSupport(
                suffixes=(".r",),
                names=(),
                highlight="r",
                language=languages.Language.R,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.PERL:
            return FileTypeSupport(
                suffixes=(".pl",),
                names=(),
                highlight="perl",
                language=languages.Language.PERL,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.JSON:
            return FileTypeSupport(
                suffixes=(".json",),
                names=(),
                highlight="json",
                language=None,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.YAML:
            return FileTypeSupport(
                suffixes=(".yml", ".yaml"),
                names=(),
                highlight="yaml",
                language=None,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.TOML:
            return FileTypeSupport(
                suffixes=(".toml",),
                names=(),
                highlight="ini",
                language=None,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.INI:
            return FileTypeSupport(
                suffixes=(".ini", ".cfg"),
                names=(),
                highlight="ini",
                language=None,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.CSS:
            return FileTypeSupport(
                suffixes=(".css",),
                names=(),
                highlight="css",
                language=None,
                docs=Docs.NEVER,
                generated_suffixes=(".min.css",),
                image_type=None,
            )
        case FileType.SCSS:
            return FileTypeSupport(
                suffixes=(".scss",),
                names=(),
                highlight="scss",
                language=None,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.HTML:
            return FileTypeSupport(
                suffixes=(".html",),
                names=(),
                highlight="xml",
                language=None,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.XML:
            return FileTypeSupport(
                suffixes=(".xml",),
                names=(),
                highlight="xml",
                language=None,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.SVG:
            return FileTypeSupport(
                suffixes=(".svg",),
                names=(),
                highlight="xml",
                language=None,
                docs=Docs.IN_DOCS_FOLDER,
                generated_suffixes=(),
                image_type="image/svg+xml",
            )
        case FileType.MARKDOWN:
            return FileTypeSupport(
                suffixes=(".md", ".markdown", ".cheatmd"),
                names=(),
                highlight="markdown",
                language=None,
                docs=Docs.ALWAYS,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.RESTRUCTUREDTEXT:
            return FileTypeSupport(
                suffixes=(".rst",),
                names=(),
                highlight="text",
                language=None,
                docs=Docs.ALWAYS,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.ASCIIDOC:
            return FileTypeSupport(
                suffixes=(".adoc", ".asciidoc"),
                names=(),
                highlight="text",
                language=None,
                docs=Docs.ALWAYS,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.RDOC:
            return FileTypeSupport(
                suffixes=(".rdoc",),
                names=(),
                highlight="text",
                language=None,
                docs=Docs.ALWAYS,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.POD:
            return FileTypeSupport(
                suffixes=(".pod",),
                names=(),
                highlight="text",
                language=None,
                docs=Docs.ALWAYS,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.RD:
            return FileTypeSupport(
                suffixes=(".rd",),
                names=(),
                highlight="text",
                language=None,
                docs=Docs.ALWAYS,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.DOXYGEN:
            return FileTypeSupport(
                suffixes=(".dox",),
                names=(),
                highlight="text",
                language=None,
                docs=Docs.ALWAYS,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.TEXT:
            return FileTypeSupport(
                suffixes=(".txt",),
                names=(),
                highlight="text",
                language=None,
                docs=Docs.IN_DOCS_FOLDER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.DIFF:
            return FileTypeSupport(
                suffixes=(".diff", ".patch"),
                names=(),
                highlight="diff",
                language=None,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.GRAPHQL:
            return FileTypeSupport(
                suffixes=(".graphql",),
                names=(),
                highlight="graphql",
                language=None,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.MAKEFILE:
            return FileTypeSupport(
                suffixes=(),
                names=("Makefile",),
                highlight="makefile",
                language=None,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.DOCKERFILE:
            return FileTypeSupport(
                suffixes=(),
                names=("Dockerfile",),
                highlight="dockerfile",
                language=None,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )
        case FileType.PNG:
            return FileTypeSupport(
                suffixes=(".png",),
                names=(),
                highlight="text",
                language=None,
                docs=Docs.IN_DOCS_FOLDER,
                generated_suffixes=(),
                image_type="image/png",
            )
        case FileType.JPEG:
            return FileTypeSupport(
                suffixes=(".jpg", ".jpeg"),
                names=(),
                highlight="text",
                language=None,
                docs=Docs.IN_DOCS_FOLDER,
                generated_suffixes=(),
                image_type="image/jpeg",
            )
        case FileType.GIF:
            return FileTypeSupport(
                suffixes=(".gif",),
                names=(),
                highlight="text",
                language=None,
                docs=Docs.IN_DOCS_FOLDER,
                generated_suffixes=(),
                image_type="image/gif",
            )
        case FileType.WEBP:
            return FileTypeSupport(
                suffixes=(".webp",),
                names=(),
                highlight="text",
                language=None,
                docs=Docs.IN_DOCS_FOLDER,
                generated_suffixes=(),
                image_type="image/webp",
            )
        case FileType.AVIF:
            return FileTypeSupport(
                suffixes=(".avif",),
                names=(),
                highlight="text",
                language=None,
                docs=Docs.IN_DOCS_FOLDER,
                generated_suffixes=(),
                image_type="image/avif",
            )
        case FileType.BMP:
            return FileTypeSupport(
                suffixes=(".bmp",),
                names=(),
                highlight="text",
                language=None,
                docs=Docs.IN_DOCS_FOLDER,
                generated_suffixes=(),
                image_type="image/bmp",
            )
        case FileType.ICO:
            return FileTypeSupport(
                suffixes=(".ico",),
                names=(),
                highlight="text",
                language=None,
                docs=Docs.IN_DOCS_FOLDER,
                generated_suffixes=(),
                image_type="image/x-icon",
            )
        case FileType.OTHER:
            return FileTypeSupport(
                suffixes=(),
                names=(),
                highlight="text",
                language=None,
                docs=Docs.NEVER,
                generated_suffixes=(),
                image_type=None,
            )


BY_NAME = {name: file_type for file_type in FileType for name in support(file_type).names}
BY_SUFFIX = {suffix: file_type for file_type in FileType for suffix in support(file_type).suffixes}


def of(path: str) -> FileType:
    """The type of the file at the path: by its name, else by its suffix in any case, else OTHER."""
    name = path.rsplit("/", 1)[-1]
    suffix = name[name.rfind(".") :].lower() if "." in name else ""
    return BY_NAME.get(name) or BY_SUFFIX.get(suffix, FileType.OTHER)


def support_of(path: str) -> FileTypeSupport:
    return support(of(path))


def language_support_of(path: str) -> languages.LanguageSupport:
    """The LanguageSupport of the file at the path; NO_LANGUAGE for a type without a language."""
    language = support_of(path).language
    return languages.NO_LANGUAGE if language is None else languages.support(language)
