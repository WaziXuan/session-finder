"""Grouping (children_of / kids_for), tags (parent_tag / link_note) and the
list / find / grep default-hiding behaviour."""
from __future__ import annotations

from tests.sf_testkit import (CB_CHILD, CB_PARENT, CB_SIBLING, CL_PARENT,
                              FinderCase, cl_session, cl_subagent, find_session,
                              wb_session, write_sidecar)


class GroupingTest(FinderCase):
    def setUp(self) -> None:
        super().setUp()
        cl_session(self.roots["cl"], CL_PARENT, title="Claude lead", first="lead asked")
        child = wb_session(self.roots["cb"], CB_CHILD, first="child task")
        write_sidecar(child, src="cl", id=CL_PARENT, agent="ds:impl",
                      title="Implementation")

    def test_child_groups_under_the_parent_source(self):
        rows = self.rows()
        parent = self.only(rows, CL_PARENT)
        kids = find_session.children_of(rows)
        self.assertEqual([k["id"] for k in find_session.kids_for(kids, parent)],
                         [CB_CHILD])
        self.assertEqual(find_session.kids_for(kids, self.only(rows, CB_CHILD)), [])

    def test_parent_tag_uses_the_parent_source(self):
        rows = self.rows()
        child = self.only(rows, CB_CHILD)
        self.assertEqual(find_session.parent_tag(child), "cl|db444e85")
        self.assertEqual(find_session.parent_tag(child),
                         find_session.tag_of(self.only(rows, CL_PARENT)))

    def test_link_note_names_the_parent_tag(self):
        child = self.only(self.rows(), CB_CHILD)
        self.assertEqual(find_session.link_note(child), "↳ sub-agent of cl|db444e85")

    def test_link_note_wording_for_fork_and_side(self):
        base = {"src": "cb", "id": CB_CHILD, "parent": CL_PARENT,
                "parent_src": "cl"}
        self.assertEqual(find_session.link_note({**base, "agent": "fork"}),
                         "↳ forked from cl|db444e85")
        self.assertEqual(find_session.link_note({**base, "agent": "side"}),
                         "↳ side chat of cl|db444e85")

    def test_parent_tag_without_parent_is_unknown(self):
        row = {"src": "cb", "id": CB_CHILD, "parent": ""}
        self.assertEqual(find_session.parent_tag(row), "(parent unknown)")
        self.assertEqual(find_session.parent_tag({**row, "parent": "?"}),
                         "(parent unknown)")

    def test_named_session_ids_keep_their_full_tag(self):
        # ds-director names every phase of a job ds-<parent8>-<job>-<phase>; a fixed-width
        # cut would give all phases the same tag.
        tags = {find_session.tag_of({"src": "cb", "id": f"ds-db444e85-j1-{phase}"})
                for phase in ("tests", "impl", "review")}
        self.assertEqual(tags, {"cb|ds-db444e85-j1-tests", "cb|ds-db444e85-j1-impl",
                                "cb|ds-db444e85-j1-review"})
        self.assertEqual(find_session.tag_of({"src": "cb", "id": CB_CHILD}), "cb|0193b2c4-1111")

    def test_parent_tag_without_parent_src_uses_own_source(self):
        row = {"src": "cl", "id": CB_CHILD, "parent": CL_PARENT}
        self.assertEqual(find_session.parent_tag(row), "cl|db444e85")

    def test_same_source_child_without_parent_src_still_groups(self):
        path = cl_subagent(self.roots["cl"], CL_PARENT, name="agent-a1b2c3")
        self.assertTrue(path.exists())
        rows = self.rows()
        parent = self.only(rows, CL_PARENT)
        kids = find_session.children_of(rows)
        # setUp already linked CB_CHILD to the same parent through a sidecar; both belong.
        self.assertEqual(sorted(k["id"] for k in find_session.kids_for(kids, parent)),
                         sorted(["agent-a1b2c3", CB_CHILD]))

    def test_same_source_sidecar_groups_under_own_source(self):
        cb_parent = wb_session(self.roots["cb"], CB_PARENT, first="cb lead")
        sibling = wb_session(self.roots["cb"], CB_SIBLING, first="cb helper")
        write_sidecar(sibling, src="cb", id=CB_PARENT, agent="implement")
        rows = self.rows()
        kids = find_session.children_of(rows)
        self.assertEqual(
            [k["id"] for k in find_session.kids_for(kids, self.only(rows, CB_PARENT))],
            [CB_SIBLING])
        self.assertTrue(cb_parent.exists())

    def test_hosts_are_kept_apart(self):
        parent = {"src": "cl", "host": "", "id": "p1", "parent": ""}
        local = {"src": "cb", "host": "", "id": "c1", "parent": "p1",
                 "parent_src": "cl", "agent": "subagent"}
        remote = {"src": "cb", "host": "REMOTE-A", "id": "c2", "parent": "p1",
                  "parent_src": "cl", "agent": "subagent"}
        kids = find_session.children_of([parent, local, remote])
        self.assertEqual([k["id"] for k in find_session.kids_for(kids, parent)], ["c1"])
        self.assertEqual(
            [k["id"] for k in find_session.kids_for(kids, {**parent, "host": "REMOTE-A"})],
            ["c2"])

    def test_children_of_empty_rows(self):
        self.assertEqual(find_session.children_of([]), {})
        self.assertEqual(find_session.kids_for({}, {"src": "cl", "id": CL_PARENT}), [])

    def test_is_sub_needs_a_parent_and_a_sub_agent_kind(self):
        self.assertTrue(find_session.is_sub({"parent": "p", "agent": "subagent"}))
        self.assertFalse(find_session.is_sub({"parent": "", "agent": "subagent"}))
        self.assertFalse(find_session.is_sub({"parent": "p", "agent": "side"}))
        self.assertFalse(find_session.is_sub({"parent": "p", "agent": "fork"}))
        self.assertFalse(find_session.is_sub({}))


class ListingTest(FinderCase):
    def setUp(self) -> None:
        super().setUp()
        cl_session(self.roots["cl"], CL_PARENT, title="Claude lead", first="lead asked")
        child = wb_session(self.roots["cb"], CB_CHILD, first="zebra crossing notes")
        write_sidecar(child, src="cl", id=CL_PARENT, agent="ds:impl",
                      title="Implementation")

    def test_list_hides_linked_child_by_default(self):
        rc, out = self.list_()
        self.assertEqual(rc, 0)
        self.assertIn("# 1 sessions", out)
        self.assertIn("1 sub-agent sessions hidden", out)
        self.assertIn("+ 1 sub-agent(s)", out)
        self.assertNotIn("Implementation", out)

    def test_list_shows_child_with_subagents_flag(self):
        rows = self.rows()
        child = self.only(rows, CB_CHILD)
        rc, out = self.list_(subagents=True)
        self.assertEqual(rc, 0)
        self.assertIn(find_session.tag_of(child), out)
        self.assertIn("↳ sub-agent of cl|db444e85", out)

    def test_list_counts_every_linked_child(self):
        sibling = wb_session(self.roots["cb"], CB_SIBLING, first="second child")
        write_sidecar(sibling, src="cl", id=CL_PARENT, agent="ds:impl",
                      title="Second")
        rc, out = self.list_()
        self.assertEqual(rc, 0)
        self.assertIn("+ 2 sub-agent(s)", out)
        self.assertIn("2 sub-agent sessions hidden", out)
        self.assertNotIn("Second", out)

    def test_find_skips_linked_child_unless_subagents(self):
        rc, out = self.find("zebra")
        self.assertEqual(rc, 1)
        self.assertIn("no session matched", out)
        rc, out = self.find("zebra", subagents=True)
        self.assertEqual(rc, 0)
        self.assertIn("Implementation", out)

    def test_find_shows_parent_with_its_child_count(self):
        rc, out = self.find("lead")
        self.assertEqual(rc, 0)
        self.assertIn("+ 1 sub-agent(s)", out)

    def test_grep_skips_linked_child_unless_subagents(self):
        rc, out = self.grep("zebra")
        self.assertEqual(rc, 1)
        self.assertIn("no session mentions", out)
        rc, out = self.grep("zebra", subagents=True)
        self.assertEqual(rc, 0)
        self.assertIn("zebra", out)
        self.assertIn("↳ sub-agent of cl|db444e85", out)
