# Domain language

Terms the code uses. A name here is the name in the code.

- **Pull request (PR).** What a page shows: one GitHub pull request, identified by `owner/repo#number` and built per head commit.
- **File.** One changed file of the PR, with its hunks, runs and content on one side (`content_side`: the new file, or the old one for a deleted file).
- **Hunk.** A unified-diff hunk from GitHub's diff of the PR, as lines `[tag, old, new, text]`.
- **Run.** A maximal block of changed hunk lines (removed then added) with a class: `substantive`, or the noise classes `import`, `rename`, `whitespace`, `moved`.
- **Row.** One line of the diff table: a removed line (`del`), an added line (`add`) or an unchanged one (`ctx`).
- **Row key.** The address of a row on its own side: `O<old line>` for a removed line, `L<new line>` for the rest. Defined in `page/25-address.js`; threads, search hits, deep links and element ids all name rows by key.
- **File view.** The rows of one file grouped into blocks with every fold decided (`fileBlocks` in `page/30-rows.js`). The diff table draws the view; `reveal(file, key)` opens whatever hides a row; `toggleFold(id)` answers a click on a fold row.
- **Fold.** Something the view hides until opened: the middle of a context block, a noise run, several noise runs grouped as one, or an import section. A fold has an opaque **fold id** that only the file view reads.
- **Pinned row.** A row a fold may never hide: a search hit, a row with a thread, or the row with the comment being written.
- **Thread.** A GitHub review thread, placed on the row its line maps to, or listed as outdated when its line is gone.
- **Shell.** The tabbed document at `/` that holds the start page and one page per PR in iframes. Its two sides are `shell/tabs.js` (the strip and the frames) and `shell/frame.js` (what a page inside it does); the messages between them are known only to those two files. A page reaches the shell through `SHELL`, null when the page is the top-level document.
- **Tab.** One entry in the shell's strip: the home tab for the start page, or a PR page, keyed by its path.
- **Theme.** The colour scheme (`auto`, `light`, `sepia`, `dark`) saved as `dr:theme`. `shell/theme.js` applies it in every document and keeps the native window and the other open documents in step; the palettes live in `render.py`.
- **Bug report.** The prompt for Claude Code that the sidebar's footer builds from a typed description and the page's state (`page/75-bugreport.js`). `LOG` in `page/00-data.js` keeps the recent actions and errors the prompt quotes.
