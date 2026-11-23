"""Sync side: _index.json carries parent_src, cloud_rows keeps it and the
grouping/tag rules hold for sessions read from another machine."""
from __future__ import annotations

import json
import shutil
import unittest

from tests.sf_testkit import (CB_CHILD, CL_PARENT, FinderCase, cl_session,
                              find_session, wb_session, write_sidecar)

try:
    import zstandard  # noqa: F401
    HAVE_ZSTD = True
except ImportError:  # pragma: no cover - the sync path needs zstd chunks
    HAVE_ZSTD = False

REMOTE = "REMOTE-A"


def cloud_entry(src, sid, **kw) -> dict:
    entry = {
        "src": src,
        "id": sid,
        "cloud_rel": f"proj/{sid}.jsonl",
        "chunks": [f"proj/{sid}.jsonl"],
        "title": f"{src} session",
        "has_title": True,
        "cwd": "/work",
        "first_user": "hello",
        "bytes": 512,
        "mtime": "2026-11-20 10:00",
    }
    entry.update(kw)
    return entry


class CloudRowsTest(FinderCase):
    def setUp(self) -> None:
        super().setUp()
        self.cloud_on = True
        self.cloud_dir.mkdir()
        if find_session.HOST.upper() == REMOTE:
            self.skipTest(f"this machine is named {REMOTE}")

    def write_index(self, host: str, sessions: list) -> None:
        folder = self.cloud_dir / host
        folder.mkdir(parents=True, exist_ok=True)
        body = {"host": host, "format": 3, "sessions": sessions}
        (folder / "_index.json").write_text(json.dumps(body), encoding="utf-8")

    def test_cloud_rows_keep_parent_src(self):
        self.write_index(REMOTE, [
            cloud_entry("cl", CL_PARENT, title="Claude lead"),
            cloud_entry("cb", CB_CHILD, parent=CL_PARENT, parent_src="cl",
                        agent="ds:impl", title="Implementation"),
        ])
        rows = find_session.cloud_rows()
        child = self.only(rows, CB_CHILD)
        self.assertEqual(child.get("parent"), CL_PARENT)
        self.assertEqual(child.get("parent_src"), "cl")
        self.assertEqual(child.get("agent"), "ds:impl")
        self.assertTrue(find_session.is_sub(child))

    def test_cloud_child_groups_under_the_cloud_parent(self):
        self.write_index(REMOTE, [
            cloud_entry("cl", CL_PARENT, title="Claude lead"),
            cloud_entry("cb", CB_CHILD, parent=CL_PARENT, parent_src="cl",
                        agent="ds:impl"),
        ])
        rows = find_session.cloud_rows()
        parent = self.only(rows, CL_PARENT)
        child = self.only(rows, CB_CHILD)
        kids = find_session.children_of(rows)
        self.assertEqual([k["id"] for k in find_session.kids_for(kids, parent)],
                         [CB_CHILD])
        self.assertEqual(find_session.parent_tag(child), find_session.tag_of(parent))

    def test_cloud_entry_without_parent_src_uses_its_own_source(self):
        self.write_index(REMOTE, [
            cloud_entry("cl", CL_PARENT, title="Claude lead"),
            cloud_entry("cl", "agent-a1b2c3", parent=CL_PARENT, agent="subagent"),
        ])
        rows = find_session.cloud_rows()
        sub = self.only(rows, "agent-a1b2c3")
        self.assertFalse(sub.get("parent_src"))
        kids = find_session.children_of(rows)
        self.assertEqual(
            [k["id"] for k in find_session.kids_for(kids, self.only(rows, CL_PARENT))],
            ["agent-a1b2c3"])

    def test_plain_cloud_row_has_no_parent(self):
        self.write_index(REMOTE, [cloud_entry("cb", CB_CHILD)])
        row = self.only(find_session.cloud_rows(), CB_CHILD)
        self.assertFalse(row.get("parent"))
        self.assertFalse(find_session.is_sub(row))


@unittest.skipUnless(HAVE_ZSTD, "sync writes zstd chunks")
class SyncRoundTripTest(FinderCase):
    def setUp(self) -> None:
        super().setUp()
        self.cloud_on = True
        self.cloud_dir.mkdir()
        if find_session.HOST.upper() == REMOTE:
            self.skipTest(f"this machine is named {REMOTE}")
        cl_session(self.roots["cl"], CL_PARENT, title="Claude lead", first="lead asked")
        child = wb_session(self.roots["cb"], CB_CHILD, first="directed task")
        write_sidecar(child, src="cl", id=CL_PARENT, agent="ds:impl",
                      title="Implementation")

    def pushed_index(self) -> dict:
        find_session.sync_once(None)
        path = self.cloud_dir / find_session.HOST / "_index.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def push_as_remote(self) -> None:
        self.pushed_index()
        shutil.copytree(self.cloud_dir / find_session.HOST, self.cloud_dir / REMOTE)

    def test_index_entry_carries_parent_src(self):
        sessions = self.pushed_index()["sessions"]
        child = next(e for e in sessions if e["id"] == CB_CHILD)
        self.assertEqual(child.get("parent"), CL_PARENT)
        self.assertEqual(child.get("parent_src"), "cl")
        self.assertEqual(child.get("agent"), "ds:impl")
        parent = next(e for e in sessions if e["id"] == CL_PARENT)
        self.assertFalse(parent.get("parent_src"))

    def test_pushed_rows_keep_links_on_the_other_machine(self):
        self.push_as_remote()
        rows = find_session.cloud_rows()
        parent = self.only(rows, CL_PARENT)
        child = self.only(rows, CB_CHILD)
        self.assertEqual(child.get("parent_src"), "cl")
        kids = find_session.children_of(rows)
        self.assertEqual([k["id"] for k in find_session.kids_for(kids, parent)],
                         [CB_CHILD])
        self.assertEqual(find_session.parent_tag(child), find_session.tag_of(parent))

    def test_show_cloud_parent_lists_cloud_child_from_another_source(self):
        self.push_as_remote()
        child = self.only(find_session.cloud_rows(), CB_CHILD)
        rc, out = self.show(f"cl@{REMOTE}|{CL_PARENT}")
        self.assertEqual(rc, 0)
        self.assertIn("sub-agent(s)/side chat(s) of this session", out)
        self.assertIn(find_session.tag_of(child), out)

    def test_show_cloud_child_header_uses_parent_source_tag(self):
        self.push_as_remote()
        rc, out = self.show(f"cb@{REMOTE}|{CB_CHILD}")
        self.assertEqual(rc, 0)
        self.assertIn(f"# [ds:impl] ↳ sub-agent of cl@{REMOTE}|db444e85", out)
