# codewiki — 코드 프로젝트용 AI Wiki 툴킷

소스코드 저장소에서 **AI와 사람이 함께 쓰는 지식 위키**(Obsidian 호환 Markdown)를
만들고, 코드가 바뀌면 낡은 문서만 골라 증분 갱신하는 툴킷.

- 지원 언어: C, C++, Python, IDL (혼합 프로젝트 OK)
- 의존성: **Python 3.8+ 표준 라이브러리만.** 이 폴더 하나만 복사하면 어디서든 동작.
  (universal-ctags가 설치되어 있으면 C/C++ 심볼 추출이 자동으로 정밀해짐 — 선택사항)
- 프로젝트마다 독립적인 위키를 만들 수 있음 (`init`을 프로젝트마다 실행)

## 설계 핵심 (왜 이렇게 만들었나)

1. **코드는 복사하지 않는다.** 원본(raw source)은 git 그 자체다. 위키는
   `경로:라인`, `sym:경로#심볼` 형태의 anchor로 참조만 한다.
2. **기계가 뽑는 사실과 AI의 해석을 분리한다.**
   - 사실(심볼, include/import) → `.codewiki/facts.db` + `wiki/files/` 자동 stub.
     갱신하지 않고 매번 재생성 → 절대 낡지 않는다.
   - 해석(역할, 흐름, 설계 의도) → `wiki/modules|flows|decisions/`.
     AI가 쓰고, 증분 갱신하고, lint로 검증한다.
3. **모든 주장에 신뢰도 라벨.** `^[confirmed: anchor]`는 lint가 근거를 기계 검증.
   근거 없는 확신은 에러. 자세한 규칙은 위키에 설치되는 `conventions.md` 참고.
4. **비대칭 규칙.** 기계 색인은 동적 호출·함수 포인터·매크로·템플릿·IDL 생성
   코드를 놓친다. 그래서 "DB에 있으면 사실, DB에 없으면 모름"이지
   "없으면 거짓"이 아니다. 이 규칙이 도구/AI 전체에 강제되어 있다.

## 빠른 시작 (외울 것은 setup과 update 두 개뿐)

```bash
# 편하게 쓰려면 alias 등록 (~/.zshrc 또는 ~/.bashrc에 추가)
alias cw='python3 ~/codewiki/cw.py'

# 1. 프로젝트 루트(.git 있는 곳)에서 — 프로젝트마다 1회
cd /path/to/project
cw setup          # init+index+stubs+map을 한 번에 실행

# 2. setup이 출력한 지도 + prompts/1-generate.md 를 AI에게 주고 위키 생성 시키기
#    (Claude Code + codewiki 스킬이 있으면 "위키 만들어줘" 한마디면 됨)

# 3. Obsidian에서 /path/to/project/wiki 를 vault로 열기
```

경로를 생략하면 현재 디렉터리가 대상. `cw setup /path/to/project`처럼
경로를 줘도 된다. 개별 단계(init/index/stubs/map)는 필요할 때만 따로 실행.

## 코드가 바뀐 뒤 (증분 갱신)

```bash
cw update         # 프로젝트 루트에서 — 변경 재색인 + 낡은 문서 목록 출력
# → 그 목록과 prompts/2-update.md 를 AI에게 준다
#   (Claude Code면 "위키 갱신해줘" 한마디 — lint와 mark-done까지 알아서 함)
# → 수동으로 할 경우 AI가 고친 뒤:
cw lint
cw update --mark-done
```

주기적으로(예: 분기마다) `prompts/3-verify.md`로 위키를 작성하지 않은
별도 AI에게 표본 감사를 시키는 것을 권장.

## 명령 요약

| 명령 | 하는 일 | LLM 비용 |
|---|---|---|
| `setup` | **init+index+stubs+map 한 번에** (처음에 이것만 쓰면 됨) | 0 |
| `init` | wiki/ 템플릿 + .codewiki/ 설치 (기존 문서는 덮어쓰지 않음) | 0 |
| `index` | 심볼·include·import 추출 → facts.db | 0 |
| `stubs` | wiki/files/ 자동 문서 + INDEX.md 재생성 | 0 |
| `map` | 모듈 후보·fan-in·엔트리포인트 요약 — `.codewiki/map.md`로도 저장 | 0 |
| `lint` | anchor 실존, confirmed 근거, 문서 최신성 검사 | 0 |
| `update` | git diff → 낡은 문서 탐지 + 부분 재색인 | 0 |
| `status` | 색인/동기화 상태 | 0 |

LLM 비용이 드는 것은 AI에게 시키는 생성/갱신/감사뿐이고, 그때도 프롬프트가
"stub과 map을 먼저 읽고, 필요한 파일 본문만 읽어라"로 토큰을 아끼게 되어 있다.

## 위키 구조 (init 후 프로젝트에 생기는 것)

```
project/
├── .codewiki/          # facts.db, state.json, config.json (Obsidian 밖)
└── wiki/               # ← Obsidian vault로 열 것
    ├── 00-overview.md  # 전체 그림 (AI 생성, bottom-up으로 마지막에)
    ├── conventions.md  # 라벨·anchor·소유권 규칙 (사람·AI 공용 계약서)
    ├── glossary.md     # 도메인 용어 (사람 주도)
    ├── modules/        # 모듈별 책임·경계·의존 (AI 생성 + lint 검증)
    ├── flows/          # 핵심 실행 흐름 3~7개, 언어 경계 기록 (AI 생성)
    ├── decisions/      # ADR — 코드에 없는 "왜" (사람 주도, 가장 가치 높음)
    └── files/          # 파일별 자동 stub — 편집 금지, 낡지 않음
```

## 한계 (정직하게)

- **C/C++ 심볼 추출은 내장 스캐너 휴리스틱**이다. 주석·문자열·전처리 제거 후
  중괄호/괄호 짝을 맞추는 방식이라 멀티라인 시그니처, 생성자 초기화 리스트,
  함수 시작-끝 라인까지 잡지만, 컴파일하지 않으므로 매크로 전개·템플릿
  인스턴스 해석·operator 오버로드 일부는 못 한다. universal-ctags가 있으면
  자동으로 그쪽을 쓴다(설치가 안 되는 환경이면 스캐너만으로 충분).
- **호출 그래프는 근사치**다. 함수 포인터, 가상 함수, DI, IDL 스텁 경유
  호출은 기계가 못 잇는다. 그래서 flow 문서(AI가 읽고 쓴 것)가 존재한다.
- **시그니처가 같고 동작만 바뀐 변경**은 depends 기반 stale 탐지가
  잡아주지만, depends 범위 밖 문서가 낡는 것은 원리적으로 못 잡는다.
  → 주기적 표본 감사(prompts/3-verify.md)로 보완.
- `update`는 git 저장소에서만 동작한다.

## 회사에서 쓰려면

1. 이 `codewiki/` 폴더를 통째로 가져간다 (파일 5개 + 템플릿).
2. Python 3.8+만 있으면 된다. 인터넷/외부 패키지 불필요.
3. AI 도구는 아무거나: 프롬프트 3종은 특정 도구에 묶여 있지 않다.
   에이전트가 셸을 실행할 수 있으면 전 과정 자동, 아니면 map/update 출력을
   사람이 복사해서 채팅에 붙여넣어도 된다.
# project_wiki
