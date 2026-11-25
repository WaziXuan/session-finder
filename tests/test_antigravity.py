"""Antigravity: the jsonl log under brain/, the summary database that owns the title /
workspace / parent link, and the step database used when a conversation's log is gone."""
from __future__ import annotations

import json
import sqlite3
import unittest

from tests.sf_testkit import FinderCase, find_session

CONV = "5b754e44-ae4e-4368-abc8-a74c448cb9e0"
CHILD = "7c1d2e3f-0000-4000-8000-000000000002"
ORPHAN = "9a8b7c6d-0000-4000-8000-000000000003"

SUMMARY_COLS = ("conversation_id", "title", "preview", "step_count", "last_modified_time",
                "workspace_uris", "status", "source", "project_id", "agent_name",
                "parent_conversation_id", "nesting_depth")


def log_path(root, cid):
    return root / "brain" / cid / ".system_generated" / "logs" / "transcript.jsonl"


def write_log(root, cid, first="hi there", answer="all good", chunks=0) -> "Path":
    """One Antigravity conversation log; chunks=N also writes that many checkpoints."""
    records = [
        {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE",
         "created_at": "2026-11-25T04:40:18Z",
         "content": f"<USER_REQUEST>\n{first}\n</USER_REQUEST>\n"
                    "<ADDITIONAL_METADATA>\nThe current local time is: 2026-11-25T12:40:18+08:00.\n"
                    "</ADDITIONAL_METADATA>"},
        {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
         "created_at": "2026-11-25T04:40:23Z", "model": "gemini-4-hydrogen-high",
         "content": answer, "thinking": "thinking about it"},
    ]
    body = "\n".join(json.dumps(o, ensure_ascii=False) for o in records) + "\n"
    for i in range(chunks):
        part = log_path(root, cid).parent / "chunks" / "transcript" / f"{i:08d}.jsonl"
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_text(body, encoding="utf-8")
    path = log_path(root, cid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def write_summaries(root, rows) -> None:
    root.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(root / "conversation_summaries.db")
    con.execute(f"create table conversation_summaries ({', '.join(SUMMARY_COLS)})")
    for r in rows:
        r = {"workspace_uris": "[]", **r}
        con.execute(f"insert into conversation_summaries values "
                    f"({', '.join('?' * len(SUMMARY_COLS))})", [r.get(c) for c in SUMMARY_COLS])
    con.commit()
    con.close()


def _varint(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _field(fn: int, payload) -> bytes:
    if isinstance(payload, int):
        return _varint(fn << 3) + _varint(payload)
    return _varint(fn << 3 | 2) + _varint(len(payload)) + payload


def step_blob(step_type: int, text: str, secs: int = 1795581618) -> bytes:
    stamp = _field(1, secs) + _field(2, 0)
    if step_type == 14:
        body = _field(19, _field(2, text.encode()))
    else:
        body = _field(20, _field(1, text.encode()))
    return _field(1, step_type) + _field(4, 3) + _field(5, stamp) + body


def write_steps_db(root, cid, steps) -> "Path":
    """A conversation whose jsonl log is gone: only the per-conversation step db is left."""
    conv = root / "conversations"
    conv.mkdir(parents=True, exist_ok=True)
    path = conv / f"{cid}.db"
    con = sqlite3.connect(path)
    con.execute("create table steps (idx integer primary key, step_type integer, status integer,"
                " has_subtrajectory numeric, metadata blob, error_details blob,"
                " permissions blob, task_details blob, render_info blob, step_payload blob,"
                " step_format integer)")
    for i, (step_type, text) in enumerate(steps):
        con.execute("insert into steps values (?,?,?,?,?,?,?,?,?,?,?)",
                    (i, step_type, 3, 0, None, None, None, None, None,
                     step_blob(step_type, text), 0))
    con.commit()
    con.close()
    return path


class AntigravityTest(FinderCase):
    def setUp(self) -> None:
        super().setUp()
        find_session.Agy._db = None
        self.addCleanup(setattr, find_session.Agy, "_db", None)
        self.root = self.roots["ag"]

    def test_log_session_is_listed_with_db_metadata(self):
        write_log(self.root, CONV, first="explain the resampler")
        write_summaries(self.root, [{
            "conversation_id": CONV, "title": "Resampler notes", "preview": "explain the resampler",
            "step_count": 2, "last_modified_time": "2026-11-25 04:40:23.37+00:00",
            "workspace_uris": '["file:///d%3A/Sandbox"]', "status": "CASCADE_RUN_STATUS_IDLE",
        }])
        row = self.only(self.rows("ag"), CONV)
        self.assertEqual(row["title"], "Resampler notes")
        self.assertEqual(row["cwd"], "D:\\Sandbox")
        # the <USER_REQUEST> wrapper and the host context after it are not the human's words
        self.assertEqual(row["first_user"], "explain the resampler")

    def test_show_reads_both_turns_and_ignores_injected_context(self):
        write_log(self.root, CONV, first="what model are you", answer="a hydrogen one")
        write_summaries(self.root, [{"conversation_id": CONV, "title": "Model talk"}])
        rc, out = self.show(f"ag|{CONV[:8]}", role="all")
        self.assertEqual(rc, 0)
        self.assertIn("user      what model are you", out)
        self.assertIn("assistant a hydrogen one", out)
        self.assertNotIn("ADDITIONAL_METADATA", out)
        self.assertNotIn("thinking about it", out)

    def test_checkpointed_chunks_are_not_double_counted(self):
        write_log(self.root, CONV, chunks=2)
        write_summaries(self.root, [{"conversation_id": CONV, "title": "Chunked"}])
        rc, out = self.show(f"ag|{CONV[:8]}", role="all")
        self.assertEqual(rc, 0)
        self.assertEqual(out.count("hi there"), 1)

    def test_grep_finds_a_word_from_the_answer(self):
        write_log(self.root, CONV, answer="the flux capacitor is fine")
        write_summaries(self.root, [{"conversation_id": CONV, "title": "Repairs"}])
        rc, out = self.grep("capacitor", source="ag")
        self.assertEqual(rc, 0)
        self.assertIn("Repairs", out)

    def test_sub_conversation_hides_under_its_parent(self):
        write_log(self.root, CONV, first="lead task")
        write_log(self.root, CHILD, first="spawned task")
        write_summaries(self.root, [
            {"conversation_id": CONV, "title": "Lead"},
            {"conversation_id": CHILD, "title": "Worker", "agent_name": "swarm",
             "parent_conversation_id": CONV, "nesting_depth": 1},
        ])
        rows = self.rows("ag")
        child = self.only(rows, CHILD)
        self.assertEqual(child["parent"], CONV)
        self.assertTrue(find_session.is_sub(child))
        rc, out = self.list_(source="ag")
        self.assertNotIn("Worker", out)
        rc, out = self.show(f"ag|{CONV[:8]}")
        self.assertIn(find_session.tag_of(child), out)

    def test_step_database_is_read_when_the_log_is_gone(self):
        write_summaries(self.root, [{"conversation_id": ORPHAN, "title": "Only a db"}])
        write_steps_db(self.root, ORPHAN, [(14, "where did the log go"),
                                           (15, "into the step database")])
        row = self.only(self.rows("ag"), ORPHAN)
        self.assertEqual(row["title"], "Only a db")
        self.assertEqual(row["first_user"], "where did the log go")
        rc, out = self.show(f"ag|{ORPHAN[:8]}", role="all")
        self.assertEqual(rc, 0)
        self.assertIn("into the step database", out)

    def test_binary_step_database_is_never_pushed(self):
        write_log(self.root, CONV, first="shared one")
        write_summaries(self.root, [{"conversation_id": CONV, "title": "Shared"},
                                    {"conversation_id": ORPHAN, "title": "Local only"}])
        write_steps_db(self.root, ORPHAN, [(14, "kept on this machine")])
        self.assertFalse(find_session.Agy().pushable(self.root / "conversations" / f"{ORPHAN}.db"))
        self.cloud_on = True
        self.cloud_dir.mkdir(exist_ok=True)
        find_session.sync_once(None)
        index = json.loads((self.cloud_dir / find_session.HOST / "_index.json").read_text("utf-8"))
        ids = {(e["src"], e["id"]) for e in index["sessions"]}
        self.assertIn(("ag", CONV), ids)
        self.assertNotIn(("ag", ORPHAN), ids)


if __name__ == "__main__":
    unittest.main()
