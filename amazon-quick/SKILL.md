---
name: session-finder
description: "Find and read past conversations from WorkBuddy, Codex, Claude Code, CodeBuddy, Qoder and Antigravity on this Windows machine, then optionally sync them to the shared archive. Use when the user asks what they previously asked, wants to locate a chat by keyword or phrase, read a specific session, grep old conversations, or save recent sessions."
---

# Session Finder (local, via MCP)

This machine keeps six local conversation stores. Session Finder indexes them and
serves them through the `session-finder` MCP server. Never guess from memory what
the user said earlier — retrieve it.

## When to use

- "What did I ask about X before?" / "以前聊过什么" → `find_sessions`, then `show_session`.
- A remembered phrase, symbol, path or error message → `grep_sessions`.
- "What did I talk about recently?" → `list_sessions`.
- "Save these sessions / make other machines able to find them" → `sync_sessions`.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_sessions(source, limit)` | Recent sessions. |
| `find_sessions(query, source, limit)` | Search titles and first user messages. |
| `grep_sessions(pattern, source)` | Full-text search across transcripts. |
| `show_session(ref, role, head, max_chars)` | Read a slice of one session. |
| `sync_sessions()` | Refresh index and copy excerpts to SessionSync. |

Source keys: `all` (default), `wb` WorkBuddy, `cx` Codex, `cl` Claude Code,
`cb` CodeBuddy, `qd` Qoder CN, `ag` Google Antigravity. Each may be suffixed
with `@HOST` to query another machine, e.g. `cl@MACMINI6`.

## Workflow

1. Narrow down first: `find_sessions` for intent, `grep_sessions` for exact text.
2. Then `show_session` on the most promising `ref` (e.g. `ag|5b754e44`).
   Use `role="user"` for what the human said, `role="all"` for the exchange.
3. Answer with the session id and only the relevant slice. Do not dump whole
   transcripts into the conversation.

## Rules

- Search all sources by default; honor an explicit client name or source prefix.
- Results are excerpts from local files. If a session is missing, run `sync_sessions`
  once and retry; do not invent an answer.
- Never read or repeat credentials, tokens or private keys that appear in a
  transcript. Antigravity's raw protobuf `conversations/*.db` files are not
  synced; only their jsonl logs are indexed.
- Amazon Quick's own conversations live in the cloud and are NOT in this index.
  Say so plainly if the user expects to find a Quick chat here.
- The underlying CLI is at
  `C:\Users\OOF\.workbuddy\skills\session-finder\scripts\find_session.py`.
  If a tool errors, report the error text; do not silently switch to guessing.
