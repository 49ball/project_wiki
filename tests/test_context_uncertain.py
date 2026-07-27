# -*- coding: utf-8 -*-
"""query 사다리 — '확인 필요'를 기계가 판정한다.

AI 에게 '애매하면 코드 봐'라고 하면 약한 모델은 아예 안 열어보거나
매번 다 열어본다. 규칙으로 판정해 목록을 주면 목록 소비로 끝난다.
"""
import importlib.util
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cw", ROOT / "cw.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


def make_db(symbols=(), gaps=()):
    con = sqlite3.connect(":memory:")
    con.executescript(cw.SCHEMA)
    cur = con.cursor()
    for i, (path, name) in enumerate(symbols, 1):
        cur.execute("INSERT INTO files(id,path,sha,lang,loc) "
                    "VALUES(?,?,'x','c',10)", (i, path))
        cur.execute("INSERT INTO symbols(file_id,name,kind,signature,"
                    "line_start,line_end,provenance) "
                    "VALUES(?,?,'function','',1,2,'tree-sitter')", (i, name))
    for path, kind, detail in gaps:
        cur.execute("INSERT INTO gaps(file,line,kind,detail,status) "
                    "VALUES(?,1,?,?,'open')", (path, kind, detail))
    return cur


class TestUncertainties(unittest.TestCase):

    def test_duplicate_symbol_names_flagged(self):
        cur = make_db(symbols=[("a.c", "init"), ("b.c", "init"),
                               ("c.c", "init")])
        out = cw.uncertainties(ROOT, cur, "init", ["a.c", "b.c", "c.c"])
        self.assertTrue(any("3곳" in u for u in out), out)

    def test_single_definition_not_flagged(self):
        cur = make_db(symbols=[("a.c", "solo")])
        out = cw.uncertainties(ROOT, cur, "solo", ["a.c"])
        self.assertFalse(any("곳에 정의" in u for u in out), out)

    def test_gaps_in_related_file_flagged(self):
        cur = make_db(symbols=[("can.c", "handle")],
                      gaps=[("can.c", "macro_mangled_decl", "매크로가 가림")])
        out = cw.uncertainties(ROOT, cur, "handle", ["can.c"])
        self.assertTrue(any("can.c" in u for u in out), out)

    def test_informational_gaps_are_not_flagged(self):
        """ifdef 는 코드의 성질이지 '못 읽음'이 아니다. 노이즈가 된다."""
        cur = make_db(symbols=[("v.c", "go")],
                      gaps=[("v.c", "ifdef_branch", "조건부 컴파일")])
        out = cw.uncertainties(ROOT, cur, "go", ["v.c"])
        self.assertEqual(out, [])

    def test_clean_case_returns_empty(self):
        cur = make_db(symbols=[("a.c", "clean")])
        self.assertEqual(cw.uncertainties(ROOT, cur, "clean", ["a.c"]), [])

    def test_symbol_not_found_is_flagged(self):
        cur = make_db()
        out = cw.uncertainties(ROOT, cur, "ghost", [])
        self.assertTrue(any("찾지 못" in u for u in out), out)


if __name__ == "__main__":
    unittest.main()
