# -*- coding: utf-8 -*-
"""장식 매크로를 파싱 전에 지워서 선언이 읽히게 한다.

계기: 사내 parse-report 의 '범인 매크로' 상위 10개를 받아보니 성격이
둘로 갈렸다.

  - 남이 만든 코드(RTI, CasADi, gmock)에 있는 것 → 파일째 제외하면 된다.
    위키에 넣을 코드가 아니므로 이미 exclude_files 로 해결된다.
  - 우리가 짠 코드에 박힌 장식 매크로(MEDIA_PUBLIC 1,002곳,
    ADSTDTFSIMD_FORCE_INLINE 254곳) → 파일을 뺄 수 없다. 문서화 대상이다.

후자는 `MEDIA_PUBLIC void start_camera(int)` 처럼 타입 자리 앞에 붙어서
파서를 깨뜨린다. 매크로가 무엇으로 전개되는지는 알 필요가 없다.
**지우기만 하면 나머지가 그대로 읽힌다.**

이름을 cw.py 에 박아넣지 않고 설정(ignore_macros)으로 받는다.
사내 매크로 이름은 사내 사정이고, 다른 프로젝트는 다른 이름을 쓴다.
"""
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cw", ROOT / "cw.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


class TestBlankMacros(unittest.TestCase):
    """텍스트 치환 자체의 규칙."""

    def test_replaces_with_equal_length_spaces(self):
        """길이를 보존해야 오류 줄·열 번호가 원본과 어긋나지 않는다."""
        src = "MEDIA_PUBLIC void f(void);\n"
        got = cw.blank_macros(src, ["MEDIA_PUBLIC"])
        self.assertEqual(got, " " * len("MEDIA_PUBLIC") + " void f(void);\n")
        self.assertEqual(len(got), len(src))

    def test_preserves_line_count(self):
        src = "MEDIA_PUBLIC int a;\nint b;\nMEDIA_PUBLIC int c;\n"
        got = cw.blank_macros(src, ["MEDIA_PUBLIC"])
        self.assertEqual(got.count("\n"), src.count("\n"))

    def test_empty_list_returns_text_unchanged(self):
        src = "MEDIA_PUBLIC int a;\n"
        self.assertEqual(cw.blank_macros(src, []), src)
        self.assertEqual(cw.blank_macros(src, None), src)

    def test_whole_word_only(self):
        """MEDIA_PUBLIC 을 지운다고 MEDIA_PUBLIC_V2 까지 지우면 안 된다."""
        src = "MEDIA_PUBLIC_V2 int a;\nx_MEDIA_PUBLIC int b;\n"
        self.assertEqual(cw.blank_macros(src, ["MEDIA_PUBLIC"]), src)

    def test_leaves_preprocessor_lines_alone(self):
        """#define 줄에서 이름을 지우면 정의가 깨져 새 오류가 생긴다."""
        src = ("#define MEDIA_PUBLIC __attribute__((visibility(\"default\")))\n"
               "#ifdef MEDIA_PUBLIC\n"
               "MEDIA_PUBLIC int a;\n")
        got = cw.blank_macros(src, ["MEDIA_PUBLIC"]).split("\n")
        self.assertIn("MEDIA_PUBLIC", got[0])
        self.assertIn("MEDIA_PUBLIC", got[1])
        self.assertNotIn("MEDIA_PUBLIC", got[2])

    def test_longer_names_win(self):
        """짧은 이름이 긴 이름의 앞부분을 먼저 먹으면 안 된다."""
        src = "C_VEC_MEM_EXT int a;\n"
        got = cw.blank_macros(src, ["C_VEC_MEM", "C_VEC_MEM_EXT"])
        self.assertNotIn("C_VEC", got)
        self.assertIn("int a;", got)


class TestParsingWithIgnoredMacros(unittest.TestCase):
    """실제로 심볼이 잡히는가."""

    def setUp(self):
        ok, reason = cw.ts_status()
        if not ok:
            self.skipTest(reason)

    def names(self, src, ignore=()):
        syms, _e = cw.parse_file(Path("t.c"), "c", src, ignore_macros=ignore)
        return {s[0] for s in syms}

    # 실측으로 확인한 것: 장식 매크로가 붙어 있어도 이름 자체는 이미 잡힌다
    # (커밋 04898ce '매크로 뭉갬 declarator 처리'). 그러므로 이 기능의 이득은
    # "못 찾던 심볼을 찾는 것"이 아니라 **거짓 구멍을 없애는 것**이다.
    # 거짓 구멍은 판정을 부당하게 '나쁨'으로 만들고, 멀쩡한 심볼에 '확인 필요'
    # 배지를 달아 진짜 못 읽은 곳을 노이즈에 묻는다.

    def test_name_is_already_found_without_ignoring(self):
        """회귀 방지: 매크로를 안 지워도 이름은 잡혀야 한다."""
        src = "MEDIA_PUBLIC void start_camera(int id);\n"
        self.assertIn("start_camera", self.names(src))

    def test_ignoring_removes_the_false_gap(self):
        """지우면 그 자리는 더 이상 '못 읽은 곳'이 아니다."""
        src = "MEDIA_PUBLIC void start_camera(int id);\n"
        self.names(src)
        self.assertIn("parse_error", [g[0] for g in cw._LAST_GAPS])
        self.names(src, ["MEDIA_PUBLIC"])
        self.assertEqual([g[0] for g in cw._LAST_GAPS], [])

    SRC = ("MEDIA_PUBLIC void start_camera(int id);\n"
           "typedef struct { int w; } frame_t;\n"
           "static int helper(int x) { return x + 1; }\n"
           "MEDIA_PUBLIC int process(frame_t *f) { return helper(f->w); }\n")

    def test_ignoring_loses_no_symbol_or_edge(self):
        """지우는 것이 사실을 잃지 않아야 한다 — 이 기능의 안전 조건이다.

        이름·종류·줄 번호가 하나도 달라지면 안 된다. 달라진다면 구멍을
        없애려다 위키의 내용을 바꾼 것이므로 이 기능은 쓰면 안 된다.
        """
        def facts(syms):
            return [(n, k, ls, le) for (n, k, _sig, ls, le, _p) in syms]

        plain_s, plain_e = cw.parse_file(Path("t.c"), "c", self.SRC)
        blank_s, blank_e = cw.parse_file(Path("t.c"), "c", self.SRC,
                                         ignore_macros=["MEDIA_PUBLIC"])
        self.assertEqual(facts(plain_s), facts(blank_s))
        self.assertEqual(plain_e, blank_e)

    def test_signature_drops_the_decoration(self):
        """유일하게 달라지는 것: 시그니처에서 장식 매크로가 빠진다.

        의도한 동작이다. `int process(frame_t *f)` 가 읽는 사람에게
        `MEDIA_PUBLIC int process(frame_t *f)` 보다 낫고, 매크로가 붙어
        있었다는 사실은 소스를 보면 있다. 사고가 아니라 결정이므로 못박아 둔다.
        """
        def sig(syms, name):
            return next(s[2] for s in syms if s[0] == name)

        plain_s, _ = cw.parse_file(Path("t.c"), "c", self.SRC)
        blank_s, _ = cw.parse_file(Path("t.c"), "c", self.SRC,
                                   ignore_macros=["MEDIA_PUBLIC"])
        self.assertEqual(sig(plain_s, "process"),
                         "MEDIA_PUBLIC int process(frame_t *f)")
        self.assertEqual(sig(blank_s, "process"), "int process(frame_t *f)")

    def test_line_numbers_stay_correct(self):
        """지운 뒤에도 심볼의 줄 번호가 원본과 같아야 한다."""
        src = ("int first(void);\n"
               "\n"
               "MEDIA_PUBLIC void target(int id);\n")
        syms, _e = cw.parse_file(Path("t.c"), "c", src,
                                 ignore_macros=["MEDIA_PUBLIC"])
        line = {s[0]: s[3] for s in syms}
        self.assertEqual(line["target"], 3)

    def test_no_gaps_left_for_ignored_macro(self):
        """지웠으면 그 자리는 더 이상 '못 읽은 곳'이 아니다."""
        src = "MEDIA_PUBLIC void f(int id);\n"
        cw.parse_file(Path("t.c"), "c", src, ignore_macros=["MEDIA_PUBLIC"])
        kinds = [g[0] for g in cw._LAST_GAPS]
        self.assertNotIn("parse_error", kinds)

    def test_default_is_no_change(self):
        """ignore_macros 를 안 주면 기존 동작 그대로여야 한다."""
        src = "int plain(void);\n"
        self.assertIn("plain", self.names(src))


class TestConfigWiring(unittest.TestCase):
    """설정 파일에 적은 이름이 색인까지 흘러가는가."""

    def setUp(self):
        ok, reason = cw.ts_status()
        if not ok:
            self.skipTest(reason)

    def test_index_honours_config(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "cam.c").write_text(
                "MEDIA_PUBLIC void start_camera(int id);\n", encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_init(root, show_next=False)
            cfg_p = root / ".codewiki" / "config.json"
            cfg = json.loads(cfg_p.read_text(encoding="utf-8"))
            self.assertIn("ignore_macros", cfg,
                          "cw init 이 이 열쇠를 알려줘야 사용자가 발견한다")
            cfg["ignore_macros"] = ["MEDIA_PUBLIC"]
            cfg_p.write_text(json.dumps(cfg, ensure_ascii=False),
                             encoding="utf-8")
            with redirect_stdout(buf):
                cw.cmd_index(root)
            con = cw.open_db(root)
            names = {r[0] for r in con.execute("SELECT name FROM symbols")}
            self.assertIn("start_camera", names)


# parse-report 가 범인 목록을 곧바로 붙여넣기용 설정으로 뱉게 했다가 걷어냈다.
# 실측해보니 타입 이름으로 쓰이는 매크로(RTI_BOOL 등)를 지우면 함수가
# 통째로 사라지는데, 목록만 봐서는 그런 매크로를 구분할 수 없었다.
# 지금은 `cw try-macros` 가 하나씩 지워보고 안전한 것만 고른다.
# → tests/test_try_macros.py


if __name__ == "__main__":
    unittest.main()
