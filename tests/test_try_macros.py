# -*- coding: utf-8 -*-
"""후보 매크로를 실제로 지워보고 도움이 되는 것만 골라준다.

계기: ignore_macros 를 만들고 나서 실측해보니 매크로 종류별로 결과가
정반대였다.

  MEDIA_PUBLIC void f(int);      지우면 → 구멍 사라짐        (장식)
  RTI_BOOL check_cert(int);      지우면 → **함수가 사라짐**  (타입)
  MOCK_METHOD(int, run, (int));  지우면 → 구멍 1개가 3개로   (인자 받는)

그런데 셋은 겉모습이 똑같다. 그냥 대문자 단어다. 소스만 보고는 구분할
방법이 없다.

설계 §2.2-①은 "애매하면 알아서 판단해"를 금지한다. 사내 AI는 판단력이
약하고, 사용자는 출력을 반출할 수 없다. 그러므로 **기계가 직접 지워보고
재서** 판정해야 한다. 추측이 아니라 실측이다.

가장 중요한 것은 '해로움' 판정이다. 심볼이 하나라도 사라지는 매크로를
목록에 넣으면 위키에서 함수가 조용히 증발한다. 이 프로젝트가 가장
피하려는 일이다.
"""
import importlib.util
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cw", ROOT / "cw.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


class TestClassifyMacro(unittest.TestCase):
    """한 매크로를 지웠을 때 좋아지는가 / 나빠지는가."""

    def setUp(self):
        ok, reason = cw.ts_status()
        if not ok:
            self.skipTest(reason)

    def verdict(self, name, src, lang="c", suffix=".c"):
        return cw.try_macro([(Path("t" + suffix), lang, src)], name)

    def test_decoration_macro_helps(self):
        v = self.verdict("MEDIA_PUBLIC",
                         "MEDIA_PUBLIC void start_camera(int id);\n"
                         "MEDIA_PUBLIC int stop_camera(void);\n")
        self.assertEqual(v["verdict"], "도움됨")
        self.assertGreater(v["gaps_before"] - v["gaps_after"], 0)
        self.assertEqual(v["lost_symbols"], [])

    def test_type_macro_is_harmful(self):
        """반환 타입을 지우면 함수 자체가 사라진다 — 절대 권하면 안 된다."""
        v = self.verdict("RTI_BOOL", "RTI_BOOL check_cert(int id);\n")
        self.assertEqual(v["verdict"], "해로움")
        self.assertIn("check_cert", v["lost_symbols"])

    def test_function_like_macro_is_not_recommended(self):
        """인자를 받는 매크로는 이름만 지우면 괄호가 남아 더 나빠진다."""
        v = self.verdict("C_VEC_MEM", "C_VEC_MEM(int, buf, 32);\n")
        self.assertNotEqual(v["verdict"], "도움됨")

    def test_losing_a_garbage_symbol_is_not_harm(self):
        """깨진 파싱이 뱉은 쓰레기 이름이 사라지는 건 이득이지 손해가 아니다.

        `MEDIA_PUBLIC class Foo {...}` 를 잘못 읽으면 이름이 `class` 인
        심볼이 튀어나온다. 매크로를 지우면 그게 사라지는데, 이걸 '심볼
        손실'로 세면 멀쩡한 장식 매크로가 '해로움'으로 막힌다.
        """
        v = cw.try_macro(
            [(Path("t.cpp"), "cpp",
              "MEDIA_PUBLIC class Foo { int run(int a); };\n")],
            "MEDIA_PUBLIC")
        self.assertNotIn("class", v["lost_symbols"])
        self.assertNotEqual(v["verdict"], "해로움")

    def test_irrelevant_macro_has_no_effect(self):
        v = self.verdict("NOT_PRESENT", "int plain(void);\n")
        self.assertEqual(v["verdict"], "효과 없음")

    def test_harmful_beats_helpful(self):
        """일부 파일에서 이득이 나도 심볼을 잃으면 '해로움'이다."""
        src = ("RTI_BOOL check_cert(int id);\n"
               "RTI_BOOL void weird(int id);\n")
        v = self.verdict("RTI_BOOL", src)
        self.assertEqual(v["verdict"], "해로움")


class TestTryMacrosCommand(unittest.TestCase):

    def setUp(self):
        ok, reason = cw.ts_status()
        if not ok:
            self.skipTest(reason)

    def build(self, root):
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        for i in range(3):
            (root / f"m{i}.c").write_text(
                f"MEDIA_PUBLIC void start_{i}(int id);\n"
                f"RTI_BOOL check_{i}(int id);\n", encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            cw.cmd_init(root, show_next=False)
            cw.cmd_index(root)
        return buf

    def test_recommends_only_the_safe_one(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.build(root)
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_try_macros(root)
            out = buf.getvalue()
            self.assertIn("MEDIA_PUBLIC", out)
            # 붙여넣기용 목록에 해로운 이름이 섞이면 안 된다
            snippet = out.split('"ignore_macros"')[-1].split("\n")[0]
            self.assertIn("MEDIA_PUBLIC", snippet)
            self.assertNotIn("RTI_BOOL", snippet)

    def test_measures_files_without_gaps_too(self):
        """구멍이 없는 파일에서 손해가 나는 매크로를 추천하면 안 된다.

        구멍이 기록된 파일만 재면 안전해 보이지만, 설정은 **모든 파일**에
        적용된다. 같은 매크로가 다른 파일에서 타입으로 쓰이고 있으면
        그 파일의 함수가 조용히 사라진다. 재보지도 않은 채로.
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            # 구멍이 나는 파일 — 여기서만 보면 RTI_BOOL 을 지우는 게 이득이다
            for i in range(3):
                (root / f"gap{i}.c").write_text(
                    f"MEDIA_PUBLIC RTI_BOOL check_{i}(int id);\n",
                    encoding="utf-8")
            # 구멍이 없는 파일 — 여기서 지우면 함수가 사라진다
            for i in range(3):
                (root / f"clean{i}.c").write_text(
                    f"RTI_BOOL verify_{i}(int id);\n", encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_init(root, show_next=False)
                cw.cmd_index(root)
                cw.cmd_try_macros(root)
            out = buf.getvalue()
            snippet = out.split('"ignore_macros"')[-1].split("\n")[0]
            self.assertNotIn("RTI_BOOL", snippet,
                             "구멍 없는 파일의 손해를 못 보고 추천했다")
            self.assertIn("MEDIA_PUBLIC", snippet)

    def test_reports_when_nothing_helps(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "a.c").write_text("int plain(void);\n", encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_init(root, show_next=False)
                cw.cmd_index(root)
                cw.cmd_try_macros(root)
            self.assertIn("없습니다", buf.getvalue())


class TestReportDoesNotDumpRawList(unittest.TestCase):
    """parse-report 가 검증 없이 붙여넣기 목록을 뱉으면 안 된다.

    RTI_BOOL 같은 타입 매크로가 섞여 들어가면 위키에서 함수가 사라진다.
    사내에서는 그걸 알아챌 방법이 없다.
    """

    def setUp(self):
        ok, reason = cw.ts_status()
        if not ok:
            self.skipTest(reason)

    def test_report_points_to_try_macros_instead(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for i in range(4):
                (root / f"m{i}.c").write_text(
                    "VENDOR_INLINE int f(int x) { return x; }\n",
                    encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_init(root, show_next=False)
                cw.cmd_index(root)
                cw.cmd_parse_report(root)
            out = buf.getvalue()
            self.assertIn("VENDOR_INLINE", out, "범인은 그대로 지목해야 한다")
            self.assertIn("try-macros", out, "검증 명령으로 안내해야 한다")
            self.assertNotIn('"ignore_macros": [', out,
                             "검증 없이 붙여넣을 목록을 주면 안 된다")


if __name__ == "__main__":
    unittest.main()
