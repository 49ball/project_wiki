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


if __name__ == "__main__":
    unittest.main()
