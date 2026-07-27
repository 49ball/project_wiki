# -*- coding: utf-8 -*-
"""배지·각주 파싱 — 표기법의 기반.

설계: docs/superpowers/specs/2026-07-27-confidence-notation-design.md
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cw", ROOT / "cw.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)

SAMPLE = """## 책임

- ✅ CAN 프레임을 디코딩해 상위로 올린다[^handle_frame]
- ✅ 불량 CRC 프레임은 버린다[^check_crc]
- 🔍 큐를 쓰는 건 ISR에서 동적 할당을 피하려는 것[^isr_alloc]
- 📄 설계 시 동적 할당을 금지했다[^commit_a3f21b]
- ❓ 실제 출하되는 변형이 무엇인지
- 그냥 설명 문장 (배지 없음)

## 주의점

- ⚠️ 핸들러는 매크로로 테이블 등록된다[^can_table]

[^handle_frame]: `src/hal/can.c#handle_frame`
[^check_crc`]: 잘못된 라벨은 매칭되지 않아야 한다
[^check_crc]: `src/hal/can.c#check_crc`
[^isr_alloc]: ISR 문맥에서 호출됨 — `src/hal/can.c:88`
[^commit_a3f21b]: 커밋 a3f21b "fix: ISR에서 동적할당 제거"
[^can_table]: `src/hal/can_table.c:12`
"""


class TestParseClaims(unittest.TestCase):

    def test_finds_all_badged_lines(self):
        claims = cw.parse_claims(SAMPLE)
        self.assertEqual(len(claims), 6, [c.text for c in claims])

    def test_badge_maps_to_grade(self):
        grades = [c.badge for c in cw.parse_claims(SAMPLE)]
        self.assertEqual(
            grades,
            ["confirmed", "confirmed", "inferred", "sourced",
             "unknown", "caution"])

    def test_warning_emoji_with_variation_selector(self):
        """⚠️는 U+26A0 + U+FE0F 두 코드포인트다. 긴 것부터 매칭해야 한다."""
        for src in ("- ⚠️ 변이 선택자 있음\n", "- ⚠ 변이 선택자 없음\n"):
            claims = cw.parse_claims(src)
            self.assertEqual(len(claims), 1, src)
            self.assertEqual(claims[0].badge, "caution")
            self.assertNotIn("️", claims[0].text,
                             "변이 선택자가 본문에 남았다")

    def test_extracts_footnote_refs(self):
        claims = cw.parse_claims(SAMPLE)
        self.assertEqual(claims[0].refs, ["handle_frame"])
        self.assertEqual(claims[4].refs, [], "❓는 각주가 없다")

    def test_ref_marker_removed_from_text(self):
        claims = cw.parse_claims(SAMPLE)
        self.assertNotIn("[^", claims[0].text)
        self.assertTrue(claims[0].text.endswith("올린다"), claims[0].text)

    def test_line_numbers_are_one_based(self):
        claims = cw.parse_claims(SAMPLE)
        self.assertEqual(claims[0].line, 3)

    def test_ignores_unbadged_bullets(self):
        for c in cw.parse_claims(SAMPLE):
            self.assertNotIn("그냥 설명", c.text)


class TestParseFootnotes(unittest.TestCase):

    def test_collects_definitions(self):
        notes = cw.parse_footnotes(SAMPLE)
        self.assertIn("handle_frame", notes)
        self.assertIn("`src/hal/can.c#handle_frame`", notes["handle_frame"])

    def test_ignores_malformed_label(self):
        notes = cw.parse_footnotes(SAMPLE)
        self.assertNotIn("check_crc`", notes)

    def test_definition_must_start_at_line_start(self):
        """본문 안의 [^ref] 는 정의가 아니다."""
        notes = cw.parse_footnotes("- ✅ 문장[^x]\n\n[^x]: 진짜 정의\n")
        self.assertEqual(list(notes), ["x"])


class TestAnchorSymPrefixOptional(unittest.TestCase):

    def test_accepts_without_sym_prefix(self):
        m = cw.RE_ANCHOR_SYM.match("src/hal/can.c#handle_frame")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("path"), "src/hal/can.c")
        self.assertEqual(m.group("name"), "handle_frame")

    def test_still_accepts_legacy_sym_prefix(self):
        m = cw.RE_ANCHOR_SYM.match("sym:src/hal/can.c#handle_frame")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("path"), "src/hal/can.c")

    def test_line_anchor_not_swallowed(self):
        self.assertIsNone(cw.RE_ANCHOR_SYM.match("src/hal/can.c:88"))


class TestSummaryCallout(unittest.TestCase):

    BODY = ("- ✅ 하나[^a]\n- ✅ 둘[^b]\n- 🔍 셋[^c]\n- ❓ 넷\n"
            "\n[^a]: x\n[^b]: y\n[^c]: z\n")

    def test_counts_by_badge(self):
        line = cw.summary_callout(self.BODY)
        self.assertIn("✅확인 2", line)
        self.assertIn("🔍추론 1", line)
        self.assertIn("❓모름 1", line)

    def test_omits_zero_counts(self):
        line = cw.summary_callout(self.BODY)
        self.assertNotIn("📄", line, "0건인 배지는 적지 않는다")

    def test_empty_when_no_claims(self):
        self.assertEqual(cw.summary_callout("그냥 산문입니다.\n"), "")

    def test_inserts_after_frontmatter(self):
        text = "---\ntype: module\n---\n\n# 제목\n\n" + self.BODY
        out = cw.apply_summary(text)
        lines = out.split("\n")
        self.assertEqual(lines[0], "---")
        idx = next(i for i, l in enumerate(lines) if l.startswith("> [!info]"))
        end_fm = next(i for i, l in enumerate(lines[1:], 1) if l == "---")
        self.assertGreater(idx, end_fm, "프론트매터 뒤에 와야 한다")
        self.assertLess(idx, lines.index("# 제목"), "제목 앞에 와야 한다")

    def test_is_idempotent(self):
        text = "---\ntype: module\n---\n\n" + self.BODY
        once = cw.apply_summary(text)
        twice = cw.apply_summary(once)
        self.assertEqual(once, twice, "두 번 적용해도 같아야 한다")

    def test_replaces_stale_summary(self):
        text = ("---\ntype: module\n---\n\n"
                "> [!info] ✅확인 99 · 🔍추론 99\n\n" + self.BODY)
        out = cw.apply_summary(text)
        self.assertNotIn("99", out)
        self.assertIn("✅확인 2", out)

    def test_no_frontmatter_still_works(self):
        out = cw.apply_summary(self.BODY)
        self.assertTrue(out.startswith("> [!info]"))


if __name__ == "__main__":
    unittest.main()
