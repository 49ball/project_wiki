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
CREATE INDEX IF NOT EXISTS idx_sym_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_edge_src ON edges(src_file);
CREATE INDEX IF NOT EXISTS idx_edge_dst ON edges(dst_file);
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
# 함수 정의 휴리스틱: 반환타입류 + 이름( ... ) {
# 백트래킹 폭발 방지: 무제한 \s(개행 포함) 스캔을 금지하고,
# 개행은 (a) 인자 목록 안(길이 제한), (b) 여는 중괄호 직전 1회만 허용.
RE_FUNC = re.compile(
    r'^(?![ \t]*(?:if|for|while|switch|return|else|do|case|catch|new|delete)\b)'
    r'[ \t]*(?:[\w:<>\*&~, \t]+?[ \t\*&])?'
    r'((?:\w+::)*[~\w]+)[ \t]*\(([^;{}]{0,400}?)\)[ \t]*'
    r'(?:const[ \t]*)?(?:noexcept[ \t]*)?(?:override[ \t]*)?'
    r'(?:\r?\n[ \t]*)?\{',
    re.M)
KEYWORD_BLACKLIST = {"if", "for", "while", "switch", "sizeof", "catch",
                     "return", "defined", "assert"}


def parse_c_cpp(path: Path, text: str):
    """정규식 휴리스틱 — 놓치는 것이 있을 수 있고(멀티라인 시그니처, 매크로),
    provenance='regex'로 그 사실을 기록한다. universal-ctags가 있으면 대체됨."""
    symbols, edges = [], []
    for m in RE_INCLUDE.finditer(text):
        edges.append((None, m.group(1), None, "includes", "regex", "confirmed"))
    for m in RE_CLASS.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        symbols.append((m.group(2), m.group(1), m.group(0).strip()[:120],
                        line, line, "regex"))
    for m in RE_FUNC.finditer(text):
        name = m.group(1)
        if name.split("::")[-1] in KEYWORD_BLACKLIST:
            continue
        line = text.count("\n", 0, m.start(1)) + 1
        sig = f"{name}({m.group(2).strip()[:80]})"
        symbols.append((name, "function", sig, line, line, "regex"))
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


def parse_file(path: Path, lang: str, text: str):
    if lang == "python":
        return parse_python(path, text)
    if lang in ("c", "cpp"):
        if has_universal_ctags():
            r = parse_c_cpp_ctags(path, text)
            if r is not None:
                return r
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
        rel = str(p.relative_to(root))
        files_by_name.setdefault(p.name, []).append(rel)

    targets = all_paths
    if only_files is not None:
        wanted = set(only_files)
        targets = [p for p in all_paths if str(p.relative_to(root)) in wanted]
        # 삭제된 파일 정리
        for f in only_files:
            if not (root / f).exists():
                cur.execute("DELETE FROM symbols WHERE file_id IN "
                            "(SELECT id FROM files WHERE path=?)", (f,))
                cur.execute("DELETE FROM edges WHERE src_file=?", (f,))
                cur.execute("DELETE FROM files WHERE path=?", (f,))
    else:
        cur.execute("DELETE FROM files")
        cur.execute("DELETE FROM symbols")
        cur.execute("DELETE FROM edges")

    n_sym = n_edge = 0
    show_progress = sys.stderr.isatty() and len(targets) > 10
    for i, p in enumerate(targets, 1):
        rel = str(p.relative_to(root))
        if show_progress and (i % 10 == 0 or i == len(targets)):
            print(f"\r색인 중... {i}/{len(targets)}  {rel[:60]:<60}",
                  end="", file=sys.stderr, flush=True)
        try:
            raw = p.read_bytes()
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            continue
        lang = LANG_BY_EXT[p.suffix.lower()]
        sha = sha_of(raw)
        loc = text.count("\n") + 1
        cur.execute("DELETE FROM symbols WHERE file_id IN "
                    "(SELECT id FROM files WHERE path=?)", (rel,))
        cur.execute("DELETE FROM edges WHERE src_file=?", (rel,))
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
    if show_progress:
        print(file=sys.stderr)
    con.commit()

    head = run_git(root, "rev-parse", "--short", "HEAD")
    state_p = root / ".codewiki" / "state.json"
    state = {}
    if state_p.exists():
        state = json.loads(state_p.read_text(encoding="utf-8"))
    state["last_indexed_commit"] = head
    state_p.write_text(json.dumps(state, indent=2), encoding="utf-8")

    scope = f"{len(targets)}개 파일(부분)" if only_files else f"{len(targets)}개 파일(전체)"
    print(f"색인 완료: {scope}, 심볼 {n_sym}개, 관계 {n_edge}개"
          f"{' [ctags]' if has_universal_ctags() else ' [regex — universal-ctags 설치 시 C/C++ 정밀도 향상]'}")
    if head:
        print(f"기준 커밋: {head}")

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
        body.append("## 참고\n\n- 이 목록에 없는 관계(동적 호출, 함수 포인터, "
                    "DI 등)는 '없는 것'이 아니라 '기계가 확인 못 한 것'입니다.\n")
        out.write_text("".join(body), encoding="utf-8")
    if show_progress:
        print(file=sys.stderr)
    _write_index(root, cur)
    print(f"stub 생성 완료: wiki/files/ 아래 {len(files)}개 + INDEX.md")


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

def cmd_map(root: Path):
    con = open_db(root)
    cur = con.cursor()
    print("# 프로젝트 지도 (AI에게 그대로 붙여넣어도 됨)\n")
    print("## 디렉터리 = 모듈 후보\n")
    dirs = {}
    for (path, lang, loc) in cur.execute("SELECT path, lang, loc FROM files"):
        top = path.split("/")[0] if "/" in path else "(root)"
        d = dirs.setdefault(top, {"files": 0, "loc": 0, "langs": set()})
        d["files"] += 1
        d["loc"] += loc
        d["langs"].add(lang)
    for name in sorted(dirs, key=lambda k: -dirs[k]["loc"]):
        d = dirs[name]
        print(f"- `{name}/` : {d['files']}개 파일, {d['loc']}줄, "
              f"언어={','.join(sorted(d['langs']))}")
    print("\n## 변경 파급이 큰 파일 (fan-in 상위)\n")
    for path, c in _fan_in(cur):
        print(f"- {path}  ← {c}개 파일이 참조")
    print("\n## 엔트리포인트 후보\n")
    rows = cur.execute(
        "SELECT f.path, s.name, s.line_start FROM symbols s "
        "JOIN files f ON f.id=s.file_id WHERE s.name='main' "
        "OR s.kind='idl_interface' ORDER BY f.path").fetchall()
    for path, name, ls in rows:
        print(f"- {path}:{ls}  `{name}`")
    py_mains = cur.execute("SELECT path FROM files WHERE lang='python'").fetchall()
    for (path,) in py_mains:
        try:
            if "__main__" in (root / path).read_text(encoding="utf-8",
                                                     errors="replace"):
                print(f"- {path}  `if __name__ == '__main__'`")
        except Exception:
            pass

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
        if dtype in ("module", "flow", "contract", "overview"):
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
    cfg_p = cw_dir / "config.json"
    if not cfg_p.exists():
        cfg_p.write_text(json.dumps(
            {"exclude_dirs": DEFAULT_EXCLUDES,
             "_설명": "exclude_dirs: 색인에서 제외할 디렉터리 패턴"},
            indent=2, ensure_ascii=False), encoding="utf-8")
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

# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["setup", "init", "index", "stubs",
                                        "map", "lint", "update", "status"])
    ap.add_argument("path", nargs="?", default=".",
                    help="대상 프로젝트 루트 (기본: 현재 디렉터리)")
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
        print("  위의 지도 출력 + prompts/1-generate.md 를 AI에게 주고")
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


if __name__ == "__main__":
    main()
