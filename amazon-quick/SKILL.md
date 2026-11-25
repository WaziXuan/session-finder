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

**Measured on Quick 1.2.4757 (2026-11-25): the agent runs in a device sandbox.** Asked
to read `C:\Users\OOF\.workbuddy` or run `find_session.py`, it answered that the path
"doesn't exist on the device OOF78x" and that the sandbox "blocks running the script
directly". So parts A and B as plain instructions do **not** work on this build — the
sandbox does not see the real user profile.

What is still open (from Quick's logs):

- `[UserMCP] No enabled servers in config` and `[OutpostMCP] Connected 0
  desktop-local MCP server(s)` — the user-MCP machinery exists but nothing is
  configured.
- `local_mcp_connect gate=resolved` — the local-MCP gate is open.
- `QuickDesktopUserMCP` is **missing** from the QBS capability index, so the
  "add a local MCP server" entry may not be shown in the UI at all.

Therefore the only path that can cross the sandbox is **MCP**, because the MCP
process is started by Quick's main process on real Windows, not inside the sandbox:
the tool executes on the real `C:\Users\OOF`, while the agent only sees the tool
results. Part B (search) fits this perfectly; part A (save) can also be exposed as an
MCP tool that takes the conversation text and writes it locally.

Also checked: `~/.quickwork/profiles/<id>/sessions/sessions.db` now exists, but
`session_messages` has 0 rows — the dialogue is not cached locally, so reading that
database does not substitute for an export.

Steps:

1. **Import the skill (still worth doing)**
   **Settings → Capabilities → Skills → import / create**, upload
   `quick-session-finder/SKILL.md`. The skill is what tells the agent to use the MCP
   tools below.
2. **Look for a local-MCP entry**
   **Settings → Capabilities → MCP → + Add MCP → Local**. If it is absent, this build
   has the user-MCP feature gated off; there is no other way to cross the sandbox —
   stop here and wait for the feature (or a newer build).
3. If the entry exists, register `session_finder_mcp.py` (command/args below). That is
   the whole setup; do **not** bother authorizing `~/.quickwork/session-finder-export`
   for the sandboxed agent, because it cannot see that path.

Folder whitelist note (only relevant if a future build runs the agent unsandboxed):
`~/.quickwork/allowed_folders.db` starts empty, so writes fail until the export folder
is added with read+write. A folder attached to a synced Space is uploaded to the cloud.

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
