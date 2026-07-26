# -*- coding: utf-8 -*-
"""공개 코드 회귀 — 파서가 실제 코드에서 죽지 않고 심볼을 뽑는지 본다.

코퍼스가 없으면 skip 한다. tests/fetch_corpus.sh 로 받는다.
사내·오프라인에서도 테스트가 깨지면 안 되므로 skip 이 정상 경로다.
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = Path(__file__).resolve().parent / "corpus"
_spec = importlib.util.spec_from_file_location("cw", ROOT / "cw.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)

MAX_FILES = 400   # 회귀 시간을 묶어둔다


def corpus_files(repo, suffixes=(".c", ".h", ".cpp", ".hpp")):
    base = CORPUS / repo
    if not base.is_dir():
        return []
    out = []
    for p in sorted(base.rglob("*")):
        if p.suffix.lower() in suffixes and p.is_file():
            out.append(p)
            if len(out) >= MAX_FILES:
                break
    return out


class TestCorpusRegression(unittest.TestCase):

    def _run(self, repo):
        files = corpus_files(repo)
        if not files:
            self.skipTest(f"코퍼스 없음: {repo} (tests/fetch_corpus.sh 실행)")
        total_sym = 0
        crashed = []
        for p in files:
            _raw, text, _enc = cw.read_source(p)   # (raw, text, enc) 순서다
            lang = cw.LANG_BY_EXT.get(p.suffix.lower(), "c")
            try:
                syms, _edges = cw.parse_file(p, lang, text)
                total_sym += len(syms)
            except Exception as e:            # 파서는 절대 죽으면 안 된다
                crashed.append(f"{p}: {e!r}")
        print(f"\n[{repo}] 파일 {len(files)}개, 심볼 {total_sym}개, "
              f"크래시 {len(crashed)}건")
        self.assertEqual(crashed, [], f"파서 크래시: {crashed[:3]}")
        self.assertGreater(total_sym, 0, "심볼을 하나도 못 뽑았다")

    def test_vsomeip(self):
        self._run("vsomeip")

    def test_zephyr(self):
        self._run("zephyr")

    def test_freertos(self):
        self._run("freertos")


if __name__ == "__main__":
    unittest.main()
