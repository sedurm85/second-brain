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
