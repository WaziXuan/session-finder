"""parent_link(): the <sid>.parent.json sidecar next to a transcript."""
from __future__ import annotations

import json

from tests.sf_testkit import (CB_CHILD, CL_PARENT, FinderCase, find_session,
                              sidecar_path, wb_session, write_sidecar,
                              write_sidecar_raw)


class ParentLinkTest(FinderCase):
    def setUp(self) -> None:
        super().setUp()
        self.transcript = wb_session(self.roots["cb"], CB_CHILD, first="directed task")

    def test_returns_all_four_fields(self):
        write_sidecar(self.transcript, src="cl", id=CL_PARENT,
                      agent="ds:impl", title="Directed from Claude")
        self.assertEqual(find_session.parent_link(self.transcript), {
            "parent": CL_PARENT,
            "parent_src": "cl",
            "agent": "ds:impl",
            "title": "Directed from Claude",
        })

    def test_agent_defaults_to_subagent(self):
        write_sidecar(self.transcript, src="cl", id=CL_PARENT)
        self.assertEqual(find_session.parent_link(self.transcript), {
            "parent": CL_PARENT, "parent_src": "cl", "agent": "subagent",
        })

    def test_agent_empty_string_falls_back_to_subagent(self):
        write_sidecar(self.transcript, src="cl", id=CL_PARENT, agent="")
        self.assertEqual(find_session.parent_link(self.transcript)["agent"], "subagent")

    def test_title_key_absent_when_not_given(self):
        write_sidecar(self.transcript, src="cl", id=CL_PARENT, agent="subagent")
        self.assertNotIn("title", find_session.parent_link(self.transcript))

    def test_title_key_absent_when_empty(self):
        write_sidecar(self.transcript, src="cl", id=CL_PARENT, title="")
        self.assertNotIn("title", find_session.parent_link(self.transcript))

    def test_extra_keys_are_ignored(self):
        write_sidecar(self.transcript, src="cl", id=CL_PARENT, agent="ds:impl",
                      created_by="ds-director", nested={"x": 1})
        self.assertEqual(set(find_session.parent_link(self.transcript)),
                         {"parent", "parent_src", "agent"})

    def test_accepts_every_known_source_key(self):
        for key in ("wb", "cx", "cl", "cb"):
            write_sidecar(self.transcript, src=key, id=CL_PARENT)
            self.assertEqual(find_session.parent_link(self.transcript)["parent_src"], key)

    def test_unknown_source_key_returns_empty(self):
        write_sidecar(self.transcript, src="zz", id=CL_PARENT)
        self.assertEqual(find_session.parent_link(self.transcript), {})

    def test_missing_src_returns_empty(self):
        write_sidecar(self.transcript, id=CL_PARENT)
        self.assertEqual(find_session.parent_link(self.transcript), {})

    def test_empty_src_returns_empty(self):
        write_sidecar(self.transcript, src="", id=CL_PARENT)
        self.assertEqual(find_session.parent_link(self.transcript), {})

    def test_missing_id_returns_empty(self):
        write_sidecar(self.transcript, src="cl")
        self.assertEqual(find_session.parent_link(self.transcript), {})

    def test_empty_id_returns_empty(self):
        write_sidecar(self.transcript, src="cl", id="")
        self.assertEqual(find_session.parent_link(self.transcript), {})

    def test_no_sidecar_returns_empty(self):
        self.assertEqual(find_session.parent_link(self.transcript), {})

    def test_nonexistent_transcript_returns_empty(self):
        missing = self.roots["cb"] / "proj" / "never-written.jsonl"
        self.assertEqual(find_session.parent_link(missing), {})

    def test_broken_json_returns_empty(self):
        write_sidecar_raw(self.transcript, '{"src": "cl", "id": ')
        self.assertEqual(find_session.parent_link(self.transcript), {})

    def test_empty_sidecar_returns_empty(self):
        write_sidecar_raw(self.transcript, "")
        self.assertEqual(find_session.parent_link(self.transcript), {})

    def test_non_object_json_returns_empty(self):
        write_sidecar_raw(self.transcript, json.dumps([["cl", CL_PARENT]]))
        self.assertEqual(find_session.parent_link(self.transcript), {})

    def test_sidecar_of_another_session_is_ignored(self):
        sidecar_path(self.transcript).with_name("someone-else.parent.json").write_text(
            json.dumps({"src": "cl", "id": CL_PARENT}), encoding="utf-8")
        self.assertEqual(find_session.parent_link(self.transcript), {})

    def test_sidecar_must_sit_next_to_the_transcript(self):
        elsewhere = self.roots["cb"] / "other" / sidecar_path(self.transcript).name
        elsewhere.parent.mkdir(parents=True, exist_ok=True)
        elsewhere.write_text(json.dumps({"src": "cl", "id": CL_PARENT}),
                             encoding="utf-8")
        self.assertEqual(find_session.parent_link(self.transcript), {})

    def test_jsonl_zst_transcript_finds_bare_sid_sidecar(self):
        path = self.roots["cb"] / "proj" / f"{CB_CHILD}.jsonl.zst"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x28\xb5\x2f\xfd")  # magic only; parent_link reads no transcript
        write_sidecar(path, src="cl", id=CL_PARENT, agent="ds:impl")
        self.assertEqual(find_session.parent_link(path), {
            "parent": CL_PARENT, "parent_src": "cl", "agent": "ds:impl",
        })

    def test_id_and_title_kept_verbatim(self):
        weird = "Agent-A/000+weird id"
        write_sidecar(self.transcript, src="cx", id=weird, title="标题 with spaces")
        link = find_session.parent_link(self.transcript)
        self.assertEqual(link["parent"], weird)
        self.assertEqual(link["title"], "标题 with spaces")
