---
name: differ-file-descriptions
description: Write the Markdown description of what a pull request changes in one file, shown under the file's header in differ. Use before every call to the `describe_file` tool of the differ MCP server, and when the reader asks for file descriptions on the differ page.
---

# differ file descriptions

The reader reads a file's description before the file's diff, to know what to look for in it.

## What a description says

A description says what the pull request changes in the file. It is about the change, never about what the file is
or does.

Good descriptions:

- "Most of the code moves, unchanged, to the new file `parsing/tokens.py`."
- "`load_config` takes a new parameter, `strict`. `cli.py` passes it from the `--strict` flag."
- "`fetch_all` is now `fetch_pages`. Its only callers, in `sync.py` and `jobs.py`, change with it."
- "The only changes are formatting: line breaks and quotes. The code behaves as before."
- "New file. `cli.py` calls its `parse_args` in place of the `argparse` code that `cli.py` had before."
- "New tests for `load_config` with `strict=True`, which `config.py` adds in this pull request."

Bad descriptions, and what is wrong with each:

- "This module loads the configuration." It describes the file, not the change.
- "Refactors the helper to use the new approach." The reader can't tell which helper or which approach.
- "Adds DI for the repo layer." "DI" is an acronym, and "repo layer" is a term the code doesn't use.
- "Simplifies the parser to improve readability." The reader can't tell what changed, and the reason is a guess.

## Rules

- Write one to four short sentences in the present tense, in simple words and ordinary grammar. When the file has
  several separate changes, write a list instead, with one short sentence per change.
- Name every thing by its real name, as the code spells it, in backticks: the function, class, parameter, file or
  flag. Don't write "it", "this", "the helper" or "the new logic" when the reader could ask "which one?".
- Use only words from the code, from the pull request, or from ordinary English. Don't invent terms. Spell out an
  abbreviation unless the code itself uses it.
- Give the reason for a change only when the diff, a commit message or the pull request's description (`gh pr view`)
  states it. Otherwise leave the reason out.
- When code moves between files, name both files and say whether the code changed on the way.
- When the change in this file follows from a change in another file, name that file and say what it changes.
- For a new file, say where its code comes from (another file, named, or new code), and which file uses it.
- For a deleted file, say where its code went, or that nothing replaces it, and which files change with it.
- For a renamed file without other changes, write "No change beyond the rename."
- For a file that a tool writes, such as a lock file or a snapshot, name the change in the source file that
  produced it, as in "Follows the new `httpx` dependency in `pyproject.toml`." Say nothing about the file's content.
- Leave out what the header already shows: the file's path, whether it is added, deleted or renamed, and its counts
  of added and removed lines.

## Steps

For each file the reader asked about, or every file of the diff when the reader asks for all of them:

1. Read the file's diff with the `pull_request_diff` tool and the file's `path`. Also read the diff of any file the
   change depends on: code that moved from or to it, a caller, or where a new parameter comes from.
2. Write the description by the rules above.
3. Call `describe_file` with the file's path as the diff shows it and the description as `body`.
