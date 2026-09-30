# 세컨드브레인 (second-brain) — Claude Code 플러그인 스펙 v0.1

## 한 줄
Claude와 대화하다 "이거 기억해둬"라고 하면 마크다운 볼트에 쌓이고, 나중에 "그때 왜 그렇게 정했지?"라고 물으면 **의사결정 맥락**까지 찾아주는 개인 지식 창고. 외부 계정·API 키·서버 0. Obsidian과 호환되는 평문 마크다운.

## 왜 (릴스 hongik.man 2026-09-29, 댓글 1.9K)
"아이디어, 자료, 업무 기록이 쌓이는 개인 지식 창고, 과거의 의사결정 맥락을 바로 찾아 새로운 기획에 활용" — 수요는 확인됐고, Claude Code 사용자라면 설치 한 줄로 끝나야 한다.

## 볼트 (기본 `~/brain/`, `~/.config/second-brain/config.json`으로 변경 가능)
```
~/brain/
  BRAIN.md                 자동 생성 인덱스(최근·프로젝트별·미해결 결정). 세션 시작 시 상단 N줄 주입
  inbox.md                 빠른 캡처 임시함 (정리 전)
  notes/YYYY/MM/<slug>.md  type: note | idea | source | meeting
  decisions/<NNN>-<slug>.md  type: decision (ADR): 상황·선택지·결정·이유·되돌아볼 날짜·상태(open|decided|superseded)
  projects/<slug>.md       프로젝트 허브: 관련 노트·결정 [[링크]] 자동 수집
  people/<slug>.md         사람(누가 무슨 맥락)
```
프론트매터(필수: title, type, created, tags / 선택: project, source, people, links, revisit, status, supersedes)
본문은 자유 마크다운, `[[파일명]]` 위키링크. 절대 삭제하지 않고 `superseded`로 표시.

## 스킬 (플러그인 `skills/`)
| 스킬 | 호출 | 하는 일 |
|---|---|---|
| brain-capture | "기억해둬/저장해/메모해" 자동 트리거 또는 `/brain 저장 <내용>` | 대화 맥락에서 제목·태그·타입·프로젝트를 추론해 노트 생성. URL이면 source 노트(WebFetch 요약+원문 링크). 저장 후 파일 경로 1줄 |
| brain-decide | "이렇게 결정했어/정했어" 또는 `/brain 결정` | ADR 생성: 상황·고려한 선택지·결정·이유·되돌아볼 날짜. 관련 노트 자동 링크 |
| brain-ask | "그때 왜/언제/뭐라고 했지", "brain에서 찾아" 또는 `/brain 물어 <질문>` | `brain.py search`(BM25-lite + 최신 가중 + 태그/프로젝트 필터) 상위 N → 파일 읽고 종합. **인용은 [[파일]]로, 없는 건 "기록 없음"** |
| brain-review | `/brain 리뷰` (주간) | 최근 7일 노트·미해결 결정(revisit 도래)·고아 노트·링크 제안 → BRAIN.md 갱신 + 요약. 선택: `~/.local/k-skill-cron/notify_kakao.py` 있으면 카톡 |
| brain-import | `/brain 가져오기 <경로|URL>` | Obsidian 볼트·Claude Code 메모리 디렉토리(`~/.claude*/projects/*/memory`)·마크다운 폴더·URL을 볼트 형식으로 변환(원본 보존, 중복 스킵) |
| brain-setup | `/brain 설정` | 볼트 위치·git 자동 커밋 여부·인덱스 줄 수 설정, 볼트 생성, 샘플 노트 1개 |

## 스크립트 (`scripts/brain.py`, Python 3.9+ stdlib만, 결정적 작업은 전부 여기)
`new`(프론트매터 검증·slug·경로) · `search <query> [--type --project --tag --since --limit]`(제목 3배·태그 2배·본문 1배 가중 BM25-lite + 최근성 감쇠, JSON 출력) · `index`(BRAIN.md 재생성: 최근 20·프로젝트별·미해결 결정·고아 노트) · `review --days 7` · `import <path|memory-dir>` · `link <file> <file>` · `config get|set`
테스트: `tests/test_brain.py` (unittest, 30개+: 프론트매터 파싱·slug 충돌·검색 랭킹·인덱스·import 중복·superseded 처리)

## 훅 (`hooks/hooks.json`)
- SessionStart: `brain.py index --head 40` 출력을 컨텍스트에 주입(볼트 없으면 무음)
- (선택) Stop: 세션에 "결정" 키워드가 있었는데 decisions에 새 파일이 없으면 1줄 제안 — v0.2

## 배포
- 저장소 `sedurm85/second-brain`(공개, MIT), `.claude-plugin/plugin.json` + `marketplace.json`
- 설치: `/plugin marketplace add sedurm85/second-brain` → `/plugin install second-brain@second-brain`
- README(한국어, 영문 요약): 30초 설치, 5개 시나리오 대화 예시, 볼트 구조, Obsidian 열기, FAQ(데이터는 내 컴퓨터에만)
- 카페 글 초안 `content/story/2026-09-30-second-brain-plugin-cafe.md` (본인 도전기 톤, 발행은 사장님 승인 후)

## 범위 밖 (v0.2+)
임베딩 검색(외부 키 필요) · 웹 뷰어 · 모바일 캡처 · 팀 공유 볼트

## 완료 기준
`python3 -m unittest` 통과 · 로컬에서 `/plugin marketplace add <로컬경로>`로 설치 후 6개 스킬 각 1회 동작 · 볼트 삭제 후 재설치해도 데이터 안전(볼트는 플러그인 밖) · GitHub 공개 · README

## v0.2 대시보드

`brain.py serve [--port 7777] [--open] [--demo]` — `ThreadingHTTPServer`, 127.0.0.1 전용, 읽기 전용. 시작 시 stdout 한 줄 `http://127.0.0.1:<port>`, 요청 로그는 stderr 한 줄. `--demo`는 `~/.cache/second-brain/demo`에 가공 데이터 볼트(프로젝트 3·노트 18·결정 6·사람 2·링크 25+, 최근 60일)를 새로 만들어 서빙. 스킬 `brain-view`가 백그라운드로 띄운다. 프론트는 `web/index.html` 단일 파일.

모든 API: `application/json; charset=utf-8`, `ensure_ascii=False`, `Cache-Control: no-store`. 오류는 `{error}` JSON — 입력 오류 400, 없음 404, 예외 500(서버는 계속 동작).

| 메서드·경로 | 파라미터 | 응답 |
|---|---|---|
| `GET /` | — | `web/index.html` (없으면 404 JSON) |
| `GET /web/<file>` | — | `web/` 아래 정적 파일, 확장자별 content-type, 밖으로 탈출 시 404 |
| `GET /api/summary` | — | `{vault, generated_at, counts:{notes,decisions,projects,people,total}, this_week:{new_notes,new_decisions}, open_decisions:[{path,title,revisit,days_left}], recent:[{path,title,type,created,project,tags}]}` (recent 최대 30) |
| `GET /api/graph` | — | `{nodes:[{id,path,title,type,project,created,tags,degree}], edges:[{source,target}]}` — id=파일 stem, 위키링크+프론트매터 links/supersedes/people에서 추출, 무방향 중복 제거, 없는 대상 제외 |
| `GET /api/search` | `q`, `limit`(기본 20) | `[{path,title,type,created,tags,project,score,snippets:[...]}]` (CLI `search --json` 결과와 동일 순서, `snippet` 키도 유지) |
| `GET /api/note` | `path`(볼트 상대 `.md`) | `{path,title,type,frontmatter,body,links_out:[stem],links_in:[stem]}` — 절대경로·`..`·볼트 밖(심볼릭 링크 포함) 400 |
| `GET /api/timeline` | `days`(기본 30) | `[{date,items:[{path,title,type,project}]}]` 날짜 내림차순, 오늘 포함 최근 days일 |
| `GET /api/decisions` | — | `{open,decided,superseded}` 각 `[{path,title,created,revisit,days_left,project,supersedes}]` — open은 revisit 오름차순 |
| `GET /api/projects` | — | `[{name,path?,note_count,decision_count,last_activity,recent:[{path,title,type}]}]` last_activity 내림차순, 허브 없는 프로젝트는 path 생략 |

## v0.3 비서 모드 (2026-09-30)

목표: 대시보드를 "눈에 보이는 개인 비서"로. 내 상태(자동화·지표·할 일·되돌아볼 결정)를 한 화면에, "오늘 뭐 있어?"에 말로 답하고, 원하면 카톡으로 먼저 알린다. 플러그인은 범용 — 사용자별 소스는 `~/.config/second-brain/widgets.json`.

### widgets.json (사용자 설정, 플러그인 밖)
```json
{
  "allow_commands": false,
  "widgets": [
    {"id":"marketset","title":"마켓세트 카톡","kind":"log","source":"~/.local/k-skill-cron/marketset.log",
     "status":{"ok_pattern":"len:","fail_pattern":"Traceback|Error","stale_minutes":1500},"lines":5},
    {"id":"cafe-growth","title":"카페 회원 추이","kind":"csv","source":"~/.local/naver-publish/growth.csv","x":"date","y":"members","last":30},
    {"id":"flight","title":"항공권 최저가","kind":"json","source":"~/.local/k-skill-cron/flight_state.json","fields":["best_price","route","checked_at"]},
    {"id":"jobscout","title":"채용 스카우트","kind":"markdown","source":"~/.local/k-skill-cron/jobscout_result.md","lines":8},
    {"id":"disk","title":"디스크","kind":"command","source":"df -h / | tail -1","timeout_sec":5}
  ]
}
```
- kind: `log`(마지막 N줄, 상태 패턴·stale 판정) · `json`(fields 추출) · `csv`(x/y 열, 마지막 N행 → 스파크라인) · `markdown`(첫 N줄) · `command`(`allow_commands: true`일 때만, timeout, stdout ≤ 4KB)
- 경로는 `~` 허용, 홈 밖 금지. 존재하지 않으면 status `missing`
- status: `ok | warn | fail | stale | missing | unknown`. stale = mtime이 `stale_minutes` 초과
- state(v0.3.1): `active`(기본) | `paused`. paused = 끝났거나 세운 자동화. 내용은 그대로 읽되 status는 항상 `paused`, 「오늘」 요약·카톡 경고·top_widgets에서 제외, 목록 뒤로 정렬. 대시보드는 「진행 중 / 멈춤(접힘)」 두 묶음으로 표시

### API 추가 (`brain.py serve`)
- `GET /api/widgets` → `[{id,title,kind,status,updated_at(mtime ISO),age_minutes,summary(≤200자),data}]` — data: log `{lines:[…]}` / json `{fields:{…}}` / csv `{columns,rows:[[x,y]…]}` / markdown `{text}` / command `{stdout,exit_code}`. 서버 내 60초 캐시
- `GET /api/today` → `{date, greeting(시간대별), weekday, revisit:[결정 open + revisit ≤ 오늘+7], inbox:[inbox.md의 `- [ ]` 항목], this_week, widgets_summary:{ok,warn,fail,stale,missing}, top_widgets:[status가 fail/stale인 것 우선 5개]}`
- CLI: `brain.py today [--json]` (same as /api/today + widgets 요약, 사람용은 카톡 200자 버전도 출력), `brain.py widgets [--json]`

### 대시보드 (web/index.html)
- 최상단 **「오늘」**: 인사 + 날짜, 3열 카드(되돌아볼 결정 / inbox 할 일(체크 불가, 표시만) / 자동화 상태 요약 pill 5개), 그 아래 **위젯 그리드**(status pill, summary, kind별 미니 뷰: log 마지막 줄·csv 스파크라인·json 필드 표·markdown 발췌·command 출력). 60초 자동 갱신, 위젯 없으면 "widgets.json으로 내 자동화를 붙여보세요" + 예시 링크
- 기존 섹션(그래프·결정 보드·타임라인·프로젝트)은 그 아래 유지

### 스킬
- `brain-today`: "오늘 뭐 있어", "비서", "브리핑", "상태 알려줘", "자비스" 트리거 또는 `/second-brain:brain-today`. `brain.py today --json` → 3~6줄 브리핑(되돌아볼 결정 → 죽은 자동화 → inbox → 이번 주 신규). `~/.local/k-skill-cron/notify_kakao.py` 있으면 "카톡으로도 보낼까요?" 1회 제안(자동 전송 금지)
- `brain-setup`에 위젯 설정 안내 추가(예시 widgets.json 생성 옵션 `config init-widgets`)

### 사장님 개인 설정(플러그인 밖, 저장소에 넣지 않음)
`~/.config/second-brain/widgets.json` — 크론 12개 로그·항공권/실거래/잡스카우트 상태·카페 growth.csv·블로그 발행 로그. + 콘텐츠 에이전트 lite: `~/.local/naver-publish/topic_recommender.py`(조회수·최근 글 → 반응 좋은 글 5·다음 주 주제 3, 결과 md → markdown 위젯). 크론 등록은 사장님 승인 후.

## v0.4 대시보드 리디자인 (2026-09-30)

근거: frontend-design 스킬 + taste-skill(안티슬롭) 감사. v0.3 화면은 두 스킬이 꼽는 "AI가 만든 대시보드" 기본값(똑같은 둥근 카드 반복, 순검정, 좌측 사이드바, 알약 배지, 가운뎃점 메타, 카드별 등장 애니메이션)에 그대로 해당했다.

- 디자인 리드: 개인 AI 비서의 아침 상태판, 사용자 1인. "관제실이 아니라 서재". 다이얼 변주 6 / 모션 4 / 밀도 6
- 토큰: 차콜 `#151614` + 본 잉크 `#f1eee6`(라이트 `#f2f2ef` / `#1c1c1a`). 인터페이스는 단색, 색은 상태(정상·주의·실패)와 데이터(노트 타입 7색)에만. 서체 IBM Plex Sans KR 한 가족(모노는 로그 발췌에만), 모서리 6px 하나, 알약 배지 없음, 그림자는 패널·모달·툴팁에만
- 레이아웃: 56px 상단 바(사이드바 제거), 최대 폭 1180. 히어로 7:5 = 비서의 두세 문장 보고(`reportSentences`, 자동화 상태·되돌아볼 결정·할 일·이번 주 요약을 자연어로) + 살아 있는 지식 그래프(테두리 없는 진짜 내용, 라벨은 캔버스 폭 60px당 1개 예산). 자동화는 카드 대신 「주의 필요 / 정상 / 멈춤」 세 묶음의 행(주의 행만 로그 발췌를 펼침). 개요·결정 보드·타임라인·프로젝트는 카드 껍데기 없이 상단 괘선으로 묶음
- 모션: 카드별 등장·호버 이동 전부 제거. 첫 로드에 보고 문장 한 번(`.report.once`), 버튼 :active 1px. reduced-motion 존중
- 문구: 줄표(—) UI 문자열에서 제거, 가운뎃점은 한 줄 1개, 섹션 설명 한 문장. 검색 placeholder "기억 검색"
- 접근성: 본문 건너뛰기 링크, 상태는 아이콘+낱말, 포커스 링 잉크색, 라이트 상태색 대비 4.5:1 이상
- 유지: 섹션 id·내비 라벨 7개·API·클래스 계약 그대로(테스트 103 통과). `?theme=light|dark`로 테마 강제(저장 안 함), 딥링크(#decisions)는 내용 로딩 후 스크롤
- 알려진 한계: 그래프 라벨은 군집에서 여전히 겹침, 손으로 그린 SVG 아이콘(무의존 원칙과 상충해 유지), Google Fonts 차단 시 시스템 한글 폰트로 폴백

## v0.5 「관제실」 시각 층 (2026-09-30, 같은 날 v0.4 피드백 반영)

사장님 피드백 "너무 밋밋함, 화려함이 없음". v0.4의 구조(보고 문장, 그래프 히어로, 주의/정상/멈춤 행)는 유지하고 시각 층만 올렸다. 안티슬롭 규칙 중 "네온·글로우 금지"는 사장님이 명시적으로 존재감을 요구했으므로 브리프 우선 원칙으로 해제, 단 강조색은 시안 하나로 고정.

- 토큰: 바탕 `#0a0e14` 깊은 남색, 잉크 `#e8f1f8`, 강조 `#4fd1ff`(라이트 `#0b7fc2`). 데이터 7색·상태 3색 모두 어두운 바탕에서 빛나는 톤으로. `--glass`(유리 그라데이션), `--glass-edge`(윗변 하이라이트), `--panel-shadow`, `--rp: 14px`(패널 모서리), 컨트롤은 여전히 6px
- 바탕 재질: `body::before` 시안·민트 앰비언트 두 점 + 96px 격자(.045, 아래로 사라짐), `body::after` SVG 그레인(.045, overlay)
- 유리 패널 `.glass`: 히어로 본문, 그래프, 자동화 묶음(`.wgroup`), 개요 타일, 결정 열, 타임라인, 프로젝트. 행·괘선 구조는 패널 안에서 그대로
- 발광: 보고 문장의 수치 `<b class="k">`(시안, 실패 낱말은 붉게), 개요 숫자(시안 40px, 카운트업), 실패 행 왼쪽 막대와 실패 아이콘, 그래프 노드(`shadowBlur` 8+연결수×1.5, 흐린 노드는 0), 갱신 시각 앞 라이브 점(2.4s 펄스)
- 등장 시퀀스: `html.entering`일 때만(탭이 보이고 모션 축소 아님) 보고→그래프→자동화→나머지 순 .7s, 1.6초 뒤 클래스 해제. 백그라운드 탭·캡처에서 첫 프레임에 멈추는 문제 방지
- 수정: 결정 보드 마크업 닫힘(표현식 안 주석이 닫는 태그를 삼켰음)
- **v0.5.1**: 사장님 "노드도 한 행을 점유하는 카드로" → 지식 그래프를 히어로 옆 5/12 칸에서 빼 전폭 유리 패널(높이 clamp 440~680px)로. 보고 패널은 좌 보고 문장 / 우 지금 볼 것·결정·할 일 2열. 내비 순서 화면 순서로(오늘→지식 그래프→자동화→개요→결정→타임라인→프로젝트)

## v0.6 2차 1단계 「시간」 (2026-09-30)

- 새 모듈 `scripts/agenda.py`(stdlib): ICS 파서(접힘 해제, TZID/UTC/DATE, RRULE DAILY·WEEKLY(BYDAY)·MONTHLY·YEARLY + INTERVAL·COUNT·UNTIL, EXDATE, RECURRENCE-ID 대체, STATUS:CANCELLED 제외, DURATION), ICS 소스(로컬 path 또는 url_file의 비공개 주소, 15분 캐시 `~/.cache/second-brain/`, 실패 시 캐시 stale), EventKit 소스(JXA, 최초 권한 요청, 캘린더 이름 필터)
- 설정 `config.json` `calendar.sources[]` `{kind: ics|eventkit, name, url_file|path|calendars}`. 소스가 없으면 아무 것도 호출하지 않고 `unconfigured` + 안내(hint)만. 인증 정보(비공개 주소)는 별도 파일, 볼트 밖
- `collect_agenda(cfg, now, days)` → `{today, upcoming, next, current, conflicts, sources, total}`; 중복(제목·시작·끝 동일) 제거, 겹침 계산(busy만)
- brain.py: `/api/agenda?days=`, `/api/today`에 `agenda{today,next,current,conflicts,sources,total,upcoming_count,sentence}`, 서버 60초 AgendaCache. `today`/`brief` 사람용에 일정 문장, 카톡 200자에 `일정 10:00 …` 조각. CLI `agenda [--days]`, `brief [--kakao]`(헬퍼 `~/.local/k-skill-cron/notify_kakao.py` 또는 config `kakao_cmd`), `calendar list|add <ics|eventkit> <이름> [--url-file|--path|--calendars]|remove <이름>|test`
- 대시보드: 보고 문장에 "오늘 일정 N개: 10:00 …, 15:00 … 등, 종일 …", 히어로 하단 「오늘 시간표」 스트립(06~24시 축, 블록, 지금 표시, 겹침은 호박 테두리, 지난 일정 흐림) + 목록, 새 섹션 「이번 주」(7열, 오늘 강조), 내비에 「이번 주」. 미연결이면 스트립 자리에 연결 방법 안내
- 스킬 brain-today: 일정 줄 추가, 카톡은 `brief --kakao`로, 구글 연결 안내(비공개 주소는 대화에 붙이지 말고 파일에 저장)
- 테스트 +11(파서·반복·수집·API·CLI), 총 114
- 남은 것(1단계 완료 기준): 사장님 구글 비공개 ICS 등록 → 아침 7시 `brief --kakao` 크론(승인 뒤)

## v0.7 일정 노트 (2026-09-30, 2차 2단계 앞당김)

사장님 피드백 "일정 정보값이 너무 적다, 따로 기록도 해야". 캘린더 정보를 전부 끌어오고, 일정마다 볼트 노트를 붙여 메모·준비 체크리스트를 대시보드에서 바로 쓴다(첫 쓰기 기능).

- agenda.py: DESCRIPTION·URL·ATTENDEE(CN) 파싱, 각 일정에 `key`(`YYYY-MM-DD|제목`)·`days_left`. 문장: 오늘 없으면 "내일은 …", 그것도 없으면 "다음 일정은 N일 뒤 …"
- 노트 타입 `event`(NOTE_TYPES): `events/YYYY/YYYY-MM-DD-slug.md`, frontmatter `event_key`, `event_date`, `event_end`(여러 날), `location`; 본문 `## 준비`(체크박스) / `## 메모`(`- YYYY-MM-DD HH:MM  내용`). 기간 노트는 같은 이름의 날짜들에 함께 붙음(slug 포함 매칭)
- `attach_event_notes(vault, agenda)`: today/upcoming/current/next에 `note {path, checklist[{line,done,text}], done, total, memo, location}` 부착. /api/agenda·/api/today 모두. today에 `agenda.upcoming`(≤6) 추가
- 쓰기 API: `GET /api/session → {token, writable}`(서버 시작마다 새 토큰), `POST /api/event-note` 헤더 `X-Brain-Token` 필수 + Host 루프백만. body `{action: memo|todo|check, key, title?, date?, end?, location?, text? | path, line, done}`. 본문 64KB 한도, 4000자/회. 성공 시 AgendaCache 무효화, git_autocommit 존중
- CLI `event memo|todo|show <키> [내용] [--body-file] [--end] [--location]`. 스킬 `brain-event`(원문 그대로 기록, 요약 금지)
- 대시보드: 일정 항목(시간표 목록·이번 주·다가오는 일정)이 버튼 → 상세 패널(언제/어디(네이버 지도 링크)/캘린더/참석/링크, 설명, 준비 체크리스트 토글+추가, 메모 목록+작성). ✎ n/m 표시. 「지금 볼 것」에 「다가오는 일정」 D-N 목록. `?event=<key>` 딥링크로 패널 바로 열기. 타입 색 `--t-event`, 모양 팔각형
- 테스트 +5(부착·메모·체크·토큰 403·CLI), 총 119

## v0.8 「코어」 화면 (2026-09-30)

사장님이 말한 자비스의 원형(reznikov_engineering 릴스): 화면 전체가 살아 있는 코어 하나, 말할 때 요동치고 쉴 때 차분한 빛의 띠, 최소 HUD, 말하는 비서(이름·자막). 보드(`/`)는 보고서, 코어(`/core`)는 얼굴과 목소리.

- `web/core.html` 단일 파일(무의존): Canvas 2D 가산 합성. 성운 라디얼 + 먼지 140 + 빛의 띠 7(회전 타원 호, 밝은 머리) + 코어 구체(안쪽 별먼지·테두리 링). `level`(0~1)이 크기·밝기·속도를 키움. 상태색: 대기 청록 / 말하기 보라 / 생각 호박 (실패는 상태색이 아니라 바깥 붉은 링 펄스 + HUD 글자). reduced-motion이면 2초마다 정지 프레임
- HUD: 좌상 시각·날짜·다음 일정, 우상 상태 3줄(일정·자동화·할 일/결정, 강조색 세로 괘선), 중앙 하단 이름 + 자막(타자 효과), 좌하 레일(보드로·브리핑 읽기·소리), 하단 마이크 + 입력창
- 말하기: `speechSynthesis` ko-KR(유나 우선), 문장마다 발화, onboundary로 level 흔들림. 자동 재생은 `?speak=1`일 때만(브라우저 정책)
- 듣기: `webkitSpeechRecognition` ko-KR(있으면 마이크·스페이스), 없으면 입력창. 로컬 규칙 답변: 오늘/일정, 이번 주, 자동화 상태, 할 일, 결정, 브리핑, 보드 열기, 인사. 그 밖은 `POST /api/ask`
- `/api/ask`(토큰 필수): 오늘 상태 JSON을 붙여 헤드리스 `claude -p --output-format text`에 질문(해요체 2~3문장 200자). 명령은 config `ask_cmd` 또는 env `SECOND_BRAIN_ASK_CMD`(테스트는 가짜 스크립트). 볼트 없어도 동작
- `/api/session`에 `assistant_name`(config, 기본 「브레인」). `brain.py config set assistant_name 자비스`
- 보드 상단 바에 「코어」 링크, brain-view 스킬에 코어 안내. 테스트 +3, 총 122
- 확인(Orca): 한국어 음성 9종·음성 인식 API 존재. 마이크 권한은 사용 시 브라우저가 물음

## v0.9 「사무실」 화면 (2026-09-30)

사장님 참조(godseng.mom 릴스 「자동으로 일하는 나만의 직원」): 팀별 방에 직원 캐릭터, 초록 불은 가동 중. 우리 데이터에 그대로 대응: 자동화 하나 = 직원 하나, 팀 = 방, Claude 배경 작업 = 「Claude 작업실」 직원.

- `web/office.html`(무의존): 직원 스프라이트는 16x20 격자 SVG 사각형으로 즉석 생성(머리·셔츠·피부색은 id 해시), 상태별 자세: 가동=타이핑 2프레임(0.5s)·모니터 깜빡, 정상=대기, 실패=머리 감싸기+붉은 램프+붉은 말풍선, 오래됨=눈 감고 zzz, 멈춤=빈 의자. 방 머리에 창문(시간대별 하늘색), 램프(가동 파랑 깜빡/주의 붉음/정상 초록). 팀장의 한마디(지금 제가 하는 일·가동 인원·막힌 담당), 활동 로그 2열, 직원 클릭 → 상세(로그 발췌)
- `/api/office`: `teams`(widgets.json `team` 또는 id 접두어 기본 배정: 콘텐츠팀/생활팀/커리어팀/운영팀), `widgets`(+team·source), `running`(crontab `>> 로그`↔위젯 source로 스크립트 경로를 얻고 `ps` 명령줄과 파일명 패턴 `[\s/]stem*.py|.sh` 매칭, Claude Helper 등 제외), `jobs`(`$CLAUDE_CONFIG_DIR/jobs/*/state.json`, 일하는 중·막힘 24h / 끝난 것 3h, timeline 최근 5줄), `events`(위젯 갱신 + 작업 detail 시간순 24개), `assistant_name`. 환경변수 `SECOND_BRAIN_JOBS_DIR`로 대체 가능
- 보드·코어 상단에 「사무실」 링크. brain-view 스킬: 화면 셋 안내. 테스트 +4, 총 126

## v0.10 할 일 체계 (2026-09-30, 2차 2단계)

- inbox.md 체크박스 문법: `- [ ] 내용 @due(YYYY-MM-DD)|@today|@tomorrow`, `@someday`, `@waiting(누구) @since(날짜)`, `@project(이름)`. 파서 `parse_tasks`, 묶음 `bucket_tasks`(오늘=마감 지남·오늘 / 이번 주=7일 안 / 언젠가=@someday·마감 없음 / 기다림=@waiting, 완료 최근 5)
- 파생 할 일 `derived_tasks`: 결정 되돌아볼 날(7일 안, kind decision), 자동화 실패·지연(kind automation, 멈춤 제외), 일정 준비 미완(kind prep, 7일 안, 항목별). `/api/tasks` = inbox + 파생을 묶음별로. `/api/today`에 `tasks{counts, today, waiting}`; `inbox` 문자열 목록은 오늘 묶음의 직접 항목으로 유지
- 쓰기 `POST /api/task`(토큰): add(text, due|someday|waiting|project) · check(line, done) · move(line, to today|tomorrow|week|someday|clear|날짜; 다른 태그 유지) · remove(line). CLI `task add|list|done|undo|move|remove`
- 보드: 새 섹션 「할 일」(추가 폼 + 4열 유리 패널, 체크·호버 시 오늘/내일/언젠가/삭제, 파생 항목은 종류 배지와 원본 링크), 히어로 할 일 카드는 오늘 묶음 요약. 코어: "~ 할 일 추가"/"할 일 추가: ~" 음성 등록(끝에 오늘/내일이면 마감), "할 일" 질문은 오늘 묶음으로 답
- 테스트 +4, 총 130

## v0.11 동선과 출발 알림 (2026-09-30)

- 일정 노트 `## 동선` 절: `- HH:MM 내용 (…NN분)` 단계, 여러 날 계획은 `### MM-DD`(또는 YYYY-MM-DD) 소제목으로 날짜 전환. `parse_steps` → `note.steps[{day,time,text,minutes,line}]`
- `attach_event_notes`가 `agenda.steps_today`(오늘)·`steps_upcoming`(20개)을 붙임(같은 노트 중복 제외). `/api/today`·`/api/agenda`에 포함
- 쓰기: `POST /api/event-note {action: step, key, text: "HH:MM …", day?}`, CLI `event step <키> "<HH:MM 내용>" [--day]`
- 보드: 시간표 트랙 아래 동선 마커(가는 막대), 목록 아래 「오늘 동선」 줄, 상세 패널에 「동선」 절(날짜 접두어)과 추가 폼. 코어 브리핑에 "오늘 동선은 …" 문장
- `brain.py remind [--kakao] [--steps-before 10] [--events-before 30]`: 오늘 동선 단계(기본 10분 전)와 시간 일정(30분 전)을 카톡으로. 같은 알림은 `~/.cache/second-brain/reminded.json`으로 하루 한 번. launchd `com.secondbrain.remind`(600초 주기). 위젯 brain-remind(운영팀)
- 테스트 +3, 총 133

## v0.12 미팅 준비·두뇌 강화·저녁 마감 (2026-09-30)

- 관련 기록(미팅 준비): 오늘·3일 안 일정마다 제목+참석자+장소로 볼트 검색(`dash_search`) 상위 3건 → `event.related[{path,title,type,snippet}]`, `event.prep`(관련 기록·노트·참석자·회의/미팅/면접/상담/발표/인터뷰 제목). 보드 「지금 볼 것」에 「준비할 일정」 카드(관련 기록 링크), 상세 패널 「관련 기록」 절. 코어 "준비/미팅/면접" 질문에 관련 기록 이름으로 답
- `ask_assistant(question, today, vault)`: [오늘 상태] + [기억](볼트 검색 상위 5 발췌 220자)로 헤드리스 Claude에 묻고, 근거 기록 제목을 밝히도록 지시. 응답에 `memory_used`. 코어 힌트 줄에 "참고한 기록"
- `POST /api/config`(토큰, 화이트리스트 assistant_name 1~24자). 코어에서 "이름은 자비스로 해/너를 자비스라고 부를게"로 즉시 변경
- 저녁 마감: `task_action carry`(오늘 묶음 미완료를 전부 내일 마감으로, 기다림 제외), CLI `task carry`, 코어 "남은 할 일 내일로". `brief --evening [--kakao]` = "[날짜 저녁 마감] 남은 할 일 N: … → 내일로 옮길까요? / 내일 첫 일정". launchd `com.secondbrain.evening` 21:30. 위젯 brain-evening
- 테스트 +3, 총 136

## v0.13 지금 실행·멈춤·재개 (2026-09-30)

1차에서 킵해 둔 버튼. 사장님 "멈추지 말고 계속" 지시로 마지막 항목으로 구현.

- `widget_commands(widgets)`: 위젯 source(로그 경로)를 crontab `>> 로그` 명령 또는 launchd plist `StandardOutPath`와 짝지어 실행 방법을 얻음 → `{id: {kind: cron, cmd} | {kind: launchd, label}}`. 같은 로그를 쓰는 위젯도 실행법을 공유
- `run_widget`: cron은 같은 명령을 `/bin/sh -c`로 백그라운드(새 세션, stdout→그 로그), launchd는 `launchctl kickstart -k gui/<uid>/<label>`. `SECOND_BRAIN_RUN_DRY=1` 또는 `dry: true`면 실행 없이 방법만
- `set_widget_state`: widgets.json의 `state`를 active|paused로 저장(크론 줄은 건드리지 않음. 이 환경에서 crontab 쓰기가 멈추는 문제도 있어 launchd/크론 자체 정지는 사용자 몫)
- `POST /api/widget {action: run|pause|resume, id}`(토큰). run은 widgets.json `"allow_run": true`일 때만(기본 false). 성공 시 위젯 캐시 무효화. `/api/widgets`에 `runnable`, `/api/office`에 `runnable{id: kind}`
- 보드: 자동화 행 호버 시 「지금 실행」(실행 가능할 때) · 「멈춤/다시 켜기」. 사무실: 책상 호버 시 ▶, 누르면 팀장의 한마디로 결과 안내, 5초 뒤 갱신(가동 감지로 타이핑 시작)
- 테스트 +3, 총 139. 실사용: brain-remind launchd를 버튼으로 실행해 로그 갱신 확인

## v0.14 agents 명령 (2026-09-30)

- `brain.py agents install [brief remind evening] [--dry-run] [--force]` / `status` / `remove`: 아침 브리핑(07:00)·출발 알림(600초)·저녁 마감(21:30) launchd 사용자 에이전트를 `~/Library/LaunchAgents/com.secondbrain.*.plist`로 생성·bootstrap. 파이썬·brain.py 경로는 실행 중인 것을, `CLAUDE_CONFIG_DIR`은 설정돼 있으면 그대로 넘김. 로그는 `~/.cache/second-brain/agents/<name>.log`, 위젯(brain-brief 등, 운영팀) 자동 추가. 이미 있으면 건너뜀(`--force`로 덮어쓰기). `SECOND_BRAIN_LAUNCH_AGENTS`로 폴더 대체(테스트)
- brain-setup 스킬에 「비서 설정」 절: 캘린더·이름·카톡·에이전트·allow_run·화면 안내
- 사장님 환경의 기존 에이전트(k-skill-cron 로그 경로)는 그대로 두고 건너뜀
- 테스트 +3, 총 142

## v0.15 메일 (2026-09-30, 2차 3단계, 기본 꺼짐)

- `scripts/mailer.py`(stdlib imaplib, 읽기 전용): IMAP SSL, 폴더 readonly select, `BODY.PEEK[HEADER.FIELDS (...)]`로 헤더만(본문 안 읽음), 최근 N일·최대 M통, 보낸편지함도 읽어 답장 여부 판단. 10분 캐시 `~/.cache/second-brain/mail.json`, 실패 시 캐시 stale 폴백
- 분류 `triage`: reply(사람이 보낸 안 읽은/깃발), waiting(내가 보낸 메일 중 In-Reply-To로 답이 오지 않은 것), info(List-Id/Unsubscribe·Auto-Submitted·Precedence bulk·noreply류 주소·광고/뉴스레터/영수증/인증번호 제목). `bookings`(예약·booking·e-ticket 제목) 별도
- 설정 `config.json mail {host, port, user, password_file, folder, sent_folder, days, max, aliases, web}`. 비밀번호는 별도 파일(앱 비밀번호, 600). CLI `mail add <주소> --password-file F [--host] [--sent-folder]`(gmail/naver 호스트 자동) · `test` · `list` · `remove`
- `/api/mail`, `/api/today`에 `mail{configured,status,counts,reply[3],waiting[3],sentence,web}`. 보고 문장 "답장할 메일 N통, 먼저 …", 카톡 조각 "메일 답장 N·대기 M". 보드 「메일」 3열(설정됐을 때만 표시, 클릭 시 웹메일 rfc822msgid 검색으로 열기). 코어 "메일" 질문 답변
- Mail.app(JXA) 방식은 사장님 환경에 Mail.app이 없어 보류. 테스트 +3, 총 145(가짜 비밀번호로 실접속 실패 → status fail 경로 포함)

## v0.16 데모 모드 비서 샘플 · 코어 선제 알림 (2026-09-30)

- `serve --demo`가 비서 기능까지 보여준다: `build_demo_assistant`가 inbox 할 일 5개(오늘·내일·언젠가·기다림·완료), 일정 노트 2개(팀 주간회의 준비·메모, 제주 출장 여러 날 동선 4단계), 데모 캘린더 ICS(오늘 3·내일 1·3일 뒤 출장+항공), 데모 위젯 로그 4개(정상 2·실패 1·CSV 1)를 `볼트/_demo/`에 만든다. `CONFIG_OVERRIDES`(파일 설정 위 덧씌우기, 저장 안 함)로 서버가 데모 캘린더·위젯·이름(데모 비서)을 쓴다. `_demo`는 SKIP_DIRS. 데모 노트 수 29→31(일정 노트 2)
- 코어 선제 알림 `proactive(t)`: 60초 갱신마다 새로 실패한 자동화, 15분 안 시작하는 일정, 10분 안 동선 단계를 말한다(한 번씩). 브라우저 자동재생 정책 때문에 첫 포인터·키 입력 뒤부터 소리, 그 전엔 자막만. 힌트 문구로 안내
- 코어 명령 추가: "내일 뭐 있어"(내일 일정), "다음 일정"
- 테스트 +2, 총 147

## v0.17 직원 근무 그래프 (2026-09-30)

- `widget_history(w, days, today)`: 로그 위젯의 끝 2MB에서 `20YY-MM-DD`가 있는 줄을 날짜별로 세어 runs, 그중 fail_pattern 줄을 fails로. `GET /api/widget-history?id=&days=`(기본 14). 위젯 결과에 `status_cfg`(fail_pattern 전달용)
- 사무실 상세 패널에 최근 14일 막대(실행 파랑·실패 붉음, 없는 날은 옅은 바닥선), 합계. 테스트 +2, 총 149

## v0.18 백업·사무실 라이트·마감 정리 (2026-09-30)

- `brain.py backup [--dest] [--keep 14]`: 볼트를 `~/.cache/second-brain/backups/brain-YYYYMMDD.zip`으로(같은 날은 덮어씀, `.git` 등 SKIP_DIRS 제외), 오래된 것은 keep개만. `agents install backup`으로 매일 23:00 launchd(위젯 brain-backup, ok_pattern "백업 완료"). 사장님 환경에 설치·첫 백업(67파일 103KB)
- 사무실 라이트 테마(보드의 brain-theme 저장값·`?theme=` 따름), 이번 주 격자에 동선 표시, 카톡 문구 개선, today inbox 표시 정리, README 배너(힉스필드)
- 테스트 150. 오늘 하루 v0.3 → v0.18: 커밋 40여 개, 릴리스 v0.12.0·v0.16.0

## v0.19 볼트 정제 enrich (2026-09-30)

- `brain.py enrich [paths…] [--all] [--force] [--limit N] [--dry-run]`: 헤드리스 Claude(`claude -p`, 또는 `SECOND_BRAIN_ASK_CMD`)가 노트 6개씩 묶어 제목(40자 명사구)·요약(2~3문장)·태그(3~5)·관련 링크(카탈로그 stem 중 최대 3)를 JSON으로 제안하면 적용. 원래 제목은 `original_title`에 보존, `claude-memory` 태그는 걷어냄, 존재하지 않는 stem 링크는 버림. 기본 대상은 `imported_from`가 있고 `summary`가 없는 노트
- `summary`는 검색 가중치 2.0으로 인덱싱되고 노트 패널 맨 위에 강조 표시
- 응답 파싱: ```json 펜스·앞뒤 잡담 허용(첫 `[`/`{` ~ 마지막 `]`/`}`)

## v0.20 사무실 직원 속마음 (2026-09-30)

- 상태 한 줄이 MZ 톤으로: 가동=「일하고 있어요」, 정상=「놀고 있어요 (할 일 다 함)」, 오래됨=「일하는 척하고 있어요」, 실패=「멘붕이에요」, 주의=「눈치 보고 있어요」, 멈춤=「휴가 중이에요」, 미등록/첫 근무 전=「첫 출근 준비 중이에요」. Claude 작업은 working/done/blocked → 「일하고 있어요/퇴근했어요/막혀서 멍 때리는 중」
- 일 관련 말풍선(최근 로그·실패 메시지)이 없는 직원은 속마음 말풍선(점선 이탤릭): 공통 풀 + 상태별 풀 + 시간대(아침·점심·오후·저녁·밤) + 요일(월·금·주말) 풀을 섞어 `hash(id+20초 슬롯)`으로 뽑아 20초마다 교대. 상태는 진짜 데이터, 문구만 재미
- 팀장 한마디도 사고 없을 때 캐주얼 문구 4종 회전. 상태 라벨 아래 근무 시간표는 별도 줄
- 테스트 155

## v0.21 일정 준비 제안 (2026-09-30)

- `brain.py prepare [--days 7] [--key K] [--force] [--dry-run]`: 7일 안 일정 중 준비 항목이 3개 미만이거나 동선이 없는 것마다 Claude에게 일정 제목·시간·장소·설명·참석자·기존 항목·관련 노트를 주고 `{prep 3~6, steps "HH:MM 내용" 0~4, memo 한 줄}`을 받는다. 결과는 노트에 쓰지 않고 `~/.cache/second-brain/suggestions.json`에 `pending`으로 보관(한 일정에 한 번, 지난 일정은 자동 제외)
- 보드: `/api/today.suggestions{count, by_key}` → 「준비할 일정」에 「제안 N」 배지, 일정 패널에 「{비서 이름}의 제안」 블록(체크박스 기본 선택, 「채택」은 고른 항목만 `event_note_action`으로 준비/동선/메모에 기록, 「무시」는 dismissed). `POST /api/suggestion {action: accept|dismiss, key, indexes?}`, `GET /api/suggestions`
- 카톡 브리핑에 「준비 제안 N건(보드에서 채택)」 한 줄. launchd 에이전트 `prepare` 매일 06:40(아침 브리핑 20분 전), 위젯 ok_pattern에 「준비 제안|제안할 일정이 없어요」
- 원칙: 읽기는 자동, 쓰기는 사람 승인. 예약번호 등 고유 사실은 만들지 않도록 프롬프트에 명시, 'HH:MM' 형식이 아닌 동선은 버림
- 테스트 159

## v0.22 하루 일지 (2026-09-30)

- 타입 `journal` 추가 → `journal/YYYY/YYYY-MM-DD.md`(하루 한 장, 프론트매터 `journal_date`·`summary`), 본문 `## 오늘`(3~5줄) / `## 잘한 것` / `## 내일 첫 일` / (`## 일정`, `## 자동화`)
- `journal_material`: 오늘 만든 노트(요약 포함)·일정 노트에 오늘 남긴 메모·오늘 일정·완료한 할 일(오늘 일지에만)·남은 할 일(오늘→이번 주→언젠가)·자동화 실패/지연·Claude 배경 작업. 재료가 없으면 쓰지 않음
- `brain.py journal [--date] [--force] [--kakao] [--dry-run]`; `brief --evening --journal`이 먼저 일지를 쓰고(--force) 마감 카톡 끝에 「일지: …」 한 줄. evening 에이전트 인자에 `--journal` 포함
- 프롬프트 원칙: 재료에 없는 일 금지, 1인칭 담담한 평서문, 이모지·자기 칭찬 없음. 보드 타입 라벨 「일지」·색 추가
- enrich 후속: `claude-memory` 표식 태그는 정제 결과에서도 제거(실제 볼트 18개 정리)
- 테스트 164

## v0.23 주간 회고·의미 기반 링크 제안 (2026-09-30)

- `brain.py retro [--days 7] [--force] [--kakao] [--dry-run]`: 지난 N일 재료(새 노트+요약, 결정의 「결정/이유」 절, 14일 안 되돌아볼 결정, 일지의 「오늘」 줄, 프로젝트별 건수, 고아, 자동화 실패 횟수(widget_history))를 Claude에게 → `{week 3~5, patterns 1~2, questions 정확히 3(열린 질문, 결정 지목), next_week 1~3, kakao}` → `journal/YYYY/날짜-weekly.md`(journal_kind weekly, 질문·다음 주는 체크박스). 월 09:00 launchd 에이전트 `retro`(위젯 stale 8일)
- `review --semantic [--limit 25]`: 고아·최근 노트를 후보로 카탈로그(stem|제목|요약)와 견줘 Claude가 내용상 관련 쌍(후보당 최대 2)을 고름. 존재하지 않는 stem·이미 연결된 쌍·중복은 버림, 이유는 「의미: …」 접두. 적용은 여전히 사람이 `link A B`. brain-review 스킬 갱신
- journal target_path: 제목이 「… 주간 회고」면 `-weekly` 접미
- 테스트 169

## v0.24 코어 대화 기억·사무실 직원 한 줄 (2026-09-30)

- 코어: `/api/ask` 문답을 `~/.cache/second-brain/core_log.jsonl`에 남기고, 오늘 최근 4턴을 「[최근 대화]」로 다음 질문에 붙여 이어 말하기가 된다. 답 아래 「이 대화 기억해」 버튼 또는 "기억해/저장해" 명령 → `POST /api/remember {question, answer, title?}` → `notes/…` 노트(태그 코어·대화, 「## 질문 / ## 답 / 맥락」). 오늘 일지 재료에 `core_chat`(최대 6턴) 포함
- 사무실: 책상 상세에 「이번 주 한 줄」 버튼 → `POST /api/widget {action: brief, id, force?}` → `staff_brief`: 로그 끝 80줄 + 7일 실행/실패 수를 Claude에게 → `{did, issue, mood}`. `staff_briefs.json`에 하루 1회 캐시(「다시」로 force). 사무실도 세션 토큰을 미리 받는다
- 테스트 172

## v0.24.1 영문 README·직원 한 줄 집계 보정 (2026-09-30)

- `README.en.md` 추가(한국어 README와 상호 링크). UI·프롬프트는 한국어 우선이라고 명시
- staff brief: 로그 줄에 날짜가 없어 `widget_history`가 0을 내면 runs_7d/fails_7d를 비워서 보낸다(Claude가 "집계 불일치"를 문제로 짚던 오탐 제거). README의 「Claude가 대신 쓰는 것」 항목 정리

## v0.25 회고 질문 노출·첫 문장 보정 (2026-09-30)

- `retro_questions`: 14일 안 가장 최근 주간 회고의 「되돌아볼 질문」 중 미체크 항목(최대 3)을 `/api/today.retro_questions{text, line, path, date}`로. 보드 「되돌아볼 결정」 카드 아래 체크박스(체크는 기존 `event-note check`로 그 노트 줄을 토글), 코어 "결정/회고" 질문에 첫 질문을 읽어 줌
- 보드 첫 문장: 아직 첫 기록이 없는(unknown) 자동화는 실패·오래됨과 묶지 않고 「새로 등록한 N개는 아직 첫 기록이 없어요」로 따로 말함
- 테스트 178

## v0.26 doctor 점검·첫 문장 챙길 것 (2026-09-30)

- `brain.py doctor`: 볼트(노트 수·요약 수·정제 대기)·Python·Claude CLI(ask_cmd 실행 파일)·캘린더(소스·ICS 주소 파일 존재)·카톡 헬퍼·자동화 위젯(문제 목록)·알림 에이전트(loaded 수)·캐시 폴더를 ✓/✗ + → 고치는 법으로. 외부 호출 없음. ✗가 있으면 종료 코드 2. brain-setup 스킬 0단계
- 보드 첫 문장 「챙길 것」에 채택 대기 준비 제안 N건·회고 질문 N개 추가. brain-today 스킬에 준비 제안·회고 질문 줄

## CI

- `.github/workflows/tests.yml`: push(main)·PR마다 ubuntu-latest+macOS-latest에서 `python -m unittest discover -s tests -q` 실행. Python 3.9~3.13 매트릭스, macOS는 비용 절감 위해 3.12·3.13만
- `scripts/brain.py`에 `from __future__ import annotations` 추가(3.9 방어용). `agenda.py`/`mailer.py`는 이미 있었고 `str | None` 같은 PEP 604 어노테이션도 그 덕에 3.9에서 안전함을 확인
- match문·zip(strict=)·PEP 695 제네릭 등 3.10+ 전용 런타임 문법은 scripts/*.py에서 발견되지 않음
- 테스트 180

## v0.27 노트 캐시 (2026-09-30)

- `load_notes(vault)`가 요청마다 전체 마크다운을 다시 읽던 것을 프로세스 생존 기간 캐시로 바꿈. 캐시 키는 볼트 지문(노트 디렉토리를 `os.walk`+`stat`만으로 훑은 (경로, mtime_ns, size) 튜플) — 내용을 읽지 않아 매 호출마다 검사해도 저비용이고, 생성·수정·삭제·이름변경(Obsidian 등 외부 편집 포함)을 자동으로 감지한다. `write_note()`가 모든 쓰기 경로(create_note/import/relink/enrich 등)의 단일 통로라 그 안에서 `invalidate_notes_cache()`를 한 번 더 호출해 지문 방식 위에 belt-and-braces를 얹었다
- 노트 300개 볼트에서 `dash_today()` 측정: 캐시 전 평균 145.6ms/호출·`load_notes` 3회·`Note` 실제 파싱 903회(3×300) → 캐시 후 평균 29.7ms/호출(첫 호출만 콜드 67ms, 이후 4회는 ~20ms)·`load_notes` 호출 수는 여전히 3회지만 파싱은 첫 호출 301회 이후 0회
- `SECOND_BRAIN_NO_NOTE_CACHE=1`로 캐시를 완전히 끌 수 있다(테스트용). `tests/test_notes_cache.py` 추가(생성/외부 편집/삭제 즉시 반영, 캐시 적중 시 내용 동일+Note 재사용, 환경변수로 비활성화). 테스트 185
## v0.27 사무실 모바일·접근성 (2026-09-30)

- `web/office.html`에 `max-width: 720px`/`420px` 미디어쿼리 추가: 방(rooms) 1열, 책상(desks) 390px 기준 3열, 말풍선은 책상 폭(92%) 기준 한 줄 말줄임(방 밖으로 안 넘침), 상단바(topbar)는 줄바꿈되어 상태 램프가 아래 줄로, 활동 로그 항목은 줄바꿈 허용
- 우측 상세(`.detail`) 패널이 720px 이하에서는 하단 바텀시트(전체 폭, `bottom:0`, `max-height:70vh`, 스크롤 가능)로 전환, 닫기·Escape는 기존 그대로 유지
- `.runbtn`(role=button span)에 Enter/Space 키보드 실행 추가 — 클릭 핸들러를 `runWidget()` 함수로 분리해 클릭·keydown 모두에서 재사용, `.desk`에는 accent색 `:focus-visible` 아웃라인 추가
- `#leadSay`에 `aria-live="polite"` 추가, `prefers-reduced-motion: reduce`일 때 0.5초 타이핑 프레임 `setInterval`을 아예 예약하지 않도록 JS 가드 추가
- Playwright로 390×800 뷰포트에서 `document.documentElement.scrollWidth === 390` 확인(상세 패널 오픈 상태 포함), `tests.test_office` 통과 유지
## v0.27 스킬 brain-journal·brain-doctor

- `brain-journal` 스킬 추가: 기존 `journal`/`retro` CLI를 대화로 노출. 오늘 일지 작성·조회, 주간 회고 작성 후 되돌아볼 질문 3개를 하나씩 물어 답을 `new --type note`로 원문 그대로 저장하고 회고 노트와 `link`. 체크박스 토글은 CLI가 없어 보드 안내만 한다.
- `brain-doctor` 스킬 추가: 기존 `doctor` CLI를 대화로 노출. ✗ 항목마다 fix가 `brain.py` 명령이면 승인 후 대신 실행(`enrich`는 별도로 한 번 더 확인), 외부 설정은 안내만.
- README.md·README.en.md의 "그 밖에" 문장에 두 스킬 추가.
