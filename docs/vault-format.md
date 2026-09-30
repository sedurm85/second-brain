# 볼트 형식

세컨드브레인 볼트는 평범한 마크다운 폴더예요. 기본 위치는 `~/brain/`이고, `~/.config/second-brain/config.json`의 `vault` 값으로 바꿀 수 있어요(홈 디렉토리 아래만 허용).

```
~/brain/
  BRAIN.md                    자동 생성 인덱스 (직접 수정 금지, brain.py index가 덮어씀)
  inbox.md                    빠른 캡처 임시함
  notes/YYYY/MM/<slug>.md     note | idea | source | meeting
  decisions/<NNN>-<slug>.md   decision (ADR, 3자리 순번)
  projects/<slug>.md          프로젝트 허브
  people/<slug>.md            사람
```

## 파일 이름(slug) 규칙

- 제목에서 만들어요. 한글은 그대로, 영문은 소문자, 공백·`_` → `-`, 파일명 금지문자와 구두점(`\ / : * ? " < > | # ^ [ ] { } ( ) ! @ $ % & + = , ; ' ~ .`, 백틱 포함)은 제거, 최대 60자.
- 파일 이름(확장자 뺀 stem)은 **볼트 전체에서 유일**해요. 겹치면 `-2`, `-3`을 붙여요. 그래서 `[[파일명]]` 위키링크가 항상 한 파일을 가리켜요.
- 결정은 `decisions/` 안의 가장 큰 번호 + 1로 `001-`, `002-`… 순번이 붙어요.

## 프론트매터 필드

YAML의 단순 부분집합만 써요: `key: value`, `key: [a, b]`, 여러 줄 `- item` 리스트. 콜론·`#`·따옴표가 들어간 값은 큰따옴표로 감싸서 저장해요.

| 필드 | 필수 | 형식 | 설명 |
|---|---|---|---|
| `title` | 필수 | 문자열 | 제목. 검색 가중치 3배 |
| `type` | 필수 | `note` `idea` `source` `meeting` `decision` `project` `person` | 타입에 따라 저장 폴더가 정해져요 |
| `created` | 필수 | `YYYY-MM-DD` | 생성일. 최근성 가중(90일 반감)과 `--since` 필터 기준 |
| `tags` | 필수 | 리스트(빈 리스트 가능) | 검색 가중치 2배, 리뷰 링크 제안 기준 |
| `project` | 선택 | 문자열 | 프로젝트 이름. 같은 slug의 `projects/` 허브에 자동 수집 |
| `source` | 선택 | URL | 원문 링크(source 노트) |
| `people` | 선택 | `[[사람]]` 리스트 | 관련 인물 |
| `links` | 선택 | `[[노트]]` 리스트 | 명시적 연결(`brain.py link`가 양방향으로 추가) |
| `revisit` | 선택 | `YYYY-MM-DD` | 되돌아볼 날짜. 지나면 BRAIN.md·리뷰에 "도래" 표시 |
| `status` | 결정 필수 | `open` `decided` `superseded` | 결정 상태. 새 결정 기본값 `open` |
| `supersedes` | 선택 | `[[결정]]` 리스트 | 이 결정이 대체한 옛 결정 |
| `superseded_by` | 자동 | `[[결정]]` | `decide --supersede`가 옛 결정에 기록 |
| `imported_from` | 자동 | 파일명 | `import`로 가져온 원본 파일 이름 |

본문은 자유 마크다운이고, 다른 노트는 `[[파일명]]`으로 연결해요. 프론트매터 `links`/`supersedes`/`people`와 본문 위키링크가 모두 링크로 집계돼요(고아 노트 판정 기준). 기록은 지우지 않고 `superseded`로 표시해요.

## 예시 1 — note

`notes/2026/09/쿠버네티스-노드-교체-팁.md`

```markdown
---
title: 쿠버네티스 노드 교체 팁
type: note
created: 2026-09-30
tags:
  - k8s
  - ops
project: 인프라 개선
links:
  - "[[002-노드그룹-관리형으로-전환]]"
---

# 쿠버네티스 노드 교체 팁

drain 전에 PDB부터 확인. 자세한 배경은 [[002-노드그룹-관리형으로-전환]].
```

## 예시 2 — decision

`decisions/002-노드그룹-관리형으로-전환.md`

```markdown
---
title: 노드그룹 관리형으로 전환
type: decision
created: 2026-09-30
tags:
  - k8s
  - cost
project: 인프라 개선
revisit: 2026-12-31
status: open
supersedes:
  - "[[001-자체-관리-노드그룹-유지]]"
---

# 노드그룹 관리형으로 전환

## 상황
패치 작업에 매달 이틀씩 쓰고 있다.

## 고려한 선택지
- 자체 관리 유지
- 관리형 노드그룹
- Karpenter

## 결정
관리형 노드그룹으로 전환한다.

## 이유
운영 부담 감소, 비용 차이 5% 이내.

## 되돌아볼 날짜
2026-12-31 — 비용 리포트 보고 Karpenter 재검토.
```

## 예시 3 — project

`projects/인프라-개선.md` — `<!-- brain:auto -->` 블록은 `brain.py index`가 매번 다시 써요. 블록 밖은 자유롭게 편집해도 돼요.

```markdown
---
title: 인프라 개선
type: project
created: 2026-09-01
tags:
  - infra
---

# 인프라 개선

## 목표
운영 시간 30% 절감.

<!-- brain:auto:begin -->
## 관련 기록 (자동)

- 2026-09-30 · decision · [[002-노드그룹-관리형으로-전환]] (open)
- 2026-09-30 · note · [[쿠버네티스-노드-교체-팁]]
<!-- brain:auto:end -->
```

## Obsidian에서 열기

1. Obsidian → "Open folder as vault" → `~/brain` 선택.
2. `[[위키링크]]`, 태그, 프로퍼티(프론트매터)가 그대로 인식돼요. 그래프 뷰로 연결 상태를 볼 수 있어요.
3. 설정 → 파일 및 링크 → "새 링크 형식"을 "가장 짧은 경로"로 두면 세컨드브레인 규칙(파일명만으로 링크)과 맞아요.
4. Obsidian에서 직접 만든 노트도 검색·인덱스에 잡혀요. 프론트매터가 없으면 파일명이 제목, 타입은 `note`로 취급해요.
5. `.obsidian/`, `.git/`, `.trash/` 폴더는 무시해요. `BRAIN.md`는 자동 생성이니 Obsidian에서 고치지 마세요.

## 가져오기 매핑 (`brain.py import`)

| 원본 | 결과 |
|---|---|
| 프론트매터 있는 마크다운 | `title`/`type`/`created`(또는 `date`)/`tags` 매핑, 나머지 단순 필드 유지 |
| 프론트매터 없는 마크다운 | 첫 헤딩 → `title`(없으면 파일명), 파일 수정시각 → `created`, `type: note` |
| Claude Code 메모리(`name`/`description`/`type` 또는 `metadata.type`) | feedback→note, project→project, reference→source, user→person. `description`은 본문 첫 줄 인용으로, `modified` → `created`, 태그 `claude-memory` + 원래 타입. `MEMORY.md` 인덱스는 건너뜀 |

- 위키링크 재작성: 가져오기 전에 원본 전체를 훑어 `원래 이름(메모리 name·파일명) → 새 파일명` 매핑을 만들고, 본문과 프론트매터 `links`의 `[[원래이름]]`을 `[[새파일명]]`으로 바꿔요. 매핑에 없는 링크는 그대로 두고, 이미 잘못 가져온 볼트는 `brain.py relink [--dry-run]`으로 복구해요.
- 메모리 제목: `name`이 영문 슬러그면 `description` 첫 문장(` — `/` - ` 앞 → 첫 마침표/쉼표 앞 → 40자 절단)을 `title`로 써요. 짧고 구분자 없는 설명이면 기존처럼 `name`에서 만들고, 파일명은 항상 `name` 기준이라 바뀌지 않아요.

원본 파일은 읽기만 하고, 볼트에 같은 `title`+`created` 노트가 있으면 건너뛰어요. `--dry-run`으로 먼저 확인할 수 있어요.
