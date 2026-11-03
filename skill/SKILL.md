---
name: differ-comments
description: Receive the line comments a reader sends from differ, a pull request viewer, to this session, and answer them in their threads. Use when the user asks to "listen for differ comments" (the prompt that differ's Copy connect prompt button copies), when a differ comment or follow-up arrives on the monitor (a JSON frame with "event": "comment" or "follow_up"), or when the user asks to re-arm or check the differ listener.
---

# differ comments

differ shows a pull request on one page, and the reader comments on its lines. A comment can go to GitHub, or to
this session with the **Send to Claude** button. Each comment starts a thread on its lines, and the reader can reply
in the thread to carry on the conversation. Comments and replies reach this session through a WebSocket that the
Monitor tool holds open. You answer each one with a reply, which differ adds to the end of the thread, and you change
the code when the reader asks for a change.

## Connect

1. Find the WebSocket URL. The connect prompt names it:
   `ws://127.0.0.1:<port>/api/<owner>/<repo>/<number>/listen`. Use its port everywhere below. Without a URL, read the
   pull request of the current branch with `gh pr view --json url` (`https://github.com/<owner>/<repo>/pull/<number>`),
   and try port 8766, where the differ desktop app serves, then 8765, where `server.py` serves by default. The desktop
   app takes another port when 8766 is taken; then ask the user for the connect prompt.
2. Arm the monitor:

   ```
   Monitor({
     ws: { url: "ws://127.0.0.1:<port>/api/<owner>/<repo>/<number>/listen" },
     description: "differ comments on <owner>/<repo>#<number>",
     timeout_ms: 3600000
   })
   ```

3. End the turn with one line: you listen for differ comments on `<owner>/<repo>#<number>`.

Threads that wait for your reply arrive as soon as the monitor connects: those that the reader wrote in while no
session listened, and those you had not answered yet.

## Handle a comment or a follow-up

A new comment arrives as one text frame with `"event": "comment"`:

```json
{"event": "comment", "pr": "acme/widgets#12", "id": "5f0c2a9e41b7", "path": "src/app.py", "line": 11, "start_line": null,
 "side": "RIGHT", "commit": "<sha>", "code": ["    print(\"hello\")"],
 "messages": [{"author": "reader", "body": "Use a logger here", "at": "2026-11-03T09:12:40Z"}],
 "seen": 1, "replay": false}
```

When the reader replies in a thread, the whole thread arrives again with `"event": "follow_up"`. Its `messages` hold
every message of the thread, oldest first, yours included:

```json
{"event": "follow_up", "pr": "acme/widgets#12", "id": "5f0c2a9e41b7", "path": "src/app.py", "line": 11, "start_line": null,
 "side": "RIGHT", "commit": "<sha>", "code": ["    print(\"hello\")"],
 "messages": [{"author": "reader", "body": "Use a logger here", "at": "2026-11-03T09:12:40Z"},
              {"author": "claude", "body": "Replaced the print with `log.info`.", "at": "2026-11-03T09:13:05Z"},
              {"author": "reader", "body": "Make it debug, not info", "at": "2026-11-03T09:20:11Z"}],
 "seen": 3, "replay": false}
```

- `id` names the thread. It stays the same for every reply in it.
- `line` is the last line of the comment, and `start_line` the first when it covers several lines.
- `side` is `RIGHT` for a line of the file at `commit`, and `LEFT` for a line of the old file, before the pull request.
- `code` holds the lines the comment covers, as the reader saw them when they started the thread.
- `messages` has `author` `reader` for the reader and `claude` for you.
- `seen` is the number of messages in `messages`. Send it back with your reply.
- `replay` is true when differ sends the thread again because this session connected again. Skip a replayed thread
  that you are still working on.

Several frames with one `id` can wait for you, when the reader replies before you answer. Handle only the latest of
them: it holds every message of the others. For each frame you handle:

1. Read the whole thread in `messages`. Answer each message of the reader that none of your replies answers yet, in
   the light of what was said before. A reply of yours can come after a message of the reader that it does not
   answer, when the reader wrote while you worked.
2. Read the file around the line. The branch may have new commits since `commit`, so find the code by its text.
3. When the reader asks for a change, make it. Follow this repository's own rules for commits and pushes.
4. Reply once, also when nothing needs a change. The reply is one or two plain sentences: what you changed, or the
   answer. differ shows Markdown.

Send the reply with `curl`. differ refuses a POST without its own Origin header and a JSON body. Write the body as
JSON, with quotes, backslashes and new lines escaped. Copy `seen` from the frame you answer, so that differ knows which
of the reader's messages your reply answers:

```bash
curl -sS -X POST "http://127.0.0.1:<port>/api/<owner>/<repo>/<number>/claude_reply" \
  -H "Origin: http://127.0.0.1:<port>" -H "Content-Type: application/json" \
  --data-binary @- <<'EOF'
{"id": "5f0c2a9e41b7", "seen": 1, "body": "Replaced the print with `log.info`."}
EOF
```

differ adds the reply to the end of the thread. The answer is the thread as JSON, with `"state": "answered"`, or
another state when the reader wrote again after the frame you answered; that newer message comes to you as its own
frame. An answer with `"error"` says what to fix.

## When the monitor ends

- **It expired.** Arm it again at once. differ keeps the comments while no session listens, so nothing is lost.
- **Close code 4000.** Another session listens on this pull request. Tell the user in one line and stop.
- **The connection was refused.** differ is not running. Say so once, and arm the monitor again only when the user
  asks.
- **Any other close.** Arm it again after 5 seconds, at most three times; then tell the user.

## Rules

- Answer every comment in differ, never on GitHub.
- Reply once per frame you handle. Every reply you send adds a message to the thread.
- Never delete a thread. The reader deletes threads in differ.
