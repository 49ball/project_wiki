# -*- coding: utf-8 -*-
"""cw parse-report — 데이터가 아니라 결론을 낸다.

설계 §2.2-①: 사용자가 사내 출력을 반출할 수 없으므로 판단을 도구에 내장한다.
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


def build(root, files):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for name, body in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf):
        cw.cmd_init(root, show_next=False)
        cw.cmd_index(root)


class TestParseReport(unittest.TestCase):

    def setUp(self):
        ok, reason = cw.ts_status()
        if not ok:
            self.skipTest(reason)

    def test_clean_code_reports_good(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            build(root, {"a.c": "int foo(int x) { return x; }\n",
                         "b.c": "int bar(void) { return 1; }\n"})
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cw.cmd_parse_report(root)
            out = buf.getvalue()
            self.assertEqual(code, 0, out)
            self.assertIn("좋음", out)

    def test_include_guards_do_not_ruin_verdict(self):
        """가드만 있는 평범한 헤더 묶음은 '좋음'이어야 한다.
        여기서 '나쁨'이 나오면 판정이 쓸모없어진다."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            files = {f"h{i}.h": f"#ifndef H{i}_H\n#define H{i}_H\n"
                                f"int f{i}(void);\n#endif\n" for i in range(6)}
            files["a.c"] = "int foo(int x) { return x; }\n"
            build(root, files)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cw.cmd_parse_report(root)
            out = buf.getvalue()
            self.assertEqual(code, 0, out)
            self.assertIn("좋음", out)

    def test_macro_heavy_reports_bad_and_names_cause(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            body = ("#define WRAP(s) void h_##s(int i)\n"
                    "WRAP(a) { f(i); }\n")
            build(root, {f"m{i}.c": body for i in range(5)})
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cw.cmd_parse_report(root)
            out = buf.getvalue()
            self.assertEqual(code, 1, "조치 필요 시 종료코드 1")
            self.assertTrue("나쁨" in out or "보통" in out, out)
            self.assertIn("token_paste", out)

    def test_report_gives_recommendation_not_raw_numbers_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            build(root, {"m.c": "#define W(s) void h_##s(int i)\n"
                                "W(a) { f(i); }\n"})
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_parse_report(root)
            out = buf.getvalue()
            self.assertIn("→", out, "권고(→) 줄이 있어야 한다")

    def test_informational_gaps_do_not_affect_verdict(self):
        """조건부 컴파일이 많다고 '나쁨'이 되면 안 된다 — 파싱 실패가 아니다."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            files = {f"v{i}.c": f"#ifdef VAR_{i}\nint g{i}(void)"
                                f"{{return 1;}}\n#else\nint g{i}(void)"
                                f"{{return 2;}}\n#endif\n" for i in range(6)}
            build(root, files)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cw.cmd_parse_report(root)
            out = buf.getvalue()
            self.assertEqual(code, 0, out)
            self.assertIn("ifdef_branch", out, "정보성 구멍은 보고는 되어야 한다")


class TestDoctorMentionsParser(unittest.TestCase):

    def test_doctor_reports_tree_sitter_status(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "a.c").write_text("int f(void){return 0;}\n",
                                      encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_init(root, show_next=False)
                try:
                    cw.cmd_doctor(root)
                except SystemExit:
                    pass
            self.assertIn("tree-sitter", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
