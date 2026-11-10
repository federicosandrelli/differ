---
name: differ-file-descriptions
description: Write the Markdown description of why a pull request changes one file, and what it changes, shown under the file's header in differ, and comment on the file's few lines that matter most to the pull request's goal. Use before every call to the `describe_file` tool of the differ MCP server, and when the reader asks for file descriptions on the differ page.
---

# differ file descriptions

The reader reads a file's description before the file's diff. The diff already shows what changed, so the
description's main job is to say why the pull request changes this file. Then it says what changes, so the reader
knows what to look for. Then a comment on the file's most important lines takes the reader straight to them.

## What a description says

A description says why the pull request changes the file, then what it changes. It is about the change, never about
what the file is or does.

Good descriptions:

```markdown
`load_config` takes a new parameter, `strict`, so that the new `--strict` flag in `cli.py` can reject unknown keys.

- `strict` defaults to `False`, so other callers behave as before.
- An unknown key with `strict=True` raises `ConfigError`, which names the key.
```

```markdown
Most of the code moves, unchanged, to the new file `parsing/tokens.py`, so that `lexer.py` can use the tokens
without importing the parser.

- `Token` and `TokenKind` move as they were.
- `Parser` imports them from `parsing/tokens.py`.
```

```markdown
`fetch_all` is now `fetch_pages`, because it returns one page at a time since this pull request.

- Its only callers change with it:
  - `sync.py` loops over the pages.
  - `jobs.py` stops at the first page.
```

```markdown
New tests for `load_config` with `strict=True`, to check the new `strict` parameter in `config.py`.

- One test per kind of unknown key: at the top level, and nested.
```

```markdown
The only changes are formatting, from running `ruff format`. The code behaves as before.
```

Bad descriptions, and what is wrong with each:

- "`load_config` takes a new parameter, `strict`." It says what changes, which the diff shows, but not why.
- Five sentences, then six bullets that walk through the diff line by line. The reader wants the gist, and the diff
  holds the rest.
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
- Write one or two short sentences, then one to three short bullet points. The sentences say why the file changes
  and give the gist of the change. Each bullet names one change worth looking for, with its own reason when it has
  one. A bullet may hold nested bullets, such as the callers of a renamed function. Leave out the bullets only when
  the sentences say it all, as for a formatting change or a lock file.
- Write in the present tense, in simple words and ordinary grammar.
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

## Comments on the salient lines

After the description, comment on the file's salient lines: the lines that matter most to the goal of the pull
request, where the reader should look first. They are few, usually one or two places in a file, and often none:

- Pick a line that does what the pull request is for: the check that rejects the unknown key, the call that now
  passes the new flag, the line that changes the behaviour. Not an import, a signature change that only follows
  another file, or a line that moved unchanged.
- Cover a few lines together, with `start_line`, when the important part spans them, such as a short condition.
- Comment on no line of a file whose changes only serve another file, or need no care: formatting, a rename, a lock
  file, a moved block. Comment on a test only when the test is what the pull request is about.

Start each comment with the `start_thread` tool: the file's `path` as the diff shows it, `line` and, for several
lines, `start_line`. Lines take the `RIGHT` side, the default; a line the pull request removes takes `side` `LEFT`,
when its removal is the point. The body is one short sentence that says why these lines matter to the goal, without
repeating the description. Check that the code the tool answers is the code you meant.

`start_thread` refuses lines where you already started a thread, and names that thread. Then leave it as it is, or
reply in it with `reply_in_thread` when your description of the file changed what it should say.

## Steps

Read the pull request's title and description once. Then, for each file the reader asked about, or every file of the
diff when the reader asks for all of them:

1. Read the file's diff with the `pull_request_diff` tool and the file's `path`, and the messages of the commits that
   change it. Also read the diff of any file the change depends on: code that moved from or to it, a caller, or where
   a new parameter comes from.
2. Find the reason, as "Where the reason comes from" says, and write the description by the rules above.
3. Call `describe_file` with the file's path as the diff shows it and the description as `body`.
4. Find the file's salient lines, if it has any, and comment on each with `start_thread`, as "Comments on the
   salient lines" says.
