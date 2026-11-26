"""spread_skill(): a client counts as installed when its home exists, even before
its skills folder does (Antigravity makes ~/.gemini/config/skills only later)."""
from __future__ import annotations

from tests.sf_testkit import FinderCase, find_session


class SpreadTest(FinderCase):
    def setUp(self) -> None:
        super().setUp()
        self.home = self.tmp / "home"
        (self.home / ".gemini" / "config").mkdir(parents=True)
        skill_home = self.tmp / "installed" / "skills" / "session-finder"
        skill_home.mkdir(parents=True)
        (skill_home / "SKILL.md").write_text("# skill\n", encoding="utf-8")
        saved = (find_session.HOME, find_session.SKILL_HOME)
        self.addCleanup(lambda: (setattr(find_session, "HOME", saved[0]),
                                 setattr(find_session, "SKILL_HOME", saved[1])))
        find_session.HOME = self.home
        find_session.SKILL_HOME = skill_home

    def test_antigravity_without_skills_folder_gets_skill_md(self):
        find_session.spread_skill()
        self.assertTrue((self.home / ".gemini/config/skills/session-finder/SKILL.md").is_file())

    def test_absent_client_gets_nothing(self):
        find_session.spread_skill()
        self.assertFalse((self.home / ".qoder-cn").exists())
