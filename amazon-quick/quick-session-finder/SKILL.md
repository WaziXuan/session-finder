---
name: session-finder
description: Session Finder for Amazon Quick Desktop — works in both directions. (A) SAVE: export this Quick conversation to a local JSONL archive so other agents on this Windows machine (WorkBuddy, Codex, Claude Code, CodeBuddy, Qoder, Antigravity, DeepSeek Harness) can find and read it. (B) SEARCH: read the user's past conversations from those other agents, by title, by anything said inside them, or by session id, and continue where one left off. Use when the user says "export/save this Quick chat", "let other agents read my Quick history", "what did I ask Codex/Claude/Cursor last week", "find my earlier chat about X", "把 Quick 的对话存到本地", "让别的 agent 能读到", "以前在别的 agent 里聊过什么", "找一下我之前的对话". Saving runs entirely with Quick's own local file tools — no credentials, no cloud API, no settings changes.
---

# Session Finder for Amazon Quick

Quick's own conversations live in a credential-gated cloud outpost that other
agents cannot read, and Quick cannot see the other agents' local stores by
default. This skill bridges both directions using only local files:

- **A. Save** — write this conversation to a local JSONL file so other agents
  can index it (Session Finder's `qk` source).
- **B. Search** — run the local Session Finder CLI to read the other agents'
  histories and answer the user's question.

Never read `~/.quickwork/profiles.json`, any token, key or password. Never call
Quick's outpost API. Never change Quick's settings.

---

## A. Save this conversation (write a local copy)

### Where

```
C:\Users\OOF\.quickwork\session-finder-export\
```

Create the folder if missing. One file per conversation: `<session-id>.jsonl`.
Use the current conversation's id if visible; otherwise
`YYYYMMDD-<6 hex>.jsonl`. Never overwrite a file that already has content — append.

### Format (JSONL, one object per line)

```jsonl
{"type":"meta","id":"20261125-1a2b3c4d","title":"Short title","cwd":"D:\\Sandbox","source":"quick","exported_at":"2026-11-25T09:00:00Z"}
{"role":"user","content":[{"type":"text","text":"what the human actually typed"}],"timestamp":"2026-11-25T08:59:00Z"}
{"role":"assistant","content":[{"type":"text","text":"your answer, condensed but faithful"}],"timestamp":"2026-11-25T08:59:05Z"}
```

- Line 1 is `type:"meta"`: `id` matches the filename stem, `title` is a 3–12 word
  summary in the user's language, `cwd` if known (else `""`), `source` always
  `"quick"`, `exported_at` current UTC ISO-8601.
- Then one record per turn in order: `"role":"user"` for the human, `"role":"assistant"`
  for you. Text goes in `content` as `[{"type":"text","text":...}]`.
- User turns: what the human actually said — do not paraphrase their request.
- Assistant turns: keep decisions, conclusions, file paths, commands and code;
  you may drop long tool dumps. Faithful summary, not a verbatim transcript.
- Skip tool calls, tool output, system prompts and injected context.
- **Redact** every token, API key, password, private key or auth header before
  writing — replace with `<vault://name>` or `[REDACTED]`.

### Incremental

Read the existing file first, collect `(role, text)` pairs, append only turns not
already present, keep the single `meta` line first, never reorder or rewrite. If
there is nothing new, say "already up to date".

---

## B. Search the other agents' history (read)

The other clients' transcripts are read by a local Python script. Run it with your
own command-execution tool. If you have a `session-finder` MCP server connected
(tools `list_sessions` / `find_sessions` / `grep_sessions` / `show_session`), prefer
those — they wrap the same script.

Windows command (PowerShell):

```powershell
& "C:\Users\OOF\.workbuddy\skills\session-finder\.venv\Scripts\python.exe" `
  "C:\Users\OOF\.workbuddy\skills\session-finder\scripts\find_session.py" `
  find "the phrase the user remembers" --source all --limit 10
```

Other verbs (same binary):

| Command | Use |
| --- | --- |
| `list --source all --limit 20` | recent sessions |
| `find "<words>" --source all` | by title / first message |
| `grep "<exact text>" --source cl` | full-text across transcripts |
| `show "cl\|b0599fab" --role user --head 40 --max-chars 400` | read one slice |

Sources: `all`, `wb` WorkBuddy, `cx` Codex, `cl` Claude Code, `cb` CodeBuddy,
`qd` Qoder CN, `ag` Antigravity, `ds` DeepSeek Harness, `qk` Amazon Quick (the
files written by part A).

### How to answer a "what did I ask before" question

1. `find` for intent, or `grep` for an exact phrase/path/error.
2. `show` the best hit with `--role user` to see what the human asked, or
   `--role all --tail 15` to see where it ended.
3. Answer with the session tag (e.g. `cl|b0599fab`) and only the relevant slice.
   Never paste a whole transcript — some files reach 20 MB.
4. If nothing matches, say so plainly and suggest `sync` or another wording; do
   not invent a session.

### If the command cannot run

If your sandbox blocks executing that path or the script, tell the user exactly
what failed. Do not claim a search succeeded. Offer the MCP route
(`session_finder_mcp.py`, see `../SKILL.md`) as the alternative.

---

## After either action

- Save: report the file path and how many new turns were appended.
- Search: report the session tag(s) used and the answer, in the user's language.
