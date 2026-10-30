---
name: session-finder
description: Locates and reads the user's own past agent conversations across four clients — WorkBuddy, ChatGPT Codex, Claude Code and CodeBuddy CLI — on this machine and, if multi-machine sync is on, on the user's other machines, by title, by anything said inside the conversation (full-text), or by session id, then extracts what was actually asked or decided, or picks up where that session left off. Use when the user refers to an earlier conversation ("that session where we analysed X", "find my earlier chat about Y", "what did I ask Codex/Claude last week", "continue the session from my other PC", "那个分析 XXX 的会话", "上次那个 session", "找一下我之前的对话", "以前 codex/claude 里聊过的", "之前在哪个对话里提到过 XXX", "另一台电脑上聊过的", "接着那个会话干"), shows a screenshot of a session title, or when prior-session context is needed but the built-in conversation search returns nothing.
---

# Session finder

Find a past session across four local stores, then read only the slice that
matters. Never read a whole transcript into context — files reach 20 MB.

## The four sources

| key | client | transcript location | title source |
|-----|--------|--------------------|--------------|
| `wb` | WorkBuddy | `~/.workbuddy/projects/<proj>/<id>.jsonl` | `{"type":"ai-title","aiTitle":…}` record |
| `cx` | ChatGPT Codex | `~/.codex/sessions/<Y>/<M>/<D>/rollout-*.jsonl[.zst]` | `~/.codex/session_index.jsonl` → `thread_name` |
| `cl` | Claude Code | `~/.claude/projects/<proj>/<id>.jsonl` | `{"type":"custom-title","customTitle":…}` record |
| `cb` | CodeBuddy CLI | `~/.codebuddy/projects/<proj>/<id>.jsonl` | same records as wb; usually untitled → first prompt |

`<proj>` is the cwd with `:` and `\` mangled (`D:\Work` → `d-Work` for wb,
`D--Work` for cl). Access to these dot-directories is often denied to an agent's
own file tools — use the script.

## Run it with the right interpreter

Codex compresses most rollouts with zstd, so the script needs `zstandard`.
The script and its venv live only in `~/.workbuddy/skills/session-finder/`; the copies of
this SKILL.md in other clients' skills folders all point there — always use the paths below.

```bash
S="$HOME/.workbuddy/skills/session-finder/scripts/find_session.py"
PY="$HOME/.workbuddy/skills/session-finder/.venv/Scripts/python.exe"   # Windows
# PY="$HOME/.workbuddy/skills/session-finder/.venv/bin/python"          # macOS / Linux
```

(`$HOME` works in PowerShell too; in cmd use `%USERPROFILE%`.)

Without `zstandard` every `.zst` session reports
`session is zstd-compressed and zstandard is not installed` and is skipped.

## Commands

```bash
"$PY" "$S" list                        # all sources, newest first; tags look like cx|01a16eae (cb uses 13 chars: cb|01a179d5-4c09)
"$PY" "$S" list --source cl            # all|wb|cx|cl|cb
"$PY" "$S" find invoice parser         # title + first prompt; title hits weigh 10, body 3
"$PY" "$S" grep docker compose         # full text: every word must occur in the same session
"$PY" "$S" grep "permission denied" --role user --per 5 --context 120
"$PY" "$S" grep PIPESTATUS --deep --host local   # also search tool calls and their output
"$PY" "$S" show "cl|11c4d28c"          # what the human said (default)
"$PY" "$S" show cx|01a07b55 --role all --grep ffmpeg --head 5
"$PY" "$S" show "cl|11c4d28c" --role all --tail 15 --max-chars 1500   # the last 15 messages: where it ended
"$PY" "$S" files "cl|e04d7a2d"         # files the session touched, most-mentioned first
"$PY" "$S" sync-root                   # where multi-machine sync goes (exit 1 = off, this machine only)
```

**Which one**: remember the title or the first question → `find`; remember only
something that was discussed → `grep` (sessions ranked by hit count, each hit with
80 chars of context on each side; then `show --grep` for more); looking for a command
the agent ran or a file it read → `grep --deep`. `grep` is case-insensitive substring
matching and handles CJK as-is. A full local pass takes about 2 s.

`show` accepts `wb|` / `cx|` / `cl|` / `cb|` prefixes or a bare id prefix (searched
across sources; several matches are listed). Defaults: `--role user --head 20
--max-chars 600`, always truncated. `find` with a quoted multi-word argument matches the
phrase (+6) and the separate words.

The index cache is `%LOCALAPPDATA%\session-finder\index.json` (`~/.cache/session-finder/`
elsewhere), invalidated per file by (mtime, size); delete it to force a rebuild.

## Continue / inherit a session

When the user says "continue that session", "pick up where X left off", "接着 XX 那个会话",
don't use the client's native resume and don't read the whole thing. Read it into the
current session in this order (works in any client, from any machine):

1. Locate: `find` / `grep` → a tag such as `cl@LAPTOP|11c4d28c`.
2. Intent: `show <tag> --role user --head 200 --max-chars 200` — everything the user
   asked for, including changes of direction.
3. Progress: `show <tag> --role all --tail 15 --max-chars 1500` — where it ended, what was
   decided, what's left. Raise `--tail` for short sessions; the last reply is often a summary.
4. Files: `files <tag>`, then check they exist here. Paths from another machine or OS
   (`D:\…` vs `~/…`) need mapping to this machine; say so if you can't find them.
5. Missing detail: `show <tag> --role all --grep <word>`, never the whole transcript.
6. Tell the user in two or three sentences what state you understood and the next step, then carry on.

A session on another machine may still be in progress: the synced copy lags a few
minutes (pushes at most every 5 min, plus the sync client's own delay), so say how
recent your view is. Tool calls and reasoning don't come along; use `--deep` / `--grep` when needed.

## Other machines (optional SessionSync)

Off by default. When `sync-root` prints a folder (set by the installer's `-Sync` /
`--sync`, by `sync-root <folder>`, or by `SESSION_SYNC_ROOT`), each machine runs
`find_session.py watch` in the background (Windows: scheduled task `SessionFinderSync`;
Linux: systemd --user `session-finder-sync`). The watcher scans every 15 s and pushes a
session once it has been quiet for 60 s, or every 5 min while it keeps changing, plus a
full pass every 30 min. Target: `<SessionSync>/<COMPUTERNAME>/{wb,cx,cl,cb}/…` plus an
`_index.json` (titles, first prompts, mtimes, chunk lists). Codex Guardian-review
rollouts are not pushed. Deleting a session locally does not remove it there.

Uploads are incremental within a file: a jsonl session only grows, so each push is
just the appended tail, saved as `<file>.jsonl.NNN.zst` (cut at a line break).
`show` / `files` read the chunks in order. A rewritten file (shorter, or changed
before the last offset) is re-pushed from scratch; 20+ chunks are merged into one
once the session goes quiet. Finished Codex `.jsonl.zst` rollouts are copied whole.

- `list` / `find` merge local sessions with every *other* machine's index; rows from
  them are tagged `cl@LAPTOP|xxxxxxxx`. Only the small index is read, so cloud
  placeholders stay undownloaded until you `show` / `files` that session.
- `--host local` = this machine only; `--host LAPTOP` = one machine's synced copy.
- `show "cl@LAPTOP|11c4d28c"` reads the synced file (the sync client downloads it on open).
- Everything a search reads lives in `<SessionSync>/_search/<HOST>/`: `index.json`
  plus one `<src>/<id>-<hash>.txt.zst` per session — its dialogue only (user +
  assistant text, no tool calls/output; ~1.6 MB per machine). Keep `_search` available
  offline; raw chunks under `<SessionSync>/<HOST>/` can stay on-demand. `grep` reads the
  text files; `show`, `files` and `grep --deep` read raw chunks of that one session.
- This machine's own synced folder is skipped; local files are fresher.
- Linux without a sync client: mirror the folder with rclone and set `SESSION_SYNC_RCLONE`
  to the remote path; the watcher pushes what it writes with `rclone copy` (saved in
  `~/.cache/session-finder/rclone_remote`; failed uploads queue in `rclone_pending.json`).
- Run `"$PY" "$S" sync` to push now. Log: `sync.log` in the state folder; per-file upload
  offsets: `sync_state.json` next to it (delete it to force a full re-push).

## Auto-update (sync users only)

The installed script checks `<SessionSync>/_install/session-finder/scripts/find_session.py`
on every run (the watcher every 10 min). If its `VERSION` is higher and the file is
complete (last line `# end of find_session.py`), it replaces itself, copies the new
SKILL.md into the `~/.workbuddy`, `~/.claude`, `~/.codex` and `~/.codebuddy` skills, and
reruns the same command — you may see `(session-finder 已从同步文件夹自动更新：A -> B)` on
stderr; just use the output. `publish.ps1` in the repo puts a new version there.
Set `SESSION_FINDER_NO_UPDATE=1` to skip the check.

## Per-source quirks worth remembering

**WorkBuddy (`wb`)**
- The UI title exists only as `ai-title` records inside the jsonl.
- The human's words are wrapped in `<user_query>…</user_query>` behind ~9 KB of
  `<user_info>` / `<identity_context>` / `<system-reminder>`; the first user record is not the question.
- Record types: `session-meta`, `ai-title`, `file-history-snapshot`, `reasoning`,
  `function_call`, and `message` with a `role`. Only records with `role` are dialogue.

**Codex (`cx`)**
- Most rollouts are `.jsonl.zst`, streamed and read line by line.
- `payload.id` equals the UUID at the end of the file name; one `session_id` can have
  several rollouts (Guardian review, sub-agents) whose ids differ only in the second
  half, so 8-char prefixes collide — the script prints full ids; use a longer prefix.
- Only `{"type":"response_item","payload":{"type":"message","role":"user"}}` is the human;
  the same shape with `role=developer` is system prompt and is filtered.
- `session_index.jsonl` covers about half the sessions; the rest fall back to the first 50 chars of the first prompt.
- Many rollouts are not human conversations: Codex's approval reviewer writes
  `Guardian review` sessions starting "The following is the Codex agent history…".
  They are skipped, or they would drown every search.
- The first user message is often an injected `# AGENTS.md instructions for <dir>`; skipped.

**Claude Code (`cl`)**
- Title: the last `custom-title` in the file, else `summary`, else `last-prompt` / `agent-name`.
- Tool results are also `{"type":"user"}` with `tool_result` content blocks — skipped,
  or "what the user said" fills up with command output.
- Sub-agent records (`isSidechain: true`, `origin.kind != "human"`) are skipped too.
- `cwd` is often missing; it's recovered from the directory name (`D--Work` → `D:\Work`).

## Output discipline

- A single transcript can reach 20 MB; pasting it into context makes the conversation crawl.
- `list` / `find` / `grep` to get the id, then `show --grep` / `--tail` for the slice; write
  only the conclusion back.
- Conclusions worth keeping go into a report file, not raw transcript in the chat.
