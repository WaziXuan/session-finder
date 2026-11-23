"""cmd_show(): cross-source children in the footer, parent tag on a child."""
from __future__ import annotations

from tests.sf_testkit import (CB_CHILD, CB_SIBLING, CL_PARENT, FinderCase,
                              cl_session, cl_subagent, find_session, wb_session,
                              write_sidecar)


class ShowTest(FinderCase):
    def setUp(self) -> None:
        super().setUp()
        cl_session(self.roots["cl"], CL_PARENT, title="Claude lead", first="lead asked")
        child = wb_session(self.roots["cb"], CB_CHILD, first="child task")
        write_sidecar(child, src="cl", id=CL_PARENT, agent="ds:impl",
                      title="Implementation")

    def test_parent_footer_lists_child_from_another_source(self):
        child = self.only(self.rows(), CB_CHILD)
        rc, out = self.show(f"cl|{CL_PARENT}")
        self.assertEqual(rc, 0)
        self.assertIn("sub-agent(s)/side chat(s) of this session", out)
        self.assertIn(find_session.tag_of(child), out)
        self.assertIn("Implementation", out)

    def test_parent_footer_lists_same_source_child_too(self):
        cl_subagent(self.roots["cl"], CL_PARENT, name="agent-a1b2c3")
        sub = self.only(self.rows(), "agent-a1b2c3")
        rc, out = self.show(f"cl|{CL_PARENT}")
        self.assertEqual(rc, 0)
        self.assertIn(find_session.tag_of(sub), out)

    def test_parent_footer_lists_both_children(self):
        sibling = wb_session(self.roots["cb"], CB_SIBLING, first="second child")
        write_sidecar(sibling, src="cl", id=CL_PARENT, agent="ds:impl",
                      title="Second")
        rows = self.rows()
        rc, out = self.show(f"cl|{CL_PARENT}")
        self.assertEqual(rc, 0)
        for sid in (CB_CHILD, CB_SIBLING):
            self.assertIn(find_session.tag_of(self.only(rows, sid)), out)

    def test_child_header_shows_the_parent_source_tag(self):
        rc, out = self.show(f"cb|{CB_CHILD}")
        self.assertEqual(rc, 0)
        self.assertIn("# [ds:impl] ↳ sub-agent of cl|db444e85", out)

    def test_child_of_same_source_keeps_own_source_tag(self):
        cl_subagent(self.roots["cl"], CL_PARENT, name="agent-a1b2c3")
        rc, out = self.show("cl|agent-a1b2c3")
        self.assertEqual(rc, 0)
        self.assertIn("↳ sub-agent of cl|db444e85", out)

    def test_parent_without_children_has_no_footer(self):
        lonely = "cl-lonely-session"
        cl_session(self.roots["cl"], lonely, title="Lonely", first="alone")
        rc, out = self.show(f"cl|{lonely}")
        self.assertEqual(rc, 0)
        self.assertNotIn("sub-agent(s)/side chat(s) of this session", out)

    def test_unknown_session_fails(self):
        rc, out = self.show("cl|deadbeef")
        self.assertEqual(rc, 1)
        self.assertIn("no session matched", out)
