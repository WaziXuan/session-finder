"""Amazon Quick: the Quick History Export skill writes one WB-shaped jsonl per
conversation to ~/.quickwork/session-finder-export/, with a leading meta record
carrying the title and cwd. Session Finder reads them through the qk source, which
reuses WB's dialogue reader — no credentials or cloud API involved."""
from __future__ import annotations

import json
import unittest

from tests.sf_testkit import FinderCase, find_session

SID = "20261125-1a2b3c4d"


def write_qk(root, sid=SID, first="hello from quick", title=None,
             cwd="D:\\Sandbox", answer="quick answer"):
    records = []
    if title is not None:
        records.append({"type": "meta", "id": sid, "title": title, "cwd": cwd,
                        "source": "quick", "exported_at": "2026-11-25T09:00:00Z"})
    records.append({"role": "user", "content": [{"type": "text", "text": first}],
                    "timestamp": "2026-11-25T08:59:00Z"})
    if answer:
        records.append({"role": "assistant", "content": [{"type": "text", "text": answer}],
                        "timestamp": "2026-11-25T08:59:05Z"})
    path = root / f"{sid}.jsonl"
    path.write_text("\n".join(json.dumps(o, ensure_ascii=False) for o in records) + "\n",
                    encoding="utf-8")
    return path


class QuickTest(FinderCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.roots["qk"]

    def test_alias_resolution(self):
        for alias in ("qk", "quick", "amazon-quick", "aws-quick"):
            self.assertEqual(find_session.norm_source(alias), "qk")

    def test_listed_with_meta_title_and_cwd(self):
        write_qk(self.root, title="Quick chat title", cwd="D:\\Work")
        row = self.only(self.rows("qk"), SID)
        self.assertEqual(row["title"], "Quick chat title")
        self.assertEqual(row["cwd"], "D:\\Work")
        self.assertEqual(row["first_user"], "hello from quick")

    def test_title_falls_back_when_no_meta(self):
        write_qk(self.root, title=None, first="a prompt without meta")
        row = self.only(self.rows("qk"), SID)
        self.assertEqual(row["first_user"], "a prompt without meta")

    def test_show_reads_user_and_assistant(self):
        write_qk(self.root, answer="the conclusion")
        rc, out = self.show(f"qk|{SID}", role="all")
        self.assertEqual(rc, 0)
        self.assertIn("hello from quick", out)
        self.assertIn("the conclusion", out)

    def test_grep_finds_answer_text(self):
        write_qk(self.root, answer="flux capacitor ready")
        rc, out = self.grep("capacitor", source="qk")
        self.assertEqual(rc, 0)
        self.assertIn("flux capacitor", out)

    def test_source_listed_in_all(self):
        write_qk(self.root, title="Visible in all")
        rc, out = self.list_(source="all")
        self.assertEqual(rc, 0)
        self.assertIn("qk|", out)
        self.assertIn("Visible in all", out)


if __name__ == "__main__":
    unittest.main()
