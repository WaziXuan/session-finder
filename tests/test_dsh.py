"""DeepSeek Harness: a zstd-compressed JSONL event stream per session, with the
title and cwd mirrored in session_projcache. Only goal/change and user-role
records with source.kind='user' are the human's words; runtime snapshots, skill
catalogs and tool notices share the user role but must not be indexed."""
from __future__ import annotations

import json
import unittest

import zstandard

from tests.sf_testkit import FinderCase, find_session

SID = "session-11111111-2222-4333-8444-555555555555"
OTHER = "session-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def write_ds(root, sid, objective=None, user_text=None, answer=None,
             title=None, cwd="D:\\Sandbox"):
    d = root / "--D-Sandbox--" / sid
    d.mkdir(parents=True, exist_ok=True)
    base = 1795581618000
    recs = [{"type": "session", "version": 4, "id": sid, "createdAt": base, "cwd": cwd}]
    seq = 1
    if objective:
        recs.append({"type": "goal/change", "seq": seq, "time": base + 1,
                     "data": {"operation": "create", "goal": {"objective": objective}}})
        seq += 1
    if user_text:
        recs.append({"type": "user/message", "seq": seq, "time": base + 2,
                     "data": {"source": {"kind": "user"},
                              "content": [{"type": "text", "text": user_text}]}})
        seq += 1
    # Injected records also arrive with role=user; they must never look like a prompt.
    recs.append({"type": "user/message", "seq": seq, "time": base + 3,
                 "data": {"source": {"kind": "runtime-context"},
                          "content": [{"type": "text", "text": "injected runtime context"}]}})
    seq += 1
    recs.append({"type": "user/message", "seq": seq, "time": base + 4,
                 "data": {"source": {"kind": "skill-catalog"},
                          "content": [{"type": "text", "text": "injected skill catalog"}]}})
    if answer:
        recs.append({"type": "assistant/message", "seq": seq + 1, "time": base + 5,
                     "data": {"message": {"role": "assistant",
                                          "content": [{"type": "text", "text": answer}]}}})
    body = "\n".join(json.dumps(o, ensure_ascii=False) for o in recs) + "\n"
    (d / "session.v4.jsonl.zstd").write_bytes(
        zstandard.ZstdCompressor().compress(body.encode("utf-8")))
    cache = root.parent / "storages" / "session_projcache" / "sessions"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"{sid}.json").write_text(json.dumps({
        "version": 7,
        "record": {"identity": {"cwd": cwd},
                   "rows": {"title": {"ver": 1, "seq": 9, "val": title}}},
    }, ensure_ascii=False), encoding="utf-8")


class DshTest(FinderCase):
    def setUp(self) -> None:
        super().setUp()
        find_session.Dsh._meta = None
        self.addCleanup(setattr, find_session.Dsh, "_meta", None)
        self.root = self.roots["ds"]

    def test_listed_with_cached_title_and_goal_as_first_user(self):
        write_ds(self.root, SID, objective="fix the blender render",
                 title="Render fix", cwd="D:\\Work")
        row = self.only(self.rows("ds"), SID)
        self.assertEqual(row["title"], "Render fix")
        self.assertEqual(row["cwd"], "D:\\Work")
        self.assertEqual(row["first_user"], "fix the blender render")

    def test_show_reads_human_and_answer_but_not_injected_context(self):
        write_ds(self.root, SID, objective="where is the token", answer="in the vault")
        rc, out = self.show(f"ds|{SID[:12]}", role="all")
        self.assertEqual(rc, 0)
        self.assertIn("where is the token", out)
        self.assertIn("in the vault", out)
        self.assertNotIn("injected runtime context", out)
        self.assertNotIn("injected skill catalog", out)

    def test_user_message_is_used_when_there_is_no_goal(self):
        write_ds(self.root, OTHER, user_text="greetings from dsh", title="Greeting")
        row = self.only(self.rows("ds"), OTHER)
        self.assertEqual(row["first_user"], "greetings from dsh")

    def test_grep_finds_answer_text(self):
        write_ds(self.root, SID, objective="small talk", answer="flux capacitor is fine")
        rc, out = self.grep("capacitor", source="ds")
        self.assertEqual(rc, 0)
        self.assertIn("flux capacitor", out)

    def test_sync_pushes_text_excerpt_but_not_the_event_stream(self):
        write_ds(self.root, SID, objective="a shared session", title="Shared")
        self.cloud_on = True
        self.cloud_dir.mkdir(exist_ok=True)
        find_session.sync_once(None)
        index = json.loads((self.cloud_dir / find_session.HOST / "_index.json").read_text("utf-8"))
        ids = {(e["src"], e["id"]) for e in index["sessions"]}
        self.assertIn(("ds", SID), ids)
        cloud_text = list((self.cloud_dir / "_search" / find_session.HOST / "ds").glob("*.txt.zst"))
        self.assertEqual(len(cloud_text), 1)
        with cloud_text[0].open("rb") as f:
            plain = zstandard.ZstdDecompressor().stream_reader(f).read().decode("utf-8")
        self.assertIn("a shared session", plain)
        self.assertNotIn("injected runtime context", plain)


if __name__ == "__main__":
    unittest.main()
