---
name: differ-file-descriptions
description: Write the Markdown description of why a pull request changes one file, and what it changes, shown under the file's header in differ. Use before every call to the `describe_file` tool of the differ MCP server, and when the reader asks for file descriptions on the differ page.
---

# differ file descriptions

The reader reads a file's description before the file's diff. The diff already shows what changed, so the
description's main job is to say why the pull request changes this file. Then it says what changes, so the reader
knows what to look for.

## What a description says

A description says why the pull request changes the file, then what it changes. It is about the change, never about
what the file is or does.

Good descriptions:

- "`load_config` takes a new parameter, `strict`, so that the new `--strict` flag in `cli.py` can reject unknown
  keys."
- "Most of the code moves, unchanged, to the new file `parsing/tokens.py`, so that `lexer.py` can use the tokens
  without importing the parser."
- "`fetch_all` is now `fetch_pages`, because it returns one page at a time since this pull request. Its only
  callers, in `sync.py` and `jobs.py`, change with it."
- "New file. It holds `parse_args`, which moves out of `cli.py` so that `server.py` can parse the same flags."
- "New tests for `load_config` with `strict=True`, to check the new `strict` parameter in `config.py`."
- "The only changes are formatting, from running `ruff format`. The code behaves as before."

Bad descriptions, and what is wrong with each:

- "`load_config` takes a new parameter, `strict`." It says what changes, which the diff shows, but not why.
- "This module loads the configuration." It describes the file, not the change.
- "Refactors the helper to use the new approach." The reader can't tell which helper or which approach.
- "Adds DI for the repo layer." "DI" is an acronym, and "repo layer" is a term the code doesn't use.
- "Simplifies the parser to improve readability." The reason is a guess, and the reader can't tell what changed.

## Where the reason comes from

Take the reason from what the pull request states or shows. Never guess it.

- The pull request's title and description: `gh pr view`.
- The messages of the commits that change the file: `git log --format=%B <base>..HEAD -- <path>`, where `<base>` is
  the `base` commit that `pull_request_diff` prints first.
- The diffs of the files that use the change: a caller of a new parameter, the new home of moved code, or the code
  that a new test checks. A change in this file often exists to serve a change in another file. Then that other
  change is the reason, so name the other file and say what it needs.
- The reader's comments and messages in this session.

When none of these gives a reason, say only what changes. Don't invent one.

## Rules

- Lead with the reason. Use "so that", "because" or "to" to tie it to the change.
- Write one to four short sentences in the present tense, in simple words and ordinary grammar. When the file has
  several separate changes, write a list instead, with one short sentence per change and its reason.
- Name every thing by its real name, as the code spells it, in backticks: the function, class, parameter, file or
  flag. Don't write "it", "this", "the helper" or "the new logic" when the reader could ask "which one?".
- Use only words from the code, from the pull request, or from ordinary English. Don't invent terms. Spell out an
  abbreviation unless the code itself uses it.
- When code moves between files, name both files and say whether the code changed on the way.
- For a new file, say why it exists and where its code comes from (another file, named, or new code).
- For a deleted file, say why it goes, and where its code went or that nothing replaces it.
- For a renamed file without other changes, say why it was renamed, or write "No change beyond the rename."
- For a file that a tool writes, such as a lock file or a snapshot, name the change in the source file that
  produced it, as in "Follows the new `httpx` dependency in `pyproject.toml`." Say nothing about the file's content.
- Leave out what the header already shows: the file's path, whether it is added, deleted or renamed, and its counts
  of added and removed lines.

## Steps

Read the pull request's title and description once. Then, for each file the reader asked about, or every file of the
diff when the reader asks for all of them:

1. Read the file's diff with the `pull_request_diff` tool and the file's `path`, and the messages of the commits that
   change it. Also read the diff of any file the change depends on: code that moved from or to it, a caller, or where
   a new parameter comes from.
2. Find the reason, as "Where the reason comes from" says, and write the description by the rules above.
3. Call `describe_file` with the file's path as the diff shows it and the description as `body`.
