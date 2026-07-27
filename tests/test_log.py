# -*- coding: utf-8 -*-
"""기록장 — query 가 못 답한 것이 ingest 의 입력이 된다."""
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


def fresh(tmp):
    root = Path(tmp)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "a.c").write_text("int f(void){return 0;}\n", encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf):
        cw.cmd_init(root, show_next=False)
    return root


class TestLogAppend(unittest.TestCase):

    def test_appends_row(self):
        with tempfile.TemporaryDirectory() as d:
            root = fresh(d)
            cw.cmd_log(root, op="query", text="CAN 흐름",
                       result="위키없음 → 코드 3개 열람")
            body = (root / "wiki" / "log.md").read_text(encoding="utf-8")
            self.assertIn("CAN 흐름", body)
            self.assertIn("위키없음", body)

    def test_appends_not_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            root = fresh(d)
            cw.cmd_log(root, op="query", text="첫째", result="ok")
            cw.cmd_log(root, op="query", text="둘째", result="ok")
            body = (root / "wiki" / "log.md").read_text(encoding="utf-8")
            self.assertIn("첫째", body)
            self.assertIn("둘째", body)

    def test_pipe_in_text_is_escaped(self):
        """마크다운 표가 깨지면 안 된다."""
        with tempfile.TemporaryDirectory() as d:
            root = fresh(d)
            cw.cmd_log(root, op="query", text="a | b", result="ok")
            body = (root / "wiki" / "log.md").read_text(encoding="utf-8")
            self.assertIn("a \\| b", body)


class TestLogGaps(unittest.TestCase):

    def test_gaps_lists_only_wiki_misses_with_counts(self):
        with tempfile.TemporaryDirectory() as d:
            root = fresh(d)
            cw.cmd_log(root, op="query", text="CAN 흐름",
                       result="위키없음 → 코드 열람")
            cw.cmd_log(root, op="query", text="CAN 흐름",
                       result="위키없음 → 코드 열람")
            cw.cmd_log(root, op="query", text="호출자", result="위키로 답함")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_log(root, gaps_only=True)
            out = buf.getvalue()
            self.assertIn("CAN 흐름", out)
            self.assertIn("2", out, "질문 횟수가 보여야 한다")
            self.assertNotIn("호출자", out, "위키로 답한 건 구멍이 아니다")

    def test_missing_log_file_is_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cw.cmd_log(root, gaps_only=True)
            self.assertEqual(code, 1)
            self.assertIn("없습니다", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
