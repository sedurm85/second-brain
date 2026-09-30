# 볼트 형식

세컨드브레인 볼트는 평범한 마크다운 폴더예요. 기본 위치는 `~/brain/`이고, `~/.config/second-brain/config.json`의 `vault` 값으로 바꿀 수 있어요(홈 디렉토리 아래만 허용).

```
~/brain/
  BRAIN.md                    자동 생성 인덱스 (직접 수정 금지, brain.py index가 덮어씀)
  inbox.md                    빠른 캡처 임시함
  notes/YYYY/MM/<slug>.md     note | idea | source | meeting
  events/YYYY/<date>-<slug>.md event (일정 노트: 준비·동선·메모, 캘린더 일정에 붙음)
  journal/YYYY/<date>.md      journal (하루 일지) · <date>-weekly.md (주간 회고)
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
| `type` | 필수 | `note` `idea` `source` `meeting` `event` `journal` `decision` `project` `person` | 타입에 따라 저장 폴더가 정해져요 |
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
| `summary` | 선택 | 문자열 | 2~3문장 요약. `enrich`·`journal`·`retro`가 채우고, 검색 가중치 2배, 노트 패널 맨 위에 표시 |
| `original_title` | 자동 | 문자열 | `enrich`가 제목을 다듬었을 때 원래 제목 |
| `event_key` | event 필수 | `YYYY-MM-DD\|제목` | 캘린더 일정과 노트를 잇는 키(날짜 + 공백 정리한 제목) |
| `event_date` `event_end` | event | `YYYY-MM-DD` | 일정 날짜(여러 날이면 끝 날짜). 그 기간의 날짜마다 같은 노트가 붙어요 |
| `location` | event 선택 | 문자열 | 장소(보드에서 지도 링크) |
| `journal_date` | journal | `YYYY-MM-DD` | 일지가 다루는 날 |
| `journal_kind` | journal 선택 | `weekly` | 주간 회고 표시(파일명 `-weekly`) |

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

## 예시 4 — event (일정 노트)

`events/2026/2026-10-03-대한항공-ke1355.md` — 보드의 일정 카드에서 「준비 항목 추가」「동선 추가」「메모 남기기」를 누르면 이 절들에 줄이 붙어요. `## 준비`의 체크박스는 보드·아침 브리핑에, `## 동선`의 `- HH:MM 내용 (…NN분)` 줄은 그날 시간표와 10분 전 카톡 알림에 쓰여요. 여러 날 동선은 `### MM-DD` 소제목으로 날짜를 바꿔요.

```markdown
---
title: 대한항공 KE1355
type: event
created: 2026-09-30
tags: []
event_key: "2026-10-03|대한항공 KE1355"
event_date: 2026-10-03
location: 김포공항
---

# 대한항공 KE1355

## 준비
- [x] 여권
- [ ] 모바일 탑승권 발급

## 동선
- 13:30 집 출발 (콜밴, 50분)
- 14:25 공항 도착·수속

## 메모
- 2026-09-30 16:40  좌석은 12A
```

## 예시 5 — journal (하루 일지 · 주간 회고)

`journal/2026/2026-09-30.md`는 21:30 저녁 마감(`brief --evening --journal`)이 그날 재료(새 노트·일정 메모·완료한 할 일·자동화 사고·코어 대화)로 써요. 절은 `## 오늘`(3~5줄) · `## 잘한 것` · `## 내일 첫 일`. `journal/2026/2026-09-30-weekly.md`는 월요일 `retro`가 쓰는 주간 회고로 `## 이번 주` · `## 눈에 띄는 것` · `## 되돌아볼 질문`(체크박스 3개, 보드 「되돌아볼 결정」 카드에 떠요) · `## 다음 주`. 둘 다 프론트매터 `summary`에 카톡용 한 줄이 들어가요.

## 볼트 밖 파일 (비밀·캐시)

| 경로 | 내용 |
|---|---|
| `~/.config/second-brain/config.json` | 볼트 위치, 캘린더 소스, 비서 이름, `kakao_cmd`, `ask_cmd` |
| `~/.config/second-brain/google.ics.url` | 구글 캘린더 비공개 ICS 주소 한 줄(권한 600). 볼트·대화에 넣지 않기 |
| `~/.config/second-brain/widgets.json` | 자동화 위젯(아래) |
| `~/.cache/second-brain/` | 캘린더·메일 캐시, `suggestions.json`(준비 제안 보류함: pending/accepted/dismissed), `core_log.jsonl`(코어 문답), `staff_briefs.json`(직원 한 줄), `backups/`(zip), `agents/`(launchd 로그) |

제안(준비 체크리스트·동선·링크)은 캐시에만 있다가 사용자가 보드에서 채택하거나 `link A B`를 실행할 때 볼트에 들어가요. 볼트에 자동으로 쓰는 건 일지·회고·`enrich` 정제(요약·태그·링크 필드) 셋뿐이고, 모두 노트 내용을 지우지 않아요.

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

## 위젯 설정 (`~/.config/second-brain/widgets.json`, v0.3)

볼트 밖의 사용자 설정이에요. `brain.py config init-widgets`로 예시를 만들 수 있고(기존 파일은 덮어쓰지 않아요), `brain.py widgets`·`/api/widgets`로 상태를 봐요. 파일이 없으면 위젯 0개예요.

| 필드 | 대상 kind | 설명 |
|---|---|---|
| `allow_commands` | (최상위) | `true`일 때만 `command` 위젯을 실행해요. 아니면 status `unknown`, summary "명령 실행 비활성(allow_commands)" |
| `allow_run` | (최상위) | `true`일 때 보드 행·사무실 책상의 「지금 실행」 버튼이 켜져요. 위젯 `source`와 같은 로그를 쓰는 crontab 줄 또는 launchd `StandardOutPath`를 찾아 그 명령을 한 번 실행해요 |
| `widgets[].team` | 모두 | 사무실 화면의 방(팀) 이름. 없으면 「기타」 |
| `widgets[].id` | 전체 | 위젯 식별자(없으면 `widget-N`) |
| `widgets[].title` | 전체 | 표시 이름(없으면 id) |
| `widgets[].kind` | 전체 | `log` · `json` · `csv` · `markdown` · `command` |
| `widgets[].source` | 전체 | 파일 경로(`~` 허용, 홈 밖·`..`·홈 밖을 가리키는 심볼릭 링크는 거부 → `missing`+`error`). `command`는 셸 명령 |
| `widgets[].status.ok_pattern` | log | 마지막 N줄에서 매치되면 `ok` (정규식) |
| `widgets[].status.fail_pattern` | log | 마지막 N줄에서 매치되면 `fail` (ok보다 우선) |
| `widgets[].status.stale_minutes` | 파일 kind | mtime이 이 분을 넘으면 `stale` (패턴 판정보다 우선) |
| `widgets[].state` | 모두 | `active`(기본) 또는 `paused`. paused면 status가 항상 `paused`로 고정되고 경고·요약에서 빠짐. 끝난 크론을 지우지 않고 기록만 남길 때 |
| `widgets[].lines` | log · markdown | log는 마지막 N줄(기본 5), markdown은 첫 N줄(기본 10) |
| `widgets[].fields` | json | 추출할 키 목록, 점 경로 `a.b`·배열 인덱스 `a.0` 지원. 없는 키는 `null` + `warn` |
| `widgets[].x`, `widgets[].y` | csv | x/y 열 이름(헤더 행 필수). y 숫자 변환 실패 행은 건너뜀(쉼표 `1,234` 허용) |
| `widgets[].last` | csv | 마지막 N행(기본 30). summary는 "최근 값 / 30일 변화" |
| `widgets[].timeout_sec` | command | 제한 시간(기본 10, 최대 60초). stdout은 4KB에서 잘리고, exit≠0·시간 초과는 `fail` |

status는 `ok | warn | fail | stale | missing | unknown`이에요. 파일 없음은 `missing`, 파싱 실패·필드 누락은 `warn`, 패턴이 둘 다 안 맞으면 `unknown`. 서버(`serve`)는 위젯별로 60초 동안(파일 mtime이 그대로면) 결과를 캐시해요.

`inbox.md`의 `- [ ] 할 일` 줄은 `brain.py today`·`/api/today`의 `inbox`에 표시돼요(체크된 `- [x]`는 제외).
