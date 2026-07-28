# -*- coding: utf-8 -*-
"""스킬이 저절로 깔리게 한다.

계기: 스킬 설치 방법이 README FAQ 에 `cp -r` 한 줄로만 있었다. 안 읽으면
스킬 없이 쓰게 되고, 그러면 "위키 만들어줘" 한마디로 되는 흐름이 통째로
동작하지 않는다. 사내에 배포하면 사람마다 이걸 챙겨야 한다.

두 군데에 깐다.
  - 프로젝트 `.claude/skills/` — 저장소를 받은 사람은 아무것도 안 해도 됨
  - 개인 `~/.claude/skills/` — 그 사람의 모든 프로젝트에서 동작

이미 같은 내용이면 건드리지 않는다. 매번 '갱신됨'이 뜨면 사용자가 무시하게
되고, 그러면 진짜 갱신됐을 때도 못 알아챈다.
"""
import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cw", ROOT / "cw.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


class TestInstallSkills(unittest.TestCase):

    def test_copies_every_skill(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "skills"
            new, updated, same = cw.install_skills(dest)
            self.assertIn("codewiki", new)
            self.assertIn("codewiki-query", new)
            self.assertEqual((updated, same), ([], []))
            self.assertTrue((dest / "codewiki" / "SKILL.md").exists())

    def test_second_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "skills"
            cw.install_skills(dest)
            new, updated, same = cw.install_skills(dest)
            self.assertEqual((new, updated), ([], []))
            self.assertIn("codewiki", same)

    def test_updates_when_content_differs(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "skills"
            cw.install_skills(dest)
            (dest / "codewiki" / "SKILL.md").write_text("낡은 내용\n",
                                                        encoding="utf-8")
            new, updated, same = cw.install_skills(dest)
            self.assertEqual(new, [])
            self.assertIn("codewiki", updated)
            self.assertIn("codewiki-query", same)

    def test_missing_source_is_not_a_crash(self):
        """배포본에 skill/ 이 빠져 있어도 도구 전체가 죽으면 안 된다."""
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "skills"
            new, updated, same = cw.install_skills(dest, src=Path(d) / "없음")
            self.assertEqual((new, updated, same), ([], [], []))


class TestSetupInstallsSkills(unittest.TestCase):

    def setUp(self):
        ok, reason = cw.ts_status()
        if not ok:
            self.skipTest(reason)
        self._home = os.environ.get("HOME")

    def tearDown(self):
        if self._home is not None:
            os.environ["HOME"] = self._home

    def test_setup_installs_into_project_and_home(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "proj"
            root.mkdir()
            fake_home = Path(d) / "home"
            fake_home.mkdir()
            os.environ["HOME"] = str(fake_home)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "a.c").write_text("int f(void);\n", encoding="utf-8")

            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_install_skills(root)
            out = buf.getvalue()

            self.assertTrue((root / ".claude" / "skills" / "codewiki"
                             / "SKILL.md").exists(), "프로젝트에 깔려야 한다")
            self.assertTrue((fake_home / ".claude" / "skills" / "codewiki"
                             / "SKILL.md").exists(), "개인 홈에도 깔려야 한다")
            self.assertIn("codewiki", out)

    def test_reports_where_it_installed(self):
        """어디에 깔았는지 말해줘야 한다 — 홈 폴더에 조용히 쓰면 안 된다."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "proj"
            root.mkdir()
            fake_home = Path(d) / "home"
            fake_home.mkdir()
            os.environ["HOME"] = str(fake_home)
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_install_skills(root)
            out = buf.getvalue()
            self.assertIn(".claude/skills", out)


if __name__ == "__main__":
    unittest.main()
