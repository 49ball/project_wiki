# -*- coding: utf-8 -*-
"""새 세션에서 AI 가 위키를 곧바로 찾게 만든다.

계기: 새 세션에서 질문하면 AI 가 위키를 찾는 데 시간이 걸렸다.

원인 두 개.

  1. CLAUDE.md 안내문에 실제 경로가 아니라 `<codewiki>` 라는 빈칸이
     적혀 있었다. 그래서 AI 가 cw.py 를 직접 찾아 헤맸다.
  2. CLAUDE.md 가 이미 있으면 아무것도 넣지 않고 "직접 추가하세요" 라고만
     했다. 사용자가 안 하면 영원히 없다.

CLAUDE.md 는 세션이 시작될 때 자동으로 읽힌다. 거기에 **실행 가능한
명령 그대로** 적어두면 탐색이 아예 필요 없다.
"""
import importlib.util
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cw", ROOT / "cw.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


def _init(root):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "a.c").write_text("int f(void);\n", encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf):
        cw.cmd_init(root, show_next=False)
    return buf.getvalue()


class TestAgentNote(unittest.TestCase):

    def test_writes_real_toolkit_path(self):
        """빈칸이 아니라 그대로 실행되는 명령이어야 한다."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _init(root)
            text = (root / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertNotIn("<codewiki>", text)
            self.assertIn(str(cw.TOOLKIT_DIR / "cw.py"), text)

    def test_appends_to_existing_file(self):
        """이미 CLAUDE.md 가 있어도 안내를 넣어야 한다. 기존 내용은 보존한다."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "CLAUDE.md").write_text("# 우리 프로젝트 규칙\n\n- 탭 금지\n",
                                            encoding="utf-8")
            _init(root)
            text = (root / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertIn("탭 금지", text, "기존 내용을 지우면 안 된다")
            self.assertIn("cw.py", text)

    def test_running_twice_keeps_one_block(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _init(root)
            _init(root)
            text = (root / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertEqual(text.count(cw.AGENT_NOTE_BEGIN), 1)

    def test_block_is_refreshed_when_stale(self):
        """툴킷을 옮기면 경로가 바뀐다. 낡은 블록이 남아 있으면 안 된다."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "CLAUDE.md").write_text(
                f"# 규칙\n\n{cw.AGENT_NOTE_BEGIN}\n낡은 내용\n"
                f"{cw.AGENT_NOTE_END}\n", encoding="utf-8")
            _init(root)
            text = (root / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertNotIn("낡은 내용", text)
            self.assertIn("규칙", text)
            self.assertIn("cw.py", text)

    def test_both_files_get_the_note(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _init(root)
            for fname in ("CLAUDE.md", "AGENTS.md"):
                self.assertIn("cw.py",
                              (root / fname).read_text(encoding="utf-8"))

    def test_note_tells_the_project_path(self):
        """AI 가 '어느 프로젝트냐'를 되묻지 않아도 되게 경로를 적어둔다."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _init(root)
            text = (root / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertIn(str(root.resolve()), text)


if __name__ == "__main__":
    unittest.main()
