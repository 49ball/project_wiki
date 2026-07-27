# -*- coding: utf-8 -*-
"""파싱 오류의 '범인 매크로'를 지목한다.

계기: 사내 코드에서 parse_error 58,248곳이 나왔는데, 도구가
"매크로 전개기가 필요합니다"라고만 하고 **어떤 매크로인지는** 말해주지
않았다. 그래서 원격에서 추측만 반복하게 됐다.

C 관례상 매크로는 대문자다. 오류가 난 줄에서 대문자 식별자를 모으면
범인 후보가 나온다. 완벽한 판별은 아니지만, "다음에 뭘 처리할지"를
정하는 데는 충분하다.
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cw", ROOT / "cw.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


class TestCulpritExtraction(unittest.TestCase):

    def setUp(self):
        ok, reason = cw.ts_status()
        if not ok:
            self.skipTest(reason)

    def culprits(self, src, lang="c"):
        _s, _e, gaps = cw.parse_c_cpp_ts(Path("t.c"), src, lang)
        out = set()
        for g in gaps:
            if g[0] in ("parse_error", "parse_missing") and len(g) > 4 and g[4]:
                out.update(g[4].split(","))
        return out

    def test_macro_wrapped_definition(self):
        got = self.culprits(
            "#define STATIC_INLINE static inline\n"
            "STATIC_INLINE int foo(int x) { return x; }\n")
        self.assertIn("STATIC_INLINE", got)

    def test_vendor_interrupt_macro(self):
        got = self.culprits("IFX_INTERRUPT(isr0, 0, 10) { handle(); }\n")
        self.assertIn("IFX_INTERRUPT", got)

    def test_autosar_func_macro(self):
        got = self.culprits(
            "FUNC(void, RTE_CODE) Rte_Write(void)\n{\n  send();\n}\n")
        self.assertIn("FUNC", got)

    def test_clean_code_has_no_culprits(self):
        self.assertEqual(self.culprits("int foo(int x) { return x; }\n"), set())

    def test_common_constants_are_not_culprits(self):
        """NULL, TRUE 같은 흔한 상수는 범인이 아니다 — 노이즈가 된다."""
        got = self.culprits(
            "#define WRAP static inline\n"
            "WRAP int f(void) { return NULL != 0 ? TRUE : FALSE; }\n")
        self.assertNotIn("NULL", got)
        self.assertNotIn("TRUE", got)
        self.assertIn("WRAP", got)

    def test_gap_tuple_is_five_long(self):
        _s, _e, gaps = cw.parse_c_cpp_ts(
            Path("t.c"), "static h_t t[] = { { 1, cb } };\n", "c")
        for g in gaps:
            self.assertEqual(len(g), 5,
                             "(kind, line, detail, affects_symbol, evidence)")


class TestReportNamesCulprits(unittest.TestCase):
    """parse-report 가 범인 매크로를 이름으로 지목해야 한다."""

    def setUp(self):
        ok, reason = cw.ts_status()
        if not ok:
            self.skipTest(reason)

    def test_report_lists_top_culprit_macros(self):
        import io
        import subprocess
        import tempfile
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for i in range(4):
                (root / f"m{i}.c").write_text(
                    "#define VENDOR_INLINE static inline\n"
                    "VENDOR_INLINE int f(int x) { return x; }\n",
                    encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_init(root, show_next=False)
                cw.cmd_index(root)
                cw.cmd_parse_report(root)
            out = buf.getvalue()
            self.assertIn("VENDOR_INLINE", out,
                          "범인 매크로를 이름으로 지목해야 한다")


if __name__ == "__main__":
    unittest.main()
