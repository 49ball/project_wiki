---
name: codewiki
description: Use when the user asks to create, update, verify, or explore a project knowledge wiki generated from source code — triggers include 코드 위키, 프로젝트 위키 생성/갱신, 온보딩 문서 만들기, "위키가 낡았다", stale wiki, cw.py, codewiki, or code changes that need wiki sync
---

# codewiki — 코드 프로젝트 지식 위키 도구

## 개요

`cw.py`는 소스 저장소(C/C++/Python/IDL)에서 사실(심볼·include·import)을 추출해
Obsidian 호환 위키를 만들고 증분 갱신하는 로컬 CLI다.
**AI가 쓰는 프로즈와 기계가 뽑는 사실을 분리**하고, 모든 주장에 근거 anchor를
강제하는 것이 핵심이다.

**툴킷 위치 찾기**: 기본 `~/codewiki/cw.py`. 없으면 사용자에게 위치를 물어라.
이하 `CW="python3 ~/codewiki/cw.py"`로 표기.

## 명령 요약

| 명령 | 하는 일 |
|---|---|
| `$CW doctor <proj>` | 환경 점검 + 파서 자가 테스트 (새 환경 첫 실행 시) |
| `$CW setup <proj>` | init+index+stubs+map 한 번에 (초기 준비는 이것 하나) |
| `$CW init <proj>` | wiki/ 템플릿 + .codewiki/ 설치 (기존 문서 보존) |
| `$CW index <proj>` | 심볼·관계 추출 → facts.db |
| `$CW stubs <proj>` | wiki/files/ 자동 stub 재생성 |
| `$CW map <proj>` | 모듈 후보·fan-in·엔트리포인트 지도 (`.codewiki/map.md`에도 저장) |
| `$CW lint <proj>` | anchor 실존·confirmed 근거·최신성 검사 (에러 시 exit 1) |
| `$CW update <proj>` | git diff로 stale 문서 탐지 + 부분 재색인 |
| `$CW update <proj> --mark-done` | 위키 동기화 완료를 현재 커밋으로 기록 |
| `$CW context <proj> <심볼>` | 심볼의 정의·호출자·호출 대상·관련 문서 조립 — 코드 수정 작업 전에 실행하면 좋음 |

## 워크플로우 (요청별)

작업 전에 반드시 `<proj>/wiki/conventions.md`를 읽고 그 규칙을 따르라.

- **처음 위키 생성** ("위키 만들어줘", 온보딩 문서 요청):
  `$CW setup <proj>` 실행 후, `~/codewiki/prompts/1-generate.md`를
  읽고 그 절차대로 모듈/흐름/개요 문서를 작성하라.
- **코드 변경 후 갱신** ("위키 갱신", "코드 바꿨어"):
  `$CW update <proj>` 실행 → 출력된 stale 문서 목록에 대해
  `~/codewiki/prompts/2-update.md` 절차를 따르라.
  끝나면 `lint` 통과 확인 후 `update --mark-done`.
- **위키 감사/검증 요청**: `~/codewiki/prompts/3-verify.md` 절차를 따르라.

## 절대 규칙 (프롬프트 파일과 동일)

1. 코드를 위키로 복사하지 않는다 — anchor(`경로:라인`, `sym:경로#심볼`)로 참조.
2. 단정문에는 라벨: `^[confirmed: <anchor>]` / `^[inferred]` / `^[unknown]`.
3. facts.db에 없음 = "확인 못 함"이지 "존재하지 않음"이 아니다.
4. `wiki/files/`와 `<!-- human -->` 섹션은 편집 금지.
5. 마무리는 항상 `lint` 에러 0 → `update --mark-done` 순서.

## 흔한 실수

- lint를 안 돌리고 완료 선언 → 반드시 exit 0 확인
- stale 문서를 처음부터 재작성 → 낡은 문장만 최소 수정
- `validated_at` 갱신 누락 → 고친 문서는 현재 커밋 해시로 올릴 것
- git 저장소가 아닌 프로젝트에 update 시도 → init/index/stubs/lint만 가능
