---
name: session-finder
description: Session Finder integration for Amazon Quick Desktop. Two directions — (1) let other agents read your Quick history by exporting it locally with the quick-history-export skill (recommended, no credentials), and (2) optionally let Quick search the other agents' histories through a local MCP server. Use when the user asks to connect Quick to Session Finder, find a past chat from any client, or make Quick's own conversations searchable elsewhere.
---

# Session Finder ↔ Amazon Quick

There are two separate skills here. Import them into Quick from
**Settings → Capabilities → Skills** (upload the `.md` file or a folder).

## 1. `export-history/SKILL.md` — Quick writes its own history (recommended)

This is the direction that makes **other agents able to read Quick's chats**.
Quick's conversations live in a credential-gated cloud outpost, so no other agent
can reach them. This skill has Quick itself write a redacted, local JSONL copy of
the current conversation to:

```
C:\Users\OOF\.quickwork\session-finder-export\
```

Session Finder's `qk` source then indexes those files, and any agent on this machine
can `find` / `grep` / `show` them. No credentials, no cloud API, no config changes.
Import `export-history/SKILL.md`, then in a Quick chat say "export this conversation".

## 2. `session_finder_mcp.py` — Quick reads the other agents' history (optional)

A zero-dependency stdio MCP server that wraps the local Session Finder CLI. Quick
can call `list_sessions`, `find_sessions`, `grep_sessions`, `show_session` and
`sync_sessions` over its own MCP client. This is the opposite direction and is not
needed for other agents to read Quick.

To wire it up: **Settings → Capabilities → MCP → + Add MCP → Local**, with

- command: `C:\Users\OOF\.workbuddy\skills\session-finder\.venv\Scripts\python.exe`
- args: `["C:\Users\OOF\Downloads\session-finder-kit\amazon-quick\session_finder_mcp.py"]`

If the UI has no "Add MCP" entry, this connector is gated off in your Quick build —
skip it; direction 1 needs no MCP at all.

## Source keys

`all` (default), `wb` WorkBuddy, `cx` Codex, `cl` Claude Code, `cb` CodeBuddy,
`qd` Qoder CN, `ag` Antigravity, `ds` DeepSeek Harness, `qk` Amazon Quick.
