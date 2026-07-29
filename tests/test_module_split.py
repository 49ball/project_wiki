# -*- coding: utf-8 -*-
"""모듈 후보를 트리 깊이에 맞춰 쪼갠다.

계기: 깊이를 1칸(최상위 폴더) 아니면 2칸으로 **고정**해서 뽑고 있었다.
최상위 폴더가 3개 이하면 2칸, 아니면 1칸. 그래서 트리가 깊으면 뭉개졌다.

    실제:  can/driver, can/proto, net/tcp, sec/crypto, hal, osal  (6개)
    기계:  src/modules/, src/platform/                            (2개)

`src/modules/` 를 한 모듈이라고 부르는 것은 의미가 없다. CAN·네트워크·
보안이 전부 그 안에 있다. 목록이 이러면 AI 가 잡는 경계도 같이 이상해진다.

고정 깊이로는 맞출 수 없다. 가지마다 깊이가 다르기 때문이다.
그래서 **크면 쪼개고 작으면 둔다** — 파일 수가 기준을 넘고 하위 폴더가
있으면 한 단계 내려간다.

기준값은 저장소 크기에 따라 움직인다. 9,099 파일짜리에 파일 3개 기준을
쓰면 모듈이 수백 개가 되고, 12 파일짜리에 300개 기준을 쓰면 1개가 된다.
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


def _index(root, dirs, n_each=2):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for d in dirs:
        p = root / d
        p.mkdir(parents=True, exist_ok=True)
        for i in range(n_each):
            (p / f"f{i}.c").write_text(f"int fn{i}(void);\n", encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf):
        cw.cmd_init(root, show_next=False)
        cw.cmd_index(root)
    return cw.open_db(root).cursor()


class TestAdaptiveSplit(unittest.TestCase):

    def names(self, cur):
        return {n for n, _f, _l in cw.module_candidates(cur)}

    def test_deep_tree_is_split_to_meaningful_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cur = _index(root, [
                "src/modules/can/driver", "src/modules/can/proto",
                "src/modules/net/tcp", "src/modules/sec/crypto",
                "src/platform/hal", "src/platform/osal",
            ])
            got = self.names(cur)
            self.assertIn("src/modules/can/driver", got)
            self.assertIn("src/platform/osal", got)
            self.assertNotIn("src/modules", got,
                             "CAN·네트워크·보안을 한 덩어리로 두면 안 된다")

    def test_flat_tree_stays_flat(self):
        """이미 적당한 크기면 더 쪼갤 이유가 없다."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cur = _index(root, ["src/can", "src/net", "src/sec"])
            got = self.names(cur)
            self.assertEqual(got, {"src/can", "src/net", "src/sec"})

    def test_big_directory_does_not_explode_into_hundreds(self):
        """저장소가 크면 기준도 커져야 한다. 안 그러면 모듈이 수백 개가 된다."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dirs = [f"src/big/sub{i:02d}/deeper" for i in range(40)]
            cur = _index(root, dirs, n_each=8)
            got = self.names(cur)
            self.assertLessEqual(len(got), cw.MODULE_GROUP_BUDGET)
            self.assertFalse(
                [n for n in got if n.endswith("/deeper")],
                "그룹이 이미 적당한 크기면 잎사귀까지 내려가면 안 된다")

    def test_files_next_to_subdirs_get_their_own_group(self):
        """하위 폴더가 있으면서 직속 파일도 있는 폴더 — 둘 다 잡혀야 한다."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cur = _index(root, [
                "src/core", "src/core/can", "src/core/net", "src/core/sec",
                "src/other",
            ])
            got = self.names(cur)
            self.assertIn("src/core", got, "직속 파일이 어디에도 안 속하면 샌다")
            self.assertIn("src/core/can", got)

    def test_every_file_belongs_to_exactly_one_group(self):
        """어느 파일도 새면 안 된다 — 덮기 검사의 전제다."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cur = _index(root, [
                "src/modules/can/driver", "src/modules/net", "src/plat",
                "tools",
            ])
            total = cur.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            counted = sum(f for _n, f, _l in cw.module_candidates(cur))
            self.assertEqual(counted, total)


if __name__ == "__main__":
    unittest.main()
