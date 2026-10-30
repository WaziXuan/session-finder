# session-finder

[中文说明](README.zh-CN.md)

Find, read and continue your past AI coding-agent conversations — across **Claude Code**,
**ChatGPT Codex**, **WorkBuddy** and **CodeBuddy CLI**, and optionally across all your machines.

Each client stores sessions as jsonl, but in different places, with different fields and
title sources, and a single file can reach 20 MB. session-finder is an agent skill plus
one Python script: it indexes all of them, finds a session by title, by anything said
in it, or by id, and reads back only the part you need — so your agent can answer
"what did we decide in that session last week?" or "continue the session from my
laptop" without flooding its context.

```text
$ find_session.py grep docker compose --limit 2 --per 1
# 3 session(s) mention docker, compose, most hits first

cl|11c4d28c  2026-10-28 03:11  Fix staging deploy  (12 hits)
    [10-28 02:40] user      …the docker compose file still pulls the old image, check the tag…

cx|01a07b55  2026-10-21 17:40  CI cleanup  (4 hits)
    [10-21 17:12] assistant …moved the build step out of docker compose into the workflow…

… 1 more (--limit)
```
(illustrative output)

## Install

Needs Python 3.9+ and git.

**Windows (PowerShell)**

```powershell
git clone https://github.com/WaziXuan/session-finder
cd session-finder
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

**macOS / Linux**

```bash
git clone https://github.com/WaziXuan/session-finder
cd session-finder
bash install.sh
```

The installer puts the script and a venv with `zstandard` (Codex's compressed sessions
need it) into one client's skills folder — `~/.workbuddy/skills/session-finder/` if you
have WorkBuddy, else the first of `~/.claude`, `~/.codex`, `~/.codebuddy` — and a
`SKILL.md` pointing there into each of the other clients you have. A client you install
later gets its `SKILL.md` the next time session-finder runs; no need to reinstall. Nothing runs in the background and nothing leaves your machine.
**Start a new agent session** afterwards so the skill is loaded.

Prefer to let your agent do it? Paste this:

> Install https://github.com/WaziXuan/session-finder: clone it, read install.ps1 (or
> install.sh), run it without any sync option, then run `find_session.py list --limit 5`
> with the venv python it created and show me the output. If a permission check blocks
> something, stop and tell me instead of working around it.

## Use

Just ask your agent in plain words — "find the session where we set up the CI",
"what did I ask Codex about ffmpeg?", "continue the refactoring session from yesterday".
Or run it yourself:

```bash
S=~/.claude/skills/session-finder/scripts/find_session.py     # the folder the installer printed
PY=~/.claude/skills/session-finder/.venv/bin/python             # Windows: .venv/Scripts/python.exe

$PY $S list                        # all clients, newest first; tags like cx|01a16eae, cl|11c4d28c
$PY $S list --source cl            # all | wb | cx | cl | cb
$PY $S find invoice parser         # title + first prompt
$PY $S grep docker compose         # full text; every word must be in the same session
$PY $S grep PIPESTATUS --deep      # also search tool calls and their output
$PY $S show "cl|11c4d28c"          # what you said in it (truncated by default)
$PY $S show "cl|11c4d28c" --role all --tail 15 --max-chars 1500   # how it ended
$PY $S show cx|01a07b55 --role all --grep ffmpeg --head 5
$PY $S files "cl|e04d7a2d"         # files the session touched
```

Output is always truncated: the point is to keep 20 MB transcripts out of the context window.

### Continuing a session

There is no format conversion and no native resume: the agent you're talking to reads
the old session in a fixed order — everything you asked, then the last messages in full,
then the files it touched (checking they exist here) — tells you what state it
understood, and carries on. This works between any two of the four clients and across
machines. The steps are in [SKILL.md](session-finder/SKILL.md#continue--inherit-a-session).

## Optional: sync across machines

Search sessions from your other PCs too. Every machine writes compressed copies of its
own sessions to a folder that a file-sync client shares (OneDrive, Dropbox, Syncthing,
an rclone mirror…) and reads the others' indexes from there.

```powershell
.\install.ps1 -Sync                                    # uses OneDrive\SessionSync
.\install.ps1 -Sync -SyncRoot D:\Dropbox\SessionSync   # or any shared folder
```

```bash
bash install.sh --sync ~/Dropbox/SessionSync           # Linux: adds a systemd --user service
# Linux without a sync client: one-way rclone mirror for reading, rclone copy for writing
SESSION_SYNC_RCLONE='onedrive:SessionSync' bash install.sh --sync ~/OneDrive/SessionSync
```

On Windows this registers a scheduled task `SessionFinderSync` that keeps
`find_session.py watch` running: a session is pushed 60 s after it stops changing (at
most every 5 min while it keeps changing), plus a full pass every 30 min. Pushes are
incremental — jsonl only grows, so only the new tail is uploaded as a `.zst` chunk.
Results from other machines are tagged `cl@LAPTOP|xxxx`; `--host local` / `--host LAPTOP`
filter. Only small indexes and dialogue-only text files (~1.6 MB per machine) are read
for searching; raw transcripts are fetched one at a time when you `show` them.

On macOS there is no service: run `find_session.py watch` at login yourself (launchd).

**Privacy:** synced transcripts contain everything you and your agents said, including
command output. Only point this at storage you trust.

Turn it off with `find_session.py sync-root --off` and remove the scheduled task / service.

**Auto-update between your machines:** after `git pull` on one machine, bump `VERSION`
in `find_session.py` if you changed it and run `publish.ps1`; the others update themselves
from `<SessionSync>/_install` within 10 minutes.

## Notes

- Sessions are read-only. The script writes only its cache/log/state folder
  (`%LOCALAPPDATA%\session-finder\` or `~/.cache/session-finder/`) and, with sync on,
  `<SessionSync>/<this machine>/`.
- Codex's own approval-review rollouts ("Guardian review") are skipped, otherwise they
  drown every search. Injected prompts (WorkBuddy's `<user_info>`, Codex's
  `AGENTS.md instructions`, Claude Code tool results) are filtered out of "what you said".
- Codex's `session_index.jsonl` names only about half the sessions; the rest are titled
  by their first prompt.
- Messages follow the system language (Chinese or English); set `SESSION_FINDER_LANG=en`
  or `zh` to choose.

## Uninstall

```powershell
Unregister-ScheduledTask SessionFinderSync -Confirm:$false -ErrorAction SilentlyContinue
'.workbuddy', '.claude', '.codex', '.codebuddy' | ForEach-Object {
    Remove-Item -Recurse -Force "$env:USERPROFILE\$_\skills\session-finder" -ErrorAction SilentlyContinue }
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\session-finder"
```

```bash
systemctl --user disable --now session-finder-sync 2>/dev/null
rm -rf ~/.{workbuddy,claude,codex,codebuddy}/skills/session-finder ~/.cache/session-finder
```

## License

MIT
