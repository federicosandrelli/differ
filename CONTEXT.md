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
- **Tab, shell.** The page of one PR, and the tabbed frame around it served by `server.py`.
