# -*- coding: utf-8 -*-
"""위키가 '덜 쓰인 채로' 완료되는 것을 lint 가 막는다.

계기: 사내에서 모듈이 12개인데 AI가 2개만 문서화하고 끝내버렸다.

원인은 둘이었다.

  1. 지시문이 스스로 멈추라고 시켰다 — "모듈 3~10개", "흐름 3~7개만",
     "전부 다 만들지 마라"
  2. 덜 쓴 것을 잡는 장치가 없었다 — lint 는 표기법·앵커·최신성만 봤다.
     "12개 중 2개만 썼다"는 에러가 아니었다

설계 §2.2-①: '알아서 판단해'를 금지한다. `cw map` 은 이미 디렉터리별 모듈
후보를 정확히 뽑고 있다. 기계가 목록을 갖고 있는데 AI가 임의로 잘라 쓰는 게
문제였다. 그러므로 **남은 것이 있으면 기계가 에러로 지적한다.**

lint 에러 0 이 완료 조건(프롬프트 5단계)이므로, 이 검사가 곧 완료 강제다.
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


def _make_repo(root, dirs):
    """dirs: {디렉터리: 파일수}"""
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for d, n in dirs.items():
        p = root / d
        p.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            (p / f"f{i}.c").write_text(
                f"int {d.replace('/', '_')}_fn{i}(int x) {{ return x; }}\n",
                encoding="utf-8")


def _write_module_doc(root, name, depends):
    d = root / "wiki" / "modules"
    d.mkdir(parents=True, exist_ok=True)
    head = cw.run_git(root, "rev-parse", "--short", "HEAD") or "0000000"
    (d / f"{name}.md").write_text(
        "---\n"
        "type: module\n"
        f"validated_at: {head}\n"
        "depends:\n" + "".join(f"  - {g}\n" for g in depends) +
        "---\n\n"
        f"# {name}\n\n- ❓ 아직 안 씀\n",
        encoding="utf-8")


class TestModuleCandidates(unittest.TestCase):
    """map 과 lint 가 같은 목록을 봐야 한다. 다르면 영원히 안 끝난다."""

    def test_lists_directories_with_size(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_repo(root, {"src/alpha": 4, "src/beta": 3})
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_init(root, show_next=False)
                cw.cmd_index(root)
            cur = cw.open_db(root).cursor()
            got = {name: n for name, n, _loc in cw.module_candidates(cur)}
            self.assertIn("src/alpha", got)
            self.assertIn("src/beta", got)
            self.assertEqual(got["src/alpha"], 4)

    def test_sorted_biggest_first(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_repo(root, {"src/small": 2, "src/big": 6})
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_init(root, show_next=False)
                cw.cmd_index(root)
            cur = cw.open_db(root).cursor()
            names = [n for n, _f, _l in cw.module_candidates(cur)]
            self.assertEqual(names[0], "src/big")


class TestLintCatchesUnfinished(unittest.TestCase):

    def setUp(self):
        ok, reason = cw.ts_status()
        if not ok:
            self.skipTest(reason)

    def lint(self, root):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cw.cmd_lint(root)
        return code, buf.getvalue()

    def test_errors_when_most_modules_undocumented(self):
        """12개 중 2개만 쓴 상황 — 이게 실제로 벌어진 일이다."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_repo(root, {f"src/mod{i:02d}": 3 for i in range(12)})
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_init(root, show_next=False)
                cw.cmd_index(root)
            _write_module_doc(root, "mod00", ["src/mod00/*"])
            _write_module_doc(root, "mod01", ["src/mod01/*"])
            code, out = self.lint(root)
            self.assertNotEqual(code, 0, "덜 쓴 위키가 통과하면 안 된다")
            self.assertIn("src/mod05", out)
            self.assertNotIn("src/mod00", out, "쓴 것은 지적하면 안 된다")

    def test_passes_when_every_module_documented(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_repo(root, {"src/alpha": 3, "src/beta": 3})
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_init(root, show_next=False)
                cw.cmd_index(root)
            _write_module_doc(root, "alpha", ["src/alpha/*"])
            _write_module_doc(root, "beta", ["src/beta/*"])
            _code, out = self.lint(root)
            self.assertNotIn("모듈 문서가 없", out)

    def test_tiny_directories_are_not_errors(self):
        """파일 한두 개짜리 폴더까지 강제하면 목록이 노이즈가 되어 무시당한다."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_repo(root, {"src/main": 5, "src/tiny": 1})
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_init(root, show_next=False)
                cw.cmd_index(root)
            _write_module_doc(root, "main", ["src/main/*"])
            _code, out = self.lint(root)
            self.assertNotIn("src/tiny", out)

    def test_prefix_collision_does_not_count_as_covered(self):
        """`src/mod11` 을 썼다고 `src/mod1` 까지 다뤄진 걸로 세면 안 된다.

        이름이 앞부분만 같은 폴더가 조용히 빠진다. 완성도 검사가 완성도를
        속이는 셈이라 가장 나쁜 종류의 버그다.
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_repo(root, {"src/mod1": 3, "src/mod11": 3})
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_init(root, show_next=False)
                cw.cmd_index(root)
            _write_module_doc(root, "mod11", ["src/mod11/*"])
            _code, out = self.lint(root)
            self.assertIn("src/mod1/", out, "src/mod1 이 빠졌는데 안 걸렸다")

    def test_message_says_how_many_remain(self):
        """몇 개 남았는지 알아야 AI 가 멈추지 않고 계속한다."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_repo(root, {f"src/mod{i:02d}": 3 for i in range(5)})
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_init(root, show_next=False)
                cw.cmd_index(root)
            _code, out = self.lint(root)
            self.assertIn("5", out)

    def test_one_doc_may_cover_many_directories(self):
        """폴더 수 = 모듈 수가 아니다. 묶어서 덮는 것이 허용돼야 한다.

        기계는 '몇 개의 모듈로 나눌지'를 지시하지 않는다. 경계는 의미의
        문제라 사람이 정한다. 기계는 빠진 폴더가 없는지만 본다.
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _make_repo(root, {"src/can": 3, "src/net": 3, "src/sec": 3})
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_init(root, show_next=False)
                cw.cmd_index(root)
            _write_module_doc(root, "통신", ["src/can/*", "src/net/*"])
            _write_module_doc(root, "보안", ["src/sec/*"])
            code, out = self.lint(root)
            self.assertNotIn("덮이지 않", out,
                             "3개 폴더를 2개 문서로 덮은 것은 정상이다")
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
