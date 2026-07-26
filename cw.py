#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
codewiki (cw.py) — 코드 프로젝트용 AI Wiki 툴킷의 결정론적 부분.

의존성: Python 3.8+ 표준 라이브러리만 사용. (universal-ctags가 있으면 C/C++ 정밀도 향상)

명령:
  cw.py init <프로젝트경로>     위키 골격(wiki/)과 설정(.codewiki/) 설치
  cw.py index [경로]            소스에서 사실(facts) 추출 → .codewiki/facts.db
  cw.py stubs [경로]            wiki/files/ 아래 파일 stub 자동 생성 (편집 금지 영역)
  cw.py map   [경로]            모듈 후보/중요 파일 요약 출력 (AI에게 줄 지도)
  cw.py lint  [경로]            위키 문서의 anchor·라벨·최신성 검사
  cw.py update [경로]           git diff 기반으로 낡은(stale) 문서 찾기 + 재색인
  cw.py update --mark-done      AI 갱신 완료 후 현재 커밋을 기준점으로 기록
  cw.py status [경로]           색인 상태 요약

설계 원칙:
  - 코드는 절대 위키로 복사하지 않는다. anchor(경로:라인, sym:경로#이름)로 참조만 한다.
  - 기계가 뽑은 사실(facts.db)은 갱신하지 않고 매번 재생성한다.
  - DB에 없다는 것은 "존재하지 않음"이 아니라 "확인 못 함"이다. (비대칭 규칙)
"""

import argparse
import ast
import bisect
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import warnings
from pathlib import Path

TOOLKIT_DIR = Path(__file__).resolve().parent

LANG_BY_EXT = {
    ".py": "python",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp", ".inl": "cpp",
    ".idl": "idl",
    ".fidl": "idl",  # Franca IDL(SOME/IP 계열) — interface 이름 추출 목적
}

DEFAULT_EXCLUDES = [
    ".git", ".codewiki", "wiki", "build", "cmake-build-*", "out", "dist",
    "__pycache__", ".venv", "venv", "node_modules", "third_party", "external",
    ".idea", ".vscode",
]

# ---------------------------------------------------------------- 공통 유틸

def die(msg):
    print("오류: " + msg, file=sys.stderr)
    sys.exit(1)


def sha_of(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:12]


def read_source(p: Path):
    """소스 파일 읽기. UTF-8 → CP949(한국어 레거시) → latin-1 순서로 시도.
    반환: (raw bytes, 디코딩된 text, 사용된 인코딩)"""
    raw = p.read_bytes()
    for enc in ("utf-8", "cp949", "latin-1"):
        try:
            return raw, raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw, raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def run_git(root: Path, *args):
    """git 명령 실행. git 저장소가 아니거나 실패하면 None."""
    try:
        r = subprocess.run(["git", "-C", str(root)] + list(args),
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None
        return r.stdout.strip()
    except Exception:
        return None


def load_config(root: Path) -> dict:
    cfg_path = root / ".codewiki" / "config.json"
    cfg = {"exclude_dirs": DEFAULT_EXCLUDES, "extra_source_dirs": []}
    if cfg_path.exists():
        try:
            cfg.update(json.loads(cfg_path.read_text(encoding="utf-8")))
        except Exception as e:
            die(f"config.json 파싱 실패: {e}")
    return cfg


def is_excluded(rel_parts, patterns):
    for part in rel_parts:
        for pat in patterns:
            if fnmatch.fnmatch(part, pat):
                return True
    return False


def iter_source_files(root: Path, cfg: dict):
    excludes = cfg.get("exclude_dirs", DEFAULT_EXCLUDES)
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        dirnames[:] = [d for d in dirnames
                       if not is_excluded([d], excludes)]
        if rel.parts and is_excluded(rel.parts, excludes):
            dirnames[:] = []
            continue
        for fn in sorted(filenames):
            ext = Path(fn).suffix.lower()
            if ext in LANG_BY_EXT:
                yield Path(dirpath) / fn


def db_path(root: Path) -> Path:
    return root / ".codewiki" / "facts.db"


def open_db(root: Path, create=False) -> sqlite3.Connection:
    p = db_path(root)
    if not p.exists() and not create:
        die("facts.db가 없습니다. 먼저 `cw.py index`를 실행하세요.")
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    con.execute("PRAGMA journal_mode=WAL")
    return con


SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY, path TEXT UNIQUE, sha TEXT, lang TEXT, loc INTEGER);
CREATE TABLE IF NOT EXISTS symbols(
  id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT, kind TEXT,
  signature TEXT, line_start INTEGER, line_end INTEGER, provenance TEXT);
CREATE TABLE IF NOT EXISTS edges(
  id INTEGER PRIMARY KEY, src_file TEXT, src_symbol TEXT,
  dst_name TEXT, dst_file TEXT, kind TEXT, provenance TEXT, confidence TEXT);
CREATE TABLE IF NOT EXISTS gaps(
  id INTEGER PRIMARY KEY, file TEXT, line INTEGER, kind TEXT,
  detail TEXT, affects_symbol TEXT,
  status TEXT NOT NULL DEFAULT 'open', resolution TEXT, evidence TEXT);
CREATE INDEX IF NOT EXISTS idx_gap_file ON gaps(file);
CREATE INDEX IF NOT EXISTS idx_gap_sym ON gaps(affects_symbol);
CREATE INDEX IF NOT EXISTS idx_gap_kind ON gaps(kind);
CREATE INDEX IF NOT EXISTS idx_sym_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_edge_src ON edges(src_file);
CREATE INDEX IF NOT EXISTS idx_edge_dst ON edges(dst_file);
CREATE INDEX IF NOT EXISTS idx_edge_dstname ON edges(dst_name);
CREATE INDEX IF NOT EXISTS idx_sym_name ON symbols(name);
"""

# ---------------------------------------------------------------- 파서들
# 원칙: 여기서 뽑은 것은 "찾은 사실"이다. 못 찾은 것은 없다는 뜻이 아니다.

def parse_python(path: Path, text: str):
    """stdlib ast 사용 — python 심볼/임포트는 신뢰도 높음(confirmed)."""
    symbols, edges = [], []
    try:
        # 색인 대상 파일의 문법 경고(invalid escape sequence 등)는
        # 그 프로젝트의 문제이지 색인 오류가 아니므로 출력하지 않는다.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(text)
    except SyntaxError:
        return symbols, edges
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = ", ".join(a.arg for a in node.args.args)
            symbols.append((node.name, "function", f"def {node.name}({args})",
                            node.lineno, getattr(node, "end_lineno", node.lineno),
                            "python-ast"))
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    callee = None
                    if isinstance(fn, ast.Name):
                        callee = fn.id
                    elif isinstance(fn, ast.Attribute):
                        callee = fn.attr
                    if callee:
                        # 이름 기반 호출: 어느 심볼로 가는지는 미해석 → inferred
                        edges.append((node.name, callee, None, "calls",
                                      "python-ast", "inferred"))
        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(getattr(b, "id", getattr(b, "attr", "?"))
                              for b in node.bases)
            symbols.append((node.name, "class", f"class {node.name}({bases})",
                            node.lineno, getattr(node, "end_lineno", node.lineno),
                            "python-ast"))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                edges.append((None, alias.name, None, "imports",
                              "python-ast", "confirmed"))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            edges.append((None, mod, None, "imports", "python-ast", "confirmed"))
    return symbols, edges


RE_INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([^">]+)[">]', re.M)
RE_CLASS = re.compile(r'^\s*(?:template\s*<[^>]*>\s*)?(class|struct)\s+(\w+)'
                      r'(?![^{;\n]*;)', re.M)
# ---- C/C++ 스캐너 (정규식 휴리스틱 대체) ----------------------------------
# 방식: 주석·문자열·전처리 줄을 공백으로 지운 뒤(줄 번호 보존),
# 모든 '{'에 대해 바로 앞이 "이름(인자들)" 형태인지 뒤로 검사한다.
# 정규식 대비 개선: 여러 줄 시그니처, 생성자 초기화 리스트, 함수 끝 라인,
# 주석/문자열 속 가짜 코드 오탐 제거. 여전히 못 하는 것: 매크로 전개,
# 템플릿 인스턴스 해석, operator 오버로드 일부.

C_STRIP_RE = re.compile(
    r'R"([^(\n]{0,16})\([\s\S]*?\)\1"'        # C++11 raw string
    r'|//[^\n]*'                              # 줄 주석
    r'|/\*[\s\S]*?\*/'                        # 블록 주석
    r'|"(?:\\.|[^"\\\n])*"'                   # 문자열
    r"|'(?:\\.|[^'\\\n])*'"                   # 문자
    r'|^[ \t]*#[^\n]*(?:\\\n[^\n]*)*',        # 전처리 지시문(#define의 { 방지)
    re.M)

CTRL_KEYWORDS = {"if", "for", "while", "switch", "return", "else", "do",
                 "case", "catch", "sizeof", "new", "delete", "defined",
                 "assert", "constexpr", "requires", "alignas", "decltype",
                 "alignof", "static_assert", "typeid"}
TRAILER_WORDS = {"const", "noexcept", "override", "final", "mutable",
                 "volatile", "throw", "try"}


def _blank_keep_newlines(m):
    return "".join(c if c == "\n" else " " for c in m.group(0))


def _match_back(s, i, close_ch, open_ch, limit_chars=6000):
    """s[i]==close_ch에서 짝이 되는 open_ch 인덱스. 실패/한도 초과 시 -1."""
    depth = 0
    limit = max(0, i - limit_chars)
    while i >= limit:
        c = s[i]
        if c == close_ch:
            depth += 1
        elif c == open_ch:
            depth -= 1
            if depth == 0:
                return i
        i -= 1
    return -1


def _ident_back(s, i):
    """s[i]에서 뒤로 (한정 가능한) 식별자 읽기 → (name, name_start_index)."""
    j = i
    while j >= 0:
        c = s[j]
        if c.isalnum() or c in "_~":
            j -= 1
        elif c == ":" and j >= 1 and s[j - 1] == ":":
            j -= 2
        else:
            break
    return s[j + 1:i + 1], j + 1


def _is_access_colon(s, k):
    """s[k]==':' 가 public:/private:/protected: 의 콜론인지."""
    if k < 1:
        return False
    word, _ = _ident_back(s, k - 1)
    return word in ("public", "private", "protected")


def _sig_before_brace(s, brace_pos):
    """'{' 직전이 함수 시그니처면 (name, name_pos, sig) 반환, 아니면 None."""
    i = brace_pos - 1
    for _ in range(40):  # 초기화 리스트 멤버 수 상한
        while i >= 0 and s[i].isspace():
            i -= 1
        if i < 0:
            return None
        c = s[i]
        if c == ")":
            op = _match_back(s, i, ")", "(")
            if op < 0:
                return None
            j = op - 1
            while j >= 0 and s[j].isspace():
                j -= 1
            if j < 0:
                return None
            name, ns = _ident_back(s, j)
            if not name:
                return None  # 람다 `](){...}`, 캐스팅 등
            base = name.split("::")[-1].lstrip("~")
            if base in TRAILER_WORDS:  # noexcept(...) 같은 꼬리 그룹
                i = ns - 1
                continue
            if base in CTRL_KEYWORDS:
                return None
            # 생성자 초기화 리스트 항목( `: a_(1), b_(2)` )이면 더 뒤로.
            # 단 `public:` 등 접근 지정자의 콜론은 초기화 리스트가 아니다.
            k = ns - 1
            while k >= 0 and s[k].isspace():
                k -= 1
            if k >= 0 and (s[k] == "," or
                           (s[k] == ":" and (k == 0 or s[k - 1] != ":")
                            and not _is_access_colon(s, k))):
                i = k - 1
                continue
            sig = re.sub(r"\s+", " ", s[ns:i + 1]).strip()
            return name, ns, sig[:120]
        if c == "}":  # 초기화 리스트의 brace-init `count_{0}` 건너뛰기
            op = _match_back(s, i, "}", "{")
            if op < 0:
                return None
            i = op - 1
            continue
        if c.isalnum() or c == "_":
            name, ns = _ident_back(s, i)
            if name in TRAILER_WORDS:
                i = ns - 1
                continue
            if name.split("::")[-1] in CTRL_KEYWORDS:
                return None  # `return {};` 등
            arrow = s.rfind("->", max(0, ns - 200), ns)
            if arrow >= 0:  # 후행 반환 타입 `-> std::vector<int>`
                i = arrow - 1
                continue
            # 초기화 리스트의 brace-init 멤버 이름( `count_{0}` 의 count_ )
            k = ns - 1
            while k >= 0 and s[k].isspace():
                k -= 1
            if k >= 0 and (s[k] == "," or
                           (s[k] == ":" and (k == 0 or s[k - 1] != ":"))):
                i = k - 1
                continue
            return None
        if c in ">&*:":
            arrow = s.rfind("->", max(0, i - 200), i)
            if arrow >= 0:
                i = arrow - 1
                continue
            return None
        return None
    return None


CALL_RE = re.compile(r"\b([A-Za-z_]\w{1,63})\s*\(")
CALL_NOISE = CTRL_KEYWORDS | TRAILER_WORDS | {
    "int", "char", "float", "double", "void", "bool", "long", "short",
    "unsigned", "signed", "auto", "size_t", "template", "operator",
    "static_cast", "dynamic_cast", "reinterpret_cast", "const_cast"}
MAX_CALLS_PER_FUNC = 32


def _find_functions(stripped):
    nl = [m.start() for m in re.finditer("\n", stripped)]

    def line_of(pos):
        return bisect.bisect_left(nl, pos) + 1

    stack, pairs = [], {}
    for m in re.finditer(r"[{}]", stripped):
        if m.group() == "{":
            stack.append(m.start())
        elif stack:
            pairs[stack.pop()] = m.start()
    out, calls, seen = [], [], set()
    for p in sorted(pairs):
        hit = _sig_before_brace(stripped, p)
        if hit:
            name, ns, sig = hit
            if ns in seen:  # 초기화 리스트의 brace-init 등 내부 '{' 중복 방지
                continue
            seen.add(ns)
            out.append((name, "function", sig,
                        line_of(ns), line_of(pairs[p]), "scanner"))
            # 본문 속 호출 후보: `이름(` 패턴 — 어느 심볼로 가는지는 미해석
            body = stripped[p:pairs[p]]
            base = name.split("::")[-1]
            found = set()
            for cm in CALL_RE.finditer(body):
                callee = cm.group(1)
                if callee in CALL_NOISE or callee == base:
                    continue
                found.add(callee)
                if len(found) >= MAX_CALLS_PER_FUNC:
                    break
            for callee in sorted(found):
                calls.append((name, callee, None, "calls",
                              "scanner", "inferred"))
    return out, calls


def parse_c_cpp(path: Path, text: str):
    """스캐너 휴리스틱 — 컴파일하지 않으므로 매크로 전개·템플릿 해석은 못 하고,
    그 사실을 provenance='scanner'로 기록한다. universal-ctags가 있으면 대체됨."""
    symbols, edges = [], []
    for m in RE_INCLUDE.finditer(text):  # 전처리 줄은 원본에서 추출
        edges.append((None, m.group(1), None, "includes", "regex", "confirmed"))
    stripped = C_STRIP_RE.sub(_blank_keep_newlines, text)
    for m in RE_CLASS.finditer(stripped):
        line = stripped.count("\n", 0, m.start()) + 1
        symbols.append((m.group(2), m.group(1), m.group(0).strip()[:120],
                        line, line, "scanner"))
    funcs, calls = _find_functions(stripped)
    symbols += funcs
    edges += calls
    return symbols, edges


def parse_c_cpp_ctags(path: Path, text: str):
    """universal-ctags(JSON 출력 지원)가 있으면 사용 — regex보다 정확."""
    try:
        r = subprocess.run(
            ["ctags", "--output-format=json", "--fields=+neS",
             "--languages=C,C++", "-f", "-", str(path)],
            capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None
    except Exception:
        return None
    symbols = []
    kind_map = {"function": "function", "class": "class", "struct": "struct",
                "member": "method", "prototype": "prototype",
                "enum": "enum", "typedef": "typedef"}
    for line in r.stdout.splitlines():
        try:
            t = json.loads(line)
        except Exception:
            continue
        kind = kind_map.get(t.get("kind"))
        if not kind:
            continue
        ln = t.get("line", 0)
        symbols.append((t.get("name", "?"), kind,
                        (t.get("signature") or t.get("pattern") or "")[:120],
                        ln, t.get("end", ln), "ctags"))
    edges = [(None, m.group(1), None, "includes", "regex", "confirmed")
             for m in RE_INCLUDE.finditer(text)]
    return symbols, edges


RE_IDL_MODULE = re.compile(r'^\s*module\s+(\w+)', re.M)
RE_IDL_IFACE = re.compile(r'^\s*(?:abstract\s+|local\s+)?interface\s+(\w+)', re.M)
RE_IDL_STRUCT = re.compile(r'^\s*struct\s+(\w+)', re.M)
RE_IDL_ENUM = re.compile(r'^\s*enum\s+(\w+)', re.M)
RE_IDL_TYPEDEF = re.compile(r'^\s*typedef\s+[\w:<>,\s]+?(\w+)\s*(?:\[[^\]]*\])?\s*;', re.M)
RE_IDL_METHOD = re.compile(
    r'^\s*(?:oneway\s+)?(?:void|[\w:]+(?:\s*<[^>]*>)?)\s+(\w+)\s*\(', re.M)
IDL_METHOD_BLACKLIST = {"module", "interface", "struct", "enum", "typedef",
                        "exception", "union", "switch", "case", "if"}


def parse_idl(path: Path, text: str):
    """CORBA/DDS IDL — 문법이 단순해 정규식으로도 신뢰도가 상당히 높음."""
    symbols, edges = [], []
    for m in RE_INCLUDE.finditer(text):
        edges.append((None, m.group(1), None, "includes", "regex", "confirmed"))
    for rx, kind in [(RE_IDL_MODULE, "idl_module"), (RE_IDL_IFACE, "idl_interface"),
                     (RE_IDL_STRUCT, "idl_struct"), (RE_IDL_ENUM, "idl_enum"),
                     (RE_IDL_TYPEDEF, "idl_typedef")]:
        for m in rx.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            symbols.append((m.group(1), kind, m.group(0).strip()[:120],
                            line, line, "regex"))
    declared = {s[0] for s in symbols}
    for m in RE_IDL_METHOD.finditer(text):
        name = m.group(1)
        if name in IDL_METHOD_BLACKLIST or name in declared:
            continue
        line = text.count("\n", 0, m.start()) + 1
        symbols.append((name, "idl_method", m.group(0).strip()[:120],
                        line, line, "regex"))
    return symbols, edges


_HAS_UCTAGS = None

def has_universal_ctags():
    global _HAS_UCTAGS
    if _HAS_UCTAGS is None:
        try:
            r = subprocess.run(["ctags", "--version"], capture_output=True,
                               text=True, timeout=10)
            _HAS_UCTAGS = "Universal Ctags" in (r.stdout or "")
        except Exception:
            _HAS_UCTAGS = False
    return _HAS_UCTAGS


# ---------------------------------------------------------------- tree-sitter
# 교체 이유는 정확도가 아니라 "못 읽은 것을 말해주는 능력"이다.
# 정규식 스캐너는 못 읽으면 조용히 넘어가므로, 자기가 뭘 놓쳤는지 모른다.
# tree-sitter는 해석 실패를 ERROR/MISSING 노드로 알려주고, 그것이
# 미해석 대장(gaps)의 재료가 된다.

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
        from tree_sitter import Language, Parser
        langs = {"c": Language(tree_sitter_c.language()),
                 "cpp": Language(tree_sitter_cpp.language())}
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


_TS_NAME_NODES = ("identifier", "field_identifier", "qualified_identifier",
                  "operator_name", "destructor_name", "type_identifier")


def _ts_is_include_guard(node, src):
    """인클루드 가드(#ifndef FOO_H / #define FOO_H)인가?

    가드는 거의 모든 헤더에 있으므로 이걸 조건부 컴파일 구멍으로 세면
    판정이 항상 '나쁨'이 되고 커버리지 경고가 노이즈가 되어 무시당한다.
    가드는 빌드 변형이 아니라 관용구이므로 제외한다.

    판별: 최상위 preproc_ifdef 이면서 #else 가 없고,
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

    gaps 항목은 (kind, line, detail, affects_symbol) 4-튜플.
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

        # ERROR/MISSING 은 별도 if 로 둔다. elif 로 묶으면 ERROR 노드 안의
        # 함수 정의를 놓친다.
        if n.is_missing:
            gaps.append(("parse_missing", line,
                         "문법상 빠진 토큰 '%s' — 매크로 때문일 수 있음" % n.type,
                         None))
        elif n.is_error:
            gaps.append(("parse_error", line,
                         "이 구간을 문법으로 해석하지 못함", None))

        if n.type == "function_definition":
            name, mangled = _ts_decl_name(n, src)
            if name:
                symbols.append((name, "function",
                                txt(n).split("{")[0].strip()[:120],
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
        elif n.type == "initializer_list":
            # 함수 포인터 테이블 — 초기화 리스트 안의 맨 식별자는 함수를
            # 가리킬 수 있다. 호출로 안 잡히므로 구멍으로 남긴다.
            for ch in n.children:
                if ch.type == "identifier":
                    gaps.append(("fnptr_table", ch.start_point[0] + 1,
                                 "%s — 테이블 등록. 호출로 잡히지 않음" % txt(ch),
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
                             "조건부 컴파일 %s — 어느 분기가 빌드되는지 알 수 없음"
                             % (txt(cond) if cond is not None else n.type),
                             None))
        elif n.type in ("gnu_asm_expression", "asm_statement"):
            gaps.append(("inline_asm", line, "인라인 asm — 해석 불가", None))

        for ch in n.children:
            walk(ch, cur)

    walk(tree.root_node, None)
    return symbols, edges, gaps


_LAST_GAPS = []   # 직전 parse_file 호출이 발견한 구멍. cmd_index 가 회수한다.


def parse_file(path: Path, lang: str, text: str):
    """(symbols, edges) 반환. 구멍은 _LAST_GAPS 에 남긴다.

    반환 시그니처를 바꾸지 않는 이유: cmd_doctor 등 기존 호출부를 깨지 않기 위함.
    """
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

# ---------------------------------------------------------------- index

def resolve_include(dst_name, files_by_name):
    """include/import 대상을 저장소 내 파일로 해석 시도. 실패 = 외부 의존."""
    base = os.path.basename(dst_name)
    cands = files_by_name.get(base, [])
    if len(cands) == 1:
        return cands[0]
    for c in cands:  # 경로 끝부분이 일치하면 채택
        if c.endswith(dst_name):
            return c
    return None


def cmd_index(root: Path, only_files=None):
    cfg = load_config(root)
    con = open_db(root, create=True)
    con.executescript(SCHEMA)
    cur = con.cursor()

    all_paths = [p for p in iter_source_files(root, cfg)]
    files_by_name = {}
    for p in all_paths:
        rel = p.relative_to(root).as_posix()
        files_by_name.setdefault(p.name, []).append(rel)

    targets = all_paths
    if only_files is not None:
        wanted = set(only_files)
        targets = [p for p in all_paths
                   if p.relative_to(root).as_posix() in wanted]
        # 삭제된 파일 정리
        for f in only_files:
            if not (root / f).exists():
                cur.execute("DELETE FROM symbols WHERE file_id IN "
                            "(SELECT id FROM files WHERE path=?)", (f,))
                cur.execute("DELETE FROM edges WHERE src_file=?", (f,))
                cur.execute("DELETE FROM gaps WHERE file=?", (f,))
                cur.execute("DELETE FROM files WHERE path=?", (f,))
    else:
        cur.execute("DELETE FROM files")
        cur.execute("DELETE FROM symbols")
        cur.execute("DELETE FROM edges")
        cur.execute("DELETE FROM gaps")

    n_sym = n_edge = n_gap = 0
    show_progress = sys.stderr.isatty() and len(targets) > 10
    for i, p in enumerate(targets, 1):
        rel = p.relative_to(root).as_posix()
        if show_progress and (i % 10 == 0 or i == len(targets)):
            print(f"\r색인 중... {i}/{len(targets)}  {rel[:60]:<60}",
                  end="", file=sys.stderr, flush=True)
        try:
            raw, text, _enc = read_source(p)
        except Exception:
            continue
        lang = LANG_BY_EXT[p.suffix.lower()]
        sha = sha_of(raw)
        loc = text.count("\n") + 1
        cur.execute("DELETE FROM symbols WHERE file_id IN "
                    "(SELECT id FROM files WHERE path=?)", (rel,))
        cur.execute("DELETE FROM edges WHERE src_file=?", (rel,))
        cur.execute("DELETE FROM gaps WHERE file=?", (rel,))
        cur.execute("DELETE FROM files WHERE path=?", (rel,))
        cur.execute("INSERT INTO files(path, sha, lang, loc) VALUES(?,?,?,?)",
                    (rel, sha, lang, loc))
        fid = cur.lastrowid
        symbols, edges = parse_file(p, lang, text)
        for (name, kind, sig, ls, le, prov) in symbols:
            cur.execute("INSERT INTO symbols(file_id,name,kind,signature,"
                        "line_start,line_end,provenance) VALUES(?,?,?,?,?,?,?)",
                        (fid, name, kind, sig, ls, le, prov))
            n_sym += 1
        for (src_sym, dst_name, _dst_file, kind, prov, conf) in edges:
            dst_file = None
            if kind in ("includes", "imports"):
                probe = dst_name.replace(".", "/") + ".py" \
                    if kind == "imports" else dst_name
                dst_file = resolve_include(probe, files_by_name)
            cur.execute("INSERT INTO edges(src_file,src_symbol,dst_name,"
                        "dst_file,kind,provenance,confidence) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (rel, src_sym, dst_name, dst_file, kind, prov, conf))
            n_edge += 1
        # 미해석 대장 — 파서가 못 읽은 지점. 설계 §6.3
        for (gkind, gline, gdetail, gaffects) in _LAST_GAPS:
            cur.execute("INSERT INTO gaps(file,line,kind,detail,affects_symbol,"
                        "status) VALUES(?,?,?,?,?,'open')",
                        (rel, gline, gkind, gdetail, gaffects))
            n_gap += 1
    if show_progress:
        print(file=sys.stderr)
    n_bind = _link_boundaries(root, cur)
    con.commit()

    head = run_git(root, "rev-parse", "--short", "HEAD")
    state_p = root / ".codewiki" / "state.json"
    state = {}
    if state_p.exists():
        state = json.loads(state_p.read_text(encoding="utf-8"))
    state["last_indexed_commit"] = head
    state_p.write_text(json.dumps(state, indent=2), encoding="utf-8")

    scope = f"{len(targets)}개 파일(부분)" if only_files else f"{len(targets)}개 파일(전체)"
    if n_bind:
        scope += f", 경계 연결 {n_bind}개"
    print(f"색인 완료: {scope}, 심볼 {n_sym}개, 관계 {n_edge}개, 구멍 {n_gap}곳"
          f"{' [tree-sitter]' if ts_status()[0] else (' [ctags]' if has_universal_ctags() else ' [내장 스캐너]')}")
    if head:
        print(f"기준 커밋: {head}")

def _link_boundaries(root: Path, cur):
    """언어 경계 연결(미들웨어 중립). IDL/Franca 정의 파일과 코드 파일을
    (a) 파일 이름 줄기(rtiddsgen 등 생성 코드는 원본 이름을 물려받는 관례),
    (b) 인터페이스 이름 언급으로 잇는다. 전부 추정(inferred) 등급.
    RTI DDS / CycloneDDS / zenoh-pico / SOME-IP(Franca) 공통으로 동작."""
    idl_rows = cur.execute("SELECT path FROM files WHERE lang='idl'").fetchall()
    if not idl_rows:
        return 0
    cur.execute("DELETE FROM edges WHERE kind IN ('binds','generated_from')")
    stems = {Path(ip).stem: ip for (ip,) in idl_rows if len(Path(ip).stem) >= 4}
    names = {}
    for name, ipath in cur.execute(
            "SELECT s.name, f.path FROM symbols s JOIN files f ON f.id=s.file_id "
            "WHERE f.lang='idl' AND s.kind IN "
            "('idl_interface','idl_struct','idl_module') AND length(s.name)>=4"):
        names.setdefault(name, set()).add(ipath)
    # 프로젝트 내에서 유일하게 정의된 이름만 사용 (동명이인 방지)
    names = {n: ps.pop() for n, ps in names.items()
             if len(ps) == 1 and n not in CALL_NOISE}
    name_rx = None
    if names:
        alt = "|".join(re.escape(n) for n in
                       sorted(names, key=len, reverse=True))
        name_rx = re.compile(rf"\b({alt})\b")
    n_edges = 0
    for (path, lang) in cur.execute(
            "SELECT path, lang FROM files WHERE lang!='idl'").fetchall():
        base = Path(path).stem
        for stem, ipath in stems.items():
            if base == stem or base.startswith(stem):
                cur.execute(
                    "INSERT INTO edges(src_file,src_symbol,dst_name,dst_file,"
                    "kind,provenance,confidence) VALUES(?,?,?,?,?,?,?)",
                    (path, None, stem, ipath, "generated_from",
                     "stem-match", "inferred"))
                n_edges += 1
        if name_rx is None:
            continue
        try:
            text = read_source(root / path)[1]
        except Exception:
            continue
        hit_names = {m.group(1) for m in name_rx.finditer(text)}
        for n in sorted(hit_names):
            cur.execute(
                "INSERT INTO edges(src_file,src_symbol,dst_name,dst_file,"
                "kind,provenance,confidence) VALUES(?,?,?,?,?,?,?)",
                (path, None, n, names[n], "binds", "name-match", "inferred"))
            n_edges += 1
    return n_edges


# ---------------------------------------------------------------- stubs

STUB_HEADER = """---
type: file-stub
generated: true
source: {src}
source_sha: {sha}
lang: {lang}
---
> ⚙️ **자동 생성 문서 — 편집 금지.** `cw.py stubs` 실행 시 통째로 덮어써집니다.
> 원본: `{src}` ({loc}줄)

"""


def stub_rel(src_rel: str) -> str:
    return f"files/{src_rel}.md"


def cmd_stubs(root: Path):
    con = open_db(root)
    cur = con.cursor()
    wiki = root / "wiki"
    files_dir = wiki / "files"
    if files_dir.exists():
        shutil.rmtree(files_dir)
    files = cur.execute("SELECT id, path, sha, lang, loc FROM files "
                        "ORDER BY path").fetchall()
    known = {f[1] for f in files}
    show_progress = sys.stderr.isatty() and len(files) > 10
    for i, (fid, path, sha, lang, loc) in enumerate(files, 1):
        if show_progress and (i % 20 == 0 or i == len(files)):
            print(f"\rstub 생성 중... {i}/{len(files)}",
                  end="", file=sys.stderr, flush=True)
        out = wiki / stub_rel(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        body = [STUB_HEADER.format(src=path, sha=sha, lang=lang, loc=loc)]
        syms = cur.execute(
            "SELECT name, kind, signature, line_start FROM symbols "
            "WHERE file_id=? ORDER BY line_start", (fid,)).fetchall()
        if syms:
            body.append("## 심볼 (기계 추출)\n\n| 이름 | 종류 | 위치 | 시그니처 |\n|---|---|---|---|\n")
            for name, kind, sig, ls in syms:
                sig = (sig or "").replace("|", "\\|")
                body.append(f"| `{name}` | {kind} | {path}:{ls} | `{sig}` |\n")
            body.append("\n")
        deps = cur.execute(
            "SELECT dst_name, dst_file, kind FROM edges WHERE src_file=? "
            "AND kind IN ('includes','imports') ORDER BY dst_name",
            (path,)).fetchall()
        if deps:
            body.append("## 의존 (include/import — 기계 추출)\n\n")
            for dst_name, dst_file, kind in deps:
                if dst_file and dst_file in known:
                    body.append(f"- [[{stub_rel(dst_file)[:-3]}|{dst_name}]]\n")
                else:
                    body.append(f"- `{dst_name}` (외부 또는 미해석)\n")
            body.append("\n")
        bounds = cur.execute(
            "SELECT src_file, dst_name, dst_file, kind FROM edges "
            "WHERE (src_file=? OR dst_file=?) AND kind IN "
            "('binds','generated_from') ORDER BY kind, src_file",
            (path, path)).fetchall()
        if bounds:
            body.append("## 언어 경계 연결 (이름 기반 추정)\n\n")
            for src, dname, dfile, kind in bounds[:20]:
                if src == path and kind == "generated_from":
                    body.append(f"- [[{stub_rel(dfile)[:-3]}|{dfile}]] 에서 "
                                f"생성된 코드로 보임\n")
                elif src == path:
                    body.append(f"- `{dname}` ([[{stub_rel(dfile)[:-3]}|{dfile}]]"
                                f") 을 참조하는 것으로 보임\n")
                else:
                    body.append(f"- [[{stub_rel(src)[:-3]}|{src}]] 가 이 파일의 "
                                f"`{dname}` 를 참조하는 것으로 보임\n")
            body.append("\n")
        body.append("## 참고\n\n- 이 목록에 없는 관계(동적 호출, 함수 포인터, "
                    "DI 등)는 '없는 것'이 아니라 '기계가 확인 못 한 것'입니다.\n")
        out.write_text("".join(body), encoding="utf-8")
    if show_progress:
        print(file=sys.stderr)
    _write_index(root, cur)
    _write_module_map(root, cur)
    print(f"stub 생성 완료: wiki/files/ 아래 {len(files)}개 + INDEX.md "
          f"+ module-map.md")


def _module_depth(paths):
    """최상위 폴더가 3개 이하면(모든 코드가 src/ 밑 등) 한 단계 더 세분화."""
    tops = {p.split("/")[0] for p in paths if "/" in p}
    return 2 if len(tops) <= 3 else 1


def _module_of(path, depth):
    parts = path.split("/")
    if len(parts) > depth:
        return "/".join(parts[:depth])
    return "/".join(parts[:-1]) or "(root)"


def _write_module_map(root: Path, cur):
    """모듈(폴더) 사이 의존 그래프를 mermaid로 자동 생성 — 낡지 않는 조감도."""
    paths = [r[0] for r in cur.execute("SELECT path FROM files")]
    if not paths:
        return
    depth = _module_depth(paths)
    agg = {}
    for src, dst, kind in cur.execute(
            "SELECT src_file, dst_file, kind FROM edges "
            "WHERE dst_file IS NOT NULL AND kind IN "
            "('includes','imports','binds','generated_from')"):
        ms, md = _module_of(src, depth), _module_of(dst, depth)
        if ms == md:
            continue
        boundary = kind in ("binds", "generated_from")
        agg[(ms, md, boundary)] = agg.get((ms, md, boundary), 0) + 1

    def nid(m):
        return re.sub(r"\W", "_", m)

    lines = ["---\ntype: module-map\ngenerated: true\n---\n",
             "> ⚙️ 자동 생성 — 편집 금지. `cw.py stubs` 때마다 다시 그려집니다.\n\n",
             "# 모듈 관계 지도\n\n",
             "화살표 = 참조 방향(A→B: A가 B를 사용). 숫자 = 참조 건수. "
             "실선 = include/import, 점선 = 언어 경계(추정).\n\n",
             "```mermaid\nflowchart LR\n"]
    mods = sorted({m for (a, b, _) in agg for m in (a, b)})
    for m in mods:
        lines.append(f'    {nid(m)}["{m}"]\n')
    for (a, b, boundary), c in sorted(agg.items(), key=lambda x: -x[1])[:60]:
        arrow = "-.->" if boundary else "-->"
        lines.append(f'    {nid(a)} {arrow}|{c}| {nid(b)}\n')
    lines.append("```\n")
    if not agg:
        lines.append("\n(모듈 사이 참조가 아직 색인되지 않았습니다.)\n")
    (root / "wiki" / "module-map.md").write_text("".join(lines),
                                                 encoding="utf-8")


def _fan_in(cur):
    return cur.execute(
        "SELECT dst_file, COUNT(*) c FROM edges WHERE dst_file IS NOT NULL "
        "GROUP BY dst_file ORDER BY c DESC LIMIT 15").fetchall()


def _write_index(root: Path, cur):
    lines = ["---\ntype: file-index\ngenerated: true\n---\n",
             "> ⚙️ 자동 생성 — 편집 금지.\n\n# 파일 색인\n\n"]
    lines.append("## 많이 참조되는 파일 (fan-in 상위)\n\n"
                 "다른 파일이 많이 include/import 하는 파일 = 변경 파급이 큰 파일.\n\n")
    for path, c in _fan_in(cur):
        lines.append(f"- [[{stub_rel(path)[:-3]}|{path}]] ← {c}개 파일이 참조\n")
    lines.append("\n## 디렉터리별\n\n")
    dirs = {}
    for (path, lang, loc) in cur.execute(
            "SELECT path, lang, loc FROM files ORDER BY path"):
        d = str(Path(path).parent)
        dirs.setdefault(d, []).append((path, lang, loc))
    for d in sorted(dirs):
        total = sum(x[2] for x in dirs[d])
        lines.append(f"\n### `{d}/` — {len(dirs[d])}개 파일, {total}줄\n\n")
        for path, lang, loc in dirs[d]:
            lines.append(f"- [[{stub_rel(path)[:-3]}|{Path(path).name}]] ({lang}, {loc}줄)\n")
    (root / "wiki" / "files" / "INDEX.md").write_text("".join(lines),
                                                      encoding="utf-8")

# ---------------------------------------------------------------- map

MAP_ENTRY_LIMIT = 40


def cmd_map(root: Path):
    con = open_db(root)
    cur = con.cursor()
    out = ["# 프로젝트 지도 (AI에게 이 파일 또는 .codewiki/map.md를 주면 됨)\n",
           "\n## 디렉터리 = 모듈 후보\n\n"]
    all_files = cur.execute("SELECT path, lang, loc FROM files").fetchall()
    # 최상위 폴더가 너무 적으면(src 하나에 다 몰린 구조) 한 단계 더 파고든다.
    depth = 1
    tops = {p.split("/")[0] for (p, _, _) in all_files if "/" in p}
    if len(tops) <= 3:
        depth = 2
    dirs = {}
    for (path, lang, loc) in all_files:
        parts = path.split("/")
        key = "/".join(parts[:depth]) if len(parts) > depth else \
            ("/".join(parts[:-1]) or "(root)")
        d = dirs.setdefault(key, {"files": 0, "loc": 0, "langs": set()})
        d["files"] += 1
        d["loc"] += loc
        d["langs"].add(lang)
    for name in sorted(dirs, key=lambda k: -dirs[k]["loc"]):
        d = dirs[name]
        out.append(f"- `{name}/` : {d['files']}개 파일, {d['loc']}줄, "
                   f"언어={','.join(sorted(d['langs']))}\n")
    out.append("\n## 변경 파급이 큰 파일 (fan-in 상위)\n\n")
    for path, c in _fan_in(cur):
        out.append(f"- {path}  ← {c}개 파일이 참조\n")
    called = cur.execute(
        "SELECT dst_name, COUNT(DISTINCT src_file) c FROM edges "
        "WHERE kind='calls' GROUP BY dst_name "
        "HAVING c >= 2 ORDER BY c DESC LIMIT 10").fetchall()
    if called:
        out.append("\n## 여러 파일에서 호출되는 함수 상위 (이름 기반 추정)\n\n")
        for name, c in called:
            out.append(f"- `{name}()`  ← {c}개 파일에서 호출\n")
    out.append("\n## 엔트리포인트 후보\n\n")
    entries = []
    rows = cur.execute(
        "SELECT f.path, s.name, s.line_start FROM symbols s "
        "JOIN files f ON f.id=s.file_id WHERE s.name='main' "
        "OR s.kind='idl_interface' ORDER BY f.path").fetchall()
    for path, name, ls in rows:
        entries.append(f"- {path}:{ls}  `{name}`\n")
    py_mains = cur.execute("SELECT path FROM files WHERE lang='python'").fetchall()
    for (path,) in py_mains:
        try:
            if "__main__" in read_source(root / path)[1]:
                entries.append(f"- {path}  `if __name__ == '__main__'`\n")
        except Exception:
            pass
    out.extend(entries[:MAP_ENTRY_LIMIT])
    if len(entries) > MAP_ENTRY_LIMIT:
        out.append(f"- ... 외 {len(entries) - MAP_ENTRY_LIMIT}개 "
                   f"(전체는 facts.db에서 조회 가능)\n")
    text = "".join(out)
    map_p = root / ".codewiki" / "map.md"
    map_p.write_text(text, encoding="utf-8")
    print(text)
    print(f"(같은 내용이 {map_p.relative_to(root)} 에 저장됨 — "
          f"복사 대신 이 파일을 AI에게 줘도 됨)", file=sys.stderr)

# ---------------------------------------------------------------- lint

RE_LABEL = re.compile(r'\^\[(confirmed|inferred|unknown)(?::\s*([^\]]+))?\]')
RE_ANCHOR_LINE = re.compile(r'^(?P<path>[\w./+\-]+):(?P<l1>\d+)(?:-(?P<l2>\d+))?$')
RE_ANCHOR_SYM = re.compile(r'^sym:(?P<path>[\w./+\-]+)#(?P<name>[\w:~]+)$')


def parse_frontmatter(text: str):
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    fm = {}
    key = None
    for line in text[3:end].splitlines():
        if not line.strip():
            continue
        m = re.match(r'^(\w[\w_]*):\s*(.*)$', line)
        if m:
            key = m.group(1)
            val = m.group(2).strip()
            fm[key] = val if val else []
        elif line.strip().startswith("- ") and key is not None:
            if not isinstance(fm[key], list):
                fm[key] = [fm[key]] if fm[key] else []
            fm[key].append(line.strip()[2:].strip())
    return fm, text[end + 4:]


def check_anchor(root: Path, cur, anchor: str):
    """anchor 유효성 검사 → (ok, message)"""
    anchor = anchor.strip()
    m = RE_ANCHOR_SYM.match(anchor)
    if m:
        row = cur.execute(
            "SELECT s.id FROM symbols s JOIN files f ON f.id=s.file_id "
            "WHERE f.path=? AND s.name=?",
            (m.group("path"), m.group("name"))).fetchone()
        if not (root / m.group("path")).exists():
            return False, f"파일 없음: {m.group('path')}"
        if not row:
            return None, (f"심볼 미확인(DB에 없음 — 존재하지 않는다는 뜻은 아님): "
                          f"{anchor}")
        return True, ""
    m = RE_ANCHOR_LINE.match(anchor)
    if m:
        p = root / m.group("path")
        if not p.exists():
            return False, f"파일 없음: {m.group('path')}"
        loc_row = cur.execute("SELECT loc FROM files WHERE path=?",
                              (m.group("path"),)).fetchone()
        loc = loc_row[0] if loc_row else None
        l2 = int(m.group("l2") or m.group("l1"))
        if loc and l2 > loc:
            return False, f"라인 범위 초과({l2} > {loc}줄): {anchor}"
        return True, ""
    m = RE_ANCHOR_LINE.match(anchor.split("@")[0]) if "@" in anchor else None
    if m:
        return check_anchor(root, cur, anchor.split("@")[0])
    if anchor == "edges" or anchor.startswith("edge"):
        return True, ""  # facts DB 전체를 근거로 지목
    return False, f"anchor 문법 오류: {anchor}"


def wiki_docs(root: Path):
    wiki = root / "wiki"
    for p in sorted(wiki.rglob("*.md")):
        rel = p.relative_to(wiki)
        if rel.parts and rel.parts[0] == "files":
            continue  # 자동 생성 stub은 검사 제외
        if p.name.startswith("_"):
            continue  # _TEMPLATE 등 템플릿 제외
        if p.name in ("conventions.md", "glossary.md"):
            continue  # 규칙 문서의 예시/용어집은 라벨 검사 대상 아님
        yield p


def cmd_lint(root: Path):
    con = open_db(root)
    cur = con.cursor()
    errors, warns = [], []
    head = run_git(root, "rev-parse", "--short", "HEAD")
    diff_cache = {}

    for doc in wiki_docs(root):
        rel = str(doc.relative_to(root))
        text = doc.read_text(encoding="utf-8", errors="replace")
        fm, body = parse_frontmatter(text)
        dtype = fm.get("type", "")

        # 1) 라벨 검사
        for m in RE_LABEL.finditer(body):
            label, anchor = m.group(1), m.group(2)
            line_no = text.count("\n", 0, text.find(m.group(0))) + 1
            if label == "confirmed":
                if not anchor:
                    errors.append(f"{rel}:{line_no} confirmed 라벨에 근거 anchor가 "
                                  f"없음 → inferred로 바꾸거나 anchor를 다세요")
                    continue
                for a in anchor.split(","):
                    ok, msg = check_anchor(root, cur, a)
                    if ok is False:
                        errors.append(f"{rel}:{line_no} {msg}")
                    elif ok is None:
                        warns.append(f"{rel}:{line_no} {msg}")

        # 2) 프론트매터 필수 필드 (modules/flows/contracts/overview)
        if dtype in ("module", "flow", "contract", "overview", "note"):
            for field in ("validated_at", "depends"):
                if field not in fm or not fm[field]:
                    errors.append(f"{rel} 프론트매터에 {field}가 없음")

        # 3) 최신성(staleness) 검사 — git 기준
        va = fm.get("validated_at")
        deps = fm.get("depends", [])
        if isinstance(deps, str):
            deps = [deps]
        if head and va and isinstance(va, str) and va not in ("TBD", ""):
            if va not in diff_cache:
                out = run_git(root, "diff", "--name-only", f"{va}..HEAD")
                diff_cache[va] = out.splitlines() if out is not None else None
            changed = diff_cache[va]
            if changed is None:
                warns.append(f"{rel} validated_at={va} 커밋을 git에서 찾지 못함")
            else:
                hits = set()
                for ch in changed:
                    for dep in deps:
                        pat = dep[4:] + "*" if dep.startswith("sym:") else dep
                        if fnmatch.fnmatch(ch, pat) or ch == dep:
                            hits.add(ch)
                if hits:
                    errors.append(f"{rel} ⏰ STALE — validated_at={va} 이후 의존 "
                                  f"파일 변경됨: {', '.join(sorted(hits)[:5])}")

    for e in errors:
        print("에러:", e)
    for w in warns:
        print("경고:", w)
    print(f"\nlint 결과: 에러 {len(errors)}건, 경고 {len(warns)}건"
          f" (문서 {len(list(wiki_docs(root)))}개 검사)")
    sys.exit(1 if errors else 0)

# ---------------------------------------------------------------- update

def cmd_update(root: Path, mark_done=False):
    state_p = root / ".codewiki" / "state.json"
    state = json.loads(state_p.read_text(encoding="utf-8")) \
        if state_p.exists() else {}
    head = run_git(root, "rev-parse", "--short", "HEAD")
    if head is None:
        die("git 저장소가 아닙니다. update는 git 기반입니다. "
            "(git init 후 커밋하면 사용 가능)")

    if mark_done:
        state["wiki_synced_commit"] = head
        state_p.write_text(json.dumps(state, indent=2), encoding="utf-8")
        print(f"완료 표시: 위키가 커밋 {head} 기준으로 동기화되었다고 기록했습니다.")
        return

    base = state.get("wiki_synced_commit") or state.get("last_indexed_commit")
    if not base:
        die("기준 커밋이 없습니다. 먼저 `cw.py index` 후 "
            "`cw.py update --mark-done`으로 기준점을 만드세요.")
    if base == head:
        print(f"변경 없음 (기준 커밋 {base} == HEAD). 위키는 최신입니다.")
        return

    out = run_git(root, "diff", "--name-only", f"{base}..HEAD")
    if out is None:
        die(f"git diff 실패: {base}..HEAD")
    changed = [c for c in out.splitlines()
               if Path(c).suffix.lower() in LANG_BY_EXT]
    print(f"기준 {base} → HEAD {head}: 소스 변경 {len(changed)}건")
    for c in changed:
        print(f"  변경: {c}")

    if changed:
        cmd_index(root, only_files=changed)
        cmd_stubs(root)

    # stale 문서 판정
    stale = []
    for doc in wiki_docs(root):
        text = doc.read_text(encoding="utf-8", errors="replace")
        fm, _ = parse_frontmatter(text)
        deps = fm.get("depends", [])
        if isinstance(deps, str):
            deps = [deps]
        hits = set()
        for ch in changed:
            for dep in deps:
                pat = dep[4:] + "*" if dep.startswith("sym:") else dep
                if fnmatch.fnmatch(ch, pat) or ch == dep:
                    hits.add(ch)
        if hits:
            stale.append((str(doc.relative_to(root)), sorted(hits)))

    print()
    if not stale:
        print("stale 문서 없음. `cw.py update --mark-done`으로 기준점을 올리세요.")
        return
    print(f"⏰ 갱신 필요한 문서 {len(stale)}개 — AI에게 prompts/2-update.md와 "
          f"함께 아래 목록을 주세요:\n")
    for doc, hits in stale:
        print(f"- {doc}")
        for h in hits[:5]:
            print(f"    ← {h} 변경됨")
    print(f"\nAI 갱신이 끝나면: cw.py lint → cw.py update --mark-done")

# ---------------------------------------------------------------- init/status

def cmd_init(root: Path, show_next=True):
    root = root.resolve()
    if not root.exists():
        die(f"경로가 없습니다: {root}")
    tpl = TOOLKIT_DIR / "templates" / "wiki"
    wiki = root / "wiki"
    if not tpl.exists():
        die(f"템플릿 폴더가 없습니다: {tpl}")
    wiki.mkdir(exist_ok=True)
    copied = []
    for src in tpl.rglob("*"):
        rel = src.relative_to(tpl)
        dst = wiki / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        elif not dst.exists():  # 기존 문서는 절대 덮어쓰지 않음
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(str(rel))
    cw_dir = root / ".codewiki"
    cw_dir.mkdir(exist_ok=True)
    gi = cw_dir / ".gitignore"
    if not gi.exists():  # facts.db 등 도구 데이터가 커밋되지 않게
        gi.write_text("*\n", encoding="utf-8")
    cfg_p = cw_dir / "config.json"
    if not cfg_p.exists():
        cfg_p.write_text(json.dumps(
            {"exclude_dirs": DEFAULT_EXCLUDES,
             "_설명": "exclude_dirs: 색인에서 제외할 디렉터리 패턴"},
            indent=2, ensure_ascii=False), encoding="utf-8")
    # 이 저장소에서 작업하는 AI 에이전트가 위키를 자동으로 알게 하는 안내 파일
    agent_note = (
        "# 프로젝트 지식 위키 안내 (AI 에이전트용)\n\n"
        "이 저장소에는 codewiki가 관리하는 지식층이 있다. 작업 전에 활용하라:\n\n"
        "- 전체 구조: `wiki/00-overview.md` → 상세: `wiki/modules/`, `wiki/flows/`\n"
        "- 프로젝트 지도: `.codewiki/map.md` (모듈 후보·핵심 파일·엔트리포인트)\n"
        "- 특정 심볼/파일 작업 전: `python3 <codewiki>/cw.py context . <심볼이름>`\n"
        "- 위키 수정 시 규칙: `wiki/conventions.md` (라벨·anchor 필수)\n"
        "- 코드 변경 후: `python3 <codewiki>/cw.py update .` 로 낡은 문서 확인·갱신\n"
    )
    for fname in ("CLAUDE.md", "AGENTS.md"):
        p = root / fname
        if not p.exists():
            p.write_text(agent_note, encoding="utf-8")
            print(f"  {fname} 생성 (AI 에이전트용 위키 안내)")
        elif "codewiki" not in p.read_text(encoding="utf-8", errors="replace"):
            print(f"  참고: {fname}가 이미 있음 — 위키 안내 단락을 직접 추가하면 "
                  f"에이전트가 위키를 자동 활용함")
    print(f"초기화 완료: {root}")
    print(f"  wiki/ 에 템플릿 {len(copied)}개 설치 (Obsidian에서 wiki/를 vault로 여세요)")
    if show_next:
        print(f"  다음: cw.py index {root} && cw.py stubs {root}")


def cmd_status(root: Path):
    p = db_path(root)
    if not p.exists():
        print("색인 없음 — `cw.py index` 를 먼저 실행하세요.")
        return
    con = open_db(root)
    cur = con.cursor()
    nf = cur.execute("SELECT COUNT(*), COALESCE(SUM(loc),0) FROM files").fetchone()
    ns = cur.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    ne = cur.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    state_p = root / ".codewiki" / "state.json"
    state = json.loads(state_p.read_text(encoding="utf-8")) \
        if state_p.exists() else {}
    print(f"파일 {nf[0]}개 / {nf[1]}줄, 심볼 {ns}개, 관계 {ne}개")
    print(f"마지막 색인 커밋: {state.get('last_indexed_commit')}")
    print(f"위키 동기화 커밋: {state.get('wiki_synced_commit', '(미기록)')}")
    ndocs = len(list(wiki_docs(root))) if (root / "wiki").exists() else 0
    print(f"위키 문서(스텁 제외): {ndocs}개")

# ---------------------------------------------------------------- coverage

def cmd_coverage(root: Path):
    """위키가 코드의 어디를 다루고 어디가 비었는지 — '다음에 뭘 문서화할지'를
    감이 아니라 데이터로 정하게 해준다."""
    con = open_db(root)
    cur = con.cursor()
    docs = []
    if (root / "wiki").exists():
        for doc in wiki_docs(root):
            fm, _ = parse_frontmatter(
                doc.read_text(encoding="utf-8", errors="replace"))
            if fm.get("type") in ("module", "flow", "note", "contract"):
                deps = fm.get("depends", [])
                if isinstance(deps, str):
                    deps = [deps]
                if deps:
                    docs.append((str(doc.relative_to(root)), deps))
    files = [r[0] for r in cur.execute("SELECT path FROM files")]
    covered = set()
    for f in files:
        for _dname, deps in docs:
            if any(fnmatch.fnmatch(f, d) for d in deps):
                covered.add(f)
                break
    total = len(files)
    pct = 100 * len(covered) // max(total, 1)
    print(f"# 위키 커버리지\n")
    print(f"전체: {len(covered)}/{total} 파일 ({pct}%) — "
          f"depends를 가진 문서 {len(docs)}개 기준\n")

    depth = _module_depth(files)
    mods = {}
    for f in files:
        m = _module_of(f, depth)
        tot, cov = mods.get(m, (0, 0))
        mods[m] = (tot + 1, cov + (1 if f in covered else 0))
    print("## 모듈별\n")
    for m in sorted(mods, key=lambda k: mods[k][1] / mods[k][0]):
        tot, cov = mods[m]
        bar = "■" * (10 * cov // tot) + "□" * (10 - 10 * cov // tot)
        print(f"- {bar} {m}/  {cov}/{tot}")

    fan = cur.execute(
        "SELECT dst_file, COUNT(*) c FROM edges WHERE dst_file IS NOT NULL "
        "GROUP BY dst_file ORDER BY c DESC").fetchall()
    gaps = [(p, c) for p, c in fan if p not in covered][:15]
    if gaps:
        print("\n## 문서가 없는 중요 파일 (참조 많은 순 — 다음 문서화 후보)\n")
        for p, c in gaps:
            print(f"- {p}  ← {c}개 파일이 참조하는데 다루는 문서 없음")
    if not docs:
        print("\n(depends를 가진 위키 문서가 아직 없습니다 — "
              "모듈/흐름 문서를 먼저 생성하세요.)")


# ---------------------------------------------------------------- context

def cmd_context(root: Path, query: str):
    """심볼 이름(또는 파일 경로)에 대한 작업 컨텍스트를 조립해 출력.
    출력을 그대로 AI에게 주면 해당 심볼 작업에 필요한 지도가 된다."""
    con = open_db(root)
    cur = con.cursor()
    base = query.split("::")[-1]
    print(f"# 작업 컨텍스트: {query}\n")

    syms = cur.execute(
        "SELECT f.path, s.name, s.kind, s.signature, s.line_start, s.line_end "
        "FROM symbols s JOIN files f ON f.id=s.file_id "
        "WHERE s.name=? OR s.name=? OR s.name LIKE ? OR f.path=? "
        "ORDER BY f.path LIMIT 10",
        (query, base, f"%::{base}", query)).fetchall()
    def_files = sorted({r[0] for r in syms})
    if syms:
        print("## 정의 위치\n")
        for path, name, kind, sig, ls, le in syms:
            print(f"- `{name}` ({kind}) {path}:{ls}-{le}  `{sig}`")
    else:
        print(f"## 정의 위치\n\n- facts.db에서 찾지 못함 "
              f"(존재하지 않는다는 뜻은 아님 — 매크로/템플릿 가능성)")

    callers = cur.execute(
        "SELECT DISTINCT src_file, src_symbol FROM edges "
        "WHERE kind='calls' AND dst_name=? LIMIT 15", (base,)).fetchall()
    print("\n## 호출자 (이름 기반 추정 — 동명 함수 가능성 있음)\n")
    if callers:
        for src_file, src_sym in callers:
            print(f"- {src_file} 의 `{src_sym}()`")
    else:
        print("- 색인에서 찾지 못함 (동적 호출/매크로는 안 잡힘)")

    callees = cur.execute(
        "SELECT DISTINCT dst_name FROM edges WHERE kind='calls' "
        "AND (src_symbol=? OR src_symbol=?) LIMIT 20",
        (query, base)).fetchall()
    if callees:
        print("\n## 호출 대상 (본문에서 발견된 이름들)\n")
        resolved = []
        for (dn,) in callees:
            hits = cur.execute(
                "SELECT DISTINCT f.path FROM symbols s "
                "JOIN files f ON f.id=s.file_id WHERE s.name=? OR s.name "
                "LIKE ? LIMIT 2", (dn, f"%::{dn}")).fetchall()
            loc = hits[0][0] if len(hits) == 1 else \
                ("여러 곳" if len(hits) > 1 else "외부/미해석")
            resolved.append(f"- `{dn}()` → {loc}")
        print("\n".join(sorted(resolved)))

    print("\n## 관련 위키 문서\n")
    found_doc = False
    if (root / "wiki").exists():
        for doc in wiki_docs(root):
            text = doc.read_text(encoding="utf-8", errors="replace")
            fm, body = parse_frontmatter(text)
            deps = fm.get("depends", [])
            if isinstance(deps, str):
                deps = [deps]
            dep_hit = any(fnmatch.fnmatch(f, dep) for f in def_files
                          for dep in deps)
            mention = base in body
            if dep_hit or mention:
                why = "의존 범위" if dep_hit else "본문 언급"
                print(f"- {doc.relative_to(root)} ({why})")
                found_doc = True
    if not found_doc:
        print("- 없음 — 이 심볼을 다루는 위키 문서가 아직 없음")
    for f in def_files:
        print(f"\n파일 stub: wiki/{stub_rel(f)}")


# ---------------------------------------------------------------- doctor

SELFTEST_CASES = [
    ("C++ 같은 줄 중괄호", "cpp",
     "void Server::start(int port) {\n}\n", ["Server::start"]),
    ("C 다음 줄 중괄호(Allman)", "c",
     "int foo(int x)\n{\n  return x;\n}\n", ["foo"]),
    ("C++ 여러 줄 시그니처", "cpp",
     "static int helper(int a,\n    int b)\n{\n  return a;\n}\n", ["helper"]),
    ("C++ 생성자 초기화 리스트", "cpp",
     "class W {\npublic:\n  W(int i) : id_(i) {}\n};\n", ["W"]),
    ("C++ 주석 속 가짜 함수 무시", "cpp",
     "// void fake() {\nint real() {\n}\n", ["real"]),
    ("Python 함수/클래스", "python",
     "class H:\n    def run(self):\n        pass\n\ndef util(x):\n    return x\n",
     ["run", "util"]),
    ("IDL interface/메서드", "idl",
     "module M {\n  interface Svc {\n    void ping();\n  };\n};\n", ["ping"]),
]


def cmd_doctor(root: Path):
    import platform
    print(f"## 환경\n- Python {sys.version.split()[0]} / {platform.system()} "
          f"{platform.release()}")
    print(f"- universal-ctags: {'있음 (C/C++ 정밀 모드)' if has_universal_ctags() else '없음 → 내장 스캐너 사용 (정상)'}")
    head = run_git(root, "rev-parse", "--short", "HEAD")
    print(f"- git: {'커밋 ' + head if head else '저장소 아님 → update/최신성 검사 사용 불가'}")

    print("\n## 파서 자가 테스트")
    fails = 0
    for desc, lang, code, want in SELFTEST_CASES:
        syms, _ = parse_file(Path("selftest"), lang, code)
        got = sorted(s[0] for s in syms
                     if s[1] in ("function", "idl_method"))
        ok = got == sorted(want)
        if not ok:
            fails += 1
        print(f"- {'O' if ok else 'X'} {desc}"
              + ("" if ok else f"  (기대 {want}, 실측 {got})"))

    cfg = load_config(root)
    files = list(iter_source_files(root, cfg))
    print(f"\n## 색인 대상 훑기\n- 대상 파일: {len(files)}개")
    from collections import Counter
    langs = Counter(LANG_BY_EXT[p.suffix.lower()] for p in files)
    print(f"- 언어별: {dict(langs)}")
    dirs = Counter("/".join(p.relative_to(root).parts[:2][:-1]) or
                   p.relative_to(root).parts[0] for p in files)
    print("- 파일 많은 폴더 상위 8 (서드파티/생성 코드면 exclude_dirs에 추가 권장):")
    for d, c in dirs.most_common(8):
        print(f"    {d}/ : {c}개")
    non_utf8 = 0
    for p in files[:300]:
        try:
            _, _, enc = read_source(p)
            if enc != "utf-8":
                non_utf8 += 1
        except Exception:
            pass
    if non_utf8:
        print(f"- 인코딩: 표본 300개 중 {non_utf8}개가 UTF-8 아님 "
              f"→ CP949 폴백으로 자동 처리됨 (한글 주석 보존)")
    else:
        print("- 인코딩: 표본에서 문제 없음 (UTF-8)")

    print(f"\n{'문제 없음 — index를 진행하세요.' if fails == 0 else '파서 테스트 실패 — 이 출력과 함께 문의하세요.'}")
    sys.exit(1 if fails else 0)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["setup", "init", "index", "stubs",
                                        "map", "lint", "update", "status",
                                        "doctor", "context", "coverage"])
    ap.add_argument("path", nargs="?", default=".",
                    help="대상 프로젝트 루트 (기본: 현재 디렉터리)")
    ap.add_argument("query", nargs="?",
                    help="(context 전용) 심볼 이름 또는 파일 경로")
    ap.add_argument("--mark-done", action="store_true",
                    help="(update 전용) 위키 갱신 완료를 현재 커밋으로 기록")
    args = ap.parse_intermixed_args()
    root = Path(args.path).resolve()
    if args.command == "setup":
        cmd_init(root, show_next=False)
        cmd_index(root)
        cmd_stubs(root)
        print("\n" + "=" * 60)
        cmd_map(root)
        print("=" * 60)
        print("\n준비 끝. 다음 한 가지만 하면 됩니다:")
        print("  .codewiki/map.md 파일 + prompts/1-generate.md 를 AI에게 주고")
        print("  위키 생성을 시키세요. (Claude Code면 '위키 만들어줘' 한마디면 됨)")
        print("  이후 코드가 바뀌면: cw.py update")
    elif args.command == "init":
        cmd_init(root)
    elif args.command == "index":
        cmd_index(root)
    elif args.command == "stubs":
        cmd_stubs(root)
    elif args.command == "map":
        cmd_map(root)
    elif args.command == "lint":
        cmd_lint(root)
    elif args.command == "update":
        cmd_update(root, mark_done=args.mark_done)
    elif args.command == "status":
        cmd_status(root)
    elif args.command == "doctor":
        cmd_doctor(root)
    elif args.command == "coverage":
        cmd_coverage(root)
    elif args.command == "context":
        if args.query is None:
            # `cw.py context 심볼` 형태: path 자리에 온 것이 질의어
            if args.path != "." and not Path(args.path).is_dir():
                cmd_context(Path(".").resolve(), args.path)
            else:
                die("사용법: cw.py context [프로젝트경로] <심볼이름|파일경로>")
        else:
            cmd_context(root, args.query)


if __name__ == "__main__":
    main()
