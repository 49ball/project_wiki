# tree-sitter 파서 교체 + 미해석 대장 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** cw.py의 C/C++ 파서를 tree-sitter로 교체하고, 파서가 해석하지 못한 지점을 `gaps` 테이블에 기록해 사용자가 사내에서 `cw parse-report`로 자기 코드의 실태를 진단할 수 있게 한다.

**Architecture:** tree-sitter AST에서 심볼·엣지를 뽑되, **해석이 불확실한 지점을 같은 순회에서 함께 수집**한다. tree-sitter가 없거나 버전이 안 맞으면 기존 정규식 파서로 자동 폴백해 도구가 절대 죽지 않는다. `gaps` 테이블은 심볼·파일에 연결되어 이후 커버리지 동봉(설계 §6.1)의 재료가 된다.

**Tech Stack:** Python 3.8+ (표준 라이브러리), tree-sitter 0.23.x (선택적 의존), unittest (테스트), SQLite

## Global Constraints

- **cw.py는 단일 파일을 유지한다.** README FAQ가 "cw.py는 파일 하나짜리라 AI가 직접 읽고 원인을 찾을 수 있다"를 보장하고, 사용 환경(사내)에서는 구형 모델이 이 파일을 통째로 읽어 디버깅해야 한다. 파일 분리는 그 보장을 깬다.
- **tree-sitter는 선택적 의존이다.** 없으면 기존 정규식 파서로 폴백하고 경고만 출력한다. 절대 죽지 않는다.
- **테스트는 `unittest`만 쓴다.** pytest를 요구하지 않는다 — 사내에서도 테스트가 돌아야 한다.
- **버전 핀 (실측 확인됨):** `tree-sitter==0.23.2`, `tree-sitter-c==0.23.4`, `tree-sitter-cpp==0.23.4`.
  tree-sitter-c/cpp 0.23.5 이상은 ABI 15라 tree-sitter 0.23.2(ABI 13~14)와 **충돌한다.**
  tree-sitter 0.24 이상은 Python 3.10+를 요구한다. Python 3.9에서는 위 조합이 유일하게 동작한다.
- **모든 사용자 대면 출력은 한국어.** 기존 cw.py 관례를 따른다.
- **커밋 메시지는 한국어**, 기존 이력(`readme modified`, `parsing capability enhanced`) 관례에 맞춰 간결하게.

---

## File Structure

| 파일 | 책임 | 신규/수정 |
|---|---|---|
| `cw.py` | 전체 도구. tree-sitter 파서, `gaps` 스키마, `parse-report` 추가 | 수정 |
| `tests/__init__.py` | 테스트 패키지 표식 | 신규 |
| `tests/cases.py` | 파서 적합성 케이스 (심볼 + 기대 구멍). **단일 진실 출처** | 신규 |
| `tests/test_parser_conformance.py` | 케이스를 현행/tree-sitter 파서에 돌려 검증 | 신규 |
| `tests/test_gaps.py` | 구멍 탐지·저장 검증 | 신규 |
| `tests/test_corpus.py` | 공개 코드 회귀 (코퍼스 없으면 skip) | 신규 |
| `tests/fetch_corpus.sh` | 공개 코드 내려받기 | 신규 |
| `requirements-parser.txt` | tree-sitter 핀 | 신규 |

`tests/cases.py`가 Task 1과 Task 4~7 양쪽에서 쓰이는 공유 자산이다. 케이스를 한 곳에만 두어 현행 파서와 새 파서를 **같은 자로** 잰다.

---

### Task 1: 파서 적합성 케이스 + 현행 기준선

설계 §12 Phase 0. 대상 코드를 볼 수 없으므로(설계 §2.2) 공개 가능한 케이스로 기준선을 만든다.
**케이스는 지어낸 "자동차처럼 생긴 코드"가 아니라 언어 사실과 공개된 관용구만 쓴다.**

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/cases.py`
- Create: `tests/test_parser_conformance.py`

**Interfaces:**
- Produces: `tests/cases.py`의 `PARSER_CASES: list[Case]` — `Case = namedtuple("Case", "name lang src symbols gaps")`.
  `symbols`는 기대되는 함수 이름 리스트(정렬 비교), `gaps`는 기대되는 구멍 종류의 집합(`set[str]`).
  Task 4·7이 이 목록을 그대로 소비한다.

- [ ] **Step 1: 케이스 파일 작성**

`tests/cases.py`:

```python
# -*- coding: utf-8 -*-
"""파서 적합성 케이스 — 현행 파서와 tree-sitter 파서를 같은 자로 잰다.

원칙: 대상 코드베이스를 볼 수 없으므로(설계 §2.2) 여기 케이스는
"자동차처럼 생긴 코드"를 지어낸 것이 아니라, 언어 문법 사실과
공개된 관용구(AUTOSAR RTE 시그니처 형태 등)만 담는다.
"""
from collections import namedtuple

Case = namedtuple("Case", "name lang src symbols gaps")

PARSER_CASES = [
    # --- 순수 언어 문법. 지어낸 것이 아니라 C/C++ 사실이다. ---
    Case("평범한 C", "c",
         "int foo(int x)\n{\n  return bar(x);\n}\n",
         ["foo"], set()),
    Case("C++ 한정자", "cpp",
         "void Server::start(int p) {\n  sock_init();\n}\n",
         ["Server::start"], set()),
    Case("템플릿", "cpp",
         "template<typename T>\nT maxv(T a, T b) { return a>b?a:b; }\n",
         ["maxv"], set()),
    Case("람다", "cpp",
         "void f() {\n  auto g = [](int x){ return x*2; };\n}\n",
         ["f"], set()),
    Case("operator()", "cpp",
         "struct S { int operator()(int a) const { return a; } };\n",
         ["operator()"], set()),
    Case("중괄호 초기화", "c",
         "int tbl[] = { 1, 2, 3 };\nint use(void) { return tbl[0]; }\n",
         ["use"], set()),
    Case("Allman 중괄호", "c",
         "int foo(int x)\n{\n  return x;\n}\n",
         ["foo"], set()),
    Case("여러 줄 시그니처", "cpp",
         "static int helper(int a,\n    int b)\n{\n  return a;\n}\n",
         ["helper"], set()),
    Case("주석 속 가짜 함수", "cpp",
         "// void fake() {\nint real() {\n}\n",
         ["real"], set()),

    # --- 여기부터 구멍이 나야 정상인 케이스들 ---
    Case("매크로 감싼 정의", "c",
         "#define STATIC_INLINE static inline\n"
         "STATIC_INLINE int foo(int x) { return x; }\n",
         ["foo"], {"parse_error"}),
    Case("AUTOSAR RTE 시그니처", "c",
         "FUNC(void, RTE_CODE) Rte_Write_Sig(VAR(uint8, AUTOMATIC) v)\n"
         "{\n  send(v);\n}\n",
         ["Rte_Write_Sig"], {"macro_mangled_decl"}),
    Case("토큰 붙이기로 이름 생성", "c",
         "#define DEFINE_HANDLER(s) void handle_##s##_frame(int id)\n"
         "DEFINE_HANDLER(radar) { process(id); }\n",
         # 진짜 이름은 handle_radar_frame 이지만 전개 없이는 알 수 없다.
         # 매크로 이름을 반환하되 반드시 구멍으로 표시해야 한다.
         ["DEFINE_HANDLER"], {"token_paste", "macro_mangled_decl"}),
    Case("함수 포인터 테이블", "c",
         "static handler_t tbl[] = "
         "{ { 0x1A0, handle_can }, { 0x1A4, handle_lin } };\n",
         [], {"fnptr_table"}),
    Case("ifdef 변형 양쪽", "c",
         "#ifdef VARIANT_EU\nint go(void) { return 1; }\n"
         "#else\nint go(void) { return 2; }\n#endif\n",
         # 전처리를 안 하므로 양쪽 다 보인다. 이는 오류가 아니라 변형 열거다.
         ["go", "go"], {"ifdef_branch"}),
]
```

- [ ] **Step 2: 현행 파서 기준선 테스트 작성**

`tests/__init__.py`는 빈 파일로 만든다.

`tests/test_parser_conformance.py`:

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 기준선 확인 실행**

Run: `cd ~/workspace/project_wiki && python3 -m unittest tests.test_parser_conformance -v`

Expected: PASS. 출력에 `[현행 정규식 파서 기준선] 통과 N/14`와 실패한 케이스 이름이 찍힌다.
(실측 기준 `operator()`, `토큰 붙이기로 이름 생성`이 실패로 나온다.)

- [ ] **Step 4: 커밋**

```bash
git add tests/__init__.py tests/cases.py tests/test_parser_conformance.py
git commit -m "test: 파서 적합성 케이스 + 현행 파서 기준선 기록"
```

---

### Task 2: 공개 코드 회귀 하네스

설계 §2.2-②. 대상 코드를 볼 수 없으므로 성격이 같은 **실제 공개 코드**로 회귀한다.
코퍼스가 없으면 skip 해야 한다 — 사내·오프라인에서도 테스트가 깨지면 안 된다.

**Files:**
- Create: `tests/fetch_corpus.sh`
- Create: `tests/test_corpus.py`

**Interfaces:**
- Consumes: Task 1의 `cw` 로딩 방식(`importlib.util.spec_from_file_location`)을 동일하게 사용
- Produces: `tests/corpus/` 아래 클론된 저장소. `test_corpus.py`는 존재하지 않으면 `skipTest`

- [ ] **Step 1: 코퍼스 내려받기 스크립트 작성**

`tests/fetch_corpus.sh`:

```bash
#!/usr/bin/env bash
# 공개 코드 회귀 코퍼스 — 대상 코드베이스와 성격이 같은 실제 코드.
# 얕은 클론(--depth 1)으로 용량을 줄인다.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)/corpus"
mkdir -p "$DIR"

clone() {  # clone <url> <dir> [sparse-path]
  local url="$1" name="$2"
  if [ -d "$DIR/$name" ]; then
    echo "이미 있음: $name"
    return
  fi
  echo "받는 중: $name"
  git clone --depth 1 --quiet "$url" "$DIR/$name"
}

# SOME/IP 구현체 — 대상 도메인과 직결 (C++, 매크로 밀도 높음)
clone https://github.com/COVESA/vsomeip.git vsomeip
# 임베디드 RTOS — #ifdef 변형이 극심한 C
clone https://github.com/zephyrproject-rtos/zephyr.git zephyr
# 포팅 레이어가 매크로 범벅인 C
clone https://github.com/FreeRTOS/FreeRTOS-Kernel.git freertos

echo
echo "완료. tests/corpus/ 아래에 받았습니다."
echo "회귀 실행: python3 -m unittest tests.test_corpus -v"
```

- [ ] **Step 2: 실행 권한 부여 및 gitignore**

```bash
chmod +x tests/fetch_corpus.sh
printf 'tests/corpus/\n' >> .gitignore
```

- [ ] **Step 3: 회귀 테스트 작성**

`tests/test_corpus.py`:

```python
# -*- coding: utf-8 -*-
"""공개 코드 회귀 — 파서가 실제 코드에서 죽지 않고, 심볼을 뽑고,
구멍 비율이 임계치를 넘지 않는지 본다.

코퍼스가 없으면 skip 한다. tests/fetch_corpus.sh 로 받는다.
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
```

- [ ] **Step 4: 코퍼스 없이 skip 되는지 확인**

Run: `python3 -m unittest tests.test_corpus -v`
Expected: 3개 테스트 모두 `skipped '코퍼스 없음: ... '`. 실패(FAIL/ERROR) 0건.

- [ ] **Step 5: 코퍼스를 받아 실제로 통과하는지 확인**

Run: `./tests/fetch_corpus.sh && python3 -m unittest tests.test_corpus -v`
Expected: 3개 PASS. 각 저장소별로 `파일 N개, 심볼 M개, 크래시 0건` 출력.

- [ ] **Step 6: 커밋**

```bash
git add tests/fetch_corpus.sh tests/test_corpus.py .gitignore
git commit -m "test: 공개 코드 회귀 하네스 (vsomeip/zephyr/freertos)"
```

---

### Task 3: tree-sitter 선택적 의존 + 폴백

파서가 없거나 버전이 안 맞아도 도구는 죽지 않아야 한다(Global Constraints).
버전 충돌은 실측으로 확인된 실제 함정이므로 명시적으로 진단한다.

**Files:**
- Create: `requirements-parser.txt`
- Modify: `cw.py` — `has_universal_ctags()` 정의 직후(현재 479행 부근)에 추가

**Interfaces:**
- Produces:
  - `ts_languages() -> dict | None` — `{"c": Language, "cpp": Language}` 또는 실패 시 `None`
  - `ts_status() -> tuple[bool, str]` — `(사용가능여부, 사람이 읽는 사유)`. Task 9의 리포트가 소비한다.

- [ ] **Step 1: 요구사항 파일 작성**

`requirements-parser.txt`:

```
# 선택적 의존 — 없으면 cw.py는 내장 정규식 파서로 폴백한다.
#
# 버전 조합 주의 (실측 확인):
#   tree-sitter 0.23.2 는 문법 ABI 13~14 만 받는다.
#   tree-sitter-c/cpp 0.23.5+ 는 ABI 15 라 위와 충돌한다.
#   tree-sitter 0.24+ 는 Python 3.10+ 를 요구한다.
# 아래 조합은 Python 3.9 에서 동작을 확인했다.
tree-sitter==0.23.2
tree-sitter-c==0.23.4
tree-sitter-cpp==0.23.4
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_ts_availability.py`:

```python
# -*- coding: utf-8 -*-
"""tree-sitter 가용성 판정 — 없어도 죽지 않고 사유를 설명해야 한다."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cw", ROOT / "cw.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


class TestTsAvailability(unittest.TestCase):

    def test_status_returns_bool_and_reason(self):
        ok, reason = cw.ts_status()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(reason, str)
        self.assertTrue(reason, "사유 문자열이 비어 있으면 안 된다")

    def test_languages_shape(self):
        langs = cw.ts_languages()
        if langs is None:
            ok, _ = cw.ts_status()
            self.assertFalse(ok, "언어를 못 얻었는데 status 가 True 면 모순")
        else:
            self.assertIn("c", langs)
            self.assertIn("cpp", langs)

    def test_never_raises(self):
        for _ in range(3):          # 캐시 경로도 안전한지
            cw.ts_status()
            cw.ts_languages()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 실패 확인**

Run: `python3 -m unittest tests.test_ts_availability -v`
Expected: FAIL — `AttributeError: module 'cw' has no attribute 'ts_status'`

- [ ] **Step 4: cw.py에 구현 추가**

`cw.py`의 `def parse_file(path: Path, lang: str, text: str):` 정의 **바로 앞**에 삽입한다:

```python
# ---------------------------------------------------------------- tree-sitter

_TS_CACHE = None   # (languages_or_None, reason)


def _ts_load():
    """tree-sitter 로드 시도. 실패해도 예외를 밖으로 내보내지 않는다."""
    try:
        from tree_sitter import Language, Parser  # noqa: F401
    except ImportError:
        return None, ("tree-sitter 미설치 → 내장 정규식 파서 사용. "
                      "정밀 모드를 쓰려면: pip install -r requirements-parser.txt")
    try:
        import tree_sitter_c
        import tree_sitter_cpp
    except ImportError:
        return None, ("tree-sitter 문법 패키지 미설치 → 내장 정규식 파서 사용. "
                      "pip install -r requirements-parser.txt")
    try:
        from tree_sitter import Language
        langs = {"c": Language(tree_sitter_c.language()),
                 "cpp": Language(tree_sitter_cpp.language())}
        from tree_sitter import Parser
        Parser(langs["c"])          # ABI 호환성은 여기서 터진다
        return langs, "tree-sitter 사용 가능 (정밀 모드)"
    except Exception as e:
        return None, (f"tree-sitter 버전 충돌 → 내장 정규식 파서 사용 ({e}). "
                      "requirements-parser.txt 의 핀 버전으로 맞추세요: "
                      "pip install -r requirements-parser.txt")


def _ts_get():
    global _TS_CACHE
    if _TS_CACHE is None:
        _TS_CACHE = _ts_load()
    return _TS_CACHE


def ts_languages():
    """{'c': Language, 'cpp': Language} 또는 None."""
    return _ts_get()[0]


def ts_status():
    """(사용가능여부, 사람이 읽는 사유)."""
    langs, reason = _ts_get()
    return (langs is not None), reason
```

- [ ] **Step 5: 통과 확인**

Run: `python3 -m unittest tests.test_ts_availability -v`
Expected: 3개 PASS

- [ ] **Step 6: 미설치 상태에서도 통과하는지 확인**

Run: `python3 -c "
import importlib.util, sys
sys.modules['tree_sitter'] = None
from pathlib import Path
s = importlib.util.spec_from_file_location('cw', 'cw.py')
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
print(m.ts_status())
"`
Expected: `(False, 'tree-sitter 미설치 → ...')` 형태 출력. 예외 없음.

- [ ] **Step 7: 커밋**

```bash
git add requirements-parser.txt cw.py tests/test_ts_availability.py
git commit -m "feat: tree-sitter 선택적 의존 + 버전 충돌 진단"
```

---

### Task 4: tree-sitter 심볼 추출기

설계 §9. **핵심은 `parenthesized_declarator` 처리**다 — 매크로로 뭉개진 정의의 실제 AST 모양이며,
이걸 처리해야 AUTOSAR 시그니처에서 올바른 이름이 나온다(실측 확인).

**Files:**
- Modify: `cw.py` — Task 3에서 추가한 tree-sitter 블록 바로 뒤에 이어서 작성
- Test: `tests/test_parser_conformance.py` (Task 1 파일에 클래스 추가)

**Interfaces:**
- Consumes: Task 3의 `ts_languages()`
- Produces:
  - `_ts_decl_name(node, src: bytes) -> tuple[str | None, bool]` — `(이름, 매크로로_뭉개짐)`
  - `parse_c_cpp_ts(path: Path, text: str, lang: str) -> tuple[list, list, list] | None`
    반환은 `(symbols, edges, gaps)`. 기존 파서와 달리 **세 번째로 gaps를 낸다.**
    `symbols`/`edges`는 기존 튜플 형태를 그대로 지킨다.
    `gaps` 항목은 `(kind, line, detail, affects_symbol)` 4-튜플. Task 6이 이를 저장한다.
    tree-sitter 불가 시 `None`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_parser_conformance.py` 끝의 `if __name__` 블록 **앞**에 추가:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m unittest tests.test_parser_conformance.TestTreeSitterParser -v`
Expected: FAIL — `AttributeError: module 'cw' has no attribute 'parse_c_cpp_ts'`

- [ ] **Step 3: 구현 추가**

Task 3의 `ts_status()` 정의 뒤에 이어서 작성한다:

```python
_TS_NAME_NODES = ("identifier", "field_identifier", "qualified_identifier",
                  "operator_name", "destructor_name", "type_identifier")


def _ts_decl_name(node, src):
    """function_definition 에서 (이름, 매크로로_뭉개짐) 추출.

    매크로가 시그니처에 끼면 tree-sitter 는 declarator 를
    parenthesized_declarator 로 파싱한다. 이때 진짜 이름은 그 앞의
    type_identifier 에 들어간다. 예:
        FUNC(void, RTE_CODE) Rte_Write_Sig(VAR(uint8, AUTOMATIC) v)
        → type_identifier[Rte_Write_Sig] + parenthesized_declarator[(...)]
    이 모양은 ERROR 노드 없이도 나타나므로 has_error 만으로는 못 잡는다.
    """
    d = node.child_by_field_name("declarator")
    while d is not None:
        if d.type == "parenthesized_declarator":
            for ch in node.children:
                if ch is d:
                    break
                if ch.type == "type_identifier":
                    return src[ch.start_byte:ch.end_byte].decode(
                        "utf-8", "replace"), True
            return None, True
        if d.type in _TS_NAME_NODES:
            return src[d.start_byte:d.end_byte].decode("utf-8", "replace"), False
        nxt = d.child_by_field_name("declarator")
        if nxt is None:
            for ch in d.children:
                if ch.type in _TS_NAME_NODES:
                    return src[ch.start_byte:ch.end_byte].decode(
                        "utf-8", "replace"), False
            return None, True
        d = nxt
    return None, True


def parse_c_cpp_ts(path: Path, text: str, lang: str):
    """tree-sitter 파서. (symbols, edges, gaps) 반환. 불가 시 None.

    설계 §9: 교체 이유는 정확도가 아니라 '못 읽은 것을 말해주는 능력'이다.
    """
    langs = ts_languages()
    if langs is None:
        return None
    from tree_sitter import Parser
    src = text.encode("utf-8", "replace")
    try:
        tree = Parser(langs["cpp" if lang == "cpp" else "c"]).parse(src)
    except Exception:
        return None

    symbols, edges, gaps = [], [], []

    def txt(n):
        return src[n.start_byte:n.end_byte].decode("utf-8", "replace")

    def walk(n, enclosing):
        line = n.start_point[0] + 1
        cur = enclosing

        if n.type == "function_definition":
            name, mangled = _ts_decl_name(n, src)
            if name:
                symbols.append((name, "function", txt(n).split("{")[0].strip()[:120],
                                line, n.end_point[0] + 1, "tree-sitter"))
                cur = name
            if mangled:
                gaps.append(("macro_mangled_decl", line,
                             "매크로가 시그니처를 가림 — 실제 이름이 다를 수 있음",
                             name))
        elif n.type in ("class_specifier", "struct_specifier", "enum_specifier"):
            nm = n.child_by_field_name("name")
            if nm is not None:
                kind = {"class_specifier": "class", "struct_specifier": "struct",
                        "enum_specifier": "enum"}[n.type]
                symbols.append((txt(nm), kind, txt(n).split("{")[0].strip()[:120],
                                line, n.end_point[0] + 1, "tree-sitter"))
        elif n.type == "preproc_include":
            p = n.child_by_field_name("path")
            if p is not None:
                edges.append((None, txt(p).strip('"<>'), None, "includes",
                              "tree-sitter", "confirmed"))
        elif n.type == "call_expression":
            fn = n.child_by_field_name("function")
            if fn is not None and fn.type in _TS_NAME_NODES:
                edges.append((enclosing, txt(fn), None, "calls",
                              "tree-sitter", "inferred"))

        for ch in n.children:
            walk(ch, cur)

    walk(tree.root_node, None)
    return symbols, edges, gaps
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m unittest tests.test_parser_conformance -v`
Expected: `TestTreeSitterParser` 3개 PASS (또는 tree-sitter 미설치 시 skip),
`TestLegacyParserBaseline` PASS

- [ ] **Step 5: 의존성 설치 후 실제 통과 확인**

Run: `pip3 install -r requirements-parser.txt && python3 -m unittest tests.test_parser_conformance -v`
Expected: 전부 PASS. `test_symbols`가 14개 케이스 전부 통과.

- [ ] **Step 6: 커밋**

```bash
git add cw.py tests/test_parser_conformance.py
git commit -m "feat: tree-sitter 심볼 추출기 (매크로 뭉갬 declarator 처리)"
```

---

### Task 5: `gaps` 테이블 스키마

설계 §6.3. 구멍을 1급 데이터로 저장한다.

**Files:**
- Modify: `cw.py` — `SCHEMA` 문자열 (현재 139~152행)
- Test: `tests/test_gaps.py`

**Interfaces:**
- Consumes: Task 4의 gaps 4-튜플 `(kind, line, detail, affects_symbol)`
- Produces: `gaps` 테이블. 칼럼: `id, file, line, kind, detail, affects_symbol, status, resolution, evidence`.
  `status` 기본값 `'open'`. Task 6·7이 읽고 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_gaps.py`:

```python
# -*- coding: utf-8 -*-
"""gaps 테이블 — 미해석 지점 저장."""
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cw", ROOT / "cw.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


class TestGapsSchema(unittest.TestCase):

    def test_table_exists_with_expected_columns(self):
        with tempfile.TemporaryDirectory() as d:
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
        con.execute("INSERT INTO gaps(file, line, kind) VALUES('a.c', 1, 'parse_error')")
        self.assertEqual(
            con.execute("SELECT status FROM gaps").fetchone()[0], "open")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m unittest tests.test_gaps -v`
Expected: FAIL — `sqlite3.OperationalError` 또는 빈 컬럼 집합 불일치

- [ ] **Step 3: 스키마 추가**

`cw.py`의 `SCHEMA` 문자열에서 `CREATE INDEX IF NOT EXISTS idx_sym_file ...` 줄 **앞**에 삽입:

```sql
CREATE TABLE IF NOT EXISTS gaps(
  id INTEGER PRIMARY KEY, file TEXT, line INTEGER, kind TEXT,
  detail TEXT, affects_symbol TEXT,
  status TEXT NOT NULL DEFAULT 'open', resolution TEXT, evidence TEXT);
CREATE INDEX IF NOT EXISTS idx_gap_file ON gaps(file);
CREATE INDEX IF NOT EXISTS idx_gap_sym ON gaps(affects_symbol);
CREATE INDEX IF NOT EXISTS idx_gap_kind ON gaps(kind);
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m unittest tests.test_gaps -v`
Expected: 2개 PASS

- [ ] **Step 5: 커밋**

```bash
git add cw.py tests/test_gaps.py
git commit -m "feat: gaps 테이블 스키마 추가"
```

---

### Task 6: 구멍 탐지기 완성

Task 4는 `macro_mangled_decl`만 잡는다. 나머지 종류를 채운다.
설계 §6.3의 표에 대응한다.

**Files:**
- Modify: `cw.py` — `parse_c_cpp_ts()`의 `walk()` 내부
- Test: `tests/test_gaps.py` (클래스 추가)

**Interfaces:**
- Consumes: Task 4의 `parse_c_cpp_ts`, Task 1의 `PARSER_CASES[].gaps`
- Produces: gap `kind` 값 집합 —
  `parse_error`, `parse_missing`, `macro_mangled_decl`, `token_paste`,
  `fnptr_table`, `ifdef_branch`, `inline_asm`. Task 8의 리포트가 이 값들로 분류한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_gaps.py`의 `if __name__` 블록 **앞**에 추가:

```python
from tests.cases import PARSER_CASES


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
            self.assertEqual(len(g), 4, "(kind, line, detail, affects_symbol)")
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
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m unittest tests.test_gaps.TestGapDetection -v`
Expected: FAIL — `token_paste`, `fnptr_table`, `ifdef_branch`가 안 잡힘

- [ ] **Step 3: 탐지기 확장**

`parse_c_cpp_ts()`의 `walk()` 함수에서 `elif n.type == "call_expression":` 블록 **뒤**에
다음 분기들을 이어 붙인다:

```python
        elif n.type == "initializer_list":
            # 함수 포인터 테이블 — 초기화 리스트 안의 맨 식별자는
            # 함수를 가리킬 수 있다. 호출로 안 잡히므로 구멍으로 남긴다.
            for ch in n.children:
                if ch.type == "identifier":
                    gaps.append(("fnptr_table", ch.start_point[0] + 1,
                                 f"{txt(ch)} — 테이블 등록. 호출로 잡히지 않음",
                                 None))
        elif n.type == "preproc_arg":
            if "##" in txt(n):
                gaps.append(("token_paste", line,
                             "## 토큰 붙이기 — 생성되는 이름이 소스에 없음", None))
        elif n.type in ("preproc_ifdef", "preproc_if"):
            # preproc_else/elif 는 기록하지 않는다 — 머리 노드 하나가
            # 조건부 그룹 전체를 대표한다. 안 그러면 한 그룹이 2~3번 세어진다.
            if not _ts_is_include_guard(n, src):
                cond = n.child_by_field_name("name")
                gaps.append(("ifdef_branch", line,
                             f"조건부 컴파일 "
                             f"{txt(cond) if cond is not None else n.type}"
                             " — 어느 분기가 빌드되는지 알 수 없음", None))
        elif n.type in ("gnu_asm_expression", "asm_statement"):
            gaps.append(("inline_asm", line, "인라인 asm — 해석 불가", None))
```

그리고 `_ts_decl_name()` 정의 **앞**에 인클루드 가드 판별기를 추가한다:

```python
def _ts_is_include_guard(node, src):
    """인클루드 가드(#ifndef FOO_H / #define FOO_H)인가?

    가드는 거의 모든 헤더에 있으므로 이걸 구멍으로 세면 판정이 항상 '나쁨'이
    되고 커버리지 경고가 노이즈가 된다(설계 §6.1). 가드는 변형이 아니라
    관용구이므로 제외한다.

    판별: 최상위 preproc_ifdef 이면서, #else 가 없고,
          안쪽 첫 지시문이 같은 이름의 #define 인 것.
    """
    if node.type != "preproc_ifdef":
        return False
    if node.parent is None or node.parent.type != "translation_unit":
        return False
    if node.child_by_field_name("alternative") is not None:
        return False
    nm = node.child_by_field_name("name")
    if nm is None:
        return False
    guard = src[nm.start_byte:nm.end_byte].decode("utf-8", "replace")
    for ch in node.children:
        if ch.type in ("preproc_def", "preproc_function_def"):
            d = ch.child_by_field_name("name")
            return (d is not None and
                    src[d.start_byte:d.end_byte].decode(
                        "utf-8", "replace") == guard)
        if ch.type not in ("#ifndef", "#ifdef", "identifier", "comment"):
            return False
    return False
```

그리고 `walk()` 맨 앞의 `if n.type == "function_definition":` **바로 앞**에
ERROR/MISSING 처리를 넣는다:

```python
        if n.is_missing:
            gaps.append(("parse_missing", line,
                         f"문법상 빠진 토큰 '{n.type}' — 매크로 때문일 수 있음", None))
        elif n.is_error:
            gaps.append(("parse_error", line,
                         "이 구간을 문법으로 해석하지 못함", None))

```

> 주의: `is_error`/`is_missing` 검사는 `if n.type == ...` 연쇄보다 **앞**에 별도
> `if` 문으로 둔다. `elif` 로 묶으면 ERROR 노드 안의 함수 정의를 놓친다.

- [ ] **Step 4: 통과 확인**

Run: `python3 -m unittest tests.test_gaps -v`
Expected: 5개 PASS

- [ ] **Step 5: 깨끗한 코드 무구멍 회귀 재확인**

Run: `python3 -m unittest tests.test_parser_conformance -v`
Expected: 전부 PASS. 특히 `test_clean_code_has_no_gaps`가 여전히 통과해야 한다
(`ifdef_branch` 추가로 깨질 수 있으니 반드시 확인).

만약 깨지면 `PARSER_CASES`에서 `#ifdef`를 쓰는 케이스의 `gaps`에 `ifdef_branch`를 추가한다
(케이스 정의가 현실을 따라간다 — 탐지기를 약화시키지 않는다).

- [ ] **Step 6: 커밋**

```bash
git add cw.py tests/test_gaps.py tests/cases.py
git commit -m "feat: 구멍 탐지기 — ERROR/토큰붙이기/함수포인터표/ifdef/asm"
```

---

### Task 7: index 파이프라인 연결

파서를 실제로 갈아끼우고 gaps를 DB에 저장한다.

**Files:**
- Modify: `cw.py` — `parse_file()` (현재 491행 부근), `cmd_index()` (현재 518행 부근)
- Test: `tests/test_gaps.py` (클래스 추가)

**Interfaces:**
- Consumes: Task 4·6의 `parse_c_cpp_ts`, Task 5의 `gaps` 테이블
- Produces: `parse_file(path, lang, text) -> (symbols, edges)` 시그니처 **유지**.
  gaps는 모듈 전역 `_LAST_GAPS: list`에 담아 `cmd_index()`가 회수한다.
  기존 호출부(`cmd_doctor`, 테스트)를 깨지 않기 위한 선택이다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_gaps.py`의 `if __name__` 블록 앞에 추가:

```python
import subprocess


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
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m unittest tests.test_gaps.TestIndexStoresGaps -v`
Expected: FAIL — gaps 테이블이 비어 있음

- [ ] **Step 3: `parse_file` 교체**

`cw.py`의 `parse_file()` 전체를 다음으로 교체한다:

```python
_LAST_GAPS = []   # 직전 parse_file 호출이 발견한 구멍. cmd_index 가 회수한다.


def parse_file(path: Path, lang: str, text: str):
    """(symbols, edges) 반환. 구멍은 _LAST_GAPS 에 남긴다."""
    global _LAST_GAPS
    _LAST_GAPS = []
    if lang == "python":
        return parse_python(path, text)
    if lang in ("c", "cpp"):
        r = parse_c_cpp_ts(path, text, lang)
        if r is not None:
            symbols, edges, gaps = r
            _LAST_GAPS = gaps
            return symbols, edges
        if has_universal_ctags():          # tree-sitter 없을 때만 폴백
            r2 = parse_c_cpp_ctags(path, text)
            if r2 is not None:
                return r2
        return parse_c_cpp(path, text)
    if lang == "idl":
        return parse_idl(path, text)
    return [], []
```

- [ ] **Step 4: `cmd_index`에서 gaps 저장**

`cmd_index()` 안에서 심볼을 INSERT 하는 루프를 찾는다. 현재 코드:

```python
        cur.execute("DELETE FROM symbols WHERE file_id IN "
                    "(SELECT id FROM files WHERE path=?)", (rel,))
        cur.execute("DELETE FROM edges WHERE src_file=?", (rel,))
        cur.execute("DELETE FROM files WHERE path=?", (rel,))
```

이 세 줄 뒤에 gaps 삭제를 추가한다(재색인 중복 방지):

```python
        cur.execute("DELETE FROM gaps WHERE file=?", (rel,))
```

그리고 같은 함수 안에서 `symbols, edges = parse_file(p, lang, text)` 호출 뒤,
심볼 INSERT 루프 **뒤**에 gaps INSERT를 추가한다:

```python
        for (kind, line, detail, affects) in _LAST_GAPS:
            cur.execute("INSERT INTO gaps(file,line,kind,detail,affects_symbol,"
                        "status) VALUES(?,?,?,?,?,'open')",
                        (rel, line, kind, detail, affects))
            n_gap += 1
```

`cmd_index()` 상단의 카운터 초기화(`n_sym = 0` 부근)에 `n_gap = 0`을 추가하고,
마지막 출력 줄을 다음으로 바꾼다:

```python
    print(f"색인 완료: {scope}, 심볼 {n_sym}개, 관계 {n_edge}개, 구멍 {n_gap}개"
          f"{' [tree-sitter]' if ts_status()[0] else ' [내장 스캐너]'}")
```

- [ ] **Step 5: 통과 확인**

Run: `python3 -m unittest tests.test_gaps -v`
Expected: 7개 PASS

- [ ] **Step 6: 전체 회귀**

Run: `python3 -m unittest discover -s tests -v`
Expected: 전부 PASS 또는 skip. FAIL/ERROR 0건.

- [ ] **Step 7: 커밋**

```bash
git add cw.py tests/test_gaps.py
git commit -m "feat: index 가 tree-sitter 파서를 쓰고 구멍을 DB에 저장"
```

---

### Task 8: `cw parse-report` — 결론을 내는 자가 진단

설계 §2.2-①. **사용자가 사내에서 이걸 돌려 자기 코드의 실태를 파악한다.**
출력은 데이터가 아니라 **결론과 권고**여야 한다 — 사용자가 출력을 반출할 수 없기 때문이다.

**Files:**
- Modify: `cw.py` — `cmd_doctor()` 정의 앞에 `cmd_parse_report()` 추가, `main()`에 서브커맨드 등록
- Test: `tests/test_parse_report.py`

**Interfaces:**
- Consumes: Task 5의 `gaps` 테이블, Task 3의 `ts_status()`
- Produces: `cmd_parse_report(root: Path) -> int` — 종료 코드(0 정상, 1 조치 필요).
  판정 임계값은 **구멍 있는 파일 비율** 기준: `<10%` 좋음, `<30%` 보통, 그 이상 나쁨.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_parse_report.py`:

```python
# -*- coding: utf-8 -*-
"""cw parse-report — 데이터가 아니라 결론을 낸다."""
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


def build(root, files):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for name, body in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    cw.cmd_init(root, show_next=False)
    cw.cmd_index(root)


class TestParseReport(unittest.TestCase):

    def setUp(self):
        ok, reason = cw.ts_status()
        if not ok:
            self.skipTest(reason)

    def test_clean_code_reports_good(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            build(root, {"a.c": "int foo(int x) { return x; }\n",
                         "b.c": "int bar(void) { return 1; }\n"})
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cw.cmd_parse_report(root)
            out = buf.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("좋음", out)

    def test_include_guards_do_not_ruin_verdict(self):
        """가드만 있는 평범한 헤더 묶음은 '좋음'이어야 한다.
        여기서 '나쁨'이 나오면 판정이 쓸모없어진다."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            files = {f"h{i}.h": f"#ifndef H{i}_H\n#define H{i}_H\n"
                                f"int f{i}(void);\n#endif\n" for i in range(6)}
            files["a.c"] = "int foo(int x) { return x; }\n"
            build(root, files)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cw.cmd_parse_report(root)
            out = buf.getvalue()
            self.assertEqual(code, 0, out)
            self.assertIn("좋음", out)

    def test_macro_heavy_reports_bad_and_names_cause(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            body = ("#define WRAP(s) void h_##s(int i)\n"
                    "WRAP(a) { f(i); }\n")
            build(root, {f"m{i}.c": body for i in range(5)})
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cw.cmd_parse_report(root)
            out = buf.getvalue()
            self.assertEqual(code, 1, "조치 필요 시 종료코드 1")
            self.assertTrue("나쁨" in out or "보통" in out, out)
            self.assertIn("token_paste", out)

    def test_report_gives_recommendation_not_raw_numbers_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            build(root, {"m.c": "#define W(s) void h_##s(int i)\nW(a) { f(i); }\n"})
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_parse_report(root)
            out = buf.getvalue()
            self.assertIn("→", out, "권고(→) 줄이 있어야 한다")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m unittest tests.test_parse_report -v`
Expected: FAIL — `AttributeError: module 'cw' has no attribute 'cmd_parse_report'`

- [ ] **Step 3: 구현 추가**

`cw.py`의 `def cmd_doctor(root: Path):` **바로 앞**에 삽입:

```python
GAP_LABEL = {
    "parse_error": "문법 해석 실패",
    "parse_missing": "빠진 토큰(매크로 의심)",
    "macro_mangled_decl": "매크로가 시그니처를 가림",
    "token_paste": "## 토큰 붙이기(이름이 소스에 없음)",
    "fnptr_table": "함수 포인터 테이블 등록",
    "ifdef_branch": "조건부 컴파일 분기",
    "inline_asm": "인라인 asm",
}

GAP_ADVICE = {
    "parse_error": "매크로 전개기가 필요합니다. 아래 매크로부터 처리하면 크게 줄어듭니다.",
    "parse_missing": "매크로 전개기가 필요합니다.",
    "macro_mangled_decl": "이 심볼들의 이름은 실제와 다를 수 있습니다. 위키에서 사실로 단정하면 안 됩니다.",
    "token_paste": "생성되는 함수 이름이 소스에 없습니다. 매크로 전개기 없이는 찾을 수 없습니다.",
    "fnptr_table": "여기 등록된 함수들은 '호출자 없음'으로 보일 수 있습니다. 데드코드 판정 시 주의하세요.",
    "ifdef_branch": "빌드 변형별로 다른 코드가 살아납니다. 어느 변형이 출하되는지는 코드에 없습니다.",
    "inline_asm": "해석 불가 구간입니다. 수동 확인이 필요합니다.",
}

# 판정(좋음/보통/나쁨)에 반영되는 구멍 — "파서가 읽어내지 못했다"에 해당하는 것들.
# fnptr_table / ifdef_branch / inline_asm 은 파싱 실패가 아니라 코드의 성질이다.
# 이들은 따로 보고하되 판정 비율에는 넣지 않는다. 안 그러면 매크로가 멀쩡한
# 코드도 #ifdef 가 많다는 이유로 '나쁨'이 되어 판정이 쓸모없어진다.
PARSE_QUALITY_GAPS = ("parse_error", "parse_missing",
                      "macro_mangled_decl", "token_paste")


def cmd_parse_report(root: Path):
    """파서가 이 코드베이스를 얼마나 읽어냈는지 '결론'을 낸다.

    설계 §2.2-①: 사용자가 출력을 반출할 수 없으므로 판단을 도구에 내장한다.
    """
    from collections import Counter
    con = open_db(root)
    cur = con.cursor()

    n_files = cur.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    n_sym = cur.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    n_gap = cur.execute("SELECT COUNT(*) FROM gaps").fetchone()[0]

    ok, reason = ts_status()
    print("## 파서")
    print(f"- {reason}")
    if not ok:
        print("  → 정밀 모드가 아닙니다. 내장 스캐너는 자기가 못 읽은 곳을 "
              "알지 못하므로, 아래 '구멍' 수치는 실제보다 훨씬 적게 나옵니다.")

    print(f"\n## 규모\n- 파일 {n_files}개, 심볼 {n_sym}개, 구멍 {n_gap}곳")
    if n_files == 0:
        print("\n색인된 파일이 없습니다. 먼저 `cw index`를 실행하세요.")
        return 1

    # 판정은 '파서가 못 읽은 것'만으로 낸다. 조건부 컴파일이나 함수 포인터
    # 테이블은 코드의 성질이지 파싱 실패가 아니다.
    placeholders = ",".join("?" * len(PARSE_QUALITY_GAPS))
    bad_files = cur.execute(
        f"SELECT COUNT(DISTINCT file) FROM gaps WHERE kind IN ({placeholders})",
        PARSE_QUALITY_GAPS).fetchone()[0]

    ratio = bad_files / n_files
    if ratio < 0.10:
        verdict, code = "좋음", 0
    elif ratio < 0.30:
        verdict, code = "보통", 1
    else:
        verdict, code = "나쁨", 1
    print(f"\n## 판정: {verdict} "
          f"(파서가 못 읽은 파일 {bad_files}/{n_files} = {ratio*100:.0f}%)")

    kinds = Counter(r[0] for r in cur.execute("SELECT kind FROM gaps"))
    if not kinds:
        print("\n해석하지 못한 지점이 없습니다. 파서가 이 코드를 잘 읽고 있습니다.")
        return code

    quality = [(k, c) for k, c in kinds.most_common()
               if k in PARSE_QUALITY_GAPS]
    info = [(k, c) for k, c in kinds.most_common()
            if k not in PARSE_QUALITY_GAPS]

    if quality:
        print("\n## 파서가 못 읽은 것 (판정에 반영됨)")
        for kind, cnt in quality:
            print(f"- {GAP_LABEL.get(kind, kind)} ({kind}): {cnt}곳")
    if info:
        print("\n## 코드의 성질 (판정에 반영 안 됨, 그러나 알아야 함)")
        for kind, cnt in info:
            print(f"- {GAP_LABEL.get(kind, kind)} ({kind}): {cnt}곳")

    print("\n## 다음에 할 일")
    for kind, _cnt in (quality + info)[:3]:
        print(f"→ {GAP_ADVICE.get(kind, '확인이 필요합니다.')}")

    rows = cur.execute(
        f"SELECT file, COUNT(*) c FROM gaps WHERE kind IN ({placeholders}) "
        "GROUP BY file ORDER BY c DESC LIMIT 5", PARSE_QUALITY_GAPS).fetchall()
    if rows:
        print("\n## 못 읽은 곳이 몰린 파일 상위 5")
        for f, c in rows:
            print(f"- {f}: {c}곳")
        print("  → 이 파일들의 매크로를 먼저 처리하면 가장 크게 개선됩니다.")

    print(f"\n{'조치가 필요합니다.' if code else '진행해도 좋습니다.'}")
    return code
```

- [ ] **Step 4: 서브커맨드 등록**

`main()` 안에서 기존 서브파서 등록부(`sub.add_parser("doctor"...)` 부근)에 추가한다.
기존 코드의 패턴을 그대로 따른다:

```python
    p_pr = sub.add_parser("parse-report", help="파서가 코드를 얼마나 읽어냈는지 진단")
    p_pr.add_argument("path", nargs="?", default=".")
```

그리고 명령 디스패치부(`elif args.cmd == "doctor":` 부근)에 추가한다:

```python
    elif args.cmd == "parse-report":
        sys.exit(cmd_parse_report(Path(args.path).resolve()))
```

모듈 docstring 상단의 명령 목록에도 한 줄 추가한다:

```
  cw.py parse-report [경로]     파서가 코드를 얼마나 읽어냈는지 진단 + 권고
```

- [ ] **Step 5: 통과 확인**

Run: `python3 -m unittest tests.test_parse_report -v`
Expected: 3개 PASS

- [ ] **Step 6: 실제 명령으로 확인**

Run:
```bash
cd /tmp && rm -rf prdemo && mkdir prdemo && cd prdemo && git init -q
printf '#define W(s) void h_##s(int i)\nW(a) { f(i); }\n' > m.c
printf 'int ok(int x) { return x; }\n' > good.c
python3 ~/workspace/project_wiki/cw.py init . >/dev/null
python3 ~/workspace/project_wiki/cw.py index
python3 ~/workspace/project_wiki/cw.py parse-report
```
Expected: `## 판정: 나쁨` 또는 `보통`, `## 무엇을 못 읽었나`에 `token_paste`,
`## 다음에 할 일`에 `→`로 시작하는 권고 줄.

- [ ] **Step 7: 커밋**

```bash
git add cw.py tests/test_parse_report.py
git commit -m "feat: cw parse-report — 결론과 권고를 내는 자가 진단"
```

---

### Task 9: doctor 통합 + 문서 갱신

`cw doctor`가 새 파서 상태를 알려주게 하고, README에 반영한다.

**Files:**
- Modify: `cw.py` — `cmd_doctor()`, `SELFTEST_CASES`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 3의 `ts_status()`, Task 8의 `cmd_parse_report`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_parse_report.py`의 `if __name__` 앞에 추가:

```python
class TestDoctorMentionsParser(unittest.TestCase):

    def test_doctor_reports_tree_sitter_status(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "a.c").write_text("int f(void){return 0;}\n", encoding="utf-8")
            cw.cmd_init(root, show_next=False)
            buf = io.StringIO()
            with redirect_stdout(buf):
                try:
                    cw.cmd_doctor(root)
                except SystemExit:
                    pass
            out = buf.getvalue()
            self.assertIn("tree-sitter", out)
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m unittest tests.test_parse_report.TestDoctorMentionsParser -v`
Expected: FAIL — 출력에 `tree-sitter` 없음

- [ ] **Step 3: doctor 수정**

`cmd_doctor()` 안의 universal-ctags 출력 줄을 찾는다:

```python
    print(f"- universal-ctags: {'있음 (C/C++ 정밀 모드)' if has_universal_ctags() else '없음 → 내장 스캐너 사용 (정상)'}")
```

이 줄을 다음으로 교체한다:

```python
    _ts_ok, _ts_reason = ts_status()
    print(f"- C/C++ 파서: {_ts_reason}")
    if not _ts_ok:
        print(f"- universal-ctags: {'있음' if has_universal_ctags() else '없음'} "
              "(tree-sitter 없을 때만 쓰임)")
```

그리고 `cmd_doctor()` 마지막 출력 앞에 안내를 추가한다:

```python
    print("\n다음: `cw index` 후 `cw parse-report` 로 파서가 이 코드를 "
          "얼마나 읽어냈는지 확인하세요.")
```

- [ ] **Step 4: SELFTEST_CASES에 새 케이스 반영**

`SELFTEST_CASES` 리스트 끝(`("IDL interface/메서드", ...)` 뒤)에 추가한다.
`cmd_doctor`는 `function`/`idl_method` 종류만 비교하므로 형식을 맞춘다:

```python
    ("C++ operator()", "cpp",
     "struct S { int operator()(int a) const { return a; } };\n", ["operator()"]),
    ("AUTOSAR 스타일 시그니처", "c",
     "FUNC(void, RTE_CODE) Rte_Write_Sig(VAR(uint8, AUTOMATIC) v)\n{\n  send(v);\n}\n",
     ["Rte_Write_Sig"]),
```

- [ ] **Step 5: 통과 확인**

Run: `python3 -m unittest discover -s tests -v`
Expected: 전부 PASS/skip. FAIL/ERROR 0건.

Run: `python3 cw.py doctor`
Expected: `- C/C++ 파서: tree-sitter 사용 가능 (정밀 모드)`, 파서 자가 테스트 전부 `O`

- [ ] **Step 6: README 갱신**

`README.md`의 "## 7. 명령어 한눈에 보기" 표에 행을 추가한다:

```markdown
| `cw parse-report` | 파서가 이 코드를 얼마나 읽어냈는지 + 못 읽은 곳과 그 이유 |
```

같은 파일 "**Q. universal-ctags 같은 걸 설치해야 하나요?**" 답변을 교체한다:

```markdown
**Q. 설치할 게 정말 없나요?**
기본 동작은 파이썬만 있으면 됩니다. 다만 C/C++를 정밀하게 읽으려면
tree-sitter를 설치하는 걸 권합니다:

```bash
pip install -r requirements-parser.txt
```

없어도 내장 스캐너로 동작하지만, **내장 스캐너는 자기가 못 읽은 부분을
알지 못합니다.** tree-sitter는 "이 부분 해석 실패"를 알려주기 때문에
`cw parse-report`가 의미를 가집니다. 설치했는지는 `cw doctor`로 확인하세요.
```

- [ ] **Step 7: 커밋**

```bash
git add cw.py README.md tests/test_parse_report.py
git commit -m "feat: doctor 에 파서 상태 표시 + README 갱신"
```

---

## 완료 기준

- [ ] `python3 -m unittest discover -s tests -v` — FAIL/ERROR 0건
- [ ] `./tests/fetch_corpus.sh && python3 -m unittest tests.test_corpus -v` — 크래시 0건
- [ ] tree-sitter 미설치 상태에서 `python3 cw.py doctor` 및 `index` 정상 동작 (폴백)
- [ ] `python3 cw.py parse-report` 가 판정·원인·권고 3단으로 출력

## 사용자 확인 지점

Task 9 완료 후, 사용자가 **사내에서** 다음을 1회 실행한다:

```bash
pip install -r requirements-parser.txt
cw doctor
cw index
cw parse-report
```

`parse-report`의 **판정(좋음/보통/나쁨)과 상위 구멍 종류 2~3개**만 구두로 전달받으면
Phase 2(앵커 해시·등급 체계)와 Phase 3(매크로 전개기) 중 무엇을 먼저 할지 결정할 수 있다.
설계 §12에 따라, 구멍이 매크로 계열에 몰려 있으면 Phase 3을 앞당긴다.
