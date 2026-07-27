# -*- coding: utf-8 -*-
"""gaps 테이블 — 미해석 지점 저장 및 탐지."""
import importlib.util
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.cases import PARSER_CASES

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cw", ROOT / "cw.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


class TestGapsSchema(unittest.TestCase):

    def test_table_exists_with_expected_columns(self):
        con = sqlite3.connect(":memory:")
        con.executescript(cw.SCHEMA)
        cols = {r[1] for r in con.execute("PRAGMA table_info(gaps)")}
        self.assertEqual(
            cols,
            {"id", "file", "line", "kind", "detail",
             "affects_symbol", "status", "resolution", "evidence"})

    def test_status_defaults_to_open(self):
        con = sqlite3.connect(":memory:")
        con.executescript(cw.SCHEMA)
        con.execute("INSERT INTO gaps(file, line, kind) "
                    "VALUES('a.c', 1, 'parse_error')")
        self.assertEqual(
            con.execute("SELECT status FROM gaps").fetchone()[0], "open")


class TestGapDetection(unittest.TestCase):

    def setUp(self):
        ok, reason = cw.ts_status()
        if not ok:
            self.skipTest(reason)

    def test_expected_gap_kinds(self):
        bad = []
        for c in PARSER_CASES:
            _s, _e, gaps = cw.parse_c_cpp_ts(Path("t"), c.src, c.lang)
            kinds = {g[0] for g in gaps}
            missing = c.gaps - kinds
            if missing:
                bad.append(f"{c.name}: 기대한 구멍 {missing} 이 안 잡힘 (실측 {kinds})")
        self.assertEqual(bad, [], "\n".join(bad))

    def test_gap_tuple_shape(self):
        src = "static handler_t t[] = { { 1, cb } };\n"
        _s, _e, gaps = cw.parse_c_cpp_ts(Path("t"), src, "c")
        self.assertTrue(gaps)
        for g in gaps:
            self.assertEqual(
                len(g), 5,
                "(kind, line, detail, affects_symbol, evidence)")
            self.assertIsInstance(g[1], int)

    def test_fnptr_table_records_referenced_name(self):
        src = "static handler_t t[] = { { 0x1A0, handle_can } };\n"
        _s, _e, gaps = cw.parse_c_cpp_ts(Path("t"), src, "c")
        details = " ".join(g[2] for g in gaps if g[0] == "fnptr_table")
        self.assertIn("handle_can", details)

    def test_include_guard_is_not_a_gap(self):
        """가드는 거의 모든 헤더에 있다. 이걸 세면 판정이 항상 '나쁨'이 되고
        커버리지 경고가 노이즈가 되어 무시당한다(설계 §6.1)."""
        for src in (
            "#ifndef FOO_H\n#define FOO_H\nint f(void);\n#endif\n",
            "// 헤더\n#ifndef FOO_H\n#define FOO_H\nint f(void);\n#endif\n",
        ):
            _s, _e, gaps = cw.parse_c_cpp_ts(Path("t"), src, "c")
            self.assertNotIn("ifdef_branch", {g[0] for g in gaps},
                             f"인클루드 가드를 구멍으로 셌다: {src!r}")

    def test_real_variant_is_a_gap(self):
        for src in (
            "#ifdef VARIANT_EU\nint go(void){return 1;}\n"
            "#else\nint go(void){return 2;}\n#endif\n",
            "#ifdef DEBUG\nint dbg(void){return 1;}\n#endif\n",
        ):
            _s, _e, gaps = cw.parse_c_cpp_ts(Path("t"), src, "c")
            self.assertIn("ifdef_branch", {g[0] for g in gaps},
                          f"진짜 변형을 놓쳤다: {src!r}")

    def test_variant_group_counted_once(self):
        """#ifdef/#else 한 쌍은 구멍 1개다. else 까지 세면 중복이다."""
        src = ("#ifdef A\nint a(void){return 1;}\n"
               "#else\nint a(void){return 2;}\n#endif\n")
        _s, _e, gaps = cw.parse_c_cpp_ts(Path("t"), src, "c")
        self.assertEqual(
            len([g for g in gaps if g[0] == "ifdef_branch"]), 1)


class TestIndexStoresGaps(unittest.TestCase):

    def setUp(self):
        ok, reason = cw.ts_status()
        if not ok:
            self.skipTest(reason)

    def test_index_writes_gaps_rows(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "src").mkdir()
            (root / "src" / "can.c").write_text(
                "#define DEFINE_HANDLER(s) void handle_##s##_frame(int id)\n"
                "DEFINE_HANDLER(radar) { process(id); }\n"
                "static handler_t tbl[] = { { 0x1A0, handle_can } };\n",
                encoding="utf-8")
            cw.cmd_init(root, show_next=False)
            cw.cmd_index(root)
            con = cw.open_db(root)
            rows = con.execute(
                "SELECT kind, file FROM gaps ORDER BY kind").fetchall()
            kinds = {r[0] for r in rows}
            self.assertIn("token_paste", kinds)
            self.assertIn("fnptr_table", kinds)
            self.assertTrue(all(r[1] == "src/can.c" for r in rows),
                            f"file 칼럼이 상대경로여야 한다: {rows}")

    def test_reindex_does_not_duplicate_gaps(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "a.c").write_text(
                "static t_t x[] = { { 1, cb } };\n", encoding="utf-8")
            cw.cmd_init(root, show_next=False)
            cw.cmd_index(root)
            first = cw.open_db(root).execute(
                "SELECT COUNT(*) FROM gaps").fetchone()[0]
            cw.cmd_index(root)
            second = cw.open_db(root).execute(
                "SELECT COUNT(*) FROM gaps").fetchone()[0]
            self.assertEqual(first, second, "재색인 시 gaps 가 중복 누적됨")


if __name__ == "__main__":
    unittest.main()
