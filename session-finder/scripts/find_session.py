#!/usr/bin/env python3
"""Locate and read past agent sessions from four local sources.

  wb      WorkBuddy     %USERPROFILE%\\.workbuddy\\projects\\<proj>\\<sessionId>.jsonl
  cx      Codex         %USERPROFILE%\\.codex\\sessions\\<Y>\\<M>\\<D>\\rollout-*.jsonl[.zst]
                        titles live in %USERPROFILE%\\.codex\\session_index.jsonl
  cl      Claude Code   %USERPROFILE%\\.claude\\projects\\<proj>\\<sessionId>.jsonl
  cb      CodeBuddy CLI ~/.codebuddy/projects/<proj>/<sessionId>.jsonl (same records as wb)

Optional multi-machine sync: `sync` / `watch` push this machine's sessions,
zstd-compressed, to <SessionSync>\\<COMPUTERNAME>\\{wb,cx,cl,cb}\\... plus a small
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
VERSION = "2026.10.30.6"
# The installer puts the script into the first client that has it: ~/.workbuddy,
# else ~/.claude, ~/.codex, ~/.codebuddy. The other clients get only SKILL.md.
CLIENTS = (".workbuddy", ".claude", ".codex", ".codebuddy")
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
        return {"title": title or "", "cwd": cwd or path.parent.name,
                "first_user": first or ""}

    def messages(self, path):
        for line in open_lines(path):
            if '"role"' not in line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
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
        d = self.root / "sessions"
        return sorted(d.rglob("rollout-*.jsonl*")) if d.exists() else []

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
        try:
            for i, line in enumerate(open_lines(path)):
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
        return {"title": title or "", "cwd": cwd or "", "first_user": first or ""}

    def messages(self, path):
        for line in open_lines(path):
            if '"response_item"' not in line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
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
        for line in open_lines(path):
            if '"type"' not in line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
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
        return {
            "title": title or summary or (last_prompt or "")[:60],
            "cwd": cwd or guess_cwd_from_dirname(path.parent.name),
            "first_user": first or "",
        }

    def messages(self, path):
        for line in open_lines(path):
            if '"type"' not in line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
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


SOURCES = {s.key: s for s in (WB(), Codex(), Claude(), CodeBuddy())}
ALIASES = {"all": None, "wb": "wb", "workbuddy": "wb", "cx": "cx", "codex": "cx",
           "cl": "cl", "claude": "cl", "cb": "cb", "codebuddy": "cb"}


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
            if hit and hit.get("m") == st.st_mtime and hit.get("s") == st.st_size:
                meta = hit
            else:
                try:
                    meta = src.scan(p)
                except RuntimeError as e:
                    meta = {"title": "(unreadable)", "cwd": "", "first_user": str(e)}
                meta = {**meta, "m": st.st_mtime, "s": st.st_size}
                cache[key] = meta
            real_title = meta.get("title") or ""
            first = meta.get("first_user") or ""
            if any(first.startswith(m) for m in MACHINE_PROMPTS):
                continue
            rows.append({
                "src": src.key, "host": "", "id": src.sid(p), "path": str(p),
                "title": real_title or (first.splitlines()[0][:50] if first else "(untitled)"),
                "has_title": bool(real_title),
                "cwd": meta.get("cwd") or "",
                "first_user": first,
                "bytes": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
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
            rows.append({**r, "host": d.name, "path": chunks[0], "chunks": chunks, "text": text})
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
    return f"{src}|{r['id'] if full_id else r['id'][:13 if r['src'] == 'cb' else 8]}"


def fmt(rows: list[dict], full_id: bool = False) -> str:
    out = []
    w = max([12] + [len(tag_of(r, full_id)) for r in rows])
    for r in rows:
        tag = tag_of(r, full_id)
        out.append(f"{tag:<{w}} {r['mtime']}  {r['bytes']//1024:>5}KB  {r['title']}")
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
            print(L(f"未知来源前缀：{ref.split('|')[0]}（用 wb / cx / cl / cb，可加 @电脑名）",
                    f"unknown source prefix: {ref.split('|')[0]} (use wb / cx / cl / cb, optionally @HOST)"))
            return None
        hits = [r for r in index(k, host) if r["id"].startswith(sid)]
    else:
        hits = [r for r in index(None, host) if r["id"].startswith(ref)]
    if not hits:
        print(L(f"没有匹配的会话：{ref}", f"no session matched: {ref}"))
        return None
    if len(hits) > 1:
        print(L(f"前缀不唯一，有 {len(hits)} 个候选（用更长的前缀）：\n", f"ambiguous prefix, {len(hits)} candidates:\n"))
        print(fmt(hits[:10]))
        return None
    r = hits[0]
    chunks = [Path(c) for c in r.get("chunks") or [r["path"]]]
    return SOURCES[r["src"]], chunks if len(chunks) > 1 else chunks[0], r.get("host") or HOST


def chunk_label(path) -> str:
    if isinstance(path, list):
        return f"{path[0].name} (+{len(path) - 1} chunks)"
    return path.name


def cmd_list(a) -> int:
    rows = index(norm_source(a.source), a.host)
    print(L(f"# {len(rows)} 个会话（source={a.source}, host={a.host}）\n", f"# {len(rows)} sessions (source={a.source}, host={a.host})\n"))
    print(fmt(rows[: a.limit]))
    return 0


def cmd_find(a) -> int:
    # 每个参数既可当短语（带空格时），也可拆成单词；短语额外加权。
    phrases = [a_.strip().lower() for a_ in a.keywords if " " in a_.strip()]
    words = [w.lower() for a_ in a.keywords for w in a_.split() if w.strip()]
    scored = []
    for r in index(norm_source(a.source), a.host):
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
    print(fmt([r for _, r in scored[: a.limit]]))
    return 0


def dialogue(r: dict, deep: bool = False):
    """Yield (ts, role, text) for one session row, local or cloud.

    Cloud rows read the small .txt.zst extract; deep mode reads raw records
    instead (tool calls and output included), which downloads the transcript.
    """
    if deep:
        target = [Path(c) for c in r["chunks"]] if r.get("chunks") else Path(r["path"])
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
    print(f"# {src.label} @ {host} · {chunk_label(path)}\n")
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
    try:
        for line in open_lines(path):
            for m in FILE_RE.finditer(line):
                p = re.sub(r"\\{2,}", r"\\", m.group(0).rstrip("\\/"))
                if ("\\r" in p or "\\n" in p or "##" in p or " " in p
                        or "://" in p or p.endswith(".")):
                    continue
                counts[p] = counts.get(p, 0) + 1
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


def self_update() -> bool:
    """Install a newer find_session.py (and SKILL.md for every client) from
    SessionSync\\_install over this copy. Returns True if it did.

    Only the installed copy updates itself, never the kit source or _install.
    """
    me = Path(__file__).resolve()
    # An installed copy sits in <client>/skills/session-finder/scripts; a checkout doesn't.
    if os.environ.get("SESSION_FINDER_NO_UPDATE") or SKILL_HOME.parent.name != "skills":
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
            d = HOME / client / "skills" / "session-finder"
            if d.is_dir():
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


def push(p: Path, rel: str, base: Path, st, ent: dict, cz):
    """Upload what is new in one session. Returns (raw bytes, packed bytes, files to delete).

    jsonl sessions only ever grow, so each push is the appended tail since the last
    offset, as its own <rel>.NNN.zst. A rewrite (shorter file, or changed bytes just
    before the offset) starts over; MAX_CHUNKS pieces are merged once the session idles.
    """
    if p.suffix == ".zst":  # finished Codex rollout: copied whole when it changes
        if ent.get("chunks") and ent.get("m") == st.st_mtime and ent.get("s") == st.st_size:
            return 0, 0, []
        data = p.read_bytes()
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
        packed = cz.compress(data[:cut])
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


def push_text(src: Source, p: Path, sid: str, rel: str, base: Path, cz) -> str:
    """Write the session's dialogue only (no tool calls/results) as <id>-<hash>.txt.zst,
    one {"t","r","x"} record per message. Other machines `grep` these instead of
    downloading the raw transcript, which is ~99% tool output. Flat short names:
    mirroring Codex's dated folders plus a double-UUID filename overflows MAX_PATH."""
    import hashlib
    lines = [json.dumps({"t": ts, "r": role, "x": txt}, ensure_ascii=False)
             for ts, role, txt in src.messages(p)]
    name = f"{sid}-{hashlib.sha1(rel.encode('utf-8')).hexdigest()[:6]}.txt.zst"
    write_atomic(base / name, cz.compress(("\n".join(lines) + "\n").encode("utf-8")))
    return name


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
    for r in local_rows(None):
        src = SOURCES[r["src"]]
        p = Path(r["path"])
        base = dest / r["src"]
        try:
            st = p.stat()
            rel = p.relative_to(src.root).as_posix()
        except (OSError, ValueError):
            continue
        ent = state.get(r["path"])
        if not isinstance(ent, dict):
            ent = adopt_v1(ent, p, rel, base)
        if only is None or r["path"] in only:
            try:
                n_raw, n_packed, gone = push(p, rel, base, st, ent, cz)
            except OSError as e:
                log_line(f"{HOST}: skip {p}: {e}")
            else:
                pushed += bool(n_raw)
                raw += n_raw
                packed += n_packed
                doomed += gone
            stale = ent.get("text_sig") != [st.st_mtime, st.st_size] or not ent.get("text", "").startswith("_search/")
            if ent.get("chunks") and stale:
                try:
                    name = push_text(src, p, r["id"], rel, search / r["src"], cz)
                    if ent.get("text") and not ent["text"].startswith("_search/"):
                        doomed.append(base / ent["text"])  # pre-_search location
                    ent["text"] = f"_search/{HOST}/{r['src']}/{name}"
                    ent["text_sig"] = [st.st_mtime, st.st_size]
                except (OSError, RuntimeError) as e:
                    log_line(f"{HOST}: text extract failed {p}: {e}")
        state[r["path"]] = ent
        if ent.get("chunks"):
            entries.append({
                "src": r["src"], "id": r["id"], "cloud_rel": ent["chunks"][0], "chunks": ent["chunks"],
                **({"text": ent["text"]} if ent.get("text") else {}),
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
           f"({raw/1e6:.2f} MB -> {packed/1e6:.2f} MB) in {time.time()-t0:.1f}s -> {dest}{note}")
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
    ap = argparse.ArgumentParser(description=L("查找和读取 agent 会话（wb / cx / cl / cb），包括本机和同步来的其他机器",
                                             "Find and read agent sessions (wb / cx / cl / cb), local and synced from other machines"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help=L("按时间倒序列出会话", "list sessions, newest first"))
    p.add_argument("--source", default="all", help="all|wb|cx|cl|cb")
    p.add_argument("--host", default="all", help=L("all|local|<电脑名>（同步来的其他机器）", "all|local|<computer name> (other machines, synced)"))
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("find", help=L("按标题/首问关键词搜索", "search titles and first prompts"))
    p.add_argument("keywords", nargs="+")
    p.add_argument("--source", default="all", help="all|wb|cx|cl|cb")
    p.add_argument("--host", default="all", help=L("all|local|<电脑名>（同步来的其他机器）", "all|local|<computer name> (other machines, synced)"))
    p.add_argument("--limit", type=int, default=8)
    p.set_defaults(fn=cmd_find)

    p = sub.add_parser("grep", help=L("按对话内容全文搜索（标题不记得时用）；所有关键词都要在同一会话中出现", "full-text search of what was said; every word must occur in the same session"))
    p.add_argument("keywords", nargs="+")
    p.add_argument("--source", default="all", help="all|wb|cx|cl|cb")
    p.add_argument("--host", default="all", help=L("all|local|<电脑名>", "all|local|<computer name>"))
    p.add_argument("--role", choices=["user", "assistant", "all"], default="all")
    p.add_argument("--limit", type=int, default=8, help=L("最多列几个会话", "max sessions to list"))
    p.add_argument("--per", type=int, default=3, help=L("每个会话最多几条命中", "max hits per session"))
    p.add_argument("--context", type=int, default=80, help=L("命中前后各显示多少字", "characters of context around each hit"))
    p.add_argument("--deep", action="store_true",
                   help=L("连工具调用/输出一起搜（读原始记录；云端会话会被整份下载）", "also search tool calls and output (reads raw transcripts; synced ones get downloaded)"))
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

    p = sub.add_parser("watch", help=L("常驻：会话一有变化（停写 1 分钟或最多 5 分钟）就增量推送", "stay running; push a session 1 min after it stops changing (5 min at most)"))
    p.add_argument("--interval", type=int, default=15, help=L("扫描间隔秒数", "seconds between scans"))
    p.set_defaults(fn=cmd_watch)

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
