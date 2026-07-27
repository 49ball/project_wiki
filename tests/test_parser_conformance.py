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


class TestHeaderDeclarations(unittest.TestCase):
    """C 헤더는 대부분 '선언'이다. 정의만 뽑으면 헤더가 통째로 안 보인다.

    실측 계기: 사내 코드에서 528개 파일이 참조하는 헤더
    (typedef enum 만 들어있는 도메인 정의 파일)에서 심볼이 0개로 나왔다.
    그러면 위키가 그 파일을 다룰 수도, 근거 주소를 달 수도 없다.
    """

    def setUp(self):
        ok, reason = cw.ts_status()
        if not ok:
            self.skipTest(reason)

    def kinds_of(self, src, lang="c"):
        syms, _e, _g = cw.parse_c_cpp_ts(Path("h.h"), src, lang)
        return {(s[0], s[1]) for s in syms}

    def test_typedef_anonymous_enum(self):
        """typedef enum { ... } name_e; — C 에서 가장 흔한 관용구."""
        got = self.kinds_of(
            "typedef enum {\n  APP_C_DOMAIN_NONE = 0,\n"
            "  APP_C_DOMAIN_BODY,\n} app_c_domain_e;\n")
        self.assertIn(("app_c_domain_e", "typedef"), got)
        self.assertIn(("APP_C_DOMAIN_NONE", "enum_constant"), got)
        self.assertIn(("APP_C_DOMAIN_BODY", "enum_constant"), got)

    def test_named_enum_members(self):
        got = self.kinds_of("enum color_e { RED, GREEN };\n")
        self.assertIn(("color_e", "enum"), got)
        self.assertIn(("RED", "enum_constant"), got)

    def test_typedef_alias(self):
        got = self.kinds_of("typedef unsigned int domain_id_t;\n")
        self.assertIn(("domain_id_t", "typedef"), got)

    def test_typedef_anonymous_struct(self):
        got = self.kinds_of("typedef struct { int x; } point_t;\n")
        self.assertIn(("point_t", "typedef"), got)

    def test_function_prototype_is_not_a_definition(self):
        """프로토타입은 정의가 아니다. kind 를 구분해야
        '어디에 구현됐나'와 '어디에 선언됐나'를 섞지 않는다."""
        got = self.kinds_of("int do_thing(int x);\n")
        self.assertIn(("do_thing", "prototype"), got)
        self.assertNotIn(("do_thing", "function"), got)

    def test_object_and_function_macros(self):
        got = self.kinds_of("#define MAX_DOMAIN 16\n#define SQ(x) ((x)*(x))\n")
        self.assertIn(("MAX_DOMAIN", "macro"), got)
        self.assertIn(("SQ", "macro_fn"), got)

    def test_extern_global(self):
        got = self.kinds_of("extern int g_domain_count;\n")
        self.assertIn(("g_domain_count", "variable"), got)

    def test_local_variables_are_not_symbols(self):
        """함수 안의 지역변수까지 심볼로 잡으면 노이즈로 못 쓰게 된다."""
        got = self.kinds_of(
            "int f(void) {\n  int local_tmp = 1;\n  return local_tmp;\n}\n")
        names = {n for n, _k in got}
        self.assertIn("f", names)
        self.assertNotIn("local_tmp", names)

    def test_real_world_domain_header(self):
        """사내에서 발견된 실제 모양 — 가드 + typedef enum 뿐인 헤더."""
        src = ("#ifndef APP_C_DOMAIN_H_\n#define APP_C_DOMAIN_H_\n\n"
               "typedef enum {\n    APP_C_DOMAIN_NONE = 0,\n"
               "    APP_C_DOMAIN_BODY,\n    APP_C_DOMAIN_CHASSIS,\n"
               "    APP_C_DOMAIN_MAX\n} app_c_domain_e;\n\n#endif\n")
        syms, _e, gaps = cw.parse_c_cpp_ts(Path("app_c_domains.h"), src, "c")
        self.assertGreaterEqual(
            len(syms), 5,
            f"528개 파일이 참조하는 헤더인데 심볼이 {len(syms)}개뿐이면 "
            f"위키가 이 파일을 다룰 수 없다")
        self.assertEqual([g for g in gaps if g[0] == "ifdef_branch"], [],
                         "가드를 구멍으로 세면 안 된다")


if __name__ == "__main__":
    unittest.main()
