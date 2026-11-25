---
name: quick-history-export
description: Export this Amazon Quick Desktop conversation to a local JSONL archive so other agents on this Windows machine (WorkBuddy, Codex, Claude Code, CodeBuddy, Qoder, Antigravity, DeepSeek Harness) can find and read it through Session Finder. Use when the user says "export/save this Quick chat", "let other agents read my Quick history", "把 Quick 的对话存到本地 / 让别的 agent 能读到", or asks to back up a Quick conversation locally. Runs entirely with Quick's own local file tools — no credentials, no cloud API, no changes to Quick's settings.
---

# Quick History Export

You are running inside **Amazon Quick Desktop**. Your conversations normally live
in Quick's cloud store, which other agents on this machine cannot read. This skill
writes a copy of the conversation you are currently in to a local file, so the
`session-finder` skill used by the other agents can index it. Everything happens
through your own local file tools — do **not** read `~/.quickwork/profiles.json`,
any token, key, or password, and do not change Quick's settings.

## Where to write

```
C:\Users\OOF\.quickwork\session-finder-export\
```

Create the folder if it does not exist. Write **one file per conversation**, named
`<session-id>.jsonl`, where `<session-id>` is a short stable id. Use the current
conversation's id if you can see it; otherwise make one from the date and a few
random hex digits, e.g. `20261125-1a2b3c4d.jsonl`. Never overwrite a file that
already has content — append to it (see "Incremental" below).

## File format (JSONL, one JSON object per line)

Use exactly this shape — Session Finder parses these three record types:

```jsonl
{"type":"meta","id":"20261125-1a2b3c4d","title":"Short title for the chat","cwd":"D:\\Sandbox","source":"quick","exported_at":"2026-11-25T09:00:00Z"}
{"role":"user","content":[{"type":"text","text":"what the human actually typed"}],"timestamp":"2026-11-25T08:59:00Z"}
{"role":"assistant","content":[{"type":"text","text":"your answer, condensed but faithful"}],"timestamp":"2026-11-25T08:59:05Z"}
```

Rules for the records:

- **Line 1 is `type:"meta"`**: `id` must match the filename stem; `title` is a 3–12
  word summary in the language the user wrote in; `cwd` is the working directory if
  known (otherwise `""`); `source` is always `"quick"`; `exported_at` is the current
  UTC time in ISO-8601.
- **Then one record per dialogue turn**, in order: `"role":"user"` for the human's
  own words, `"role":"assistant"` for yours. Put the text in
  `content` as a list with a single `{"type":"text","text":...}` object.
- **User turns**: copy what the human actually said, including any `<user_query>`
  wrapper text if that is how it appears to you. Do not paraphrase their request.
- **Assistant turns**: keep the substance (decisions, conclusions, file paths,
  commands, code) but you may drop long tool dumps and repeated boilerplate. Aim for
  a faithful, readable summary rather than a verbatim transcript.
- **Timestamps**: ISO-8601 UTC. If you cannot tell the exact time, use the current
  time; ordering matters more than precision.
- Skip tool calls, tool output, system prompts and injected context. Only the human
  dialogue and your own answers belong in the file.

## Incremental append (no duplicates, no clobbering)

Conversations are often exported more than once. On every run:

1. If the target file exists, read it first and collect the list of `(role, text)`
   pairs already written.
2. Append only turns that are not already present (compare `text` exactly after
   stripping whitespace). Keep the `meta` line as the first line — never add a
   second one.
3. If the conversation has no new turns, do nothing and say "already up to date".
4. Never delete, reorder or rewrite existing lines.

## Redaction (mandatory)

Before writing any text, remove or replace secrets: tokens, API keys, passwords,
private keys, auth headers, full credit-card or ID numbers. Replace a value with
`<vault://name>` (a short label) or `[REDACTED]`. If you are unsure whether a string
is a secret, redact it. Never paste anything from `~/.quickwork/profiles.json`.

## Finish

Report back in one or two lines: the file path written, how many new turns were
appended, and a reminder that other agents can now find it with:

```
find_session.py find "<a phrase from the chat>" --source qk
```

If you could not write the file (folder not permitted, tool error), say exactly what
failed — do not silently claim success.
