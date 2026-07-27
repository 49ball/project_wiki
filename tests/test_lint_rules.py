# -*- coding: utf-8 -*-
"""표기법 lint 규칙."""
import importlib.util
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cw", ROOT / "cw.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


def lint(body):
    """앵커 실존 검사는 건너뛰기 위해 빈 DB 를 쓴다."""
    con = sqlite3.connect(":memory:")
    con.executescript(cw.SCHEMA)
    return cw.lint_notation("t.md", body, ROOT, con.cursor())


class TestBadgeRequiresEvidence(unittest.TestCase):

    def test_inferred_without_basis_is_error(self):
        """기존 ^[inferred] 는 근거가 없어 검증 불가였다. 이제 에러다."""
        errs, _w = lint("- 🔍 큐는 백프레셔 목적으로 보인다\n")
        self.assertTrue(errs)
        self.assertIn("🔍", errs[0])

    def test_inferred_with_basis_is_ok(self):
        errs, _w = lint(
            "- 🔍 큐는 ISR 할당 회피[^b]\n\n[^b]: ISR 문맥에서 호출됨\n")
        self.assertEqual(errs, [])

    def test_confirmed_without_anchor_is_error(self):
        errs, _w = lint("- ✅ 프레임을 디코딩한다\n")
        self.assertTrue(errs)

    def test_sourced_without_source_is_error(self):
        errs, _w = lint("- 📄 설계 시 동적할당을 금지했다\n")
        self.assertTrue(errs)

    def test_unknown_needs_nothing(self):
        errs, _w = lint("- ❓ 실제 출하 변형이 무엇인지\n")
        self.assertEqual(errs, [])

    def test_unknown_with_footnote_is_warning(self):
        """모른다면서 근거가 있는 건 라벨이 잘못됐을 가능성."""
        _e, warns = lint("- ❓ 모르겠다[^x]\n\n[^x]: 근거\n")
        self.assertTrue(warns)


class TestFootnoteIntegrity(unittest.TestCase):

    def test_undefined_reference_is_error(self):
        errs, _w = lint("- ✅ 문장[^missing]\n")
        self.assertTrue(errs)
        self.assertTrue(any("missing" in e for e in errs), errs)

    def test_orphan_definition_is_warning(self):
        _e, warns = lint(
            "- ✅ 문장[^used]\n\n[^used]: 근거\n[^unused]: 아무도 안 씀\n")
        self.assertTrue(warns)
        self.assertTrue(any("unused" in w for w in warns), warns)


class TestOneEvidencePerClaim(unittest.TestCase):

    def test_two_refs_is_warning(self):
        """근거가 둘이면 어느 근거가 어느 부분을 뒷받침하는지 알 수 없다."""
        _e, warns = lint(
            "- ✅ 문장[^a][^b]\n\n[^a]: 하나\n[^b]: 둘\n")
        self.assertTrue(warns)

    def test_one_ref_is_clean(self):
        _e, warns = lint("- ✅ 문장[^a]\n\n[^a]: 하나\n")
        self.assertEqual(warns, [])


if __name__ == "__main__":
    unittest.main()
