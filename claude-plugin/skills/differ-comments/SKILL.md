---
name: differ-comments
description: Receive the line comments a reader sends from differ, a pull request viewer, to this session, and answer them. Use when the user asks to "listen for differ comments" (the prompt that differ's Copy connect prompt button copies), when a differ comment arrives on the monitor (a JSON frame with "event": "comment"), or when the user asks to re-arm or check the differ listener.
---

# differ comments

differ shows a pull request on one page, and the reader comments on its lines. A comment can go to GitHub, or to
this session with the **Send to Claude** button. Comments reach this session through a WebSocket that the Monitor
tool holds open. You answer each comment with a reply, which differ shows in the comment's thread, and you change the
code when the comment asks for a change.

## Connect

1. Find the WebSocket URL. The connect prompt names it:
   `ws://127.0.0.1:<port>/api/<owner>/<repo>/<number>/listen`. Without a URL, read the pull request of the current
   branch with `gh pr view --json url` (`https://github.com/<owner>/<repo>/pull/<number>`), and use port 8765.
2. Arm the monitor:

   ```
   Monitor({
     ws: { url: "ws://127.0.0.1:8765/api/<owner>/<repo>/<number>/listen" },
     description: "differ comments on <owner>/<repo>#<number>",
     timeout_ms: 3600000
   })
   ```

3. End the turn with one line: you listen for differ comments on `<owner>/<repo>#<number>`.

Comments that the reader sent while no session listened arrive as soon as the monitor connects.

## Handle a comment

A comment arrives as one text frame:

```json
{"event": "comment", "pr": "acme/widgets#12", "id": "c3", "path": "src/app.py", "line": 11, "start_line": null,
 "side": "RIGHT", "commit": "<sha>", "code": ["    print(\"hello\")"], "body": "Use a logger here", "replay": false}
```

- `line` is the last line of the comment, and `start_line` the first when it covers several lines.
- `side` is `RIGHT` for a line of the file at `commit`, and `LEFT` for a line of the old file, before the pull request.
- `code` holds the lines the comment covers, as the reader saw them.
- `replay` is true when differ sends the comment again because this session connected again. Skip a replayed
  comment that you already answered or are working on.

For each comment:

1. Read the file around the line. The branch may have new commits since `commit`, so find the code by its text.
2. When the comment asks for a change, make it. Follow this repository's own rules for commits and pushes.
3. Reply to every comment, also when it needs no change. The reply is one or two plain sentences: what you changed,
   or the answer. differ shows Markdown.

Send the reply with `curl`. differ refuses a POST without its own Origin header and a JSON body. Write the body as
JSON, with quotes, backslashes and new lines escaped:

```bash
curl -sS -X POST "http://127.0.0.1:8765/api/<owner>/<repo>/<number>/claude_reply" \
  -H "Origin: http://127.0.0.1:8765" -H "Content-Type: application/json" \
  --data-binary @- <<'EOF'
{"id": "c3", "body": "Replaced the print with `log.info`."}
EOF
```

The answer is the comment as JSON with `"state": "answered"`. An answer with `"error"` says what to fix.

## When the monitor ends

- **It expired.** Arm it again at once. differ keeps the comments while no session listens, so nothing is lost.
- **Close code 4000.** Another session listens on this pull request. Tell the user in one line and stop.
- **The connection was refused.** differ is not running. Say so once, and arm the monitor again only when the user
  asks.
- **Any other close.** Arm it again after 5 seconds, at most three times; then tell the user.

## Rules

- Answer every comment in differ, never on GitHub.
- Never reply twice to one comment: a second reply replaces the first.
