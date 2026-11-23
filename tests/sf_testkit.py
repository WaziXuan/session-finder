"""Shared fixture for the session-finder tests.

Every source root, the metadata cache and the sync state live under a fresh temp
directory, and cloud_root() is stubbed out, so a test run never touches the real
~/.claude, ~/.codex, ~/.workbuddy or ~/.codebuddy trees.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["SESSION_FINDER_NO_UPDATE"] = "1"
os.environ["SESSION_FINDER_LANG"] = "en"  # deterministic L() strings in output checks
os.environ.pop("SESSION_SYNC_ROOT", None)

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "session-finder" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import find_session  # noqa: E402  (must come after the env vars above)

_ORIG_CLOUD_ROOT = find_session.cloud_root

CL_PARENT = "db444e85-f5d6-5a72-a39b-8cb176c40190"
CB_CHILD = "0193b2c4-1111-7000-8000-000000000001"
CB_SIBLING = "0193b2c4-2222-7000-8000-000000000002"
CB_PARENT = "0193b2c4-3333-7000-8000-000000000003"
CX_CHILD = "0193b2c4-4444-7000-8000-000000000004"

SHOW_ARGS = dict(session="", host="local", role="user", head=20, tail=0,
                 max_chars=600, grep=None)
LIST_ARGS = dict(source="all", host="local", limit=15, subagents=False)
FIND_ARGS = dict(keywords=[], source="all", host="local", limit=8, subagents=False)
GREP_ARGS = dict(keywords=[], source="all", host="local", role="all", limit=8,
                 per=3, context=80, deep=False, subagents=False)


def _write(path: Path, records: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(o, ensure_ascii=False) for o in records) + "\n"
    path.write_text(body, encoding="utf-8")
    return path


def write_wb(path: Path, first="hello from user", title=None, cwd="/work") -> Path:
    """A WorkBuddy / CodeBuddy transcript (both clients write the same records)."""
    records = []
    if title is not None:
        records.append({"type": "ai-title", "aiTitle": title, "cwd": cwd})
    records.append({"role": "user", "content": [{"type": "text", "text": first}],
                    "timestamp": "2026-11-20T10:00:00Z", "cwd": cwd})
    records.append({"role": "assistant", "content": [{"type": "text", "text": "ok"}],
                    "timestamp": "2026-11-20T10:00:05Z", "cwd": cwd})
    return _write(path, records)


def wb_session(root, sid, project="proj", **kw) -> Path:
    return write_wb(Path(root) / project / f"{sid}.jsonl", **kw)


def write_cl(path: Path, first="hello from user", title=None, cwd="/work") -> Path:
    records = []
    if title is not None:
        records.append({"type": "summary", "summary": title})
    records.append({"type": "user", "cwd": cwd, "timestamp": "2026-11-20T10:00:00Z",
                    "message": {"role": "user",
                                "content": [{"type": "text", "text": first}]}})
    records.append({"type": "assistant", "cwd": cwd, "timestamp": "2026-11-20T10:00:05Z",
                    "message": {"role": "assistant",
                                "content": [{"type": "text", "text": "ok"}]}})
    return _write(path, records)


def cl_session(root, sid, project="proj-cl", **kw) -> Path:
    return write_cl(Path(root) / project / f"{sid}.jsonl", **kw)


def cl_subagent(root, parent_id, name="agent-a1b2c3", project="proj-cl",
                description="Look around", agent="explore") -> Path:
    path = Path(root) / project / parent_id / "subagents" / f"{name}.jsonl"
    meta = path.with_name(name + ".meta.json")
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({"agentType": agent, "description": description}),
                    encoding="utf-8")
    return write_cl(path, first="explore the repo")


def cx_session(root, sid=CX_CHILD, first="hello from user", cwd="/work") -> Path:
    name = f"rollout-2026-11-20T10-00-00-{sid}"
    path = Path(root) / "sessions" / "2026" / "11" / "20" / f"{name}.jsonl"
    records = [
        {"timestamp": "2026-11-20T10:00:00Z", "type": "session_meta",
         "payload": {"id": sid, "cwd": cwd}},
        {"timestamp": "2026-11-20T10:00:00Z", "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": first}]}},
        {"timestamp": "2026-11-20T10:00:05Z", "type": "response_item",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "ok"}]}},
    ]
    _write(path, records)
    idx = Path(root) / "session_index.jsonl"
    idx.write_text(json.dumps({"id": sid, "thread_name": "Codex title"}) + "\n",
                   encoding="utf-8")
    return path


def sidecar_path(transcript) -> Path:
    """<sid>.parent.json next to the transcript; sid drops .jsonl / .jsonl.zst."""
    path = Path(transcript)
    name = path.name
    for suffix in (".jsonl.zst", ".jsonl"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return path.with_name(name + ".parent.json")


def write_sidecar(transcript, **fields) -> Path:
    return write_sidecar_raw(transcript, json.dumps(fields, ensure_ascii=False))


def write_sidecar_raw(transcript, text: str) -> Path:
    path = sidecar_path(transcript)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def ns(defaults: dict, **kw) -> argparse.Namespace:
    return argparse.Namespace(**{**defaults, **kw})


class FinderCase(unittest.TestCase):
    """Temp roots for all four sources, temp cache, no multi-machine sync."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="sf-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        state = self.tmp / "state"
        state.mkdir()
        self._saved = {
            "CACHE": find_session.CACHE,
            "STATE_DIR": find_session.STATE_DIR,
            "SYNC_STATE": find_session.SYNC_STATE,
            "SYNC_LOG": find_session.SYNC_LOG,
            "SYNC_ROOT_FILE": find_session.SYNC_ROOT_FILE,
            "roots": {k: s.root for k, s in find_session.SOURCES.items()},
        }
        self.addCleanup(self._restore)
        find_session.CACHE = state / "index.json"
        find_session.STATE_DIR = state
        find_session.SYNC_STATE = state / "sync_state.json"
        find_session.SYNC_LOG = state / "sync.log"
        find_session.SYNC_ROOT_FILE = state / "sync_root"
        self.cloud_dir = self.tmp / "SessionSync"
        self.cloud_on = False
        find_session.cloud_root = self._cloud_root
        find_session.Codex._titles = None
        find_session.LAST_RESOLVED.clear()
        self.roots = {}
        for key, src in find_session.SOURCES.items():
            root = self.tmp / "roots" / key
            root.mkdir(parents=True, exist_ok=True)
            src.root = root
            self.roots[key] = root

    def _restore(self) -> None:
        for key in ("CACHE", "STATE_DIR", "SYNC_STATE", "SYNC_LOG", "SYNC_ROOT_FILE"):
            setattr(find_session, key, self._saved[key])
        find_session.cloud_root = _ORIG_CLOUD_ROOT
        for key, src in find_session.SOURCES.items():
            src.root = self._saved["roots"][key]
        find_session.Codex._titles = None
        find_session.LAST_RESOLVED.clear()

    def _cloud_root(self):
        return self.cloud_dir if self.cloud_on else None

    def rows(self, only=None) -> list:
        return find_session.local_rows(only)

    def only(self, rows, sid):
        hits = [r for r in rows if r["id"] == sid]
        self.assertEqual(len(hits), 1, f"expected exactly one row for {sid}, got {len(hits)}")
        return hits[0]

    def call(self, cmd, defaults: dict, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cmd(ns(defaults, **kw))
        return rc, buf.getvalue()

    def show(self, session, **kw):
        return self.call(find_session.cmd_show, SHOW_ARGS, session=session, **kw)

    def list_(self, **kw):
        return self.call(find_session.cmd_list, LIST_ARGS, **kw)

    def find(self, *keywords, **kw):
        return self.call(find_session.cmd_find, FIND_ARGS,
                         keywords=list(keywords), **kw)

    def grep(self, *keywords, **kw):
        return self.call(find_session.cmd_grep, GREP_ARGS,
                         keywords=list(keywords), **kw)
