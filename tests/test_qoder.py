"""Qoder CN: Claude Code style transcripts plus the app's SQLite metadata."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tests.sf_testkit import FinderCase, cl_session, find_session, wb_session

QD_NATIVE = "4c72b28b-8c1c-4eca-a456-d580317bdad7"
QD_IMPORT = "fc1cf712-6f6f-4dbd-8050-ea4b5a49a7b9"
QD_CONTINUED = "eb29274a-3203-48ec-ad40-1fa68449baa1"
QD_SIDE = "6d753cec-0a6d-44b5-a444-403103f75eb0"
WB_ORIGINAL = "2fa4e9f4-204c-4f81-b69c-d1e25166674b"


def qd_session(root, sid, first, native=True, project="D--Sandbox") -> Path:
    """A Qoder transcript; a prompt typed in Qoder carries `humanInput`."""
    path = cl_session(root, sid, project=project, first=first, cwd="D:\\Sandbox")
    if native:
        rec = {"type": "user", "cwd": "D:\\Sandbox", "timestamp": "2026-11-20T11:00:00Z",
               "humanInput": {"text": "go on", "mode": "prompt"},
               "message": {"role": "user", "content": [{"type": "text", "text": "go on"}]}}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    return path


def make_main_db(app: Path, rows) -> None:
    con = sqlite3.connect(app / "main.sqlite")
    con.execute("create table chat_sessions (session_id text primary key, title text not null,"
                " cwd text not null, session_kind text not null default 'standard',"
                " owner_session_id text, deleted_at timestamp)")
    con.executemany("insert into chat_sessions (session_id, title, cwd, session_kind,"
                    " owner_session_id) values (?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()


def make_migration_db(app: Path, rows) -> None:
    con = sqlite3.connect(app / "sessionMigration.sqlite")
    con.execute("create table migration_ledger (source_product text, source_ref text,"
                " target_session_id text, status text)")
    con.executemany("insert into migration_ledger values (?, ?, ?, ?)", rows)
    con.commit()
    con.close()


class QoderFixture(FinderCase):
    """Four Qoder sessions (native, untouched import, continued import, side chat),
    the WorkBuddy original of the continued one, and both app databases."""

    def setUp(self) -> None:
        super().setUp()
        root = self.roots["qd"]
        qd_session(root, QD_NATIVE, first="check the sync root")
        qd_session(root, QD_IMPORT, first="find my dorm chat", native=False)
        qd_session(root, QD_CONTINUED, first="install the thing", native=False)
        # the user then typed one more prompt in Qoder
        p = root / "D--Sandbox" / f"{QD_CONTINUED}.jsonl"
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "humanInput": {"text": "more"},
                                "message": {"role": "user", "content": "more"}}) + "\n")
        qd_session(root, QD_SIDE, first="quick side question")
        wb_session(self.roots["wb"], WB_ORIGINAL, first="install the thing", title="Install")
        make_main_db(self.qoder_app, [
            (QD_NATIVE, "检查同步根目录", "D:\\Sandbox", "standard", None),
            (QD_IMPORT, "Dorm chat", "E:\\HKU", "standard", None),
            (QD_CONTINUED, "Install CodeQoder", "D:\\Sandbox", "standard", None),
            (QD_SIDE, "Side q", "D:\\Sandbox", "sideChat", QD_NATIVE),
        ])
        make_migration_db(self.qoder_app, [
            ("codex", "01a1a63d-5f83-7fa2-adb6-a583716f5c52", QD_IMPORT, "imported"),
            ("workbuddy", WB_ORIGINAL, QD_CONTINUED, "imported"),
            ("codex", "0000", "never-written", "failed"),
        ])


class QoderTest(QoderFixture):
    def test_aliases_and_label(self):
        self.assertEqual(find_session.norm_source("qoder"), "qd")
        self.assertEqual(find_session.SOURCES["qd"].label, "Qoder")

    def test_title_and_cwd_come_from_the_app_database(self):
        row = self.only(self.rows("qd"), QD_NATIVE)
        self.assertEqual(row["title"], "检查同步根目录")
        self.assertTrue(row["has_title"])
        self.assertEqual(row["first_user"], "check the sync root")
        self.assertEqual(self.only(self.rows("qd"), QD_IMPORT)["cwd"], "E:\\HKU")

    def test_untouched_import_is_hidden_and_linked_to_the_original(self):
        row = self.only(self.rows("qd"), QD_IMPORT)
        self.assertEqual(row["agent"], "import")
        self.assertEqual(row["parent_src"], "cx")
        self.assertTrue(find_session.is_sub(row))
        rc, out = self.list_(source="qd")
        self.assertNotIn("Dorm chat", out)
        rc, out = self.list_(source="qd", subagents=True)
        self.assertIn("[import] Dorm chat", out)
        self.assertIn("imported copy of cx|01a1a63d", out)

    def test_continued_import_is_listed(self):
        row = self.only(self.rows("qd"), QD_CONTINUED)
        self.assertEqual(row["agent"], "continued")
        self.assertFalse(find_session.is_sub(row))
        rc, out = self.list_(source="qd")
        self.assertIn("[continued] Install CodeQoder", out)

    def test_original_session_shows_its_qoder_copy(self):
        rc, out = self.list_()
        self.assertIn("1 Qoder import(s)", out)
        rc, out = self.show(f"wb|{WB_ORIGINAL}")
        self.assertEqual(rc, 0)
        self.assertIn(f"qd|{QD_CONTINUED[:8]}", out)

    def test_side_chat_hangs_under_its_owner(self):
        row = self.only(self.rows("qd"), QD_SIDE)
        self.assertEqual((row["parent"], row["agent"]), (QD_NATIVE, "side"))
        self.assertFalse(find_session.is_sub(row))

    def test_works_without_the_app_databases(self):
        for f in self.qoder_app.iterdir():
            f.unlink()
        find_session.Qoder._db = None
        row = self.only(self.rows("qd"), QD_IMPORT)
        self.assertEqual(row["title"], "find my dorm chat")
        self.assertFalse(row["parent"])

    def test_show_reads_dialogue(self):
        rc, out = self.show(f"qd|{QD_NATIVE[:8]}", role="all")
        self.assertEqual(rc, 0)
        self.assertIn("# Qoder @", out)
        self.assertIn("check the sync root", out)
        self.assertIn("go on", out)

    def test_memory_and_state_files_are_not_sessions(self):
        extra = self.roots["qd"] / "D--Sandbox" / QD_NATIVE / "compression-v2"
        extra.mkdir(parents=True)
        (extra / "log.jsonl").write_text("{}\n", encoding="utf-8")
        ids = {r["id"] for r in self.rows("qd")}
        self.assertNotIn("log", ids)
        self.assertEqual(len(ids), 4)


try:
    import zstandard  # noqa: F401
    HAVE_ZSTD = True
except ImportError:  # pragma: no cover
    HAVE_ZSTD = False


class QoderSyncTest(QoderFixture):
    def setUp(self) -> None:
        if not HAVE_ZSTD:
            self.skipTest("sync needs zstandard")
        super().setUp()
        self.cloud_on = True
        self.cloud_dir.mkdir()

    def test_qoder_sessions_are_pushed_with_title_and_link(self):
        find_session.sync_once(None)
        host = self.cloud_dir / find_session.HOST
        entries = json.loads((host / "_index.json").read_text(encoding="utf-8"))["sessions"]
        qd = {e["id"]: e for e in entries if e["src"] == "qd"}
        self.assertEqual(set(qd), {QD_NATIVE, QD_IMPORT, QD_CONTINUED, QD_SIDE})
        self.assertEqual(qd[QD_NATIVE]["title"], "检查同步根目录")
        self.assertEqual((qd[QD_IMPORT]["parent_src"], qd[QD_IMPORT]["agent"]), ("cx", "import"))
        self.assertTrue(any((host / "qd").rglob(f"{QD_NATIVE}.jsonl.*.zst")))
