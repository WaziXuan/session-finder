#!/usr/bin/env python3
"""Locate and read past agent sessions from five local sources.

  wb      WorkBuddy     %USERPROFILE%\\.workbuddy\\projects\\<proj>\\<sessionId>.jsonl
  cx      Codex         %USERPROFILE%\\.codex\\sessions\\<Y>\\<M>\\<D>\\rollout-*.jsonl[.zst]
                        titles live in %USERPROFILE%\\.codex\\session_index.jsonl
  cl      Claude Code   %USERPROFILE%\\.claude\\projects\\<proj>\\<sessionId>.jsonl
  cb      CodeBuddy CLI ~/.codebuddy/projects/<proj>/<sessionId>.jsonl (same records as wb)
  qd      Qoder CN      ~/.qoder-cn/projects/<proj>/<sessionId>.jsonl (same records as cl);
                        titles, side chats and imports in the app's main.sqlite and
                        sessionMigration.sqlite (%APPDATA%\\com.qodercn.app.stable)

Optional multi-machine sync: `sync` / `watch` push this machine's sessions,
zstd-compressed, to <SessionSync>\\<COMPUTERNAME>\\{wb,cx,cl,cb,qd}\\... plus a small
_index.json, where <SessionSync> is any folder a file-sync client shares between
your machines (OneDrive, Dropbox, an rclone mirror...). Turn it on with
`sync-root <folder>` (the installers' -Sync / --sync do this). list/find/show then
also read the other machines' folders, tagged like `cl@LAPTOP|xxxxxxxx`. Searching
only reads _index.json, so cloud placeholders are not downloaded until you `show`
one session. Without a sync folder everything works on this machine's sessions only.

Never dump a whole transcript: single files reach 20 MB and will wreck the
context window. Every subcommand truncates by default.

Codex stores most rollouts zstd-compressed, so run this with an interpreter that
has the `zstandard` package (see SKILL.md).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import platform
import re
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HOME = Path.home()
# Kept outside the skill folder so packaging stays clean.
STATE_DIR = Path(os.environ.get("LOCALAPPDATA") or HOME / ".cache") / "session-finder"
CACHE = STATE_DIR / "index.json"
SYNC_STATE = STATE_DIR / "sync_state.json"
SYNC_LOG = STATE_DIR / "sync.log"
HOST = (os.environ.get("COMPUTERNAME") or platform.node().split(".")[0] or "unknown").upper()
# Bump on every publish to SessionSync\_install: installed copies compare against it
# and update themselves (see self_update).
VERSION = "2026.11.25.2"
# Bump when scan() returns new fields, so cached metadata is rebuilt once.
META_V = 4
# The installer puts the script into the first client that has it: ~/.workbuddy,
# else ~/.claude, ~/.codex, ~/.codebuddy, ~/.qoder-cn. The other clients get only
# SKILL.md. Antigravity keeps global skills in ~/.gemini/config/skills/.
CLIENTS = (".workbuddy/skills", ".claude/skills", ".codex/skills",
           ".codebuddy/skills", ".qoder-cn/skills", ".gemini/config/skills")
SKILL_HOME = Path(__file__).resolve().parents[1]
END_MARK = b"# end of find_session.py"  # must stay the file's last line


SYNC_ROOT_FILE = STATE_DIR / "sync_root"


def _ui_zh() -> bool:
    """Chinese output on a Chinese system; SESSION_FINDER_LANG=zh|en overrides.
    On Windows the top of the user's language list (Settings > Language) decides; it
    can differ from the legacy display language. Git Bash's LANG says nothing here."""
    v = os.environ.get("SESSION_FINDER_LANG", "").strip().lower()
    if v:
        return v.startswith("zh")
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\International\User Profile") as k:
                langs = winreg.QueryValueEx(k, "Languages")[0]
            if langs:
                return langs[0].lower().startswith("zh")
        except OSError:
            pass
        try:
            import ctypes
            return ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x04
        except Exception:
            return False
    for e in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        if os.environ.get(e):
            return os.environ[e].lower().startswith("zh")
    return False


ZH = _ui_zh()


def L(zh: str, en: str) -> str:
    return zh if ZH else en


def cloud_root() -> Path | None:
    """The SessionSync folder: $SESSION_SYNC_ROOT, else the one saved by `sync-root`.
    None means sync is off and everything stays on this machine.

    Installs from before `sync-root` existed saved nothing; their folder is found
    once under OneDrive (a SessionSync with a _search folder, at most two levels
    down) and saved, so agents that run the skill without the env var see it too."""
    if os.environ.get("SESSION_SYNC_ROOT"):
        return Path(os.environ["SESSION_SYNC_ROOT"]).expanduser()
    try:
        saved = SYNC_ROOT_FILE.read_text(encoding="utf-8").strip()
        if saved:
            return Path(saved)
    except OSError:
        pass
    bases = [Path(os.environ[e]) for e in ("OneDriveConsumer", "OneDrive") if os.environ.get(e)]
    for od in bases + [HOME / "OneDrive"]:
        for pat in ("SessionSync", "*/SessionSync", "*/*/SessionSync"):
            try:
                hit = next((d for d in od.glob(pat) if (d / "_search").is_dir()), None)
            except OSError:
                hit = None
            if hit:
                try:
                    STATE_DIR.mkdir(parents=True, exist_ok=True)
                    SYNC_ROOT_FILE.write_text(str(hit), encoding="utf-8")
                except OSError:
                    pass
                return hit
    return None
FILE_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|/(?:home|Users|root|tmp|opt|srv|mnt|var|etc)/)[^\"\n\r]{3,140}?\.(?:mp4|mkv|mov|png|jpg|jpeg|webp|json|jsonl|py|js|ts|csv|"
    r"txt|md|yaml|yml|toml|docx|xlsx|pptx|pdf|zip|exe|log|ps1|sh)\b",
    re.IGNORECASE,
)
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# Auto-generated rollouts that were never a human conversation (Codex's own
# approval-review agent writes these).
MACHINE_PROMPTS = (
    "The following is the Codex agent history",
)

# Injected host context, not the human speaking.
ENVELOPES = (
    "<task-notification", "<system-reminder", "<user-context", "<user_info",
    "<identity_context", "<local-commands", "<available", "<command-",
    "<tool-result", "<previous-conversation", "<environment_context",
    "<permissions", "<collaboration", "<world_state",
    "# agents.md instructions", "# claude.md",
    # Codex side conversation: the first "user" record is the host's boundary notice.
    "side conversation boundary.",
)


def clean_user_text(txt: str) -> str:
    """Return only the human's own words from a user-role record."""
    t = (txt or "").strip()
    if not t:
        return ""
    # WorkBuddy wraps the real prompt in <user_query>, with ~9 KB of injected
    # context in front of it.
    if "<user_query>" in t:
        inner = t.split("<user_query>")[-1].split("</user_query>")[0].strip()
        if inner:
            return inner
    # Antigravity wraps the prompt in <USER_REQUEST>, then appends host context.
    if "<USER_REQUEST>" in t:
        inner = t.split("<USER_REQUEST>")[-1].split("</USER_REQUEST>")[0].strip()
        if inner:
            return inner
    if t.lower().startswith(ENVELOPES):
        return ""
    # Generic guard: any injected <tag> envelope at the start of the record
    # (Codex uses <external_codex_*>, <user_instructions>, ...).
    if t.startswith("<") and re.match(r"<[a-z_][a-z0-9_]*[\s>/]", t[:60], re.IGNORECASE):
        return ""
    if "system-reminder data-role" in t[:300]:
        return ""
    return t


def open_lines(path):
    """Yield lines from a plain or zstd-compressed jsonl, or from a list of
    chunks (a synced session split into append-only pieces) read in order."""
    if isinstance(path, (list, tuple)):
        for part in path:
            yield from open_lines(part)
        return
    if path.suffix == ".zst":
        try:
            import zstandard as zstd
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(L(
                "这个会话是 zstd 压缩的，当前 Python 没装 zstandard：用 skill 的 .venv 里的 python，或 pip install zstandard",
                "session is zstd-compressed and `zstandard` is not installed for "
                "this interpreter; use the skill's .venv python, or pip install zstandard")) from e
        with path.open("rb") as f:
            with zstd.ZstdDecompressor().stream_reader(f) as r:
                yield from io.TextIOWrapper(r, encoding="utf-8", errors="replace")
    else:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            yield from f


def _records_with(path, needle: str):
    """Parsed records from the lines that contain needle; broken lines are skipped."""
    for line in open_lines(path):
        if needle not in line:
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue


def blocks_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    out = []
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") in ("text", "input_text", "output_text") and isinstance(b.get("text"), str):
            out.append(b["text"])
    return "\n".join(out)


def stamp(ts) -> str:
    if not ts:
        return "--"
    try:
        if isinstance(ts, str) and ts.isdigit():
            ts = int(ts)
        if isinstance(ts, (int, float)):
            v = ts / 1000 if ts > 1e11 else ts
            return datetime.fromtimestamp(v).strftime("%m-%d %H:%M")
        s = str(ts).replace("Z", "+00:00")
        return datetime.fromisoformat(s).astimezone().strftime("%m-%d %H:%M")
    except (ValueError, OSError, OverflowError):
        return "--"


def subagent_of(path: Path) -> dict:
    """Claude Code (and WorkBuddy-style clients) keep a sub-agent's transcript at
    <project>/<parentId>/subagents/agent-<id>.jsonl, with <same>.meta.json next to it."""
    if path.parent.name != "subagents":
        return {}
    info = {"parent": path.parent.parent.name, "agent": "subagent"}
    meta = path.with_name(path.name.split(".jsonl")[0] + ".meta.json")
    try:
        m = json.loads(meta.read_text(encoding="utf-8"))
        info["agent"] = m.get("agentType") or info["agent"]
        if m.get("description"):
            info["title"] = m["description"]
    except (OSError, ValueError):
        pass
    return info


def parent_link(path: Path) -> dict:
    """A `<session id>.parent.json` sidecar next to a transcript ties it to a session in
    another client, e.g. a CodeBuddy run directed from a Claude Code session:
    {"src": "cl", "id": "<parent id>", "agent": "ds:impl", "title": "..."}.
    Returns {"parent", "parent_src", "agent", "title"} ("title" only when given),
    or {} when there is no sidecar or it is unusable."""
    name = path.name
    for suffix in (".jsonl.zst", ".jsonl"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    try:
        obj = json.loads(path.with_name(name + ".parent.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(obj, dict):
        return {}
    src, pid = obj.get("src"), obj.get("id")
    if not isinstance(src, str) or src not in SOURCES:
        return {}
    if not isinstance(pid, str) or not pid:
        return {}
    agent = obj.get("agent")
    link = {"parent": pid, "parent_src": src,
            "agent": agent if isinstance(agent, str) and agent else "subagent"}
    if isinstance(obj.get("title"), str) and obj["title"]:
        link["title"] = obj["title"]
    return link


def guess_cwd_from_dirname(name: str) -> str:
    """Best-effort fallback when a record has no cwd. Lossy: 'D--Sandbox' -> 'D:\\Sandbox'."""
    m = re.match(r"^([A-Za-z])--(.*)$", name)
    if not m:
        return name
    return f"{m.group(1)}:\\" + m.group(2).replace("-", "\\").strip("\\")


class Source:
    key = ""
    label = ""

    def sessions(self) -> list[Path]:
        raise NotImplementedError

    def sid(self, path: Path) -> str:
        return path.name.replace(".jsonl.zst", "").replace(".jsonl", "")

    def scan(self, path: Path) -> dict:
        """Return {title, cwd, first_user}; called once per file, then cached."""
        raise NotImplementedError

    def messages(self, path: Path):
        """Yield (timestamp, role, text) for human-readable turns."""
        raise NotImplementedError

    def live(self, path: Path, meta: dict) -> dict:
        """Facts kept outside the transcript (an app database), read every pass and never
        cached: any of title, cwd, parent, parent_src, agent. They win over scan()."""
        return {}

    def pushable(self, path: Path) -> bool:
        """False for a binary store: it cannot be appended to or redacted, so it stays local."""
        return True


class WB(Source):
    key, label = "wb", "WorkBuddy"
    root = HOME / ".workbuddy" / "projects"

    def sessions(self):
        return list(self.root.rglob("*.jsonl")) if self.root.exists() else []

    def scan(self, path):
        title = first = cwd = None
        for line in open_lines(path):
            if '"ai-title"' in line:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("aiTitle"):
                    title = o["aiTitle"]
                cwd = cwd or o.get("cwd")
                continue
            if '"role"' not in line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("role") not in ("user", "assistant"):
                continue
            cwd = cwd or o.get("cwd")
            if o.get("role") == "user" and not first:
                t = clean_user_text(blocks_text(o.get("content")))
                if t:
                    first = t
            if first and cwd and title:
                break
        sub = subagent_of(path)
        return {"title": title or sub.pop("title", ""), "cwd": cwd or path.parent.name,
                "first_user": first or "", **sub}

    def messages(self, path):
        for o in _records_with(path, '"role"'):
            role = o.get("role")
            if role not in ("user", "assistant"):
                continue
            txt = blocks_text(o.get("content"))
            if role == "user":
                txt = clean_user_text(txt)
            if txt:
                yield o.get("timestamp"), role, txt


class Codex(Source):
    key, label = "cx", "Codex"
    root = HOME / ".codex"
    _titles = None

    def sessions(self):
        # Archived threads move to archived_sessions/; keep them searchable too.
        out = []
        for d in (self.root / "sessions", self.root / "archived_sessions"):
            if d.exists():
                out += d.rglob("rollout-*.jsonl*")
        return sorted(out)

    def sid(self, path):
        stem = path.name.replace(".jsonl.zst", "").replace(".jsonl", "")
        found = UUID_RE.findall(stem)
        return found[-1] if found else stem

    def titles(self):
        if Codex._titles is None:
            Codex._titles = {}
            f = self.root / "session_index.jsonl"
            if f.exists():
                for line in open_lines(f):
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    if o.get("id") and o.get("thread_name"):
                        Codex._titles[o["id"]] = o["thread_name"]
        return Codex._titles

    def scan(self, path):
        title = self.titles().get(self.sid(path))
        cwd = first = None
        sub = {}
        try:
            for i, line in enumerate(open_lines(path)):
                if i == 0 and '"session_meta"' in line:
                    # A sub-agent rollout (guardian review, spawned agent...) names its
                    # parent thread in the first record.
                    try:
                        p = (json.loads(line).get("payload") or {})
                    except ValueError:
                        p = {}
                    s = p.get("source")
                    if isinstance(s, dict) and "subagent" in s:
                        kind = s["subagent"]
                        if isinstance(kind, dict):
                            parent = p.get("parent_thread_id") or next(
                                (v.get("parent_thread_id") for v in kind.values()
                                 if isinstance(v, dict) and v.get("parent_thread_id")), None)
                            kind = kind.get("other") or next(iter(kind), "subagent")
                        else:
                            parent = p.get("parent_thread_id")
                        sub = {"parent": parent or "?", "agent": str(kind or "subagent")}
                    elif p.get("parent_thread_id"):
                        sub = {"parent": p["parent_thread_id"], "agent": p.get("thread_source") or "subagent"}
                    elif p.get("forked_from_id"):
                        sub = {"parent": p["forked_from_id"], "agent": "fork"}
                if sub.get("agent") == "fork" and "Side conversation boundary" in line:
                    sub["agent"] = "side"  # a side chat opened next to the parent thread
                if '"cwd"' in line and not cwd:
                    try:
                        o = json.loads(line)
                    except ValueError:
                        o = None
                    if o:
                        p = o.get("payload") or {}
                        env = (p.get("state") or {}).get("environments", {}).get("environments", {})
                        cwd = (p.get("cwd") or env.get("local", {}).get("cwd")
                               or o.get("cwd"))
                if '"role": "user"' in line or '"role":"user"' in line:
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    p = o.get("payload") or {}
                    if p.get("role") == "user":
                        t = clean_user_text(blocks_text(p.get("content")))
                        if t:
                            first = t
                            break
                if i > 400:
                    break
        except RuntimeError as e:
            return {"title": "", "cwd": "", "first_user": str(e)}
        return {"title": title or "", "cwd": cwd or "", "first_user": first or "", **sub}

    def messages(self, path):
        for o in _records_with(path, '"response_item"'):
            p = o.get("payload") or {}
            if p.get("type") != "message":
                continue
            role = p.get("role")
            if role not in ("user", "assistant"):
                continue
            txt = blocks_text(p.get("content"))
            if role == "user":
                txt = clean_user_text(txt)
            if txt:
                yield o.get("timestamp"), role, txt


class Claude(Source):
    key, label = "cl", "Claude Code"
    root = HOME / ".claude" / "projects"

    def sessions(self):
        return list(self.root.rglob("*.jsonl")) if self.root.exists() else []

    def sid(self, path):
        return path.stem

    def scan(self, path):
        title = summary = last_prompt = first = None
        cwd = None
        for o in _records_with(path, '"type"'):
            t = o.get("type")
            if t == "custom-title":
                title = o.get("customTitle") or title
                continue
            if t == "summary":
                summary = o.get("summary") or summary
                continue
            if t == "last-prompt":
                last_prompt = o.get("lastPrompt") or last_prompt
                continue
            if t == "agent-name":
                last_prompt = last_prompt or o.get("agentName")
                continue
            if t not in ("user", "assistant"):
                continue
            cwd = cwd or o.get("cwd")
            if t == "user" and not first:
                content = (o.get("message") or {}).get("content")
                if isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content
                ):
                    continue
                if (o.get("origin") or {}).get("kind") not in (None, "human"):
                    continue
                txt = clean_user_text(blocks_text(content))
                if txt:
                    first = txt
            if first and cwd and title:
                break
        sub = subagent_of(path)
        return {
            "title": title or sub.pop("title", "") or summary or (last_prompt or "")[:60],
            "cwd": cwd or guess_cwd_from_dirname(
                path.parents[2].name if sub else path.parent.name),
            "first_user": first or "",
            **sub,
        }

    def messages(self, path):
        for o in _records_with(path, '"type"'):
            t = o.get("type")
            if t not in ("user", "assistant"):
                continue
            content = (o.get("message") or {}).get("content")
            if t == "user":
                if isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content
                ):
                    continue
                txt = clean_user_text(blocks_text(content))
            else:
                txt = blocks_text(content)
            if txt:
                yield o.get("timestamp"), t, txt


class CodeBuddy(WB):
    """CodeBuddy CLI writes the same records as WorkBuddy, under its own folder."""
    key, label = "cb", "CodeBuddy"
    root = HOME / ".codebuddy" / "projects"


def _app_data(name: str) -> Path:
    """Where an Electron app keeps its userData folder on this OS."""
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or HOME / "AppData" / "Roaming") / name
    if sys.platform == "darwin":
        return HOME / "Library" / "Application Support" / name
    return Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / name


def _sqlite_rows(db: Path, sql: str) -> list:
    """Read-only query on an app's live SQLite file; [] when it is missing or locked."""
    if not db.is_file():
        return []
    import sqlite3
    for uri in (f"{db.as_uri()}?mode=ro", f"{db.as_uri()}?immutable=1"):
        try:
            con = sqlite3.connect(uri, uri=True, timeout=2)
            try:
                return con.execute(sql).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            continue
    return []


class Qoder(Claude):
    """Qoder CN (the desktop app and its qodercli) writes Claude Code style records
    to ~/.qoder-cn/projects/<proj>/<sessionId>.jsonl. Titles, side chats and
    automation runs live in the app's main.sqlite; sessions it imported from
    WorkBuddy / Codex are listed in sessionMigration.sqlite."""
    key, label = "qd", "Qoder"
    root = HOME / ".qoder-cn" / "projects"
    app_dir = _app_data("com.qodercn.app.stable")
    _db = None

    def sessions(self):
        # Only top-level transcripts and sub-agent runs; memory/ and state folders hold no chats.
        if not self.root.exists():
            return []
        return [p for p in self.root.rglob("*.jsonl")
                if p.parent.parent == self.root or p.parent.name == "subagents"]

    def scan(self, path):
        meta = super().scan(path)
        # Records the app wrote for a prompt typed in Qoder carry "humanInput";
        # an imported transcript has none until the user continues it here.
        native = False
        for line in open_lines(path):
            if '"humanInput"' in line:
                native = True
                break
        return {**meta, "native": native}

    def db(self) -> dict:
        """{session id: {title, cwd, kind, owner, import_src, import_ref}}, read
        once per run: titles change in the app without touching the transcript."""
        if Qoder._db is None:
            out: dict = {}
            for sid, title, cwd, kind, owner in _sqlite_rows(
                    self.app_dir / "main.sqlite",
                    "select session_id, title, cwd, session_kind, owner_session_id"
                    " from chat_sessions"):
                out[sid] = {"title": title or "", "cwd": cwd or "", "kind": kind or "",
                            "owner": owner or ""}
            for prod, ref, sid in _sqlite_rows(
                    self.app_dir / "sessionMigration.sqlite",
                    "select source_product, source_ref, target_session_id from migration_ledger"
                    " where status in ('imported', 'partial')"):
                src = {"workbuddy": "wb", "codex": "cx", "codebuddy": "cb",
                       "claude": "cl", "claude-code": "cl"}.get((prod or "").lower())
                if src and ref:
                    out.setdefault(sid, {}).update(import_src=src, import_ref=ref)
            Qoder._db = out
        return Qoder._db

    def live(self, path: Path, meta: dict) -> dict:
        info = self.db().get(self.sid(path)) or {}
        out = {}
        if info.get("title"):
            out["title"] = info["title"]
        if info.get("cwd"):
            out["cwd"] = info["cwd"]
        if info.get("kind") == "sideChat" and info.get("owner"):
            out.update(parent=info["owner"], agent="side")
        elif info.get("kind") == "automationExecution":
            out.update(parent=info.get("owner") or "?", agent="automation")
        elif info.get("import_src"):
            # A copy of a WorkBuddy / Codex session: hidden like a sub-agent so searches
            # do not hit it twice, unless the user went on talking in Qoder.
            out.update(parent=info["import_ref"], parent_src=info["import_src"],
                       agent="continued" if meta.get("native") else "import")
        return out


def _uri_path(uri: str) -> str:
    """'file:///d%3A/Sandbox' -> 'D:\\Sandbox'; anything else is left alone."""
    from urllib.parse import unquote, urlparse
    if not uri or not isinstance(uri, str):
        return ""
    p = unquote(urlparse(uri).path or "")
    if re.match(r"^/[A-Za-z]:", p):  # Windows drive path: drop the leading slash
        return (p[1].upper() + p[2:]).replace("/", "\\")
    return p


def _pb_fields(buf: bytes):
    """Walk a protobuf wire message: yield (field number, wire type, value)."""
    i, n = 0, len(buf)
    while i < n:
        key = shift = 0
        while i < n:
            b = buf[i]
            i += 1
            key |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
        else:
            return
        fn, wt = key >> 3, key & 7
        if wt == 0:
            v = shift = 0
            while i < n:
                b = buf[i]
                i += 1
                v |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
            else:
                return
            yield fn, wt, v
        elif wt == 2:
            ln = shift = 0
            while i < n:
                b = buf[i]
                i += 1
                ln |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
            else:
                return
            yield fn, wt, buf[i:i + ln]
            i += ln
        elif wt == 5:
            yield fn, wt, buf[i:i + 4]
            i += 4
        elif wt == 1:
            yield fn, wt, buf[i:i + 8]
            i += 8
        else:
            return


def _pb_msg(buf: bytes, *path: int) -> bytes:
    """Follow a chain of field numbers into nested messages; b'' when any hop is missing."""
    cur = buf
    for fn in path:
        nxt = b""
        for f, wt, v in _pb_fields(cur):
            if f == fn and wt == 2:
                nxt = v
                break
        cur = nxt
        if not cur:
            return b""
    return cur


class Agy(Source):
    """Google Antigravity, the agent IDE. One SQLite database per conversation under
    conversations/, the same dialogue mirrored as plain jsonl under
    brain/<id>/.system_generated/logs/ (checkpointed into chunks/transcript/ as it
    grows), and titles, workspace folder and parent links in conversation_summaries.db.
    The database is the authority for metadata; the jsonl is what can be searched."""
    key, label = "ag", "Antigravity"
    root = HOME / ".gemini" / "antigravity"
    _db = None

    def sessions(self):
        logs = []
        brain = self.root / "brain"
        if brain.is_dir():
            logs = sorted(brain.glob("*/.system_generated/logs/transcript.jsonl"))
        conv = self.root / "conversations"
        if not conv.is_dir():
            return logs
        # A conversation whose log was pruned is still readable step by step from its db.
        have = {p.parents[2].name for p in logs}
        return logs + sorted(p for p in conv.glob("*.db") if p.stem not in have)

    def sid(self, path):
        return path.stem if path.suffix == ".db" else path.parents[2].name

    def db(self) -> dict:
        """{conversation id: {title, preview, cwd, agent, parent}}, read once per run:
        the title can be edited in the app without any transcript being touched."""
        if Agy._db is None:
            out: dict = {}
            for cid, title, preview, uris, agent, parent in _sqlite_rows(
                    self.root / "conversation_summaries.db",
                    "select conversation_id, title, preview, workspace_uris, agent_name,"
                    " parent_conversation_id from conversation_summaries"):
                try:
                    first = json.loads(uris or "[]")[0]
                except (ValueError, IndexError, TypeError):
                    first = ""
                out[cid] = {"title": title or "", "preview": preview or "",
                            "cwd": _uri_path(first), "agent": agent or "",
                            "parent": parent or ""}
            Agy._db = out
        return Agy._db

    def scan(self, path):
        info = self.db().get(self.sid(path)) or {}
        first = ""
        if path.suffix == ".db":
            for _idx, _st, blob in _sqlite_rows(
                    path, "select idx, step_type, step_payload from steps"
                          " where step_type = 14 order by idx limit 1"):
                first = clean_user_text(_pb_msg(blob or b"", 19, 2).decode("utf-8", "replace"))
        else:
            for line in open_lines(path):
                if '"USER_INPUT"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("type") == "USER_INPUT":
                    first = clean_user_text(o.get("content"))
                    if first:
                        break
        return {"title": info.get("title") or "", "cwd": info.get("cwd") or "",
                "first_user": first or info.get("preview") or ""}

    def live(self, path, meta):
        info = self.db().get(self.sid(path)) or {}
        out = {}
        if info.get("title"):
            out["title"] = info["title"]
        if info.get("cwd"):
            out["cwd"] = info["cwd"]
        if info.get("parent"):
            out.update(parent=info["parent"], agent=info.get("agent") or "subagent")
        return out

    def pushable(self, path):
        # A SQLite file cannot be redacted or appended to, so only the jsonl log is shared.
        return path.suffix != ".db"

    def messages(self, path):
        if path.suffix == ".db":
            yield from self._db_messages(path)
            return
        parts = [path]
        chunks = path.parent / "chunks" / "transcript"
        if chunks.is_dir():
            parts = sorted(chunks.glob("*.jsonl")) + parts  # checkpointed history first
        seen = set()
        for part in parts:
            for line in open_lines(part):
                if line in seen:  # the live log still holds the last checkpointed chunk
                    continue
                seen.add(line)
                if '"USER_INPUT"' not in line and '"PLANNER_RESPONSE"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                kind = o.get("type")
                if kind == "USER_INPUT":
                    role, txt = "user", clean_user_text(o.get("content"))
                elif kind == "PLANNER_RESPONSE":
                    role, txt = "assistant", o.get("content") or ""
                else:
                    continue
                if txt:
                    yield o.get("created_at"), role, txt

    def _db_messages(self, path):
        """Decode the step database: step_type 14 is the human, 15 the model's answer."""
        for _idx, step_type, blob in _sqlite_rows(
                path, "select idx, step_type, step_payload from steps order by idx"):
            txt = b""
            if step_type == 14:
                role, txt = "user", _pb_msg(blob or b"", 19, 2)
            elif step_type == 15:
                role, txt = "assistant", _pb_msg(blob or b"", 20, 1)
            else:
                continue
            try:
                body = txt.decode("utf-8").strip()
            except UnicodeDecodeError:
                continue
            if not body:
                continue
            if role == "user":
                body = clean_user_text(body)
                if not body:
                    continue
            secs = 0
            for f, wt, v in _pb_fields(_pb_msg(blob or b"", 5, 1)):
                if f == 1 and wt == 0:
                    secs = v
                    break
            try:
                when = datetime.fromtimestamp(secs).isoformat() if secs else None
            except (OSError, OverflowError, ValueError):
                when = None
            yield when, role, body


SOURCES = {s.key: s for s in (WB(), Codex(), Claude(), CodeBuddy(), Qoder(), Agy())}
ALIASES = {"all": None, "wb": "wb", "workbuddy": "wb", "cx": "cx", "codex": "cx",
           "cl": "cl", "claude": "cl", "cb": "cb", "codebuddy": "cb",
           "qd": "qd", "qoder": "qd", "qoder-cn": "qd",
           "ag": "ag", "antigravity": "ag", "anti-gravity": "ag"}


def norm_source(v: str):
    return ALIASES.get(v.lower(), v if v in SOURCES else None)


def load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cache(c: dict) -> None:
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def local_rows(only=None) -> list[dict]:
    keys = [only] if only else list(SOURCES)
    cache = load_cache()
    rows = []
    for k in keys:
        src = SOURCES[k]
        for p in src.sessions():
            try:
                st = p.stat()
            except OSError:
                continue
            key = str(p)
            hit = cache.get(key)
            if (hit and hit.get("m") == st.st_mtime and hit.get("s") == st.st_size
                    and hit.get("v") == META_V):
                meta = hit
            else:
                try:
                    meta = src.scan(p)
                except RuntimeError as e:
                    meta = {"title": "(unreadable)", "cwd": "", "first_user": str(e)}
                meta = {**meta, "m": st.st_mtime, "s": st.st_size, "v": META_V}
                cache[key] = meta
            real_title = meta.get("title") or ""
            first = meta.get("first_user") or ""
            parent, agent = meta.get("parent") or "", meta.get("agent") or ""
            parent_src = ""
            live = src.live(p, meta)
            if live.get("title"):
                real_title = live["title"]
            if live.get("parent"):
                parent, agent = live["parent"], live.get("agent") or "subagent"
                parent_src = live.get("parent_src") or ""
            if not parent and any(first.startswith(m) for m in MACHINE_PROMPTS):
                parent, agent = "?", "guardian"  # older rollouts without a parent id
            # A sidecar ties this session to a parent in another client. It is read
            # fresh every pass (never cached) so it can appear or vanish later.
            link = parent_link(p)
            if link and not parent:
                parent = link["parent"]
                parent_src = link["parent_src"]
                agent = link["agent"]
                if not real_title and link.get("title"):
                    real_title = link["title"]
            if agent and real_title and not real_title.startswith("["):
                real_title = f"[{agent}] {real_title}"
            row = {
                "parent": parent, "agent": agent,
                "src": src.key, "host": "", "id": src.sid(p), "path": str(p),
                "title": real_title or (first.splitlines()[0][:50] if first else "(untitled)"),
                "has_title": bool(real_title),
                "cwd": live.get("cwd") or meta.get("cwd") or "",
                "first_user": first,
                "bytes": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            }
            if parent_src:
                row["parent_src"] = parent_src
            rows.append(row)
    save_cache(cache)
    return rows


def cloud_rows(only=None, host=None) -> list[dict]:
    """Other machines' sessions, read from their _index.json only (no transcript download)."""
    root = cloud_root()
    if not root or not root.is_dir():
        return []
    rows = []
    for d in sorted(root.iterdir()):
        name = d.name.upper()
        if not d.is_dir() or name.startswith("_") or name == HOST:
            continue  # this machine's own copy is older than its local files
        if host and name != host.upper():
            continue
        # _search/<HOST>/index.json is the pinned-offline copy; <HOST>/_index.json is the
        # same thing, and the only one a machine on an older version writes.
        data = None
        for idx in (root / "_search" / d.name / "index.json", d / "_index.json"):
            try:
                data = json.loads(idx.read_text(encoding="utf-8"))
                break
            except (OSError, ValueError):
                continue
        if not data:
            continue
        for r in data.get("sessions", []):
            if r.get("src") not in SOURCES or (only and r["src"] != only):
                continue
            chunks = [str(d / r["src"] / c) for c in (r.get("chunks") or [r["cloud_rel"]])]
            t = r.get("text") or ""
            text = str(root / t) if t.startswith("_search/") else str(d / r["src"] / t) if t else ""
            extra = {}
            if not r.get("parent") and "/subagents/" in "/" + str(r.get("cloud_rel", "")):
                # pushed by a version that did not record the parent yet
                parts = str(r["cloud_rel"]).split("/")
                extra = {"parent": parts[parts.index("subagents") - 1], "agent": r.get("agent") or "subagent"}
            rows.append({**r, **extra, "host": d.name, "path": chunks[0], "chunks": chunks, "text": text})
    return rows


def index(only=None, host="all") -> list[dict]:
    """host: all | local | <machine name>."""
    rows = []
    if host in ("all", "local") or host.upper() == HOST:
        rows += local_rows(only)
    if host != "local":
        rows += cloud_rows(only, None if host == "all" else host)
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows


def tag_of(r: dict, full_id: bool = False) -> str:
    src = f"{r['src']}@{r['host']}" if r.get("host") else r["src"]
    # CodeBuddy ids are UUIDv7: the first 12 hex digits are a timestamp, so 8 collide.
    # Claude sub-agent ids all start with "agent-a", so 8 characters would collide.
    # Codex sub-agent rollouts share the parent's first 8 characters.
    # Ids a tool chose itself (--session-id ds-<parent>-<job>-<phase>) only differ at the end.
    if r["id"].startswith("ds-"):
        full_id = True
    n = (14 if r["id"].startswith("agent-") else
         13 if r["src"] == "cb" or (r["src"] == "cx" and is_sub(r)) else 8)
    return f"{src}|{r['id'] if full_id else r['id'][:n]}"


# Linked to a parent but still the user's own conversation: listed normally.
# "continued" is a Qoder copy of a WorkBuddy / Codex session the user went on with.
OWN_KINDS = ("side", "fork", "continued")


def is_sub(r: dict) -> bool:
    """A sub-agent run: hidden unless --subagents, listed under its parent.

    A sidecar link always counts: its `agent` is a display label ("ds:impl"), so
    "side"/"fork" there say how the run was directed, not that the user owns it."""
    if not r.get("parent"):
        return False
    if r.get("agent") == "continued":
        return False
    return bool(r.get("parent_src")) or r.get("agent") not in OWN_KINDS


def link_note(r: dict) -> str:
    k = r.get("agent")
    what = (L("侧旁对话，主会话", "side chat of") if k == "side" else
            L("分叉自", "forked from") if k == "fork" else
            L("Qoder 导入的副本，原会话", "Qoder's imported copy of") if k == "import" else
            L("导入 Qoder 后接着聊，原会话", "imported into Qoder and continued, from") if k == "continued" else
            L("自动化运行，所属", "automation run of") if k == "automation" else
            L("子代理，主会话", "sub-agent of"))
    return f"↳ {what} {parent_tag(r)}"


def children_of(rows: list[dict]) -> dict:
    """(parent source, host, parent id) -> sub-agent rows, newest first.

    A child that names its parent's source groups under that source, so a CodeBuddy
    session directed from a Claude Code session hangs under the Claude row."""
    kids: dict = {}
    for r in rows:
        if r.get("parent"):
            key = (r.get("parent_src") or r["src"], r.get("host") or "", r["parent"])
            kids.setdefault(key, []).append(r)
    return kids


def kids_for(kids: dict, r: dict) -> list:
    return kids.get((r["src"], r.get("host") or "", r["id"]), [])


def parent_tag(r: dict) -> str:
    p = r.get("parent") or ""
    if p in ("", "?"):
        return L("（主会话未知）", "(parent unknown)")
    return tag_of({**r, "src": r.get("parent_src") or r["src"], "id": p})


def fmt(rows: list[dict], full_id: bool = False, kids: dict | None = None) -> str:
    out = []
    w = max([12] + [len(tag_of(r, full_id)) for r in rows])
    for r in rows:
        tag = tag_of(r, full_id)
        out.append(f"{tag:<{w}} {r['mtime']}  {r['bytes']//1024:>5}KB  {r['title']}")
        if r.get("parent"):
            out.append(f"{'':{w}} {'':16}  {'':>7}  {link_note(r)}")
        if kids and kids_for(kids, r):
            ks = kids_for(kids, r)
            n_imp = sum(k.get("agent") in ("import", "continued") for k in ks)
            n_side = sum(k.get("agent") in ("side", "fork") for k in ks)
            n_sub = len(ks) - n_side - n_imp
            parts = ([L(f"{n_side} 个侧旁对话/分叉", f"{n_side} side chat(s)/fork(s)")] if n_side else []) + \
                    ([L(f"{n_imp} 份 Qoder 导入副本", f"{n_imp} Qoder import(s)")] if n_imp else []) + \
                    ([L(f"{n_sub} 个子代理", f"{n_sub} sub-agent(s)")] if n_sub else [])
            out.append(f"{'':{w}} {'':16}  {'':>7}  + {', '.join(parts)}{L('（show 末尾列出）', ' (listed at the end of show)')}")
        if r["cwd"]:
            out.append(f"{'':{w}} {'':16}  {'':>7}  cwd={r['cwd']}")
        if r["first_user"]:
            out.append(f"{'':{w}} {'':16}  {'':>7}  {L('首问', 'first')}: {r['first_user'].splitlines()[0][:100]}")
    return "\n".join(out)


def resolve(ref: str, host: str = "all"):
    if "|" in ref:
        k, sid = ref.split("|", 1)
        if "@" in k:
            k, host = k.split("@", 1)
        k = norm_source(k)
        if k not in SOURCES:
            print(L(f"未知来源前缀：{ref.split('|')[0]}（用 wb / cx / cl / cb / qd / ag，可加 @电脑名）",
                    f"unknown source prefix: {ref.split('|')[0]} (use wb / cx / cl / cb / qd / ag, optionally @HOST)"))
            return None
        hits = [r for r in index(k, host) if r["id"].startswith(sid)]
    else:
        hits = [r for r in index(None, host) if r["id"].startswith(ref)]
    if not hits:
        print(L(f"没有匹配的会话：{ref}", f"no session matched: {ref}"))
        return None
    if len(hits) > 1 and len({(h["src"], h.get("host"), h["id"]) for h in hits}) == 1:
        print(L(f"（同一个 id 有 {len(hits)} 份副本，读最新的那份）", f"({len(hits)} copies of this id, reading the newest)"))
        hits = hits[:1]
    if len(hits) > 1:
        # A short prefix shared by a session and its sub-agents means the session.
        own = [h for h in hits if not is_sub(h)]
        if len(own) == 1:
            hits = own
    if len(hits) > 1:
        print(L(f"前缀不唯一，有 {len(hits)} 个候选（用更长的前缀）：\n", f"ambiguous prefix, {len(hits)} candidates:\n"))
        print(fmt(hits[:10]))
        return None
    r = hits[0]
    chunks = [Path(c) for c in r.get("chunks") or [r["path"]]]
    LAST_RESOLVED[:] = [r]
    return SOURCES[r["src"]], chunks if len(chunks) > 1 else chunks[0], r.get("host") or HOST


LAST_RESOLVED: list = []  # the row resolve() picked, for show's sub-agent footer


def chunk_label(path) -> str:
    if isinstance(path, list):
        return f"{path[0].name} (+{len(path) - 1} chunks)"
    return path.name


def cmd_list(a) -> int:
    rows = index(norm_source(a.source), a.host)
    kids = children_of(rows)
    n_sub = sum(map(is_sub, rows))
    if not a.subagents:
        rows = [r for r in rows if not is_sub(r)]
    print(L(f"# {len(rows)} 个会话（source={a.source}, host={a.host}）", f"# {len(rows)} sessions (source={a.source}, host={a.host})"))
    if n_sub and not a.subagents:
        print(L(f"#（另有 {n_sub} 个子代理会话没列出，加 --subagents 显示）",
                f"# ({n_sub} sub-agent sessions hidden, --subagents shows them)"))
    print()
    print(fmt(rows[: a.limit], kids=kids))
    return 0


def cmd_find(a) -> int:
    # 每个参数既可当短语（带空格时），也可拆成单词；短语额外加权。
    phrases = [a_.strip().lower() for a_ in a.keywords if " " in a_.strip()]
    words = [w.lower() for a_ in a.keywords for w in a_.split() if w.strip()]
    scored = []
    all_rows = index(norm_source(a.source), a.host)
    kids = children_of(all_rows)
    for r in all_rows:
        if is_sub(r) and not a.subagents:
            continue
        title = r["title"].lower() if r.get("has_title") else ""
        blob = title + "\n" + r["first_user"].lower()
        score = 0
        for w in words:
            if w in title:
                score += 10
            if w in blob:
                score += 3
        for ph in phrases:
            if ph in blob:
                score += 6
        if score:
            scored.append((score, r))
    if not scored:
        print(L(f"没有匹配的会话：{' '.join(a.keywords)}", f"no session matched: {' '.join(a.keywords)}"))
        return 1
    scored.sort(key=lambda x: (-x[0], x[1]["mtime"]))
    print(L(f"# {len(scored)} 个匹配，最相关的在前\n", f"# {len(scored)} match(es), best first\n"))
    print(fmt([r for _, r in scored[: a.limit]], kids=kids))
    return 0


def dialogue(r: dict, deep: bool = False):
    """Yield (ts, role, text) for one session row, local or cloud.

    Cloud rows read the small .txt.zst extract; deep mode reads raw records
    instead (tool calls and output included), which downloads the transcript.
    """
    if deep:
        target = [Path(c) for c in r["chunks"]] if r.get("chunks") else Path(r["path"])
        if not isinstance(target, list) and target.suffix == ".db":
            yield from SOURCES[r["src"]].messages(target)  # binary store: dialogue only
            return
        for line in open_lines(target):
            yield None, "raw", line
        return
    if r.get("host"):
        for line in open_lines(Path(r["text"])):
            try:
                o = json.loads(line)
            except ValueError:
                continue
            yield o.get("t"), o.get("r"), o.get("x") or ""
        return
    yield from SOURCES[r["src"]].messages(Path(r["path"]))


def snippet(txt: str, words: list[str], width: int) -> str:
    flat = " ".join(txt.split())
    low = flat.lower()
    i = min([low.find(w) for w in words if w in low] or [0])
    start = max(0, i - width)
    end = min(len(flat), i + width)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


def cmd_grep(a) -> int:
    """Full-text search over what was said, not just titles. A session must
    contain every keyword (anywhere in it); messages hitting any keyword are shown."""
    words = [w.lower() for w in a.keywords]
    rows = index(norm_source(a.source), a.host)
    if not a.subagents:
        rows = [r for r in rows if not is_sub(r)]
    hits, no_text = [], 0
    for r in rows:
        if r.get("host") and not r.get("text") and not a.deep:
            no_text += 1
            continue
        found, seen = [], set()
        try:
            for ts, role, txt in dialogue(r, a.deep):
                if a.role != "all" and role not in (a.role, "raw"):
                    continue
                low = txt.lower()
                got = {w for w in words if w in low}
                if got:
                    seen |= got
                    found.append((len(got), ts, role, txt))
        except (OSError, RuntimeError):
            continue
        if found and seen == set(words):
            hits.append((r, found))
    if no_text:
        print(L(f"（跳过了 {no_text} 个还没有文本摘录的云端会话；--deep 会读原始记录）",
                f"(skipped {no_text} cloud sessions that have no text extract yet; --deep reads them raw)"))
    if not hits:
        print(L(f"没有会话提到：{' '.join(a.keywords)}", f"no session mentions: {' '.join(a.keywords)}"))
        return 1
    hits.sort(key=lambda h: (len(h[1]), h[0]["mtime"]), reverse=True)
    print(L(f"# {len(hits)} 个会话提到 {' + '.join(a.keywords)}，命中多的在前\n",
            f"# {len(hits)} session(s) mention {' + '.join(a.keywords)}, most hits first\n"))
    for r, found in hits[: a.limit]:
        print(f"{tag_of(r)}  {r['mtime']}  {r['title']}  ({len(found)} {L('处命中', 'hits')})")
        if r.get("parent"):
            print(f"    {link_note(r)}")
        best = sorted(found, key=lambda f: -f[0])[: a.per]
        for _, ts, role, txt in best:
            print(f"    [{stamp(ts)}] {role:9s} {snippet(txt, words, a.context)}")
        print()
    if len(hits) > a.limit:
        print(L(f"… 还有 {len(hits) - a.limit} 个（--limit 调整）", f"… {len(hits) - a.limit} more (--limit)"))
    return 0


def cmd_show(a) -> int:
    got = resolve(a.session, a.host)
    if not got:
        return 1
    src, path, host = got
    me = LAST_RESOLVED[0] if LAST_RESOLVED else {}
    print(f"# {src.label} @ {host} · {chunk_label(path)}")
    if me.get("parent"):
        print(f"# [{me.get('agent')}] {link_note(me)}")
    print()
    pat = re.compile(a.grep, re.IGNORECASE) if a.grep else None

    def emit(ts, role, txt):
        body = " ".join(txt.split())
        if len(body) > a.max_chars:
            body = body[: a.max_chars] + f" …[+{len(txt) - a.max_chars}{L('字', ' chars')}]"
        print(f"[{stamp(ts)}] {role:9s} {body}")

    shown = 0
    tail = deque(maxlen=a.tail) if a.tail else None
    try:
        for ts, role, txt in src.messages(path):
            if a.role != "all" and role != a.role:
                continue
            if pat and not pat.search(txt):
                continue
            shown += 1
            if tail is not None:
                tail.append((ts, role, txt))
                continue
            emit(ts, role, txt)
            if shown >= a.head:
                print(L(f"… 已截断，共 {shown} 条（--head 调整）", f"… truncated at {shown} messages (--head)"))
                break
    except RuntimeError as e:
        print(L(f"出错：{e}", f"error: {e}"))
        return 1
    if tail is not None and shown > len(tail):
        print(L(f"… 前面还有 {shown - len(tail)} 条，只给最后 {len(tail)} 条（--tail 调整）",
                f"… {shown - len(tail)} earlier messages skipped, showing the last {len(tail)} (--tail)"))
    for m in tail or ():
        emit(*m)
    if me:
        h = me.get("host") or "local"
        kids = kids_for(children_of(index(None, h)), me)
        kids.sort(key=lambda r: r.get("agent") not in OWN_KINDS)  # side chats first, stable by time
        if kids:
            print(L(f"\n# 挂在这个会话下的 {len(kids)} 个子代理/侧旁对话（show <标签> 读取）：",
                    f"\n# {len(kids)} sub-agent(s)/side chat(s) of this session (show <tag> to read):"))
            for r in kids[:30]:
                print(f"  {tag_of(r)}  {r['mtime']}  {r['title']}")
            if len(kids) > 30:
                print(f"  … +{len(kids) - 30}")
    if not shown:
        print(L("（没有符合条件的消息）", "(no message matched the filter)"))
        return 1
    return 0


def cmd_files(a) -> int:
    got = resolve(a.session, a.host)
    if not got:
        return 1
    src, path, host = got
    counts: dict[str, int] = {}

    def take(text: str) -> None:
        for m in FILE_RE.finditer(text):
            p = re.sub(r"\\{2,}", r"\\", m.group(0).rstrip("\\/"))
            if ("\\r" in p or "\\n" in p or "##" in p or " " in p
                    or "://" in p or p.endswith(".")):
                continue
            counts[p] = counts.get(p, 0) + 1

    try:
        if path.suffix == ".db":  # a binary store has to be decoded, not scanned line by line
            for _ts, _role, txt in src.messages(path):
                take(txt)
        else:
            for line in open_lines(path):
                take(line)
    except RuntimeError as e:
        print(L(f"出错：{e}", f"error: {e}"))
        return 1
    if not counts:
        print(L("（没找到文件路径）", "(no file paths found)"))
        return 1
    print(f"# {len(counts)} distinct paths · {src.label} @ {host}\n")
    for p, c in sorted(counts.items(), key=lambda x: -x[1])[: a.limit]:
        print(f"{c:>4}x  {p}")
    return 0


CHUNK_TAIL = 4096        # bytes hashed just before the upload offset, to notice rewrites
MAX_CHUNKS = 20          # merge a session's chunks back into one once it is this fragmented
QUIET_SECS = 60          # watch: push a session once it has stopped changing this long
MAX_WAIT_SECS = 300      # watch: ...or once it has kept changing this long
SWEEP_SECS = 1800        # watch: full pass, catches anything the change scan missed
UPDATE_CHECK_SECS = 600  # watch: look for a newer version in SessionSync/_install


class FileLock:
    """Exclusive, non-blocking lock on a file under STATE_DIR."""

    def __init__(self, name: str):
        self.path = STATE_DIR / name
        self.f = None

    def acquire(self, wait: float = 0) -> bool:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + wait
        while True:
            f = open(self.path, "a+b")
            try:
                if os.name == "nt":
                    import msvcrt
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.f = f
                return True
            except OSError:
                f.close()
                if time.time() >= deadline:
                    return False
                time.sleep(1)

    def release(self) -> None:
        if not self.f:
            return
        if os.name == "nt":
            import msvcrt
            self.f.seek(0)
            try:
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        self.f.close()
        self.f = None


def log_line(msg: str) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        if SYNC_LOG.exists() and SYNC_LOG.stat().st_size > 1_000_000:
            keep = SYNC_LOG.read_text(encoding="utf-8", errors="replace")[-200_000:]
            SYNC_LOG.write_text(keep, encoding="utf-8")
        with SYNC_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except OSError:
        pass


def version_key(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def skill_md(text: str) -> str:
    """SKILL.md as shipped points at ~/.workbuddy; point it at where the script really is."""
    return text.replace(".workbuddy/skills/session-finder", f"{SKILL_HOME.parent.parent.name}/skills/session-finder")


def installed() -> bool:
    """An installed copy sits in <client>/skills/session-finder/scripts; a checkout doesn't."""
    return SKILL_HOME.parent.name == "skills"


def spread_skill() -> None:
    """A client installed after session-finder gets SKILL.md the next time this runs,
    without rerunning the installer. Only fills in missing copies."""
    if not installed():
        return
    try:
        skill = None
        for client in CLIENTS:
            d = HOME / client / "session-finder"
            if not (HOME / client).is_dir() or (d / "SKILL.md").exists():
                continue
            if skill is None:
                skill = (SKILL_HOME / "SKILL.md").read_text(encoding="utf-8")
            d.mkdir(parents=True, exist_ok=True)
            (d / "SKILL.md").write_text(skill, encoding="utf-8", newline="")
            log_line(f"{HOST}: SKILL.md -> {d}")
    except OSError:
        pass


def self_update() -> bool:
    """Install a newer find_session.py (and SKILL.md for every client) from
    SessionSync\\_install over this copy. Returns True if it did.

    Only the installed copy updates itself, never the kit source or _install.
    """
    me = Path(__file__).resolve()
    if os.environ.get("SESSION_FINDER_NO_UPDATE") or not installed():
        return False
    root = cloud_root()
    if not root:
        return False
    bundle = root / "_install" / "session-finder"
    try:
        data = (bundle / "scripts" / "find_session.py").read_bytes()
        m = re.search(rb'^VERSION = "([^"]+)"', data, re.M)
        if not m or version_key(m.group(1).decode()) <= version_key(VERSION):
            return False
        # Never install a half-synced file: a cut between two functions still compiles,
        # so the last line must be the end marker.
        if not data.rstrip().endswith(END_MARK):
            return False
        compile(data, "find_session.py", "exec")
    except (OSError, SyntaxError, ValueError):
        return False
    new = m.group(1).decode()
    try:
        write_atomic(me, data)
        skill = skill_md((bundle / "SKILL.md").read_text(encoding="utf-8"))
        for client in CLIENTS:
            d = HOME / client / "session-finder"
            if (HOME / client).is_dir():
                d.mkdir(parents=True, exist_ok=True)
                (d / "SKILL.md").write_text(skill, encoding="utf-8", newline="")
    except OSError as e:
        log_line(f"{HOST}: self-update to {new} failed: {e}")
        return False
    log_line(f"{HOST}: self-update {VERSION} -> {new}")
    print(L(f"(session-finder 已从同步文件夹自动更新：{VERSION} -> {new})",
            f"(session-finder updated itself from the sync folder: {VERSION} -> {new})"), file=sys.stderr)
    return True


def tail_hash(f, off: int) -> str:
    import hashlib
    start = max(0, off - CHUNK_TAIL)
    f.seek(start)
    return hashlib.sha1(f.read(off - start)).hexdigest()


# Redaction. A <SessionSync>/_redact.json makes every machine scrub secrets out of what
# it writes to the sync folder (raw chunks, dialogue extracts, index titles); local
# transcripts are never touched.
#   {"tags": {"<first 8 hex of sha256(secret)>": "<name>", ...}, "patterns": true}
# Only hash prefixes are stored, never the secrets. A token whose hash prefix is listed
# becomes <vault://name>; "patterns" also blanks generic credential shapes
# (user:password@ in URLs, proxy links, private keys, JWTs, well-known token prefixes).
REDACT_FILE = "_redact.json"
REDACT_KEY = "__redact__"  # sync_state entry: signature of the _redact.json last applied
# no plain comma here: secrets can contain one (a trailing comma is stripped by _TRIM)
_SPLIT = re.compile(r"[\s`\"'，。；（）@:/=|\\<>\[\]{}()]+")
_TRIM = re.compile(r"^[(（\[*]+|[)）\].,;*]+$")
_SS_B64 = re.compile(r"\bss://([A-Za-z0-9+/=_-]{16,})(?=@)")
_CRED_PATTERNS = [
    # one JSONL record is one line, so never match across a newline
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[^\n\"]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"), "<redacted:private-key>"),
    (re.compile(r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"), "<redacted:jwt>"),
    (re.compile(r"\b(?:ghp_|gho_|github_pat_|sk-ant-|sk-proj-|sk_live_|rk_live_|tskey-|xox[bpa]-)[A-Za-z0-9_-]{12,}"), "<redacted:token>"),
    (re.compile(r"\b((?:https?|socks5h?|socks4|ftp|ss|ssr|trojan|vless|hysteria2|hy2|tuic|anytls)://[^:@\s/`\"'<>\\]+:)[^@\s`\"'<>\\]+(?=@)"), r"\1<redacted>"),
    (re.compile(r"\b(ss|vmess|ssr)://[A-Za-z0-9+/=_-]{16,}(?=@|[\s`\"'<>\\#]|$)"), r"\1://<redacted>"),
]


class Redactor:
    def __init__(self, tags: dict, patterns: bool, sig: str):
        self.tags = {str(k).lower()[:8]: str(v) for k, v in tags.items()}
        self.patterns = patterns
        self.sig = sig

    def _name(self, s: str):
        import hashlib
        return self.tags.get(hashlib.sha256(s.encode("utf-8", "surrogateescape")).hexdigest()[:8])

    def text(self, t: str) -> str:
        if not t:
            return t
        if self.tags:
            import base64
            import urllib.parse
            hits = {}
            for m in _SS_B64.finditer(t):  # ss://base64(method:password)@host
                b = m.group(1)
                try:
                    u = base64.b64decode(b.replace("-", "+").replace("_", "/") + "=" * (-len(b) % 4)).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    continue
                n = ":" in u and self._name(u.split(":", 1)[1])
                if n:
                    hits[b] = n
            for tok in set(_SPLIT.split(t)):
                if not 8 <= len(tok) <= 200:
                    continue
                for c in {tok, _TRIM.sub("", tok)}:
                    n = len(c) >= 8 and (self._name(c) or self._name(urllib.parse.unquote(c)))
                    if n:
                        hits[c] = n
            for frag in sorted(hits, key=len, reverse=True):
                t = t.replace(frag, f"<vault://{hits[frag]}>")
        if self.patterns:
            for rx, rep in _CRED_PATTERNS:
                t = rx.sub(rep, t)
        return t

    def blob(self, data: bytes) -> bytes:
        s = data.decode("utf-8", "surrogateescape")
        r = self.text(s)
        return data if r == s else r.encode("utf-8", "surrogateescape")


def load_redactor(root: Path):
    """The Redactor for this sync folder, or None if it has no _redact.json.
    An unreadable file raises: pushing unredacted because of a half-synced config
    would defeat the point."""
    import hashlib
    p = root / REDACT_FILE
    if not p.exists():
        return None
    try:
        raw = p.read_bytes()
        cfg = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError) as e:
        raise RuntimeError(f"{REDACT_FILE} unreadable, nothing pushed: {e}")
    r = Redactor(cfg.get("tags") or {}, bool(cfg.get("patterns")), hashlib.sha256(raw).hexdigest()[:16])
    return r if (r.tags or r.patterns) else None


def redact_folder(root: Path, host: str, red: Redactor, dry: bool = False) -> tuple[int, int]:
    """Rewrite, in place and under the same names, every file already pushed for `host`
    (raw chunks, whole Codex rollouts, dialogue extracts, indexes) that still holds
    something `red` would redact. Names don't change, so indexes and upload offsets
    stay valid. Returns (files read, files rewritten)."""
    import zstandard as zstd
    cz = zstd.ZstdCompressor(level=10)
    seen = changed = 0
    for base in (root / host, root / "_search" / host):
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.name.endswith(".tmp"):
                continue
            try:
                data = p.read_bytes()
                if p.suffix == ".zst":
                    plain = zstd.ZstdDecompressor().stream_reader(io.BytesIO(data)).read()
                elif p.suffix in (".json", ".jsonl"):
                    plain = data
                else:
                    continue
            except (OSError, zstd.ZstdError) as e:
                log_line(f"{HOST}: redact skip {p}: {e}")
                continue
            seen += 1
            new = red.blob(plain)
            if new != plain:
                changed += 1
                if not dry:
                    write_atomic(p, cz.compress(new) if p.suffix == ".zst" else new)
    return seen, changed


WRITTEN: set = set()  # files written by the current sync pass, for rclone_push


def write_atomic(out: Path, data: bytes) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, out)
    WRITTEN.add(out)


def rclone_push(root: Path, written, doomed) -> str:
    """Machines without a sync client (e.g. a Linux box that reads the folder through
    a one-way rclone mirror) set SESSION_SYNC_RCLONE to the remote path of SessionSync,
    e.g. onedrive:SessionSync. Files written this pass are copied up and
    superseded chunks deleted there, so the mirror's next pull can't bring them back.
    `copy`, never `sync`: nothing else in the remote is touched. Whatever fails stays
    queued in rclone_pending.json and is retried on the next pass."""
    remote = os.environ.get("SESSION_SYNC_RCLONE", "")
    if not remote:  # install.sh saves it here, so a manual `sync` pushes as well
        try:
            remote = (STATE_DIR / "rclone_remote").read_text(encoding="utf-8").strip()
        except OSError:
            pass
    remote = remote.rstrip("/")
    if not remote:
        return ""
    import shutil
    import subprocess
    import tempfile
    queue = STATE_DIR / "rclone_pending.json"
    try:
        q = json.loads(queue.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        q = {}

    def rels(paths):
        out = set()
        for x in paths:
            try:
                out.add(Path(x).relative_to(root).as_posix())
            except ValueError:
                pass
        return out

    rm = set(q.get("rm", [])) | rels(doomed)
    up = {u for u in (set(q.get("up", [])) | rels(written)) - rm if (root / u).exists()}
    left = {"up": up, "rm": rm}
    err = ""
    rc = shutil.which("rclone") or "rclone"
    for kind in ("up", "rm"):
        if not left[kind]:
            continue
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".lst", delete=False) as f:
            f.write("\n".join(sorted(left[kind])) + "\n")
        cmd = ([rc, "copy", str(root), remote, "--files-from-raw", f.name, "--no-traverse"]
               if kind == "up" else [rc, "delete", remote, "--files-from-raw", f.name])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            if r.returncode == 0:
                left[kind] = set()
            else:
                err = ((r.stderr or "").strip().splitlines() or [f"exit {r.returncode}"])[-1]
        except (OSError, subprocess.TimeoutExpired) as e:
            err = repr(e)
        finally:
            os.unlink(f.name)
    try:
        queue.write_text(json.dumps({k: sorted(v) for k, v in left.items()}), encoding="utf-8")
    except OSError:
        pass
    if left["up"] or left["rm"]:
        return f"; rclone FAILED, {len(left['up'])} up / {len(left['rm'])} rm queued: {err}"
    return f"; rclone {len(up)} up / {len(rm)} rm"


def adopt_v1(old, p: Path, rel: str, base: Path) -> dict:
    """Turn a pre-chunking state entry [mtime, size] into a chunk entry, reusing the
    whole-file copy already in the cloud when it ends on a line boundary."""
    if not (isinstance(old, list) and len(old) == 2):
        return {"off": 0, "n": 0, "chunks": []}
    if p.suffix == ".zst":
        return {"m": old[0], "s": old[1], "chunks": [rel]} if (base / rel).exists() else {"chunks": []}
    full = rel + ".zst"
    if not (base / full).exists():
        return {"off": 0, "n": 0, "chunks": []}
    off = old[1]
    try:
        with p.open("rb") as f:
            f.seek(off - 1)
            if off > 0 and f.read(1) == b"\n":
                return {"off": off, "tail": tail_hash(f, off), "n": 1, "chunks": [full]}
    except OSError:
        pass
    return {"off": -1, "n": 1, "chunks": [full]}  # cut mid-line: re-push from scratch


def push(p: Path, rel: str, base: Path, st, ent: dict, cz, red=None):
    """Upload what is new in one session. Returns (raw bytes, packed bytes, files to delete).

    jsonl sessions only ever grow, so each push is the appended tail since the last
    offset, as its own <rel>.NNN.zst. A rewrite (shorter file, or changed bytes just
    before the offset) starts over; MAX_CHUNKS pieces are merged once the session idles.
    Offsets always refer to the local file; `red` only changes what is written.
    """
    if p.suffix == ".zst":  # finished Codex rollout: copied whole when it changes
        if ent.get("chunks") and ent.get("m") == st.st_mtime and ent.get("s") == st.st_size:
            return 0, 0, []
        data = p.read_bytes()
        if red:
            import zstandard as zstd
            plain = zstd.ZstdDecompressor().stream_reader(io.BytesIO(data)).read()
            new = red.blob(plain)
            if new != plain:
                data = cz.compress(new)
        write_atomic(base / rel, data)
        ent.update(m=st.st_mtime, s=st.st_size, chunks=[rel])
        return len(data), len(data), []
    off = ent.get("off", 0)
    if off == st.st_size:
        return 0, 0, []
    doomed = []
    with p.open("rb") as f:
        if off < 0 or off > st.st_size or (off and tail_hash(f, off) != ent.get("tail")):
            doomed = [base / c for c in ent.get("chunks", [])]
            ent["chunks"], off = [], 0
        compact = len(ent["chunks"]) >= MAX_CHUNKS and time.time() - st.st_mtime > QUIET_SECS
        start = 0 if compact else off
        f.seek(start)
        data = f.read(st.st_size - start)
        cut = data.rfind(b"\n") + 1  # never split a record; the partial line waits
        if start + cut <= off:
            ent["off"] = off
            return 0, 0, doomed
        packed = cz.compress(red.blob(data[:cut]) if red else data[:cut])
        name = f"{rel}.{ent.get('n', 0):03d}.zst"  # n only grows, so names never collide
        ent["n"] = ent.get("n", 0) + 1
        write_atomic(base / name, packed)
        if compact:
            doomed += [base / c for c in ent["chunks"]]
            ent["chunks"] = []
        ent["chunks"].append(name)
        ent["off"] = start + cut
        ent["tail"] = tail_hash(f, ent["off"])
    return cut, len(packed), doomed


def push_text(src: Source, p: Path, sid: str, rel: str, base: Path, cz, red=None) -> str:
    """Write the session's dialogue only (no tool calls/results) as <id>-<hash>.txt.zst,
    one {"t","r","x"} record per message. Other machines `grep` these instead of
    downloading the raw transcript, which is ~99% tool output. Flat short names:
    mirroring Codex's dated folders plus a double-UUID filename overflows MAX_PATH."""
    import hashlib
    lines = [json.dumps({"t": ts, "r": role, "x": red.text(txt) if red else txt}, ensure_ascii=False)
             for ts, role, txt in src.messages(p)]
    name = f"{sid}-{hashlib.sha1(rel.encode('utf-8')).hexdigest()[:6]}.txt.zst"
    write_atomic(base / name, cz.compress(("\n".join(lines) + "\n").encode("utf-8")))
    return name


def _push_session(r: dict, only, dest: Path, search: Path, state: dict, cz, red):
    """Push one session's chunks and search text.
    Returns (state entry, stat, sessions pushed, raw bytes, packed bytes, deleted chunks)."""
    src = SOURCES[r["src"]]
    p = Path(r["path"])
    base = dest / r["src"]
    try:
        st = p.stat()
        rel = p.relative_to(src.root).as_posix()
    except (OSError, ValueError):
        return None, None, 0, 0, 0, []
    if not src.pushable(p):
        return None, None, 0, 0, 0, []
    ent = state.get(r["path"])
    if not isinstance(ent, dict):
        ent = adopt_v1(ent, p, rel, base)
    pushed = raw = packed = 0
    gone: list = []
    if only is None or r["path"] in only:
        try:
            n_raw, n_packed, gone = push(p, rel, base, st, ent, cz, red)
        except OSError as e:
            log_line(f"{HOST}: skip {p}: {e}")
        else:
            pushed = int(bool(n_raw))
            raw, packed = n_raw, n_packed
        stale = (ent.get("text_sig") != [st.st_mtime, st.st_size]
                 or not ent.get("text", "").startswith("_search/"))
        if ent.get("chunks") and stale:
            try:
                name = push_text(src, p, r["id"], rel, search / r["src"], cz, red)
                if ent.get("text") and not ent["text"].startswith("_search/"):
                    gone.append(base / ent["text"])  # pre-_search location
                ent["text"] = f"_search/{HOST}/{r['src']}/{name}"
                ent["text_sig"] = [st.st_mtime, st.st_size]
            except (OSError, RuntimeError) as e:
                log_line(f"{HOST}: text extract failed {p}: {e}")
    return ent, st, pushed, raw, packed, gone


def sync_once(only=None) -> tuple[int, str]:
    """Push this machine's sessions to <cloud>/<HOST>/ and rewrite its _index.json.

    only: set of local paths to push (watch mode); None pushes everything changed.
    Append-only: sessions deleted locally stay in the cloud. Only this machine
    writes its own folder, so the sync client never sees conflicting edits.
    """
    import zstandard as zstd
    root = cloud_root()
    if not root:
        raise RuntimeError(NO_SYNC)
    dest = root / HOST
    search = root / "_search" / HOST
    WRITTEN.clear()
    try:
        state = json.loads(SYNC_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    t0 = time.time()
    pushed = raw = packed = 0
    entries, doomed = [], []
    cz = zstd.ZstdCompressor(level=10)
    red = load_redactor(root)  # raises if _redact.json exists but can't be read
    if not red and state.get(REDACT_KEY):
        raise RuntimeError(f"{REDACT_FILE} was applied before but is missing now, nothing pushed "
                           f"(delete the {REDACT_KEY} entry in {SYNC_STATE} to really stop redacting)")
    note_red = ""
    if red and state.get(REDACT_KEY) != red.sig:
        # New or changed rules: scrub what this machine pushed earlier, in place.
        n_seen, n_changed = redact_folder(root, HOST, red)
        state[REDACT_KEY] = red.sig
        note_red = f"; redacted {n_changed}/{n_seen} earlier files"
    for r in local_rows(None):
        ent, st, n_pushed, n_raw, n_packed, gone = _push_session(
            r, only, dest, search, state, cz, red)
        if ent is None:
            continue
        pushed += n_pushed
        raw += n_raw
        packed += n_packed
        doomed += gone
        state[r["path"]] = ent
        if ent.get("chunks"):
            link = {"parent": r["parent"], "agent": r["agent"],
                    **({"parent_src": r["parent_src"]} if r.get("parent_src") else {})}
            entries.append({
                "src": r["src"], "id": r["id"], "cloud_rel": ent["chunks"][0], "chunks": ent["chunks"],
                **({"text": ent["text"]} if ent.get("text") else {}),
                **(link if r.get("parent") else {}),
                "title": r["title"], "has_title": r["has_title"], "cwd": r["cwd"],
                "first_user": r["first_user"][:500], "bytes": st.st_size, "mtime": r["mtime"],
            })
    # Keep entries for sessions already pushed but since deleted locally.
    try:
        old = json.loads((dest / "_index.json").read_text(encoding="utf-8")).get("sessions", [])
    except (OSError, ValueError):
        old = []
    have = {(e["src"], e["id"]) for e in entries}
    entries += [e for e in old if (e.get("src"), e.get("id")) not in have]
    if red:
        for e in entries:
            for k in ("title", "first_user", "cwd"):
                if isinstance(e.get(k), str):
                    e[k] = red.text(e[k])
    dest.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"host": HOST, "format": 3, "updated": datetime.now().isoformat(timespec="seconds"),
                       "sessions": entries}, ensure_ascii=False).encode("utf-8")
    # _search/<HOST>/ holds everything a search reads (index + dialogue text), so the
    # user can pin that one folder offline and leave raw chunks on-demand. The copy in
    # <HOST>/ keeps machines still on the older version working.
    write_atomic(search / "index.json", body)
    write_atomic(dest / "_index.json", body)
    # Superseded chunks go only after the index stops pointing at them.
    for x in doomed:
        try:
            x.unlink()
        except OSError:
            pass
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SYNC_STATE.write_text(json.dumps(state), encoding="utf-8")
    note = rclone_push(root, WRITTEN, doomed)
    msg = (f"{HOST}: {len(entries)} indexed, {pushed} pushed "
           f"({raw/1e6:.2f} MB -> {packed/1e6:.2f} MB) in {time.time()-t0:.1f}s -> {dest}{note}{note_red}")
    return pushed, msg


def locked_sync(only=None, wait: float = 300) -> tuple[int, str]:
    lock = FileLock("sync.lock")
    if not lock.acquire(wait):
        return 0, f"{HOST}: another sync is still running, skipped"
    try:
        return sync_once(only)
    finally:
        lock.release()


NO_SYNC = L("多机同步没有开启（只搜本机不需要它）。要开启：find_session.py sync-root <同步文件夹>，"
            "或设环境变量 SESSION_SYNC_ROOT。",
            "Multi-machine sync is off (searching this machine doesn't need it). To turn it on: "
            "find_session.py sync-root <shared folder>, or set SESSION_SYNC_ROOT.")


def cmd_sync_root(a) -> int:
    """Show, set or clear the SessionSync folder."""
    if a.off:
        SYNC_ROOT_FILE.unlink(missing_ok=True)
        print(L("多机同步已关闭（同步文件夹里已有的文件不动）。", "Multi-machine sync is off (files already in the sync folder are left alone)."))
        return 0
    if a.path:
        root = Path(a.path).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        SYNC_ROOT_FILE.write_text(str(root), encoding="utf-8")
        print(root)
        return 0
    root = cloud_root()
    if not root:
        print(NO_SYNC, file=sys.stderr)
        return 1
    print(root)
    return 0


def cmd_sync(a) -> int:
    try:
        import zstandard  # noqa: F401
    except ImportError:
        print(L("sync 需要 zstandard：用 skill 的 .venv 里的 python 运行", "sync needs `zstandard`; run with the skill's .venv python"))
        return 1
    try:
        _, msg = locked_sync()
    except RuntimeError as e:
        print(e)
        return 1
    log_line(msg)
    if not a.quiet:
        print(msg)
    return 0


def cmd_redact_cloud(a) -> int:
    """Apply _redact.json to what is already in the sync folder. This machine does its
    own folder by itself on the next sync after the rules change; use this for a
    machine that can't (e.g. one that pushes through rclone and keeps no local copy of
    its folder), or to check with --dry-run."""
    root = cloud_root()
    if not root:
        print(NO_SYNC)
        return 1
    try:
        red = load_redactor(root)
    except RuntimeError as e:
        print(e)
        return 1
    if not red:
        print(L(f"{root} 里没有 {REDACT_FILE}，没有可用的规则", f"no {REDACT_FILE} in {root}, nothing to apply"))
        return 1
    hosts = [d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith("_")] if a.all else [(a.host or HOST).upper()]
    rc = 0
    for h in hosts:
        lock = FileLock("sync.lock") if h == HOST else None
        if lock and not lock.acquire(300):
            print(f"{h}: " + L("同步正在进行，跳过", "a sync is running, skipped"))
            rc = 1
            continue
        try:
            seen, changed = redact_folder(root, h, red, dry=a.dry_run)
        finally:
            if lock:
                lock.release()
        verb = L("会改", "would rewrite") if a.dry_run else L("改了", "rewrote")
        print(f"{h}: {L('读了', 'read')} {seen}, {verb} {changed}")
        if not a.dry_run:
            log_line(f"{HOST}: redact-cloud {h}: rewrote {changed}/{seen}")
    return rc


def cmd_redact_add(a) -> int:
    """Add one secret to _redact.json without it ever being shown or saved: it is read
    without echo, and only the first 8 hex digits of its SHA-256 are written."""
    import getpass
    import hashlib
    root = cloud_root()
    if not root:
        print(NO_SYNC)
        return 1
    if not re.fullmatch(r"[\w.-]+", a.name):
        print(L("名字只能用字母、数字、_ . -", "the name may only use letters, digits, _ . -"))
        return 1
    if a.stdin:  # for scripts: one line from stdin, nothing echoed
        secret = sys.stdin.readline().rstrip("\r\n")
    else:
        secret = getpass.getpass(L(f"输入 {a.name} 的值（不显示）: ", f"value of {a.name} (not shown): "))
        if getpass.getpass(L("再输一次: ", "again: ")) != secret:
            print(L("两次不一样，什么都没改", "the two entries differ, nothing changed"))
            return 1
    if len(secret) < 8:
        print(L("少于 8 个字符的值不会被匹配（太容易误伤普通文字），没有添加",
                "values shorter than 8 characters are never matched (too many false hits), not added"))
        return 1
    tag = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8]
    secret = None
    p = root / REDACT_FILE
    try:
        cfg = json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else {"tags": {}, "patterns": True}
    except (OSError, ValueError) as e:
        print(L(f"{p} 读不了，没有改：{e}", f"can't read {p}, nothing changed: {e}"))
        return 1
    tags = cfg.setdefault("tags", {})
    old = tags.get(tag)
    tags[tag] = a.name
    write_atomic(p, (json.dumps(cfg, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    if old and old != a.name:
        print(L(f"这个值原来叫 {old}，现在改名为 {a.name}", f"this value was listed as {old}, renamed to {a.name}"))
    print(L(f"已加入 {p}：{a.name}（哈希前缀 {tag}）。各机器下次同步时会重扫自己已推送的文件。",
            f"added to {p}: {a.name} (hash prefix {tag}). Each machine rescans what it already pushed on its next sync."))
    return 0


def session_sigs() -> dict:
    sigs = {}
    for src in SOURCES.values():
        for p in src.sessions():
            try:
                st = p.stat()
            except OSError:
                continue
            sigs[str(p)] = (st.st_mtime, st.st_size)
    return sigs


def cmd_watch(a) -> int:
    """Stay running; push a session soon after it changes (debounced)."""
    if not cloud_root():
        print(NO_SYNC)
        return 1
    me = FileLock("watch.lock")
    if not me.acquire():
        return 0  # a watcher is already running; the scheduled task just retries later
    log_line(f"{HOST}: watch started v{VERSION} (scan every {a.interval}s)")
    snap, pending, last_sweep, last_check = {}, {}, 0.0, time.time()
    while True:
        try:
            now = time.time()
            if now - last_check >= UPDATE_CHECK_SECS:
                last_check = now
                spread_skill()
                if self_update():
                    # Exit so the new code runs: the scheduled task relaunches the
                    # watcher within its 5-minute repeat, and that one starts with a
                    # full pass, so nothing pending is lost.
                    log_line(f"{HOST}: watch exiting to restart on the new version")
                    me.release()
                    return 0
            if now - last_sweep >= SWEEP_SECS:
                snap = session_sigs()
                pending.clear()
                pushed, msg = locked_sync(None)
                last_sweep = now
                log_line(msg)
            else:
                cur = session_sigs()
                for path, sig in cur.items():
                    if snap.get(path) != sig:
                        pending[path] = (pending.get(path, (now,))[0], now)
                snap = cur
                ready = {p for p, (first, last) in pending.items()
                         if now - last >= QUIET_SECS or now - first >= MAX_WAIT_SECS}
                if ready:
                    pushed, msg = locked_sync(ready)
                    for p in ready:
                        pending.pop(p, None)
                    if pushed:
                        log_line(msg)
        except Exception as e:  # keep the watcher alive; the next pass retries
            log_line(f"{HOST}: watch error: {e!r}")
        time.sleep(a.interval)


def main() -> int:
    ap = argparse.ArgumentParser(description=L("查找和读取 agent 会话（wb / cx / cl / cb / qd），包括本机和同步来的其他机器",
                                             "Find and read agent sessions (wb / cx / cl / cb / qd), local and synced from other machines"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help=L("按时间倒序列出会话", "list sessions, newest first"))
    p.add_argument("--source", default="all", help="all|wb|cx|cl|cb|qd|ag")
    p.add_argument("--host", default="all", help=L("all|local|<电脑名>（同步来的其他机器）", "all|local|<computer name> (other machines, synced)"))
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--subagents", action="store_true", help=L("把子代理会话也列出来/一起搜（默认隐藏，挂在主会话下）", "include sub-agent sessions (hidden by default, linked to their parent)"))
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("find", help=L("按标题/首问关键词搜索", "search titles and first prompts"))
    p.add_argument("keywords", nargs="+")
    p.add_argument("--source", default="all", help="all|wb|cx|cl|cb|qd|ag")
    p.add_argument("--host", default="all", help=L("all|local|<电脑名>（同步来的其他机器）", "all|local|<computer name> (other machines, synced)"))
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--subagents", action="store_true", help=L("把子代理会话也列出来/一起搜（默认隐藏，挂在主会话下）", "include sub-agent sessions (hidden by default, linked to their parent)"))
    p.set_defaults(fn=cmd_find)

    p = sub.add_parser("grep", help=L("按对话内容全文搜索（标题不记得时用）；所有关键词都要在同一会话中出现", "full-text search of what was said; every word must occur in the same session"))
    p.add_argument("keywords", nargs="+")
    p.add_argument("--source", default="all", help="all|wb|cx|cl|cb|qd|ag")
    p.add_argument("--host", default="all", help=L("all|local|<电脑名>", "all|local|<computer name>"))
    p.add_argument("--role", choices=["user", "assistant", "all"], default="all")
    p.add_argument("--limit", type=int, default=8, help=L("最多列几个会话", "max sessions to list"))
    p.add_argument("--per", type=int, default=3, help=L("每个会话最多几条命中", "max hits per session"))
    p.add_argument("--context", type=int, default=80, help=L("命中前后各显示多少字", "characters of context around each hit"))
    p.add_argument("--deep", action="store_true",
                   help=L("连工具调用/输出一起搜（读原始记录；云端会话会被整份下载）", "also search tool calls and output (reads raw transcripts; synced ones get downloaded)"))
    p.add_argument("--subagents", action="store_true", help=L("把子代理会话也列出来/一起搜（默认隐藏，挂在主会话下）", "include sub-agent sessions (hidden by default, linked to their parent)"))
    p.set_defaults(fn=cmd_grep)

    p = sub.add_parser("show", help=L("读取某个会话（默认只给人类说的话，截断输出）", "read a session (what the human said by default, truncated)"))
    p.add_argument("session", help=L("'cx|<id>' 或裸 id 前缀", "'cx|<id>' or a bare id prefix"))
    p.add_argument("--role", choices=["user", "assistant", "all"], default="user")
    p.add_argument("--head", type=int, default=20)
    p.add_argument("--tail", type=int, default=0, help=L("只给最后 N 条（忽略 --head）；接着某个会话干活时用", "only the last N messages (overrides --head); for continuing a session"))
    p.add_argument("--max-chars", type=int, default=600)
    p.add_argument("--grep", default=None)
    p.add_argument("--host", default="all", help=L("all|local|<电脑名>", "all|local|<computer name>"))
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("files", help=L("列出会话中出现过的文件路径", "list file paths the session mentions"))
    p.add_argument("session")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--host", default="all", help=L("all|local|<电脑名>", "all|local|<computer name>"))
    p.set_defaults(fn=cmd_files)

    p = sub.add_parser("sync", help=L("把本机会话压缩推送到同步文件夹 SessionSync/<电脑名>（需先 sync-root）", "push this machine's sessions to SessionSync/<computer name> (needs sync-root)"))
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(fn=cmd_sync)

    p = sub.add_parser("sync-root", help=L("查看/设置/关闭多机同步文件夹（不设置就只用本机）", "show / set / turn off the multi-machine sync folder (unset = this machine only)"))
    p.add_argument("path", nargs="?", help=L("同步文件夹，例如 OneDrive 下的 SessionSync", "the shared folder, e.g. SessionSync in OneDrive"))
    p.add_argument("--off", action="store_true", help=L("关闭同步", "turn sync off"))
    p.set_defaults(fn=cmd_sync_root)

    p = sub.add_parser("redact-add", help=L("把一个密钥加入脱敏名单（输入时不显示，只存哈希前缀）", "add a secret to the redaction list (typed without echo, only a hash prefix is stored)"))
    p.add_argument("name", help=L("替换后显示的名字，例如 vps-root-password", "what it is replaced with, e.g. vps-root-password"))
    p.add_argument("--stdin", action="store_true", help=L("从标准输入读一行（给脚本用）", "read one line from stdin (for scripts)"))
    p.set_defaults(fn=cmd_redact_add)

    p = sub.add_parser("redact-cloud", help=L("按同步文件夹里的 _redact.json 把已推送的副本就地脱敏", "apply the sync folder's _redact.json to copies already pushed"))
    p.add_argument("--host", default=None, help=L("哪台机器的文件夹（默认本机）", "whose folder (default: this machine)"))
    p.add_argument("--all", action="store_true", help=L("所有机器的文件夹", "every machine's folder"))
    p.add_argument("--dry-run", action="store_true", help=L("只统计，不改", "count only, change nothing"))
    p.set_defaults(fn=cmd_redact_cloud)

    p = sub.add_parser("watch", help=L("常驻：会话一有变化（停写 1 分钟或最多 5 分钟）就增量推送", "stay running; push a session 1 min after it stops changing (5 min at most)"))
    p.add_argument("--interval", type=int, default=15, help=L("扫描间隔秒数", "seconds between scans"))
    p.set_defaults(fn=cmd_watch)

    spread_skill()
    # Update before parsing, so a flag that only the newer version knows still works.
    if sys.argv[1:2] != ["watch"] and self_update():
        # Rerun this same command with the code just installed.
        import subprocess
        env = {**os.environ, "SESSION_FINDER_NO_UPDATE": "1"}
        return subprocess.call([sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]], env=env)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

# end of find_session.py
