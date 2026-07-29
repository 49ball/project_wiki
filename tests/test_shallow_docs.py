# -*- coding: utf-8 -*-
"""덮기만 하고 내용이 없는 문서를 경고한다.

계기: lint 에 '덮이지 않은 폴더' 검사를 넣자마자 빠져나갈 구멍이 생겼다.
`- ❓ 아직 안 씀` 한 줄짜리 문서를 폴더 수만큼 찍어내면 에러가 사라진다.
정직하긴 하지만 아무 값어치가 없고, 사용자는 '다 됐다'고 오해한다.
완성도 검사가 완성도를 속이는 상태로 되돌아가는 셈이다.

에러가 아니라 **경고**로 둔다. ✅ 를 강제하면 AI 가 근거 없는 확신을
지어내게 된다. 이 프로젝트에서 그것이 가장 나쁜 결과다. 모르는 것을
❓ 로 두는 선택 자체는 정당하므로 막지 않고, 눈에 띄게만 만든다.
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


def _repo(root):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    d = root / "src" / "can"
    d.mkdir(parents=True)
    for i in range(3):
        (d / f"f{i}.c").write_text(f"int can_fn{i}(int x) {{ return x; }}\n",
                                   encoding="utf-8")


def _doc(root, name, body):
    d = root / "wiki" / "modules"
    d.mkdir(parents=True, exist_ok=True)
    head = cw.run_git(root, "rev-parse", "--short", "HEAD") or "0000000"
    (d / f"{name}.md").write_text(
        f"---\ntype: module\nvalidated_at: {head}\ndepends:\n"
        f"  - src/can/*\n---\n\n# {name}\n\n{body}\n",
        encoding="utf-8")


class TestShallowDocWarning(unittest.TestCase):

    def setUp(self):
        ok, reason = cw.ts_status()
        if not ok:
            self.skipTest(reason)

    def lint(self, root):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cw.cmd_lint(root)
        return code, buf.getvalue()

    def _run(self, body):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _repo(root)
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_init(root, show_next=False)
                cw.cmd_index(root)
            _doc(root, "can", body)
            return self.lint(root)

    def test_warns_when_only_unknowns(self):
        code, out = self._run("- ❓ 아직 안 씀\n- ❓ 확인 못 함")
        self.assertIn("경고", out)
        self.assertIn("내용이 없", out)
        self.assertEqual(code, 0, "경고이지 에러가 아니다")

    def test_warns_when_no_claims_at_all(self):
        _code, out = self._run("아직 아무것도 쓰지 않았다.")
        self.assertIn("내용이 없", out)

    def test_no_warning_when_real_claims_exist(self):
        _code, out = self._run(
            "- ✅ CAN 프레임을 받아 디코딩한다[^a]\n"
            "- ❓ 출하 변형은 알 수 없다\n\n"
            "[^a]: `src/can/f0.c#can_fn0`")
        self.assertNotIn("내용이 없", out)

    def test_inferred_alone_is_enough(self):
        """🔍 만 있어도 내용은 있는 것이다. ✅ 를 강제하면 지어내게 된다."""
        _code, out = self._run(
            "- 🔍 ISR 문맥에서 호출되는 것으로 보인다[^why]\n\n"
            "[^why]: 추론 ← `src/can/f0.c:1` 에서 호출됨")
        self.assertNotIn("내용이 없", out)


if __name__ == "__main__":
    unittest.main()
