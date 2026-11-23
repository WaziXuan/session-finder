"""local_rows(): a usable sidecar turns any source's session into a linked row."""
from __future__ import annotations

from tests.sf_testkit import (CB_CHILD, CL_PARENT, CX_CHILD, FinderCase,
                              cl_session, cl_subagent, cx_session, find_session,
                              sidecar_path, wb_session, write_sidecar,
                              write_sidecar_raw)


class LocalRowsSidecarTest(FinderCase):
    def test_untitled_session_takes_sidecar_title(self):
        path = wb_session(self.roots["cb"], CB_CHILD, first="directed task")
        write_sidecar(path, src="cl", id=CL_PARENT, agent="ds:impl",
                      title="Directed task")
        row = self.only(self.rows(), CB_CHILD)
        self.assertEqual(row["title"], "[ds:impl] Directed task")
        self.assertTrue(row["has_title"])

    def test_default_agent_prefix_is_subagent(self):
        path = wb_session(self.roots["cb"], CB_CHILD, first="directed task")
        write_sidecar(path, src="cl", id=CL_PARENT, title="Directed task")
        self.assertEqual(self.only(self.rows(), CB_CHILD)["title"],
                         "[subagent] Directed task")

    def test_sidecar_title_ignored_when_session_has_one(self):
        path = wb_session(self.roots["cb"], CB_CHILD, first="directed task",
                          title="Real Title")
        write_sidecar(path, src="cl", id=CL_PARENT, agent="ds:impl",
                      title="Sidecar Title")
        row = self.only(self.rows(), CB_CHILD)
        self.assertEqual(row["title"], "[ds:impl] Real Title")

    def test_untitled_row_without_sidecar_title_keeps_first_user(self):
        path = wb_session(self.roots["cb"], CB_CHILD, first="check the build")
        write_sidecar(path, src="cl", id=CL_PARENT, agent="ds:impl")
        row = self.only(self.rows(), CB_CHILD)
        self.assertEqual(row["title"], "check the build")
        self.assertFalse(row["has_title"])
        self.assertEqual(row["parent"], CL_PARENT)
        self.assertEqual(row.get("parent_src"), "cl")
        self.assertEqual(row["agent"], "ds:impl")

    def test_row_is_sub_when_linked(self):
        path = wb_session(self.roots["cb"], CB_CHILD, first="directed task")
        write_sidecar(path, src="cl", id=CL_PARENT)
        row = self.only(self.rows(), CB_CHILD)
        self.assertEqual(row["agent"], "subagent")
        self.assertTrue(find_session.is_sub(row))

    def test_plain_row_has_no_parent_fields(self):
        wb_session(self.roots["cb"], CB_CHILD, first="check the build")
        row = self.only(self.rows(), CB_CHILD)
        self.assertFalse(row.get("parent"))
        self.assertFalse(row.get("parent_src"))
        self.assertFalse(find_session.is_sub(row))

    def test_sidecar_after_caching_is_picked_up(self):
        path = wb_session(self.roots["cb"], CB_CHILD, first="directed task")
        before = self.only(self.rows(), CB_CHILD)
        self.assertFalse(before.get("parent"))
        write_sidecar(path, src="cl", id=CL_PARENT, agent="ds:impl")
        linked = self.only(self.rows(), CB_CHILD)
        self.assertEqual(linked["parent"], CL_PARENT)
        self.assertEqual(linked.get("parent_src"), "cl")
        self.assertEqual(linked["agent"], "ds:impl")
        sidecar_path(path).unlink()
        self.assertFalse(self.only(self.rows(), CB_CHILD).get("parent"))

    def test_broken_sidecar_leaves_row_untouched(self):
        path = wb_session(self.roots["cb"], CB_CHILD, first="check the build")
        write_sidecar_raw(path, '{"src": "cl", "id": ')
        row = self.only(self.rows(), CB_CHILD)
        self.assertFalse(row.get("parent"))
        self.assertFalse(row.get("parent_src"))
        self.assertEqual(row["title"], "check the build")

    def test_sidecar_ignored_when_transcript_knows_its_parent(self):
        path = cl_subagent(self.roots["cl"], CL_PARENT, name="agent-a1b2c3",
                           agent="explore")
        write_sidecar(path, src="cb", id=CB_CHILD, agent="ds:impl",
                      title="Sidecar Title")
        row = self.only(self.rows(), "agent-a1b2c3")
        self.assertEqual(row["parent"], CL_PARENT)
        self.assertEqual(row["agent"], "explore")
        self.assertFalse(row.get("parent_src"))
        self.assertNotIn("Sidecar", row["title"])

    def test_sidecar_applies_to_wb_and_codex_sources(self):
        wb = wb_session(self.roots["wb"], "wb-session-1", first="from workbuddy")
        cx = cx_session(self.roots["cx"], CX_CHILD, first="from codex")
        for path in (wb, cx):
            write_sidecar(path, src="cl", id=CL_PARENT, agent="ds:impl")
        rows = self.rows()
        for sid in ("wb-session-1", CX_CHILD):
            row = self.only(rows, sid)
            self.assertEqual(row["parent"], CL_PARENT, sid)
            self.assertEqual(row.get("parent_src"), "cl", sid)

    def test_sidecar_applies_to_claude_and_codebuddy_sources(self):
        cl = cl_session(self.roots["cl"], "cl-session-1", first="from claude")
        cb = wb_session(self.roots["cb"], CB_CHILD, first="from codebuddy")
        for path in (cl, cb):
            write_sidecar(path, src="cx", id=CX_CHILD, agent="ds:impl")
        rows = self.rows()
        for sid in ("cl-session-1", CB_CHILD):
            row = self.only(rows, sid)
            self.assertEqual(row["parent"], CX_CHILD, sid)
            self.assertEqual(row.get("parent_src"), "cx", sid)
            self.assertTrue(find_session.is_sub(row), sid)
