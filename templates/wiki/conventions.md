---
type: conventions
---

# 위키 작성 규칙 (사람과 AI 모두 이 규칙을 따른다)

## 대원칙

1. **코드를 복사하지 않는다.** 항상 anchor로 참조한다. (10줄 이내 발췌만 예외적 허용)
2. **기계가 뽑을 수 있는 것은 쓰지 않는다.** 심볼 목록·include 관계는 `files/` stub이 자동 담당한다. 프로즈는 "코드가 말하지 않는 것"(역할, 의도, 흐름)만 쓴다.
3. **모든 단정문에는 라벨을 단다.** 라벨 없는 단정문은 lint에서 지적된다.
4. **DB에 없다 ≠ 존재하지 않는다.** 기계 색인은 동적 호출·함수 포인터·DI·매크로·템플릿을 놓친다. "관계가 없다"고 쓰려면 근거가 필요하다.

## anchor 문법

| 형태 | 예시 | 의미 |
|---|---|---|
| 라인 | `src/net/server.cpp:41` | 파일의 특정 줄 |
| 라인 범위 | `src/net/server.cpp:41-60` | 줄 범위 |
| 심볼 | `sym:src/net/server.cpp#Server::start` | 특정 심볼 |
| facts | `edges` | facts.db의 관계 데이터 전체를 근거로 지목 |

## 신뢰도 라벨 (Obsidian 인라인 각주 문법 사용)

- `^[confirmed: <anchor>]` — 코드/테스트/facts.db로 직접 확인함. **anchor 필수.**
- `^[inferred]` — 주변 정황으로 추론함. 틀릴 수 있음.
- `^[unknown]` — 저장소만으로는 확인 불가.

예시:

> Server::start는 accept 루프를 시작한다. ^[confirmed: sym:src/net/server.cpp#Server::start]
> 큐 크기 제한은 백프레셔 목적으로 보인다. ^[inferred]
> 운영 환경에서 TLS 재협상이 쓰이는지는 확인 불가. ^[unknown]

## 문서 소유권 (한 파일 안에서)

- `<!-- auto:begin --> ... <!-- auto:end -->` — 기계 소유. 도구가 통째로 덮어씀.
- `<!-- human -->` 표시가 있는 섹션 — 사람 소유. AI는 수정하지 않는다.
- 나머지 본문 — AI 소유. 증분 갱신 대상.
- `wiki/files/` 전체 — 기계 소유. 아무도 편집하지 않는다.

## 프론트매터 필수 필드 (module / flow / contract / overview)

```yaml
---
type: module            # module | flow | contract | overview | decision
id: net                 # 짧은 고유 이름
validated_at: 9f3ab12   # 이 문서를 검증한 시점의 git 커밋(짧은 해시)
depends:                # 아래 항목이 변경되면 이 문서는 stale
  - src/net/*
  - src/core/event_loop.cpp
---
```

`depends`는 glob 패턴. 넓게 잡으면 갱신 알림이 잦고, 좁게 잡으면 낡은 걸 놓친다.
모듈 문서는 모듈 디렉터리 전체 + 직접 의존하는 핵심 파일 정도가 적당하다.

## 갱신 워크플로우

1. 코드 변경(커밋) 후 `cw.py update <프로젝트>` 실행
2. stale 문서 목록이 나오면 AI에게 `prompts/2-update.md` + 목록을 준다
3. AI가 문서를 고치고 `validated_at`을 새 커밋으로 올린다
4. `cw.py lint`로 검사 → 통과하면 `cw.py update --mark-done`
