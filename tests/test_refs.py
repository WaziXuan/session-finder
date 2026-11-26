"""Session-to-session links: a session that ran `show <tag>` / `files <tag>` links
forward to that session, and the target links back to it."""
from __future__ import annotations

import json
from pathlib import Path

from tests.sf_testkit import (CL_PARENT, CX_CHILD, FinderCase, _write, cl_session,
                              cx_session, find_session)

READER = "cl-reader-0000-0000-000000000001"


def cl_reader(root, sid, commands, project="proj-cl", quoted_only=False) -> Path:
    """A Claude transcript whose assistant ran each command through Bash. With
    quoted_only the commands only appear in tool output, never as a call."""
    recs = [{"type": "user", "cwd": "/work", "timestamp": "2026-11-26T10:00:00Z",
             "message": {"role": "user", "content": [{"type": "text", "text": "find that session"}]}}]
    for i, c in enumerate(commands):
        if quoted_only:
            recs.append({"type": "user", "timestamp": "2026-11-26T10:01:00Z",
                         "message": {"role": "user", "content": [
                             {"type": "tool_result", "tool_use_id": f"t{i}", "content": c}]}})
        else:
            recs.append({"type": "assistant", "timestamp": "2026-11-26T10:01:00Z",
                         "message": {"role": "assistant", "content": [
                             {"type": "tool_use", "id": f"t{i}", "name": "Bash",
                              "input": {"command": c, "description": "read it"}}]}})
    return _write(Path(root) / project / f"{sid}.jsonl", recs)


class RefsTest(FinderCase):
    def setUp(self) -> None:
        super().setUp()
        cl_session(self.roots["cl"], CL_PARENT, title="Earlier work", first="old question")

    def test_show_call_becomes_a_forward_ref(self):
        cl_reader(self.roots["cl"], READER,
                  [f'S=~/x/find_session.py; "$PY" "$S" show "cl|{CL_PARENT[:8]}" --tail 5'])
        r = self.only(self.rows(), READER)
        self.assertEqual([x["tag"] for x in r["refs"]], [f"cl|{CL_PARENT[:8]}"])
        self.assertEqual(r["refs"][0]["cmd"], "show")

    def test_files_and_host_tags_are_kept_once_each(self):
        cl_reader(self.roots["cl"], READER, [
            f'"$PY" "$S" files cl|{CL_PARENT[:8]}',
            f'"$PY" "$S" show cl|{CL_PARENT[:8]} --role all',
            '"$PY" "$S" show "cx@LAPTOP|0193b2c4-4444"',
        ])
        r = self.only(self.rows(), READER)
        self.assertEqual([x["tag"] for x in r["refs"]],
                         [f"cl|{CL_PARENT[:8]}", "cx@LAPTOP|0193b2c4-4444"])

    def test_quoted_output_is_not_a_ref(self):
        cl_reader(self.roots["cl"], READER, [f'find_session.py show cl|{CL_PARENT[:8]}'],
                  quoted_only=True)
        self.assertNotIn("refs", self.only(self.rows(), READER))

    def test_codex_function_call_arguments_are_read(self):
        path = cx_session(self.roots["cx"], CX_CHILD)
        args = json.dumps({"cmd": f'python find_session.py show "cl|{CL_PARENT[:8]}"'})
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": "2026-11-20T10:01:00Z", "type": "response_item",
                                "payload": {"type": "function_call", "name": "exec_command",
                                            "arguments": args}}) + "\n")
        r = self.only(self.rows(), CX_CHILD)
        self.assertEqual([x["tag"] for x in r["refs"]], [f"cl|{CL_PARENT[:8]}"])

    def test_both_ends_are_linked(self):
        cl_reader(self.roots["cl"], READER, [f'"$PY" "$S" show cl|{CL_PARENT[:8]}'])
        rows = self.rows()
        out, back = find_session.links_of(rows)
        reader, target = self.only(rows, READER), self.only(rows, CL_PARENT)
        self.assertEqual([t["id"] for _, t in out[find_session.row_key(reader)]], [CL_PARENT])
        self.assertEqual([r["id"] for r, _ in back[find_session.row_key(target)]], [READER])

    def test_self_reference_is_dropped(self):
        cl_reader(self.roots["cl"], READER, [f'"$PY" "$S" show cl|{READER[:8]}'])
        out, back = find_session.links_of(self.rows())
        self.assertEqual(out, {})
        self.assertEqual(back, {})

    def test_show_prints_both_directions(self):
        cl_reader(self.roots["cl"], READER, [f'"$PY" "$S" show cl|{CL_PARENT[:8]}',
                                             '"$PY" "$S" show cl|deadbeef',
                                             '"$PY" "$S" show cl@LAPTOP|cafebabe'])
        rc, out = self.show(f"cl|{CL_PARENT}")
        self.assertEqual(rc, 0)
        self.assertIn("1 session(s) that read this one", out)
        self.assertIn(f"← cl|{READER[:8]}", out)
        rc, out = self.show(f"cl|{READER}")
        self.assertIn("2 session(s) this one read", out)
        self.assertIn(f"→ cl|{CL_PARENT[:8]}", out)
        # Another machine may just not be synced yet; a missing local id is noise.
        self.assertIn("→ cl@LAPTOP|cafebabe  (not in the index)", out)
        self.assertNotIn("deadbeef", out)

    def test_list_counts_links(self):
        cl_reader(self.roots["cl"], READER, [f'"$PY" "$S" show cl|{CL_PARENT[:8]}'])
        rc, out = self.list_()
        self.assertIn("→ read 1 session(s)", out)
        self.assertIn("← read by 1 session(s)", out)

    def test_cloud_reader_links_back_to_a_local_session(self):
        """A session on LAPTOP that read `cl@<this host>|…` shows up here as a reader."""
        self.cloud_on = True
        idx = self.cloud_dir / "_search" / "LAPTOP" / "index.json"
        idx.parent.mkdir(parents=True)
        (self.cloud_dir / "LAPTOP").mkdir()
        tag = f"cl@{find_session.HOST}|{CL_PARENT[:8]}"
        idx.write_text(json.dumps({"host": "LAPTOP", "sessions": [{
            "src": "cx", "id": CX_CHILD, "cloud_rel": "x.jsonl", "title": "Laptop reader",
            "has_title": True, "cwd": "", "first_user": "", "bytes": 1,
            "mtime": "2026-11-26 10:00", "refs": [{"tag": tag, "cmd": "show", "ts": ""}]}]}),
            encoding="utf-8")
        rc, out = self.show(f"cl|{CL_PARENT}")
        self.assertIn("← cx@LAPTOP|0193b2c4", out)
