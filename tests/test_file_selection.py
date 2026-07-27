# -*- coding: utf-8 -*-
"""무엇을 읽을지 / 어떤 문법으로 읽을지.

실측 계기 (사내 9,099파일 코드베이스):
  - parse_error 상위 5개가 sparse_map.pb.h(4658곳), acados_solver.in.c(2213곳),
    lanelet_map.h(794곳) 등이었다.
  - .pb.h 는 protobuf 생성 코드, .in.c 는 코드 생성용 템플릿(C가 아님),
    lanelet_map.h 는 C++ 인데 확장자가 .h 라 C 문법으로 읽히고 있었다.
  - .h 에 C++ 문법이 있는 파일이 1,356개였다.
  즉 오류의 주원인은 매크로가 아니라 '엉뚱한 걸 엉뚱한 문법으로 읽는 것'이었다.
"""
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cw", ROOT / "cw.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


class TestGeneratedFileExclusion(unittest.TestCase):
    """생성 코드·템플릿은 색인하지 않는다.

    사람이 문서화할 대상이 아니고, 파싱해봐야 오류만 쌓이며,
    파일 수를 부풀려 커버리지 비율까지 왜곡한다.
    """

    def _names(self, filenames):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for fn in filenames:
                p = root / fn
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("int f(void){return 0;}\n", encoding="utf-8")
            cfg = cw.load_config(root)
            return {p.name for p in cw.iter_source_files(root, cfg)}

    def test_excludes_protobuf_generated(self):
        got = self._names(["sparse_map.pb.h", "sparse_map.pb.cc", "real.c"])
        self.assertEqual(got, {"real.c"})

    def test_excludes_codegen_templates(self):
        """`.in.c` 는 코드 생성 입력 템플릿이다. 애초에 C 문법이 아니다."""
        got = self._names(["acados_solver.in.c", "solver.in.h", "real.c"])
        self.assertEqual(got, {"real.c"})

    def test_keeps_normal_files_with_in_in_name(self):
        """'in' 이 들어갔다고 다 템플릿은 아니다. 정확히 .in.c 만 제외."""
        got = self._names(["input.c", "main.c", "inline_util.h"])
        self.assertEqual(got, {"input.c", "main.c", "inline_util.h"})

    def test_user_can_override_via_config(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "keep.pb.h").write_text("int f(void);\n", encoding="utf-8")
            (root / ".codewiki").mkdir()
            (root / ".codewiki" / "config.json").write_text(
                '{"exclude_files": []}', encoding="utf-8")
            cfg = cw.load_config(root)
            got = {p.name for p in cw.iter_source_files(root, cfg)}
            self.assertIn("keep.pb.h", got, "사용자가 껐으면 포함되어야 한다")


class TestHeaderGrammarDetection(unittest.TestCase):
    """`.h` 는 C 일 수도 C++ 일 수도 있다. 확장자만 믿으면 안 된다."""

    def setUp(self):
        ok, reason = cw.ts_status()
        if not ok:
            self.skipTest(reason)

    CPP_HEADER = (
        "#ifndef LANELET_MAP_H\n#define LANELET_MAP_H\n"
        "namespace lanelet {\n"
        "class LaneletMap {\n"
        " public:\n"
        "  explicit LaneletMap(int id) : id_(id) {}\n"
        "  virtual ~LaneletMap() = default;\n"
        "  template <typename T> T get() const { return T(); }\n"
        " private:\n"
        "  int id_;\n"
        "};\n"
        "}  // namespace lanelet\n#endif\n")

    C_HEADER = (
        "#ifndef APP_C_DOMAIN_H_\n#define APP_C_DOMAIN_H_\n"
        "typedef enum { APP_C_DOMAIN_BODY, APP_C_DOMAIN_MAX } app_c_domain_e;\n"
        "int app_c_domain_get(app_c_domain_e d);\n#endif\n")

    def _errors(self, path, src):
        # parse_file 은 (symbols, edges) 를 반환하고 구멍은 _LAST_GAPS 에 남긴다
        syms, _edges = cw.parse_file(Path(path), "c", src)
        return len([g for g in cw._LAST_GAPS
                    if g[0] in ("parse_error", "parse_missing")]), len(syms)

    def test_cpp_header_named_h_parses_cleanly(self):
        errs, syms = self._errors("lanelet_map.h", self.CPP_HEADER)
        self.assertEqual(errs, 0,
                         "C++ 헤더를 C 문법으로 읽어 오류가 남았다")
        self.assertGreaterEqual(syms, 2)

    def test_c_header_still_works(self):
        errs, syms = self._errors("app_c_domains.h", self.C_HEADER)
        self.assertEqual(errs, 0)
        names = {s[0] for s in cw.parse_file(
            Path("app_c_domains.h"), "c", self.C_HEADER)[0]}
        self.assertIn("app_c_domain_e", names)
        self.assertIn("APP_C_DOMAIN_BODY", names)

    def test_dot_c_is_not_retried_as_cpp(self):
        """.c 는 C 다. 여기까지 추측하면 진짜 C 오류를 숨기게 된다."""
        syms, _e = cw.parse_file(
            Path("weird.c"), "c", "int f(void) { return 0; }\n")
        self.assertTrue(any(s[0] == "f" for s in syms))


if __name__ == "__main__":
    unittest.main()
