# 신뢰도 표기법 + query 1단계 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 신뢰도를 배지로 노출하고 근거를 각주로 접는 표기법을 구현하고, query가 "확인 필요"를 기계 판정으로 받아 답하며 못 답한 것을 기록장에 남기게 한다.

**Architecture:** 위키 본문에는 이모지 배지(`✅🔍📄❓⚠️`)를 노출하고 긴 앵커는 이름표 각주(`[^label]`)로 접는다. `cw.py`는 배지·각주를 파싱해 lint하고, 문서 상단 요약 콜아웃을 자동 생성한다. query는 `cw context`가 기계 규칙으로 판정한 "확인 필요" 목록만 코드로 확인하고, 결과를 `cw log`에 남긴다.

**Tech Stack:** Python 3.8+ (표준 라이브러리), unittest, SQLite

## Global Constraints

- **cw.py는 단일 파일을 유지한다.** README가 "파일 하나짜리라 AI가 직접 읽고 원인을 찾을 수 있다"를 보장하고, 사내의 구형 모델이 이 파일을 통째로 읽어 디버깅해야 한다.
- **테스트는 `unittest`만 쓴다.** pytest를 요구하지 않는다 — 사내에서도 테스트가 돌아야 한다.
- **모든 사용자 대면 출력은 한국어.**
- **배지 정규식은 반드시 긴 것부터 정렬한다.** `⚠️`는 U+26A0 + U+FE0F 두 코드포인트라, `⚠`(U+26A0)를 먼저 매칭하면 변이 선택자가 남는다. (실측 확인)
- **각주 라벨은 번호가 아니라 이름을 쓴다.** AI가 문장을 추가·삭제해도 어긋나지 않는다.
- **기존 `^[confirmed: ...]` 표기의 마이그레이션 도구는 만들지 않는다.** 위키를 새로 만들기로 확정됨.
- **커밋 메시지는 한국어**, 기존 이력 관례에 맞춰 간결하게.

---

## File Structure

| 파일 | 책임 | 신규/수정 |
|---|---|---|
| `cw.py` | 배지·각주 파싱, lint 규칙, 요약 콜아웃, context 확장, log 명령 | 수정 |
| `templates/wiki/conventions.md` | 표기 규칙 문서 — 사람과 AI가 읽는 규범 | 전면 개정 |
| `templates/wiki/{00-overview,glossary}.md` | 새 표기법 예시 반영 | 수정 |
| `templates/wiki/{modules,flows,decisions,notes}/_TEMPLATE.md` | 새 표기법 | 수정 |
| `templates/wiki/log.md` | 기록장 템플릿 | 신규 |
| `skill/codewiki-query/SKILL.md` | query 연산 스킬 (프롬프트 인라인) | 신규 |
| `tests/test_notation.py` | 배지·각주 파싱 + 요약 콜아웃 | 신규 |
| `tests/test_lint_rules.py` | lint 규칙 | 신규 |
| `tests/test_context_uncertain.py` | "확인 필요" 판정 | 신규 |
| `tests/test_log.py` | 기록장 | 신규 |

파싱·lint·콜아웃은 서로 다른 관심사지만 **전부 `cw.py` 안에 둔다**(Global Constraints).
대신 테스트 파일을 관심사별로 나눠 각 부분을 독립적으로 검증한다.

---

### Task 1: 배지·각주 파싱 + 앵커 문법 완화

표기법의 기반. 이후 모든 태스크가 이 함수들을 쓴다.

**Files:**
- Modify: `cw.py` — `RE_LABEL` 정의부(현재 1299행 부근)
- Test: `tests/test_notation.py`

**Interfaces:**
- Produces:
  - `BADGES: dict` — 이모지 → 등급 문자열 (`confirmed`/`inferred`/`sourced`/`unknown`/`caution`)
  - `Claim = namedtuple("Claim", "line badge text refs")` — `line`은 1부터, `refs`는 각주 라벨 리스트
  - `parse_claims(body: str) -> list[Claim]`
  - `parse_footnotes(body: str) -> dict[str, str]` — 라벨 → 정의 본문
  - `RE_ANCHOR_SYM` — `sym:` 접두어를 선택적으로 받도록 변경

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_notation.py`:

```python
# -*- coding: utf-8 -*-
"""배지·각주 파싱 — 표기법의 기반.

설계: docs/superpowers/specs/2026-07-27-confidence-notation-design.md
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cw", ROOT / "cw.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)

SAMPLE = """## 책임

- ✅ CAN 프레임을 디코딩해 상위로 올린다[^handle_frame]
- ✅ 불량 CRC 프레임은 버린다[^check_crc]
- 🔍 큐를 쓰는 건 ISR에서 동적 할당을 피하려는 것[^isr_alloc]
- 📄 설계 시 동적 할당을 금지했다[^commit_a3f21b]
- ❓ 실제 출하되는 변형이 무엇인지
- 그냥 설명 문장 (배지 없음)

## 주의점

- ⚠️ 핸들러는 매크로로 테이블 등록된다[^can_table]

[^handle_frame]: `src/hal/can.c#handle_frame`
[^check_crc`]: 잘못된 라벨은 매칭되지 않아야 한다
[^check_crc]: `src/hal/can.c#check_crc`
[^isr_alloc]: ISR 문맥에서 호출됨 — `src/hal/can.c:88`
[^commit_a3f21b]: 커밋 a3f21b "fix: ISR에서 동적할당 제거"
[^can_table]: `src/hal/can_table.c:12`
"""


class TestParseClaims(unittest.TestCase):

    def test_finds_all_badged_lines(self):
        claims = cw.parse_claims(SAMPLE)
        self.assertEqual(len(claims), 6, [c.text for c in claims])

    def test_badge_maps_to_grade(self):
        grades = [c.badge for c in cw.parse_claims(SAMPLE)]
        self.assertEqual(
            grades,
            ["confirmed", "confirmed", "inferred", "sourced",
             "unknown", "caution"])

    def test_warning_emoji_with_variation_selector(self):
        """⚠️는 U+26A0 + U+FE0F 두 코드포인트다. 긴 것부터 매칭해야 한다."""
        for src in ("- ⚠️ 변이 선택자 있음\n", "- ⚠ 변이 선택자 없음\n"):
            claims = cw.parse_claims(src)
            self.assertEqual(len(claims), 1, src)
            self.assertEqual(claims[0].badge, "caution")
            self.assertNotIn("\ufe0f", claims[0].text,
                             "변이 선택자가 본문에 남았다")

    def test_extracts_footnote_refs(self):
        claims = cw.parse_claims(SAMPLE)
        self.assertEqual(claims[0].refs, ["handle_frame"])
        self.assertEqual(claims[4].refs, [], "❓는 각주가 없다")

    def test_ref_marker_removed_from_text(self):
        claims = cw.parse_claims(SAMPLE)
        self.assertNotIn("[^", claims[0].text)
        self.assertTrue(claims[0].text.endswith("올린다"), claims[0].text)

    def test_line_numbers_are_one_based(self):
        claims = cw.parse_claims(SAMPLE)
        self.assertEqual(claims[0].line, 3)

    def test_ignores_unbadged_bullets(self):
        for c in cw.parse_claims(SAMPLE):
            self.assertNotIn("그냥 설명", c.text)


class TestParseFootnotes(unittest.TestCase):

    def test_collects_definitions(self):
        notes = cw.parse_footnotes(SAMPLE)
        self.assertIn("handle_frame", notes)
        self.assertIn("`src/hal/can.c#handle_frame`", notes["handle_frame"])

    def test_ignores_malformed_label(self):
        notes = cw.parse_footnotes(SAMPLE)
        self.assertNotIn("check_crc`", notes)

    def test_definition_must_start_at_line_start(self):
        """본문 안의 [^ref] 는 정의가 아니다."""
        notes = cw.parse_footnotes("- ✅ 문장[^x]\n\n[^x]: 진짜 정의\n")
        self.assertEqual(list(notes), ["x"])


class TestAnchorSymPrefixOptional(unittest.TestCase):

    def test_accepts_without_sym_prefix(self):
        m = cw.RE_ANCHOR_SYM.match("src/hal/can.c#handle_frame")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("path"), "src/hal/can.c")
        self.assertEqual(m.group("name"), "handle_frame")

    def test_still_accepts_legacy_sym_prefix(self):
        m = cw.RE_ANCHOR_SYM.match("sym:src/hal/can.c#handle_frame")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("path"), "src/hal/can.c")

    def test_line_anchor_not_swallowed(self):
        self.assertIsNone(cw.RE_ANCHOR_SYM.match("src/hal/can.c:88"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패 확인**

Run: `cd ~/workspace/project_wiki && python3 -m unittest tests.test_notation -v`
Expected: FAIL — `AttributeError: module 'cw' has no attribute 'parse_claims'`

- [ ] **Step 3: 구현 추가**

`cw.py`의 `RE_LABEL = re.compile(...)` 줄 **바로 앞**에 삽입한다:

```python
# ---------------------------------------------------------------- 표기법
# 신뢰도는 본문에 배지로 노출하고, 긴 앵커는 이름표 각주로 접는다.
# 설계: docs/superpowers/specs/2026-07-27-confidence-notation-design.md

BADGES = {
    "✅": "confirmed",   # 코드에서 직접 확인. 앵커 필수
    "🔍": "inferred",    # 정황 추론. 추론의 근거 필수
    "📄": "sourced",     # 코드 밖에서 채굴(커밋·주석·사양서). 출처 필수
    "❓": "unknown",     # 확인 못 함. 각주 없음
    "⚠️": "caution",     # 함정·예외 (U+26A0 U+FE0F)
    "⚠": "caution",      # 변이 선택자 없는 형태도 받는다
}

# 긴 것부터 정렬해야 ⚠️(2코드포인트)가 ⚠(1코드포인트)보다 먼저 매칭된다.
# 반대로 두면 변이 선택자 U+FE0F 가 본문 앞에 남는다.
_BADGE_ALT = "|".join(sorted((re.escape(b) for b in BADGES),
                             key=len, reverse=True))
RE_CLAIM = re.compile(r'^\s*[-*]\s+(' + _BADGE_ALT + r')\s*(.*)$')
RE_FOOTREF = re.compile(r'\[\^([\w\-.]+)\]')
RE_FOOTDEF = re.compile(r'^\[\^([\w\-.]+)\]:[ \t]*(.+)$')

Claim = namedtuple("Claim", "line badge text refs")


def parse_claims(body: str):
    """배지가 붙은 주장 줄을 뽑는다. line 은 1부터."""
    out = []
    for i, line in enumerate(body.split("\n"), 1):
        m = RE_CLAIM.match(line)
        if not m:
            continue
        badge, rest = m.group(1), m.group(2)
        refs = RE_FOOTREF.findall(rest)
        text = RE_FOOTREF.sub("", rest).strip()
        out.append(Claim(i, BADGES[badge], text, refs))
    return out


def parse_footnotes(body: str):
    """각주 정의 수집. {라벨: 정의본문}. 줄 맨 앞에서 시작하는 것만."""
    out = {}
    for line in body.split("\n"):
        m = RE_FOOTDEF.match(line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out
```

파일 상단 import 절에 `namedtuple`을 추가한다. `import ast` 줄 **뒤**에:

```python
from collections import namedtuple
```

그리고 `RE_ANCHOR_SYM` 정의를 다음으로 교체한다:

```python
# sym: 접두어는 선택적이다. '#'이 있으면 심볼 앵커이므로 접두어가 중복이다.
# 기존 문서 호환을 위해 sym: 형태도 계속 받는다.
RE_ANCHOR_SYM = re.compile(
    r'^(?:sym:)?(?P<path>[\w./+\-]+)#(?P<name>[\w:~]+)$')
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m unittest tests.test_notation -v`
Expected: 12개 PASS

- [ ] **Step 5: 기존 테스트 회귀 확인**

Run: `python3 -m unittest discover -s tests`
Expected: OK. `RE_ANCHOR_SYM` 변경이 기존 `check_anchor` 테스트를 깨지 않아야 한다.

- [ ] **Step 6: 커밋**

```bash
git add cw.py tests/test_notation.py
git commit -m "feat: 배지·각주 파싱 + 앵커 sym: 접두어 선택화"
```

---

### Task 2: lint 규칙 개편

표기법의 강제 장치. **추론에 근거를 요구하는 것**이 기존과의 결정적 차이다.

**Files:**
- Modify: `cw.py` — `cmd_lint()` 의 "1) 라벨 검사" 블록
- Test: `tests/test_lint_rules.py`

**Interfaces:**
- Consumes: Task 1의 `parse_claims`, `parse_footnotes`, `BADGES`
- Produces: `lint_notation(rel, body, root, cur) -> tuple[list, list]` — `(errors, warns)`.
  각 원소는 사람이 읽는 한국어 문자열. `cmd_lint`가 자기 목록에 합친다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_lint_rules.py`:

```python
# -*- coding: utf-8 -*-
"""표기법 lint 규칙."""
import importlib.util
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cw", ROOT / "cw.py")
cw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw)


def lint(body):
    """앵커 실존 검사는 건너뛰기 위해 빈 DB 를 쓴다."""
    con = sqlite3.connect(":memory:")
    con.executescript(cw.SCHEMA)
    return cw.lint_notation("t.md", body, ROOT, con.cursor())


class TestBadgeRequiresEvidence(unittest.TestCase):

    def test_inferred_without_basis_is_error(self):
        """기존 ^[inferred] 는 근거가 없어 검증 불가였다. 이제 에러다."""
        errs, _w = lint("- 🔍 큐는 백프레셔 목적으로 보인다\n")
        self.assertTrue(errs)
        self.assertIn("🔍", errs[0])

    def test_inferred_with_basis_is_ok(self):
        errs, _w = lint(
            "- 🔍 큐는 ISR 할당 회피[^b]\n\n[^b]: ISR 문맥에서 호출됨\n")
        self.assertEqual(errs, [])

    def test_confirmed_without_anchor_is_error(self):
        errs, _w = lint("- ✅ 프레임을 디코딩한다\n")
        self.assertTrue(errs)

    def test_sourced_without_source_is_error(self):
        errs, _w = lint("- 📄 설계 시 동적할당을 금지했다\n")
        self.assertTrue(errs)

    def test_unknown_needs_nothing(self):
        errs, _w = lint("- ❓ 실제 출하 변형이 무엇인지\n")
        self.assertEqual(errs, [])

    def test_unknown_with_footnote_is_warning(self):
        """모른다면서 근거가 있는 건 라벨이 잘못됐을 가능성."""
        _e, warns = lint("- ❓ 모르겠다[^x]\n\n[^x]: 근거\n")
        self.assertTrue(warns)


class TestFootnoteIntegrity(unittest.TestCase):

    def test_undefined_reference_is_error(self):
        errs, _w = lint("- ✅ 문장[^missing]\n")
        self.assertTrue(errs)
        self.assertIn("missing", errs[0])

    def test_orphan_definition_is_warning(self):
        _e, warns = lint(
            "- ✅ 문장[^used]\n\n[^used]: 근거\n[^unused]: 아무도 안 씀\n")
        self.assertTrue(warns)
        self.assertIn("unused", warns[0])


class TestOneEvidencePerClaim(unittest.TestCase):

    def test_two_refs_is_warning(self):
        """근거가 둘이면 어느 근거가 어느 부분을 뒷받침하는지 알 수 없다."""
        _e, warns = lint(
            "- ✅ 문장[^a][^b]\n\n[^a]: 하나\n[^b]: 둘\n")
        self.assertTrue(warns)

    def test_one_ref_is_clean(self):
        _e, warns = lint("- ✅ 문장[^a]\n\n[^a]: 하나\n")
        self.assertEqual(warns, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m unittest tests.test_lint_rules -v`
Expected: FAIL — `AttributeError: module 'cw' has no attribute 'lint_notation'`

- [ ] **Step 3: 구현 추가**

Task 1에서 추가한 `parse_footnotes` 정의 **뒤**에 삽입한다:

```python
# 배지별로 각주가 필수인가. ⚠️(caution)는 선택.
_BADGE_NEEDS_EVIDENCE = {
    "confirmed": "코드 앵커",
    "inferred": "추론의 근거",
    "sourced": "출처(커밋·주석·사양서)",
}


def lint_notation(rel: str, body: str, root: Path, cur):
    """표기법 검사. (errors, warns) 반환."""
    errors, warns = [], []
    claims = parse_claims(body)
    notes = parse_footnotes(body)
    used = set()

    for c in claims:
        used.update(c.refs)
        need = _BADGE_NEEDS_EVIDENCE.get(c.badge)
        if need and not c.refs:
            badge_ch = next(k for k, v in BADGES.items() if v == c.badge)
            errors.append(
                f"{rel}:{c.line} {badge_ch} 주장에 {need}가 없음 — "
                f"각주를 달거나 ❓로 낮추세요: \"{c.text[:30]}\"")
        if c.badge == "unknown" and c.refs:
            warns.append(
                f"{rel}:{c.line} ❓인데 근거가 붙어 있음 — "
                f"라벨이 잘못됐을 수 있습니다")
        if len(c.refs) > 1:
            warns.append(
                f"{rel}:{c.line} 한 주장에 근거가 {len(c.refs)}개입니다. "
                f"문장을 쪼개세요 — 어느 근거가 어느 부분을 뒷받침하는지 "
                f"알 수 없습니다")
        for r in c.refs:
            if r not in notes:
                errors.append(
                    f"{rel}:{c.line} 각주 [^{r}] 의 정의가 없음")

    for label in notes:
        if label not in used:
            warns.append(f"{rel} 쓰이지 않는 각주 정의 [^{label}] (고아)")

    # 각주 정의 안의 앵커가 실존하는지
    for label, definition in notes.items():
        for m in re.finditer(r'`([^`]+)`', definition):
            a = m.group(1)
            if not (RE_ANCHOR_SYM.match(a) or RE_ANCHOR_LINE.match(a)):
                continue
            ok, msg = check_anchor(root, cur, a)
            if ok is False:
                errors.append(f"{rel} 각주 [^{label}] — {msg}")
            elif ok is None:
                warns.append(f"{rel} 각주 [^{label}] — {msg}")

    return errors, warns
```

- [ ] **Step 4: `cmd_lint`에 연결**

`cmd_lint()` 안의 "1) 라벨 검사" 블록 — `for m in RE_LABEL.finditer(body):` 로 시작해
`warns.append(f"{rel}:{line_no} {msg}")` 로 끝나는 부분 전체를 다음으로 교체한다:

```python
        # 1) 표기법 검사 (배지 + 각주)
        e, w = lint_notation(rel, body, root, cur)
        errors.extend(e)
        warns.extend(w)
```

- [ ] **Step 5: 통과 확인**

Run: `python3 -m unittest tests.test_lint_rules -v`
Expected: 10개 PASS

- [ ] **Step 6: 전체 회귀**

Run: `python3 -m unittest discover -s tests`
Expected: OK

- [ ] **Step 7: 커밋**

```bash
git add cw.py tests/test_lint_rules.py
git commit -m "feat: 표기법 lint — 추론에 근거 강제, 각주 무결성 검사"
```

---

### Task 3: 문서 상단 요약 콜아웃 자동 생성

문서를 열자마자 "이 문서는 추론이 많다"를 알게 한다. **기계 소유** — 사람도 AI도 편집하지 않는다.

**Files:**
- Modify: `cw.py` — Task 2의 `lint_notation` 뒤
- Test: `tests/test_notation.py` (클래스 추가)

**Interfaces:**
- Consumes: Task 1의 `parse_claims`
- Produces:
  - `summary_callout(body: str) -> str` — `> [!info] ✅확인 2 · 🔍추론 1` 형태 한 줄. 주장이 없으면 빈 문자열
  - `apply_summary(text: str) -> str` — 프론트매터 뒤에 콜아웃을 삽입하거나 갱신한 전체 텍스트

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_notation.py`의 `if __name__` 블록 **앞**에 추가:

```python
class TestSummaryCallout(unittest.TestCase):

    BODY = ("- ✅ 하나[^a]\n- ✅ 둘[^b]\n- 🔍 셋[^c]\n- ❓ 넷\n"
            "\n[^a]: x\n[^b]: y\n[^c]: z\n")

    def test_counts_by_badge(self):
        line = cw.summary_callout(self.BODY)
        self.assertIn("✅확인 2", line)
        self.assertIn("🔍추론 1", line)
        self.assertIn("❓모름 1", line)

    def test_omits_zero_counts(self):
        line = cw.summary_callout(self.BODY)
        self.assertNotIn("📄", line, "0건인 배지는 적지 않는다")

    def test_empty_when_no_claims(self):
        self.assertEqual(cw.summary_callout("그냥 산문입니다.\n"), "")

    def test_inserts_after_frontmatter(self):
        text = "---\ntype: module\n---\n\n# 제목\n\n" + self.BODY
        out = cw.apply_summary(text)
        lines = out.split("\n")
        self.assertTrue(lines[0] == "---")
        idx = next(i for i, l in enumerate(lines) if l.startswith("> [!info]"))
        end_fm = next(i for i, l in enumerate(lines[1:], 1) if l == "---")
        self.assertGreater(idx, end_fm, "프론트매터 뒤에 와야 한다")
        self.assertLess(idx, lines.index("# 제목"), "제목 앞에 와야 한다")

    def test_is_idempotent(self):
        text = "---\ntype: module\n---\n\n" + self.BODY
        once = cw.apply_summary(text)
        twice = cw.apply_summary(once)
        self.assertEqual(once, twice, "두 번 적용해도 같아야 한다")

    def test_replaces_stale_summary(self):
        text = ("---\ntype: module\n---\n\n"
                "> [!info] ✅확인 99 · 🔍추론 99\n\n" + self.BODY)
        out = cw.apply_summary(text)
        self.assertNotIn("99", out)
        self.assertIn("✅확인 2", out)

    def test_no_frontmatter_still_works(self):
        out = cw.apply_summary(self.BODY)
        self.assertTrue(out.startswith("> [!info]"))
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m unittest tests.test_notation.TestSummaryCallout -v`
Expected: FAIL — `AttributeError: ... 'summary_callout'`

- [ ] **Step 3: 구현 추가**

`lint_notation` 정의 **뒤**에 삽입한다:

```python
# 요약 콜아웃에 쓸 표시 순서와 이름
_SUMMARY_ORDER = [("confirmed", "✅", "확인"), ("inferred", "🔍", "추론"),
                  ("sourced", "📄", "출처"), ("unknown", "❓", "모름"),
                  ("caution", "⚠️", "주의")]
RE_SUMMARY = re.compile(r'^> \[!info\] .*$', re.M)


def summary_callout(body: str) -> str:
    """문서의 신뢰도 구성을 한 줄로. 주장이 없으면 빈 문자열."""
    from collections import Counter
    counts = Counter(c.badge for c in parse_claims(body))
    if not counts:
        return ""
    parts = [f"{ch}{name} {counts[grade]}"
             for grade, ch, name in _SUMMARY_ORDER if counts.get(grade)]
    return "> [!info] " + " · ".join(parts)


def apply_summary(text: str) -> str:
    """프론트매터 뒤에 요약 콜아웃을 삽입하거나 갱신한다. 멱등."""
    fm_end = 0
    if text.startswith("---"):
        e = text.find("\n---", 3)
        if e >= 0:
            fm_end = e + 4
            if text[fm_end:fm_end + 1] == "\n":
                fm_end += 1

    head, body = text[:fm_end], text[fm_end:]
    body = RE_SUMMARY.sub("", body).lstrip("\n")   # 기존 요약 제거
    line = summary_callout(body)
    if not line:
        return head + body
    return head + line + "\n\n" + body
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m unittest tests.test_notation -v`
Expected: 19개 PASS

- [ ] **Step 5: `cw lint`가 요약을 갱신하게 연결**

`cmd_lint()` 안, Task 2에서 넣은 표기법 검사 블록 **바로 뒤**에 추가한다:

```python
        # 요약 콜아웃은 기계 소유 — 검사하지 않고 갱신한다
        new_text = apply_summary(text)
        if new_text != text:
            doc.write_text(new_text, encoding="utf-8")
```

- [ ] **Step 6: 실제 파일로 확인**

Run:
```bash
cd /tmp && rm -rf sumdemo && mkdir sumdemo && cd sumdemo && git init -q
mkdir -p wiki/modules
printf -- '---\ntype: module\nid: x\nvalidated_at: TBD\ndepends:\n  - src/*\n---\n\n# X\n\n- ✅ 하나[^a]\n- 🔍 둘[^b]\n\n[^a]: `src/a.c#f`\n[^b]: 정황상\n' > wiki/modules/x.md
mkdir -p .codewiki && python3 ~/workspace/project_wiki/cw.py index . >/dev/null 2>&1
python3 ~/workspace/project_wiki/cw.py lint . >/dev/null 2>&1
head -12 wiki/modules/x.md
```
Expected: 프론트매터 다음 줄에 `> [!info] ✅확인 1 · 🔍추론 1` 이 삽입되어 있다.

- [ ] **Step 7: 커밋**

```bash
git add cw.py tests/test_notation.py
git commit -m "feat: 문서 상단 신뢰도 요약 콜아웃 자동 생성"
```

---

### Task 4: 템플릿과 규칙 문서 개정

사람과 AI가 실제로 읽는 규범. 여기가 안 바뀌면 새 표기가 쓰이지 않는다.

**Files:**
- Modify: `templates/wiki/conventions.md`
- Modify: `templates/wiki/modules/_TEMPLATE.md`
- Modify: `templates/wiki/flows/_TEMPLATE.md`
- Modify: `templates/wiki/decisions/_TEMPLATE.md`
- Modify: `templates/wiki/notes/_TEMPLATE.md`
- Create: `templates/wiki/log.md`

**Interfaces:**
- Consumes: Task 1~3의 표기 규칙
- Produces: `templates/wiki/log.md` — Task 6의 `cw log`가 이 파일에 append 한다

- [ ] **Step 1: `conventions.md`의 라벨 절 교체**

`## 신뢰도 라벨 (Obsidian 인라인 각주 문법 사용)` 절부터 그 아래 예시 블록까지를
다음으로 교체한다:

```markdown
## 신뢰도 배지

주장은 **불릿으로 쓰고 맨 앞에 배지**를 단다. 근거는 **이름표 각주**로 접는다.

| 배지 | 뜻 | 각주 |
|---|---|---|
| ✅ | 코드에서 직접 확인 | **필수** — 앵커 |
| 🔍 | 정황 추론 | **필수** — 추론의 근거 |
| 📄 | 코드 밖에서 채굴(커밋·주석·사양서) | **필수** — 출처 |
| ❓ | 확인 못 함 | 없음 |
| ⚠️ | 함정·예외 | 있으면 좋음 |

예시:

```markdown
- ✅ CAN 프레임을 디코딩해 상위로 올린다[^handle_frame]
- 🔍 큐를 쓰는 건 ISR에서 동적 할당을 피하려는 것[^isr_alloc]
- 📄 설계 시 동적 할당을 금지했다[^commit_a3f21b]
- ❓ 실제 출하되는 변형이 무엇인지

[^handle_frame]: `src/hal/can.c#handle_frame`
[^isr_alloc]: ISR 문맥에서 호출됨 — `src/hal/can.c:88`
[^commit_a3f21b]: 커밋 a3f21b "fix: ISR에서 동적할당 제거"
```

### 규칙

1. **각주 라벨은 번호가 아니라 이름을 쓴다.** `[^1]`은 문장을 추가·삭제하면
   어긋나지만 `[^handle_frame]`은 어긋나지 않는다. 화면에는 어차피 `1, 2, 3`으로 보인다.
2. **한 주장에 근거는 하나.** 둘 이상이면 문장을 쪼갠다 —
   어느 근거가 어느 부분을 뒷받침하는지 알 수 없어지기 때문이다.
3. **🔍에는 반드시 근거를 적는다.** 근거 없는 추론은 나중에 아무도 검증할 수 없다.
4. 문서 맨 위 `> [!info]` 요약 줄은 **기계가 생성한다.** 손대지 않는다.
```

- [ ] **Step 2: `conventions.md`의 anchor 문법 표 갱신**

`| 심볼 | \`sym:src/net/server.cpp#Server::start\` | 특정 심볼 |` 행을 다음으로 교체:

```markdown
| 심볼 | `src/net/server.cpp#Server::start` | 특정 심볼 (`sym:` 접두어는 선택) |
```

- [ ] **Step 3: 모듈 템플릿 교체**

`templates/wiki/modules/_TEMPLATE.md` 를 다음으로 교체한다:

```markdown
---
type: module
id: 모듈이름
validated_at: TBD
depends:
  - 경로/패턴/*
---

# (모듈 이름) 모듈

## 책임

(이 모듈이 소유하는 것. 무엇을 하고, 무엇을 하지 **않는지**.
 각 주장은 불릿 + 배지로 쓴다.)

- ✅ (확인된 책임)[^ref1]
- 🔍 (추론한 것)[^ref2]
- ❓ (확인 못 한 것)

## 공개 인터페이스

(다른 모듈이 이 모듈을 쓰는 진입점. 코드 복사 금지.)

- ✅ `함수/클래스` — 역할 한 줄[^ref3]

## 의존 방향

(이 모듈이 무엇에 의존하고, 무엇이 이 모듈에 의존하는지.
 files/INDEX.md 의 fan-in 참고.)

## 주의점 / 함정

(여기를 고칠 때 알아야 할 것. 없으면 섹션 삭제.)

- ⚠️ (함정)[^ref4]

[^ref1]: `경로#심볼`
[^ref2]: 추론의 근거를 적는다 — `경로:줄`
[^ref3]: `경로#심볼`
[^ref4]: `경로:줄`
```

- [ ] **Step 4: 나머지 템플릿 3개에 배지 예시 반영**

`flows/_TEMPLATE.md`, `decisions/_TEMPLATE.md`, `notes/_TEMPLATE.md` 각각에서
`^[confirmed: ...]` / `^[inferred]` / `^[unknown]` 이 나오는 자리를 배지 + 각주로 바꾼다.
치환 규칙:

| 기존 | 새로 |
|---|---|
| `문장 ^[confirmed: sym:A#B]` | `- ✅ 문장[^b]` + 하단 `[^b]: \`A#B\`` |
| `문장 ^[inferred]` | `- 🔍 문장[^why]` + 하단 `[^why]: 추론의 근거를 적는다` |
| `문장 ^[unknown]` | `- ❓ 문장` |

- [ ] **Step 5: 기록장 템플릿 생성**

`templates/wiki/log.md`:

```markdown
---
type: log
---

# 기록장

**이 파일은 기계가 씁니다. 편집하지 마세요.**

query·ingest·lint 가 무엇을 했는지 시간순으로 쌓입니다.
`query` 가 코드를 열어봤다는 건 **위키에 그 내용이 없었다**는 뜻이고,
그것이 다음에 무엇을 문서화할지 알려주는 가장 정직한 신호입니다.

`cw log --gaps` 로 "위키에 없어서 코드를 열어본 질문"만 모아 볼 수 있습니다.

| 날짜 | 연산 | 내용 | 결과 |
|---|---|---|---|
```

- [ ] **Step 6: 템플릿이 실제로 설치되는지 확인**

Run:
```bash
cd /tmp && rm -rf tpldemo && mkdir tpldemo && cd tpldemo && git init -q
python3 ~/workspace/project_wiki/cw.py init . | tail -3
ls wiki/ && grep -c "배지" wiki/conventions.md && head -3 wiki/log.md
```
Expected: `wiki/log.md` 가 설치되고, `conventions.md` 에 "배지"가 나온다.

- [ ] **Step 7: 커밋**

```bash
git add templates/
git commit -m "docs: 템플릿·규칙을 배지+각주 표기로 개정, 기록장 템플릿 추가"
```

---

### Task 5: `cw context` — "확인 필요" 기계 판정

query 사다리의 핵심. **AI에게 "애매하면 코드 봐"라고 하지 않고, 기계가 판정한 목록을 준다.**

**Files:**
- Modify: `cw.py` — `cmd_context()` 끝부분
- Test: `tests/test_context_uncertain.py`

**Interfaces:**
- Consumes: `gaps` 테이블, `symbols` 테이블, Task 1의 `parse_claims`
- Produces: `uncertainties(root, cur, query, def_files) -> list[str]` —
  사람이 읽는 한국어 항목 리스트. `cmd_context`가 "## ⚠ 코드를 직접 확인해야 할 것" 절로 출력

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_context_uncertain.py`:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m unittest tests.test_context_uncertain -v`
Expected: FAIL — `AttributeError: ... 'uncertainties'`

- [ ] **Step 3: 구현 추가**

`cmd_context` 정의 **바로 앞**에 삽입한다:

```python
def uncertainties(root: Path, cur, query: str, def_files):
    """'위키와 facts.db 만으로는 확신할 수 없는 것'을 규칙으로 판정한다.

    설계 §4.3. AI 의 느낌이 아니라 데이터로 판정하므로 약한 모델도 쓸 수 있고
    비용이 예측 가능하다.
    """
    out = []
    base = query.split("::")[-1]

    rows = cur.execute(
        "SELECT f.path, s.line_start FROM symbols s JOIN files f "
        "ON f.id=s.file_id WHERE s.name=? OR s.name LIKE ? "
        "ORDER BY f.path", (base, f"%::{base}")).fetchall()
    if not rows:
        out.append(
            f"`{base}` 를 색인에서 찾지 못했습니다 — 위키에 없다면 "
            f"코드에서 직접 찾아야 합니다 (매크로/템플릿 가능성)")
    elif len(rows) > 1:
        where = "  ".join(f"{p}:{l}" for p, l in rows[:5])
        out.append(
            f"`{base}` 이 {len(rows)}곳에 정의되어 있습니다 — "
            f"어느 것인지 코드로 확인하세요\n     {where}")

    # 파싱 실패 계열만 본다. ifdef/fnptr 은 코드의 성질이지 '못 읽음'이 아니라
    # 여기 섞으면 거의 모든 파일이 걸려 목록이 노이즈가 된다.
    if def_files:
        ph = ",".join("?" * len(def_files))
        qh = ",".join("?" * len(PARSE_QUALITY_GAPS))
        for path, cnt in cur.execute(
                f"SELECT file, COUNT(*) FROM gaps WHERE file IN ({ph}) "
                f"AND kind IN ({qh}) GROUP BY file ORDER BY COUNT(*) DESC",
                list(def_files) + list(PARSE_QUALITY_GAPS)):
            out.append(
                f"{path} 에 못 읽은 곳이 {cnt}곳 있습니다 — "
                f"호출자·정의 목록이 불완전할 수 있습니다")

    # 위키의 추론 문장
    if (root / "wiki").exists():
        for doc in wiki_docs(root):
            text = doc.read_text(encoding="utf-8", errors="replace")
            _fm, body = parse_frontmatter(text)
            if base not in body:
                continue
            for c in parse_claims(body):
                if c.badge == "inferred" and base in c.text:
                    out.append(
                        f"위키 문장 \"{c.text[:40]}\" 은 추론입니다 "
                        f"({doc.relative_to(root)}:{c.line}) — 확인 필요")
    return out
```

- [ ] **Step 4: `cmd_context` 끝에 출력 연결**

`cmd_context()` 맨 끝의 다음 두 줄:

```python
    for f in def_files:
        print(f"\n파일 stub: wiki/{stub_rel(f)}")
```

**뒤에** 추가한다:

```python
    unc = uncertainties(root, cur, query, def_files)
    print("\n## ⚠ 코드를 직접 확인해야 할 것\n")
    if unc:
        for i, u in enumerate(unc, 1):
            print(f"{i}. {u}")
        print("\n→ 이 목록에 있는 것만 코드를 여세요. 목록에 없으면 열지 마세요.")
    else:
        print("- 없음 — 위키와 색인만으로 답할 수 있습니다")
```

- [ ] **Step 5: 통과 확인**

Run: `python3 -m unittest tests.test_context_uncertain -v`
Expected: 6개 PASS

- [ ] **Step 6: 실제 명령으로 확인**

Run:
```bash
cd /tmp && rm -rf ctxdemo && mkdir ctxdemo && cd ctxdemo && git init -q
printf '#define WRAP(s) void h_##s(int i)\nWRAP(a) { f(i); }\nint init(void){return 0;}\n' > a.c
printf 'int init(void){return 1;}\n' > b.c
git add -A && git commit -qm x
python3 ~/workspace/project_wiki/cw.py setup . >/dev/null 2>&1
python3 ~/workspace/project_wiki/cw.py context . init | tail -8
```
Expected: `## ⚠ 코드를 직접 확인해야 할 것` 절에 "`init` 이 2곳에 정의" 항목이 나온다.

- [ ] **Step 7: 커밋**

```bash
git add cw.py tests/test_context_uncertain.py
git commit -m "feat: cw context 가 '확인 필요'를 기계 규칙으로 판정"
```

---

### Task 6: `cw log` — 기록장

**"코드를 열었다 = 위키에 없었다"**가 핵심 신호다. 이것이 ingest의 입력이 된다.

**Files:**
- Modify: `cw.py` — `cmd_context` 뒤, `main()` 서브커맨드 등록
- Test: `tests/test_log.py`

**Interfaces:**
- Consumes: `templates/wiki/log.md` (Task 4)
- Produces:
  - `cmd_log(root, op=None, text=None, result=None, gaps_only=False) -> int`
  - CLI: `cw log add <연산> <내용> [--result <결과>]` / `cw log` / `cw log --gaps`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_log.py`:

```python
# -*- coding: utf-8 -*-
"""기록장 — query 가 못 답한 것이 ingest 의 입력이 된다."""
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


def fresh(tmp):
    root = Path(tmp)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "a.c").write_text("int f(void){return 0;}\n", encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf):
        cw.cmd_init(root, show_next=False)
    return root


class TestLogAppend(unittest.TestCase):

    def test_appends_row(self):
        with tempfile.TemporaryDirectory() as d:
            root = fresh(d)
            cw.cmd_log(root, op="query", text="CAN 흐름",
                       result="위키없음 → 코드 3개 열람")
            body = (root / "wiki" / "log.md").read_text(encoding="utf-8")
            self.assertIn("CAN 흐름", body)
            self.assertIn("위키없음", body)

    def test_appends_not_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            root = fresh(d)
            cw.cmd_log(root, op="query", text="첫째", result="ok")
            cw.cmd_log(root, op="query", text="둘째", result="ok")
            body = (root / "wiki" / "log.md").read_text(encoding="utf-8")
            self.assertIn("첫째", body)
            self.assertIn("둘째", body)

    def test_pipe_in_text_is_escaped(self):
        """마크다운 표가 깨지면 안 된다."""
        with tempfile.TemporaryDirectory() as d:
            root = fresh(d)
            cw.cmd_log(root, op="query", text="a | b", result="ok")
            body = (root / "wiki" / "log.md").read_text(encoding="utf-8")
            self.assertIn("a \\| b", body)


class TestLogGaps(unittest.TestCase):

    def test_gaps_lists_only_wiki_misses_with_counts(self):
        with tempfile.TemporaryDirectory() as d:
            root = fresh(d)
            cw.cmd_log(root, op="query", text="CAN 흐름",
                       result="위키없음 → 코드 열람")
            cw.cmd_log(root, op="query", text="CAN 흐름",
                       result="위키없음 → 코드 열람")
            cw.cmd_log(root, op="query", text="호출자", result="위키로 답함")
            buf = io.StringIO()
            with redirect_stdout(buf):
                cw.cmd_log(root, gaps_only=True)
            out = buf.getvalue()
            self.assertIn("CAN 흐름", out)
            self.assertIn("2", out, "질문 횟수가 보여야 한다")
            self.assertNotIn("호출자", out, "위키로 답한 건 구멍이 아니다")

    def test_missing_log_file_is_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cw.cmd_log(root, gaps_only=True)
            self.assertEqual(code, 1)
            self.assertIn("없습니다", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m unittest tests.test_log -v`
Expected: FAIL — `AttributeError: ... 'cmd_log'`

- [ ] **Step 3: 구현 추가**

`cmd_context` 정의 **뒤**에 삽입한다:

```python
# ---------------------------------------------------------------- log

# 결과 문구에 이것이 들어 있으면 "위키에 없어서 코드를 봐야 했다"로 센다.
LOG_MISS_MARK = "위키없음"


def cmd_log(root: Path, op=None, text=None, result=None, gaps_only=False):
    """기록장 append / 조회.

    'query 가 코드를 열었다 = 위키에 없었다'가 핵심 신호다.
    이것이 ingest 의 입력이 된다.
    """
    import datetime
    p = root / "wiki" / "log.md"

    if op is not None:
        if not p.exists():
            print(f"기록장이 없습니다: {p} — 먼저 `cw init`을 실행하세요")
            return 1
        day = datetime.date.today().isoformat()
        esc = lambda s: (s or "").replace("|", "\\|").replace("\n", " ")
        with p.open("a", encoding="utf-8") as f:
            f.write(f"| {day} | {esc(op)} | {esc(text)} | {esc(result)} |\n")
        return 0

    if not p.exists():
        print(f"기록장이 없습니다: {p} — 먼저 `cw init`을 실행하세요")
        return 1

    rows = []
    for line in p.read_text(encoding="utf-8").split("\n"):
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4 or cells[0] in ("날짜", "---"):
            continue
        if set(cells[0]) <= set("- "):
            continue
        rows.append(cells)

    if gaps_only:
        from collections import Counter
        misses = Counter(r[2] for r in rows
                         if r[1] == "query" and LOG_MISS_MARK in r[3])
        if not misses:
            print("위키에 없어서 코드를 열어본 질문이 아직 없습니다.")
            return 0
        print("## 위키에 없어서 코드를 열어본 질문 (많이 물어본 순)\n")
        for i, (q, n) in enumerate(misses.most_common(20), 1):
            print(f"  {i}. {q}  ({n}회 질문됨)")
        print("\n→ 이것이 다음에 문서화할 후보입니다. `ingest` 에 넘기세요.")
        return 0

    if not rows:
        print("기록이 아직 없습니다.")
        return 0
    print(f"## 최근 기록 (전체 {len(rows)}건)\n")
    for r in rows[-20:]:
        print(f"  {r[0]}  {r[1]:6}  {r[2][:40]:42}  {r[3][:30]}")
    return 0
```

- [ ] **Step 4: 서브커맨드 등록**

`main()` 의 `choices=[...]` 목록에 `"log"` 를 추가한다:

```python
    ap.add_argument("command", choices=["setup", "init", "index", "stubs",
                                        "map", "lint", "update", "status",
                                        "doctor", "context", "coverage",
                                        "parse-report", "log"])
```

인자 정의부(`--mark-done` 옆)에 추가한다:

```python
    ap.add_argument("--add", metavar="연산",
                    help="(log 전용) 기록 추가 — query/ingest/lint")
    ap.add_argument("--text", help="(log --add 전용) 기록할 내용")
    ap.add_argument("--result", help="(log --add 전용) 결과 요약")
    ap.add_argument("--gaps", action="store_true",
                    help="(log 전용) 위키에 없어서 코드를 열어본 질문만")
```

> **왜 위치 인자가 아니라 플래그인가:** 기존 명령은 전부
> `cw <명령> <프로젝트경로> [질의]` 형태이고 `root`가 `args.path`에서 나온다.
> `cw log add query "..."` 처럼 위치 인자를 더 쓰면 `path`가 `"add"`로 잡혀
> 프로젝트 경로를 지정할 수 없게 된다. 플래그를 쓰면 기존 규칙이 유지된다.

디스패치부의 `elif args.command == "parse-report":` **뒤**에 추가한다:

```python
    elif args.command == "log":
        sys.exit(cmd_log(root, op=args.add, text=args.text,
                         result=args.result, gaps_only=args.gaps))
```

- [ ] **Step 5: 통과 확인**

Run: `python3 -m unittest tests.test_log -v`
Expected: 5개 PASS

- [ ] **Step 6: CLI 로 확인**

Run:
```bash
cd /tmp && rm -rf logdemo && mkdir logdemo && cd logdemo && git init -q
printf 'int f(void){return 0;}\n' > a.c && git add -A && git commit -qm x
python3 ~/workspace/project_wiki/cw.py init . >/dev/null
CW="python3 $HOME/workspace/project_wiki/cw.py"
$CW log . --add query --text "CAN 프레임 흐름" --result "위키없음 → 코드 3개 열람"
$CW log . --add query --text "CAN 프레임 흐름" --result "위키없음 → 코드 2개 열람"
$CW log . --add query --text "handle_frame 호출자" --result "위키로 답함"
$CW log . --gaps
```
Expected: `1. CAN 프레임 흐름  (2회 질문됨)` 이 나오고 "handle_frame 호출자"는 안 나온다.

- [ ] **Step 7: 커밋**

```bash
git add cw.py tests/test_log.py
git commit -m "feat: cw log — 기록장 append 와 위키 구멍 집계"
```

---

### Task 7: `codewiki-query` 스킬

프롬프트를 SKILL.md 안에 인라인해 Read 왕복을 없앤다.

**Files:**
- Create: `skill/codewiki-query/SKILL.md`
- Modify: `README.md` — 명령표에 `log` 추가, 스킬 설치 안내 갱신

**Interfaces:**
- Consumes: Task 5의 `cw context` 확장, Task 6의 `cw log`, Task 1~3의 표기법

- [ ] **Step 1: 스킬 작성**

`skill/codewiki-query/SKILL.md`:

````markdown
---
name: codewiki-query
description: Use when the user asks a question about how the code works, what something does, who calls what, whether something can be deleted, or where a feature starts — in a repo that has a wiki/ folder built by codewiki. Triggers include 이거 어떻게 동작해, 뭐하는 함수야, 누가 호출해, 지워도 돼, 어디서 시작해, 영향 범위, codewiki query.
---

# codewiki query — 위키와 색인으로 질문에 답한다

**툴킷 위치**: 기본 `~/codewiki/cw.py`. 없으면 사용자에게 물어라.
이하 `CW="python3 ~/codewiki/cw.py"`.

## 절차

### 1. 재료를 모은다

질문에서 심볼 이름을 뽑아 실행한다:

```bash
$CW context <프로젝트> <심볼>
```

출력에 다음이 들어 있다: 정의 위치, 호출자, 호출 대상, 관련 위키 문서,
그리고 **`## ⚠ 코드를 직접 확인해야 할 것`**.

### 2. 코드는 그 목록에 있는 것만 연다

**중요: 스스로 "애매한데?" 판단하지 마라.**
`⚠` 목록에 있는 항목만 해당 파일을 읽어라. 목록이 비어 있으면 코드를 열지 마라.
위키와 색인만으로 답한다.

### 3. 답을 쓴다

**사실을 나열하지 마라.** 아래는 나쁜 답과 좋은 답이다.

❌ "handle_frame은 can.c:212에 정의. 호출자 3개. check_crc를 호출."

✅ "CAN 프레임 처리는 인터럽트에서 시작합니다. 하드웨어가 프레임을 받으면
handle_frame이 불리는데, 여기서 바로 처리하지 않고 큐에 넣습니다 —
ISR에서 동적 할당을 할 수 없기 때문입니다."

차이는 **인과와 순서**다. 그리고 **왜**가 있다.

규칙:

1. 문장을 인과나 순서로 잇는다. 목록 나열 금지
2. **`decisions/`·`notes/` 를 먼저 본다.** "왜"와 "함정"은 거기 있다.
   `files/` stub 에는 없다
3. 서론을 쓰지 마라. 질문에 바로 답하고 부연한다
4. 모르는 것은 마지막에 모은다. 문장 사이에 흩지 마라

### 4. 형식

각 문장 앞에 글자 배지를 붙이고, 출처는 아래에 번호로 모은다.

```
[확인 1] CAN 프레임은 handle_frame이 받아 디코딩한 뒤 상위로 올립니다.
[확인 2] CRC 검사에 실패한 프레임은 버립니다.
[추론 3] 큐에 넣는 것은 ISR에서 동적 할당을 피하기 위한 것으로 보입니다.
[모름  ] 실제 출하되는 변형이 무엇인지는 알 수 없습니다.

── 출처 ─────────────────────────────────────────
  1  src/hal/can.c#handle_frame
  2  src/hal/can.c#check_crc
  3  추론 ← ISR 문맥에서 호출됨 (src/hal/can.c:88)

── 주의 ─────────────────────────────────────────
  이 파일에서 매크로 3곳을 읽지 못했습니다.
  위 목록이 전부가 아닐 수 있습니다.
```

- **터미널에는 이모지를 쓰지 마라.** 사내 뷰어에서 깨진다. 글자 배지를 쓴다
- 배지 종류: `[확인]` `[추론]` `[출처]` `[모름]` `[주의]`
- `[모름]` 은 번호가 없다. 자리는 공백으로 채워 정렬을 유지한다
- 배지 폭은 가장 큰 번호의 자릿수에 맞춘다 (출처가 10개 넘으면 한 칸 넓어진다)
- **주의 블록은 `⚠` 목록에서 온다.** 없으면 블록 자체를 생략한다

### 5. 기록을 남긴다

답을 마친 뒤 반드시 실행한다:

```bash
# 위키에 없어서 코드를 열어봤을 때
$CW log <프로젝트> --add query --text "<질문 요약>" \
    --result "위키없음 → 코드 N개 열람"

# 위키만으로 답했을 때
$CW log <프로젝트> --add query --text "<질문 요약>" --result "위키로 답함"
```

`--result` 에 **`위키없음`** 이라는 말이 들어가야 구멍으로 집계된다. 문구를 바꾸지 마라.

**이 기록이 "위키에 뭐가 빠졌나"의 신호가 된다.** 빠뜨리지 마라.

## 절대 규칙

1. `⚠` 목록에 없는 파일은 열지 않는다
2. 근거 없는 문장은 `[추론]` 이나 `[모름]` 으로 낮춘다. `[확인]` 으로 쓰지 않는다
3. 색인에 없다 = "확인 못 함"이지 "존재하지 않음"이 아니다.
   "아무도 호출하지 않습니다"라고 단정하지 마라
4. 답을 마쳤으면 `log add` 를 실행한다

## 흔한 실수

- `⚠` 목록을 무시하고 코드를 다 열어봄 → 느리고 비싸다
- 사실 나열로 답함 → 사용자가 원하는 건 설명이지 데이터가 아니다
- `log add` 를 빠뜨림 → ingest 가 무엇을 채울지 알 수 없게 된다
- 터미널 답변에 이모지 사용 → 깨진다
````

- [ ] **Step 2: 스킬 프론트매터 형식 확인**

Run: `head -4 skill/codewiki-query/SKILL.md && head -4 skill/codewiki/SKILL.md`
Expected: 두 파일의 프론트매터 형식(`---` / `name:` / `description:` / `---`)이 같다.

- [ ] **Step 3: 기존 `codewiki` 스킬의 표기 규칙 갱신**

**이걸 빠뜨리면 템플릿만 바뀌고 AI 는 계속 옛 표기로 쓴다.**

`skill/codewiki/SKILL.md` 의 "절대 규칙" 항목 1·2 를 다음으로 교체한다:

```markdown
1. 코드를 위키로 복사하지 않는다 — anchor(`경로:라인`, `경로#심볼`)로 참조.
2. 주장은 **불릿 + 배지**로 쓰고 근거는 **이름표 각주**로 단다:
   `- ✅ 문장[^ref]` / `- 🔍 문장[^why]` / `- ❓ 문장`
   그리고 문서 하단에 `[^ref]: \`경로#심볼\`` 형태로 정의한다.
   - ✅확인·🔍추론·📄출처 는 **각주가 필수**다. ❓모름은 각주가 없다
   - **🔍 에는 반드시 추론의 근거를 적는다** — 없으면 lint 에러
   - 한 주장에 근거는 하나. 둘 이상이면 문장을 쪼갠다
   - 문서 맨 위 `> [!info]` 요약 줄은 기계가 만든다. 손대지 않는다
```

같은 파일의 명령 요약 표에 행을 추가한다:

```markdown
| `$CW log <proj> --gaps` | 위키에 없어서 코드를 열어본 질문 (다음 문서화 후보) |
```

- [ ] **Step 4: 옛 표기가 남아 있지 않은지 확인**

Run: `grep -rn '\^\[confirmed\|\^\[inferred\|\^\[unknown' skill/ templates/ prompts/ || echo "옛 표기 없음"`
Expected: `prompts/` 에만 남아 있다 (계획 밖 — 스킬 셋이 다 나온 뒤 재생성).
`skill/` 과 `templates/` 에는 하나도 없어야 한다.

- [ ] **Step 5: README 갱신**

`README.md` 의 명령어 표에서 `| \`cw coverage\` |` 행 **뒤**에 추가한다:

```markdown
| `cw log --gaps` | 위키에 없어서 코드를 열어본 질문 목록 (다음 문서화 후보) |
```

같은 파일의 Claude Code 스킬 설치 안내를 다음으로 교체한다:

````markdown
**Q. Claude Code 스킬은 어떻게 설치하나요?**

```bash
mkdir -p ~/.claude/skills
cp -r ~/codewiki/skill/* ~/.claude/skills/
```

설치되는 스킬:

| 스킬 | 언제 쓰이나 |
|---|---|
| `codewiki` | 위키 생성·갱신 ("위키 만들어줘") |
| `codewiki-query` | 코드에 대한 질문 ("이거 어떻게 동작해?") |

이후 말로만 시키면 전체 절차가 돌아갑니다.
````

- [ ] **Step 6: 전체 회귀**

Run: `python3 -m unittest discover -s tests`
Expected: OK. FAIL/ERROR 0건.

- [ ] **Step 7: 커밋**

```bash
git add skill/ README.md
git commit -m "feat: codewiki-query 스킬 + 기존 스킬 표기 규칙 갱신"
```

---

## 완료 기준

- [ ] `python3 -m unittest discover -s tests` — FAIL/ERROR 0건
- [ ] `cw init` 후 `wiki/log.md` 와 개정된 `conventions.md` 가 설치된다
- [ ] `cw lint` 가 근거 없는 `🔍` 를 에러로 잡는다
- [ ] `cw lint` 실행 후 문서 상단에 `> [!info]` 요약이 자동 생성된다
- [ ] `cw context` 가 동명이인·gaps·추론 문장을 "확인 필요"로 지목한다
- [ ] `cw log --gaps` 가 질문 횟수 순으로 후보를 낸다

## 이 계획 밖의 것

| 항목 | 왜 뺐나 |
|---|---|
| 앵커 내용 해시 (`@a3f2b1`) | 표기법 스펙 §8, 선행 설계 Phase 2. 없어도 나머지가 동작한다. 들어오면 `uncertainties`에 조건 하나가 추가될 뿐 |
| `ingest` / `lint` 스킬 | query/ingest/lint 스펙 §9.1 의 2·3단계. 기록장이 쌓인 뒤라야 의미가 있다 |
| `cw coverage` 삭제 | 3단계에서 lint 로 옮기며 함께 처리 |
| `wiki/index.md` | 3단계 |
| `prompts/*.md` 재생성 | 스킬이 셋 다 나온 뒤에 한 번에 |

## 사용자 확인 지점

Task 4 완료 직후, **Obsidian 에서 눈으로 확인**해야 한다 (표기법 스펙 §8 가정 1·2):

1. 이름표 각주(`[^handle_frame]`)가 `1, 2, 3` 번호로 렌더링되는가
2. `> [!info]` 콜아웃이 플러그인 없이 박스로 보이는가

둘 중 하나라도 안 되면 그 자리에서 대안으로 전환한다 —
각주는 번호 라벨로, 콜아웃은 일반 인용문으로.
