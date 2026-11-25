---
name: session-finder
description: Session Finder integration for Amazon Quick Desktop. The one skill to import is quick-session-finder/SKILL.md — it does both directions (Quick exports its own history to local files so other agents can read it, and Quick searches the other agents' histories). This file explains the pieces and the optional MCP bridge.
---

# Session Finder ↔ Amazon Quick

Import **one** skill into Quick from **Settings → Capabilities → Skills**
(upload the `.md` file or its folder):

```
amazon-quick/quick-session-finder/SKILL.md
```

That single skill covers both directions:

## A. Save — Quick writes its own history (other agents can then read it)

Quick's conversations live in a credential-gated cloud outpost, so no other agent
can reach them. Part A has Quick itself write a redacted, local JSONL copy of the
conversation to:

```
C:\Users\OOF\.quickwork\session-finder-export\
```

Session Finder's `qk` source indexes those files, and any agent on this machine can
`find` / `grep` / `show` them. No credentials, no cloud API, no config changes.
Trigger it in a Quick chat with "export this conversation".

## B. Search — Quick reads the other agents' history

Part B runs the local Session Finder CLI (or the MCP tools, if connected) so Quick
can answer "what did I ask Codex/Claude last week" from the local stores. It needs
no credentials either.

## Optional: `session_finder_mcp.py` — the MCP route for part B

A zero-dependency stdio MCP server wrapping the same CLI. Use it only if Quick's
command execution cannot run the script directly and you want MCP tools instead:

- **Settings → Capabilities → MCP → + Add MCP → Local**
- command: `C:\Users\OOF\.workbuddy\skills\session-finder\.venv\Scripts\python.exe`
- args: `["D:\Sandbox\session-finder-kit\amazon-quick\session_finder_mcp.py"]`

If the UI has no "Add MCP" entry, this connector is gated off in your Quick build —
skip it; parts A and B both work without MCP.

## Source keys

`all` (default), `wb` WorkBuddy, `cx` Codex, `cl` Claude Code, `cb` CodeBuddy,
`qd` Qoder CN, `ag` Antigravity, `ds` DeepSeek Harness, `qk` Amazon Quick.
