# -*- coding: utf-8 -*-
"""파서 적합성 — 현행 정규식 파서와 tree-sitter 파서를 같은 케이스로 잰다."""
import importlib.util
import unittest
from pathlib import Path

from tests.cases import PARSER_CASES

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cw", ROOT / "cw.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


def symbols_of(pairs):
    """(name, kind, sig, ls, le, prov) 튜플들에서 함수 이름만 정렬해 뽑는다."""
    return sorted(s[0] for s in pairs if s[1] == "function")


class TestLegacyParserBaseline(unittest.TestCase):
    """현행 정규식 파서의 기준선을 기록한다.

    통과/실패를 단언하지 않는다 — 이 클래스의 목적은 '지금 어디까지 되는가'를
    남겨서 tree-sitter 교체 후 개선폭을 말할 수 있게 하는 것이다.
    """

    def test_baseline_report(self):
        passed, failed = [], []
        for c in PARSER_CASES:
            got = symbols_of(cw.parse_c_cpp(Path("t"), c.src)[0])
            (passed if got == sorted(c.symbols) else failed).append(c.name)
        print("\n[현행 정규식 파서 기준선]")
        print(f"  통과 {len(passed)}/{len(PARSER_CASES)}")
        for n in failed:
            print(f"  실패: {n}")
        self.assertGreater(len(passed), 0, "기준선 수집 자체가 실패했다면 하네스 버그")


class TestTreeSitterParser(unittest.TestCase):

    def setUp(self):
        ok, reason = cw.ts_status()
        if not ok:
            self.skipTest(reason)

    def test_symbols(self):
        bad = []
        for c in PARSER_CASES:
            r = cw.parse_c_cpp_ts(Path("t"), c.src, c.lang)
            self.assertIsNotNone(r, f"{c.name}: 파서가 None 반환")
            got = symbols_of(r[0])
            if got != sorted(c.symbols):
                bad.append(f"{c.name}: 기대 {sorted(c.symbols)}, 실측 {got}")
        self.assertEqual(bad, [], "\n".join(bad))

    def test_returns_three_tuples(self):
        r = cw.parse_c_cpp_ts(Path("t"), "int f(void){return 0;}\n", "c")
        self.assertEqual(len(r), 3, "(symbols, edges, gaps) 3-튜플이어야 한다")

    def test_clean_code_has_no_gaps(self):
        """깨끗한 코드에서 구멍이 뜨면 커버리지 경고가 노이즈가 된다."""
        for c in PARSER_CASES:
            if c.gaps:
                continue
            _s, _e, gaps = cw.parse_c_cpp_ts(Path("t"), c.src, c.lang)
            self.assertEqual(gaps, [], f"{c.name}: 깨끗한 코드에 구멍 {gaps}")

    def test_extracts_includes_and_calls(self):
        _s, edges, _g = cw.parse_c_cpp_ts(
            Path("t"),
            '#include "server.h"\nvoid run(void) { init(); step(); }\n', "c")
        # 엣지 튜플: (src_sym, dst_name, dst_file, kind, provenance, confidence)
        incs = [e[1] for e in edges if e[3] == "includes"]
        calls = [(e[0], e[1]) for e in edges if e[3] == "calls"]
        self.assertEqual(incs, ["server.h"])
        self.assertEqual(sorted(calls), [("run", "init"), ("run", "step")])


if __name__ == "__main__":
    unittest.main()
