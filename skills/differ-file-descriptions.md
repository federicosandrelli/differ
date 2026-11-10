---
name: differ-file-descriptions
description: Write differ's file descriptions (why a pull request changes a file, then what changes) and comment on the pull request's salient lines. Use before every call to the `describe_file` tool of the differ MCP server, and when the reader asks for file descriptions on the differ page.
---

# differ file descriptions

A description says why the pull request changes the file, then what changes. It is about the change, never about
what the file is or does.

## Which files

Skip these files, unless the reader names one:

- a file a tool writes, such as a lock file, a snapshot or generated code;
- a change that is only formatting, or only a rename;
- a small change that only follows another described file, such as an import;
- a test whose name says what it checks.

## The reason

Take the reason from these. Never guess it. When they give none, say only what changes.

- The pull request's title and description: `gh pr view`.
- The commit messages: `git log --format=%B <base>..HEAD -- <path>`. `<base>` is the base commit that
  `pull_request_diff` prints first.
- The diffs of files that depend on the change. When the file changes to serve another file, name that file.
- The reader's messages.

## Format

- One or two short sentences, then one to three short bullet points, each naming one change to look for. Bullets
  may nest. Drop them only when the sentences say it all.
- Present tense, simple words.
- Name things as the code spells them, in backticks. No invented terms, no unexplained abbreviations, no "it" or
  "this" when the reader could ask "which one?".
- Leave out what the file's header shows: the path, added, deleted or renamed, and the line counts.
- Moved code: name both files, and say whether the code changed.
- New file: say why it exists and where its code comes from.
- Deleted file: say why it goes and where its code went, if anywhere.
- Rename, described because the reader asked: say why, or write "No change beyond the rename."
- File a tool writes, described because the reader asked: name only the source change behind it, as in "Follows
  the new `httpx` dependency in `pyproject.toml`."

Good:

```markdown
`load_config` takes a new parameter, `strict`, so that the new `--strict` flag in `cli.py` can reject unknown keys.

- `strict` defaults to `False`, so other callers behave as before.
- An unknown key with `strict=True` raises `ConfigError`, which names the key.
```

```markdown
`Token` and `TokenKind` move, unchanged, to the new file `parsing/tokens.py`, so that `lexer.py` can use them
without importing the parser.

- Their users import them from there:
  - `Parser` in this file.
  - `Lexer` in `lexer.py`.
```

Bad:

- "`load_config` takes a new parameter, `strict`." No reason.
- "This module loads the configuration." Describes the file, not the change.
- "Adds DI for the repo layer." Unexplained abbreviation, invented term.
- "Simplifies the helper to improve readability." Guessed reason. Which helper?
- Five sentences and six bullets that follow the diff line by line. Too long.

## Salient lines

Comment on lines only when the reader asks about every file, after you have read the whole pull request. Pick the
lines that matter most to its goal: usually two in the whole pull request, never more than three.

- Salient: a line that does what the pull request is for, such as the check that rejects the unknown key.
- Not salient: an import, a signature change that only follows another file, a line that moved unchanged, or a
  test, unless the pull request is about that test.

Call `start_thread` for each:

- Give `path`, as the diff shows it, and `line`. Add `start_line` for a span.
- `side` is `RIGHT` by default. Use `LEFT` for a removed line whose removal is the point.
- The body is one short sentence on why the lines matter. Don't repeat the file's description.
- Check that the code the tool answers is the code you meant.
- The tool refuses lines where you already started a thread, and names the thread. Leave it, or reply with
  `reply_in_thread`.

## Steps

1. Read the pull request's title and description.
2. For each file the reader asked about, or every file of the diff:
   1. Read its diff with `pull_request_diff`, its commit messages, and the diffs of files the change depends on.
   2. When it needs a description, call `describe_file` with its path, as the diff shows it, and the
      description as `body`.
3. When the reader asked about every file, comment on the salient lines.
