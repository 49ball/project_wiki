# -*- coding: utf-8 -*-
"""tree-sitter 가용성 판정 — 없어도 죽지 않고 사유를 설명해야 한다."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cw", ROOT / "cw.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


class TestTsAvailability(unittest.TestCase):

    def test_status_returns_bool_and_reason(self):
        ok, reason = cw.ts_status()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(reason, str)
        self.assertTrue(reason, "사유 문자열이 비어 있으면 안 된다")

    def test_languages_shape(self):
        langs = cw.ts_languages()
        if langs is None:
            ok, _ = cw.ts_status()
            self.assertFalse(ok, "언어를 못 얻었는데 status 가 True 면 모순")
        else:
            self.assertIn("c", langs)
            self.assertIn("cpp", langs)

    def test_never_raises(self):
        for _ in range(3):          # 캐시 경로도 안전한지
            cw.ts_status()
            cw.ts_languages()


if __name__ == "__main__":
    unittest.main()
