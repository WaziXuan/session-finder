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

## Prerequisites inside Quick (do these first)

Quick grants file access per folder whitelist — on this machine
`~/.quickwork/allowed_folders.db` starts empty, so the export will fail until you add
the folder. Three steps:

1. **Authorize the export folder (required for "Save")**
   Quick → **My computer → Settings → Permissions → Folders → Add folder**, pick
   `C:\Users\OOF\.quickwork\session-finder-export`, and enable **read + write**.
   This folder is created by the setup; if it is missing, create it first.
   Note: a folder attached to a synced Space is uploaded to the cloud — keep this one
   local if you do not want Quick conversations synced.
2. **Import the skill (required)**
   **Settings → Capabilities → Skills → import / create**, upload
   `quick-session-finder/SKILL.md` (the `.md` or its folder).
3. **Enable it** in the Skills list.

No "code execution" toggle is needed: Quick ships native `file_write` / `file_read` /
`folder_create` / `ripgrep` / `fdfind` tools (confirmed in its logs), so both saving
and searching work through those. MCP is only needed if you want part B to go through
MCP; on this build `QuickDesktopUserMCP` is missing from the capability index, so that
entry may not appear — it is optional.

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
