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

## v0.27 주간 리포트 /report (2026-09-30)

- `GET /api/report?days=7` → `dash_report(vault, days, today, widgets)`: 기간 숫자 띠(노트·결정·일지·자동화 실행/실패), 새 기록(최대 30, 최신순), 결정(「결정/이유」 절 발췌·되돌아볼 날), 일지 요약, 기간 안 가장 최근 주간 회고(이번 주·눈에 띄는 것·되돌아볼 질문·다음 주 체크박스 상태), 로그 위젯의 `widget_history` 실행/실패 집계(멈춤 제외), 할 일 현황(`dash_tasks`의 counts), 프로젝트별 건수
- `web/report.html` (`/report`): A4 인쇄용 한 장. `@page`+`@media print`로 툴바 숨김, 헤더(기간·비서 이름·생성 시각)·숫자 띠·이번 주·결정 카드·질문·다음 주(체크 표시, 읽기 전용)·일지 요약·새 기록 2열·자동화 표·할 일 현황 순. 화면에서는 7/14/30일 선택 + 인쇄 버튼, 390px에서도 1열로 읽힘
- index.html 상단 내비게이션에 「리포트」 링크 추가(사무실 옆)
- 테스트 188
## v0.27 알림 채널 통합·macOS 알림 센터 (2026-09-30)

- `notify(text, title, cfg)` 하나로 `brief`/`remind`/`journal`/`retro` 네 곳의 개별 카톡 발송 코드를 통합. 채널은 config `notify_channels`(기본 `["kakao", "center"]`) 순서대로 "실제로 쓸 수 있는 첫 채널 하나"만 시도한다 — 카톡이 있으면 계속 카톡만 쓰고, macOS 알림 센터를 매번 같이 띄우지 않는다
- 카톡 헬퍼(`kakao_helper_path`)가 없을 때만 macOS에서 `osascript display notification`으로 자동 대체(`notify_center: false`로 끌 수 있음). 텍스트는 `_osa_quote`로 `"`·`\` 이스케이프
- `--kakao` 플래그는 그대로 두고 `--notify` 별칭 추가(`brief`/`remind`/`journal`/`retro`), 도움말을 "알림 보내기(카톡 헬퍼 → 없으면 macOS 알림 센터)"로 갱신. 새 `notify test [메시지]`로 실제 발송 없이(`SECOND_BRAIN_NOTIFY_DRY=1`) 또는 실제로 채널별 결과를 바로 확인
- 채널이 하나도 없을 때만 "알림 채널이 없어요(카톡 헬퍼 또는 macOS 알림 센터)" 로그 + 종료 코드 2, 그 외에는 어느 채널이든 성공하면 성공 처리
- 테스트 193 (tests/test_notify.py 13개 추가)

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
## v0.27 보드 「일지」 섹션 (2026-09-30)

- `dash_journals(vault, limit=14)` + `GET /api/journals?limit=N`(기본 14, 최대 60): 하루 일지(`## 오늘`)와 주간 회고(`## 이번 주` + `## 되돌아볼 질문` 체크박스 전부)를 최신순으로 나눠 `{daily, weekly}`로 반환. `journal_kind: weekly` 또는 `-weekly` stem으로 구분
- 보드: 내비 「일지」 + `#journals` 섹션, 두 열(하루 일지 / 주간 회고). 하루 일지는 요약 한 줄 + 펼치기로 불릿, 회고는 요약·불릿·질문 체크박스(기존 `event-note check`로 토글 후 재조회). 제목을 누르면 기존 노트 패널이 열림. 빈 상태는 「아직 일지가 없어요. 21:30 저녁 마감이 첫 일지를 써요.」
- 데모(`serve --demo`): 어제 하루 일지(4줄)·오늘 주간 회고(미체크 질문 3개)를 볼트에 추가. 데모 제안(suggestions)은 캐시 디렉터리가 볼트별로 격리되지 않아(`agenda.cache_dir()`가 실제 `~/.cache`를 씀) 생략
- 테스트 184
## v0.27 날씨 모듈 (2026-09-30)

- 새 독립 모듈 `scripts/weather.py`(stdlib, API 키 불필요): Open-Meteo 지오코딩+예보. `geocode`(장소명 정규화·KR 우선·구어체 실패 시 접미사/2어절 fallback, 미스 포함 영구 캐시·7일 재시도), `forecast`(3시간 캐시), `describe`(WMO 코드→한글), `weather_for`(요약+우산/추움/더움 플래그), `weather_sentence`(카톡 한 줄). `SECOND_BRAIN_OFFLINE=1`로 네트워크 차단, 모든 예외는 삼켜 None/빈 값. CLI `weather.py <장소> [날짜] [--json]`
- 이번 워크트리(`wt/weather`)에서는 `brain.py`를 건드리지 않았다(병합 충돌 방지) — 연결 지점은 `docs/weather-integration.md`에 정리(attach_event_notes/kakao_brief/today_human/prepare_prompt/보드 이벤트 패널)
- 실네트워크 확인: 정식 지명("김포국제공항","서울특별시")은 조회되나 구어체("서울","제주도","김포공항")는 Open-Meteo 지오코딩 데이터에 없어 못 찾음 — 모듈 로직 문제 아닌 API 데이터 한계, 필요 시 별칭 테이블은 후속 과제로 문서에 남김
- 테스트 +24, 총 204
## v0.27 복구(restore) (2026-09-30)

- `brain.py restore [zip] [--list] [--dry-run] [--replace] [--no-safety-backup]`: `backup`의 역연산. zip 인자 없으면 백업 폴더의 최신 zip을 씀, `--list`는 백업 목록(이름·파일 수·용량·시각)만 보여줌
- 안전장치: 절대경로·`..`·볼트 밖으로 탈출하는 멤버(zip-slip)가 있거나 `BRAIN.md`/`notes/`가 없는(볼트 백업이 아닌) zip은 거부
- 덮어쓰기 전 `backup_vault`로 `brain-YYYYMMDD-before-restore-HHMMSS.zip` 안전 백업을 먼저 만듦(`--no-safety-backup`로 건너뛸 수 있음)
- zip에 없는 기존 파일은 기본은 보존, `--replace`면 삭제. 복구 후 인덱스 재생성·git 커밋
- `--dry-run`은 생성/덮어쓰기/보존 개수만 보여주고 디스크를 건드리지 않음

## v0.28 날씨 통합 (2026-09-30)

- `weather.py`를 `brain.py`에 연결: `attach_event_notes`가 `location` 있는(없으면 "제주도"처럼 제목이 장소 이름인 종일 일정) 오늘/7일 이내 일정에 `e["weather"]`를 부착(실패는 항상 None, 일정 표시는 안 막음). `kakao_brief`는 내일(없으면 오늘) 일정 날씨가 우산/추움/더움일 때만 한 줄 추가, `today_human`은 "날씨: <장소> <요약>" 줄(최대 2건), `prepare_prompt`는 ctx에 `weather`를 실어 Claude가 우산·겉옷을 제안하게 함
- 구어체 지명 지오코딩 갭 해결: `weather.candidates(place)`가 원문→별칭(공항/섬)→"X공항"→"X국제공항"→"X도"→"X"/"X시"→"X시/군/구"→"X"→시설 접미사 제거 순으로 검색어를 만들고, 아무 규칙도 안 맞고 한글 3자 이상이면 최후 수단으로 앞 2글자를 `approx: True`로 시도("김포공항"→김포국제공항, "제주도"→제주시)
- 보드(`web/index.html`): 이벤트 패널 「어디」 다음에 「날씨」 행(요약·우산·근접 표시), 「다가오는 일정」 목록에 작은 날씨 요약 추가
- 실네트워크 확인: `weather.py 김포공항 2026-10-03`, `weather.py 제주도 2026-10-03` 모두 성공(각각 김포국제공항/제주시로 매칭)
- 테스트 +18, 총 236
- 테스트 185
## v0.28 이번 주 계획·검색 요약 (2026-09-30)

- `week_plan(notes, today, days=14, limit=5)`: 가장 최근 주간 회고의 `## 다음 주` 체크박스 중 미체크 항목. `retro_questions`와 같은 방식으로 `dash_today`에 `week_plan` 키로 노출, 체크는 기존 `event-note check`로 처리
- 보드(`web/index.html`) 「할 일」 카드에 「이번 주 계획」 블록 추가(회고 질문과 같은 `.rq`/`data-rq-path` 재사용), 히어로 문장에 "이번 주 계획 N개" 추가. 코어(`web/core.html`) "할 일" 규칙이 계획 있으면 첫 2개를 한 문장으로 덧붙임
- `dash_search`가 각 검색 결과에 `summary`(프론트매터 summary, 160자 절단) 키를 추가. `/api/search`는 그대로 통과. 보드 검색 결과 목록에 제목 아래 요약(무채색)·스니펫 순으로 표시
- 테스트 +8, 총 226
## v0.28 사무실 팀 KPI (2026-09-30)

- `office_kpis(widgets, today=None, days=7)` 추가(`dash_office` 바로 앞): 멈춘(state=paused) 위젯과 kind≠log 위젯을 뺀 활성 로그 위젯을 `team_for(w)` 기준으로 묶어 팀별·전체 7일 실행·실패·성공률(0-100 정수, 실행 0이면 None)·일별 막대(`days`)·소속 인원(`members`)을 집계. `/api/office` 응답에 `"kpis": {"teams": {...}, "total": {...}}`로 포함
- `web/office.html`: 각 방(`.room-head`)에 `<span class="kpi">`로 「7일 실행 N · 실패 M · 성공률 P%」 + 7칸 인라인 SVG 막대(실행=accent, 실패=crit 겹쳐 그림)를 추가, 실행 0이면 「아직 기록 없음」만 표시. 상단바 오른쪽 램프 앞에 `#kpiTotal`(회사 전체 스트립)을 추가, 420px 이하에서는 「성공률 P%」만 남는 축약형으로 전환
- 성공률 색상은 기존 토큰 재사용: 95% 이상 `--accent`, 80~94% `--warn`, 80% 미만 `--crit`. `.room-head`에 `flex-wrap`을 줘 좁은 화면에서 KPI 줄이 아래로 밀려도 방 밖으로 넘치지 않게 함
- `tests/test_office_kpi.py` 6건 추가(팀별 집계, 기본 팀 라벨이 `dash_office`와 일치, paused 제외, 전체 합산, 실행 0→rate None, `/api/office`에 kpis 포함). 전체 스위트 191 통과
- Playwright로 1280×800/390×800 두 뷰포트에서 `.kpi` 요소 존재 및 `scrollWidth`가 뷰포트를 넘지 않음을 확인(가로 스크롤 없음)
## v0.28 보드에서 노트에 기록 (2026-09-30)

- `note_append_action(vault, body)`: 보드 노트 패널에서 어떤 노트(note/idea/decision/project/journal…)든 열어 둔 채로 메모·태그·할 일을 바로 추가. `memo`/`todo`는 `_append_section`으로 `## 메모`/`## 할 일` 절 끝에 붙이고(없으면 새로 만듦, decision의 `결정`/`이유` 절은 건드리지 않음), `tag`는 프론트매터 `tags`에 중복 없이 추가(`#` 제거, 최대 12개). 경로는 `safe_vault_path`로 검증하고 `BRAIN.md`/`inbox.md`는 거부. `POST /api/note-append`로 노출, 처리 후 `dash_note`로 다시 감싸 반환
- 보드(`web/index.html`) 노트 패널 본문 아래 `.note-actions`: 메모/할 일 모드 선택 + 텍스트 입력 + 버튼 하나, 태그 입력 + 「태그 추가」 버튼. 성공 시 반환된 노트로 패널을 다시 그리고(`renderNotePanel`) `refreshLive(true)` 호출. 기존 `renderMd`는 체크박스 줄의 원본 줄 번호를 추적하지 않아 본문 안 체크박스 클릭 토글은 이번 범위에서 뺐음(하드코딩 금지 원칙)
- 테스트 +11, 총 258
## v0.28 맥 미리알림 읽기

- 새 모듈 `scripts/reminders.py`(stdlib, 읽기 전용): 맥 미리알림 앱을 JXA(`osascript -l JavaScript`)로 읽어 `{id, title, list, due, due_time, priority, notes, completed, url}` 목록으로. EventKit 캘린더와 같은 패턴 — 최초 1회 권한 허용, 권한 없으면 `LAST_ERROR`에 안내(`-1743` → "시스템 설정 → 개인정보 보호 → 미리알림에서 터미널/파이썬 허용"), `<cache_dir>/reminders.json`에 5분 캐시. 테스트는 `SECOND_BRAIN_REMINDERS_CMD`로 osascript를 가짜 스크립트로 대체
- 설정 `reminders: {"enabled": false, "lists": []}`(기본 꺼짐, EventKit과 같은 이유). CLI `brain.py reminders on [--lists A,B] · off · list · test`
- `dash_tasks`가 켜져 있으면 미리알림을 `kind: "reminder"` 항목(읽기 전용)으로 오늘/이번 주/언젠가 묶음에 합침(마감 지났거나 오늘 → 오늘, 7일 안 → 이번 주, 없음 → 언젠가). `task_action`의 `check`는 kind가 reminder면 거부(완료는 미리알림 앱에서). `kakao_brief`는 켜져 있고 개수가 있으면 "미리알림 N" 한 줄 추가
- 보드 「할 일」 4열에서 `kind === "reminder"` 항목은 체크박스 없이 「미리알림」 배지로만 표시(읽기 전용)
- 테스트 +10, 총 228
## v0.28 사무실 직원 채용·이동·퇴사 (2026-09-30)

- `widget_action`에 `add`·`move`·`rename`·`remove` 액션 추가, 전부 새 최상위 플래그 `allow_hire`(widgets.json)로 게이트. `add`는 title 1~40자·id는 `slugify` 재사용(중복이면 `-2`부터)·kind는 `log`·`json`·`csv`·`markdown`만(command는 금지)·source는 기존 `resolve_widget_source`/`ensure_in_home` 규칙 그대로 통과해야 함·ok/fail 패턴은 `_regex`로 컴파일 검증. `remove`는 `brain-`로 시작하는 비서 에이전트 위젯을 거부(`agents remove` 전용 안내). `/api/office` 응답에 `hireable` 키 추가(`runnable` 옆), `/api/widget` POST 핸들러는 기존 캐시 무효화 로직을 그대로 재사용
- `web/office.html`: 상단바에 `hireable`일 때만 보이는 「+ 직원 채용」 버튼 → 제목·경로·kind·팀(datalist)·정상/실패 패턴·지연분을 받는 모달(오버레이+focus, Escape로 닫힘, `window.confirm`/`alert` 미사용). 제출 성공 시 사무실 새로고침 후 「팀장 한마디」에 "OO 님이 OO팀에 입사했어요" 표시. 책상 상세 패널에 「부서 이동」(팀 select+확인)과 「퇴사」(첫 클릭에 "정말 퇴사 처리할까요? 로그 파일은 남아요" 인라인 확인 → 2번째 클릭으로 실행) 추가
- `tests/test_hire.py` 15건: allow_hire 게이트(add/move/rename/remove 전부), add 검증(제목 길이·중복 id·홈 밖 경로 거부·command kind 거부·잘못된 정규식 거부), move/rename/remove, `brain-` 접두 퇴사 거부, `/api/office`의 `hireable`, HTTP POST add → `/api/widgets`에 반영. Playwright로 1280/390 두 뷰포트에서 `allow_hire=false`일 때 버튼 숨김, `true`일 때 채용→반영→부서이동/퇴사 확인 흐름을 실제 브라우저로 확인. 전체 스위트 268 통과
## v0.28 노트 본문 체크박스 토글 (2026-09-30)

- `renderMd(md, opts)`에 `{lineNumbers, lineOffset}` 옵션 추가: `- [ ]`/`- [x]` 줄을 `<li class="task"><label><input data-md-line="N">…</label></li>`로 그리며, N은 원본 `md` 문자열 기준 0-based 줄 번호. 옵션을 안 준 기존 호출부는 출력이 그대로 동일
- 노트 패널(`renderNotePanel`)은 표시용으로 잘라낸 H1 제목 줄 수를 `lineOffset`으로 넘겨, 화면에 보이는 본문과 `dash_note`가 돌려준 원본 `n.body`(=`event_note_action check`가 줄 번호로 참조하는 문자열) 사이 줄 인덱스가 어긋나지 않게 함
- 체크박스 `change` 시 `POST /api/event-note {action:"check", path, line, done}` → 성공하면 `/api/note`로 다시 읽어 패널 재렌더링 + `refreshLive(true)`(회고 질문/이번 주 계획 카드 동기화), 실패하면 체크 상태를 되돌리고 `.note-actions .ev-status`에 오류 표시
- Playwright로 임시 볼트에 체크박스 2개짜리 노트를 만들어 보드에서 검색→열기→체크→파일에 `- [x]`로 저장까지 실기동 확인. 전체 스위트 264 통과
## v0.28 코어: 회사 현황·직원·미리알림

- `web/core.html`의 `answer(q)`에 로컬 규칙 두 개 추가: 「회사/사무실/직원들/자동화 현황/KPI/성공률」은 `/api/office`(60초 캐시, `officeData()`)의 `kpis.total`로 이번 주 실행·실패·성공률을 말하고 실패가 있으면 가장 힘든 팀을 덧붙임(실행 0이면 "아직 첫 기록이 없어요"). 「누가 일해/지금 뭐 하고/일하는 직원/가동」은 `running`으로 가동 중인 직원 이름을, 없으면 위젯 제목의 근무 시각(`nextShiftText`)을, `jobs`의 `working` 개수를 Claude 작업으로 덧붙여 말함. 두 규칙 모두 기존 「사무실/직원 열기」 내비게이션보다 먼저 검사해 우선함
- 기존 「할 일」 규칙에 `tasks.today`의 `kind === "reminder"` 개수를 세어 "미리알림 N개도 있어요" 한 줄 추가. `proactive(t)`에 `suggestions.count`·`retro_questions.length`를 각각 한 문장으로, 기존 `S.spoken` 가드로 페이지 로드당 한 번만 말하게 추가
- HUD 입력창 placeholder에 "회사 현황 / 누가 일해" 예시 추가
- Playwright로 데모 서버에서 `#askForm` 제출 → `#caption`에 "성공률"/"첫 기록", "다음 근무" 문구가 실제로 뜨는 것 확인. `python3 -m unittest tests.test_core` 3건 유지
## v0.28 파서 경계 테스트 (2026-09-30)

- `tests/test_edge_cases.py` 신설(54건): frontmatter 라운드트립(콜론·따옴표·유니코드·BOM·CRLF·본문 속 `---`·닫는 `---` 없음), `slugify`/경로 안전(이모지·구두점만·`..`), `tokenize`/`dash_search`(구두점만·초장문·자모), `parse_tasks`/`TAG_RE`(잘못된 날짜·중첩 괄호·탭 들여쓰기), `agenda.parse_ics`(폴딩·이스케이프·RRULE COUNT/UNTIL·EXDATE·RECURRENCE-ID·CANCELLED), `_tail_lines`, `event_key`(제목에 `|`)
- 실버그 1건 발견·수정: `scripts/brain.py`의 `_tail_lines`가 "잘린 첫 줄 버림" 로직 때문에 창(max_bytes) 전체가 개행 없는 초대형 한 줄로 채워지면 그 유일한 줄까지 버려 빈 리스트를 반환하던 문제. 뒤에 완전한 줄이 남아 있을 때만 첫 줄을 버리도록 최소 수정(정상적인 다줄 로그의 동작은 그대로)
- 나머지는 모두 의도된 동작으로 확인: int/bool 값을 넣으면 quoting 규칙상 문자열로 고정되는 것(타입 보존은 애초에 계약 아님), `@due()` 빈 문자열이 None 대신 `""`로 남는 것(둘 다 falsy라 하위 로직엔 영향 없음), DTSTART가 DTEND보다 나중이어도 보정 없이 그대로 반영, BYDAY의 서열(`2MO`) 접두사는 WEEKLY가 아니면 조용히 무시
- 전체 스위트 328 통과(기존 274 + 신규 54)
## v0.29 사무실 오늘 근무표 (2026-09-30)

- `schedule_for(cron_line, today)`: 크론 5필드(분·시·일·월·요일)를 오늘 날짜로 펼쳐 `["HH:MM", ...]`를 계산. `*`·리스트(`a,b`)·범위(`a-b`)·스텝(`*/n`, `a-b/n`)을 지원하고 요일은 0·7 모두 일요일. 일/요일 둘 다 와일드카드가 아니면 크론 규칙대로 OR 판정. `_launchd_calendar_times`는 launchd `StartCalendarInterval`(dict 또는 list)을 같은 방식으로 펼침(Weekday 0·7=일요일)
- `office_schedule(widgets, cron_lines, plist_dir, today, now)`: 위젯의 source를 크론 로그경로/launchd `StandardOutPath`와 매칭(`widget_commands`와 같은 규칙)해 오늘 슬롯을 만들고, 위젯당 하나뿐인 `updated_at`/`status`로 상태를 근사(과거 슬롯은 `updated_at`이 그 시각 이후면 done, 가장 최근 지난 슬롯이고 status가 fail이면 failed, 그 외엔 due, 미래는 upcoming). `StartInterval`은 슬롯이 아니라 `intervals`(연속 근무 배지)로, 멈춘 위젯은 제외. `next`는 가장 빨리 오는 upcoming 슬롯. `/api/office` 응답에 `schedule` 키로 추가(`dash_office`)
- `web/office.html`: 팀장의 한마디 아래 `#shiftStrip` — 24시간 타임라인(0/6/12/18/24시 눈금 + 지금 위치 표시선), 슬롯은 상태별 색 점(완료=accent·실패=crit·놓침=warn·예정=muted), 인터벌 직원은 하단에 "N분마다 계속 근무" 띠로. hover/focus 시 `data-label`로 "07:00 아침 브리핑 카톡 · 완료" 툴팁. 모바일(≤720px)은 12시간 압축 뷰(평소 6~18시, 그 밖 시간엔 지금을 중심으로 클램프). `schedule.next`가 있으면 팀장 한마디에 "다음 근무는 …분 뒤예요" 한 줄 추가(없으면 기존 `nextShift` 제목 파싱 폴백 유지)
- `tests/test_office_schedule.py` 17건: 크론 필드 파싱(범위·스텝·리스트·요일 0/7), launchd calendar dict/list/Weekday, 요일별 오늘 필터(월/일 고정), 슬롯 상태(done/failed/due/upcoming), next 선정, `/api/office`의 `schedule` 키. 전체 스위트 324 통과
## v0.28 데모 격리·풍부화 (2026-09-30)

- `cmd_serve --demo`를 `setup_demo_isolated()`로 분리: `agenda.cache_dir()`가 읽는 `XDG_CACHE_HOME`을 데모 볼트 안(`<demo>/_demo/cache_home`)으로 먼저 바꾼 뒤 볼트를 만들어, `suggestions.json`·`core_log.jsonl`·`staff_briefs.json`·`weather/`·`agents/`·`backups/`가 실제 `~/.cache/second-brain`을 절대 건드리지 않게 함(`cache_dir()`는 임포트 시가 아니라 호출 시 환경변수를 읽으므로 순서만 맞추면 됨). `SECOND_BRAIN_OFFLINE=1`도 같이 켜 네트워크(날씨)를 원천 차단
- `build_demo_assistant`가 이제 캐시까지 채움: 대기 중 제안 2건(회의·제주 출장), 위젯 2개의 직원 요약(`staff_briefs.json`), 오늘자 코어 문답 3줄(`core_log.jsonl`), 「제주 출장」 장소(김포국제공항)의 지오코딩·예보 캐시(날씨 연동이 붙기 전에도 오프라인에서 바로 풀리도록 미리 심어 둠). 위젯 로그 3개(backup/price/scrape)는 최근 7일치 날짜 줄을 갖도록 다시 씀 → 사무실 KPI 띠·이력 막대가 항상 값을 보임
- `inbox.md`에 연체된 `@due` 항목 1개 추가(오늘 마감 1개와 합쳐 today 묶음 2개), 제주 출장 일정에 `LOCATION:김포국제공항` 추가, 결정→사람·결정→프로젝트 직접 링크 2개를 그래프에 추가
- `tests/test_demo.py` +3(캐시 시딩, 위젯 이력 7일, `setup_demo_isolated`가 가짜 "실제" 캐시 경로를 건드리지 않음), `test_server_uses_overrides`에 suggestions/office kpis/journals/report 검증 추가. `tests/test_demo.py`의 기존 today 묶음 개수(1→2)도 갱신. 전체 스위트 292 통과
## v0.29 widget CLI·brain-office 스킬

- `widget_action(body, widgets, cli=False)`에 `cli` 매개변수 추가: `cli=True`면 `run`/`add`/`move`/`rename`/`remove`의 `allow_run`·`allow_hire` 게이트만 건너뛰고, 홈 경로 규칙·kind 허용 목록·정규식 검사·`brain-` 접두 퇴사 거부 등 나머지 검증은 그대로 유지. 웹 API(`/api/widget`)는 `cli` 없이 그대로 호출해 기존 게이트가 유지됨
- 새 CLI `brain.py widget <action>` (`add · move · rename · remove · pause · resume · run · brief · show · list`): 사용자 자신의 터미널이라 widgets.json의 `allow_hire`/`allow_run` 설정 없이도 바로 채용·이동·퇴사·실행할 수 있다. `show`/`list`는 평가된 상태(`collect_widgets`)와 원본 설정을 함께 보여줌
- 새 스킬 `skills/brain-office/SKILL.md`: "채용 스카우트 로그를 직원으로 등록해줘" 같은 대화로 로그 경로 확인(`test -f`)·ok/fail 패턴 제안(`Read`로 최근 줄 확인)·등록·조회까지 안내, 퇴사 전 한 줄 확인, 끝난 자동화는 삭제 대신 `pause` 권장
## v0.29 브리핑 다듬기

- `evening_brief`: 내일 첫 일정에 붙은 날씨(`attach_event_notes`가 채우는 `weather.umbrella/cold/hot`)가 눈에 띄면 " / 내일 {place} {summary}, 우산"(또는 겉옷/더위) 한 조각을 덧붙인다. `weather_mod.weather_sentence`는 형식이 다르고 cold/hot 문구가 없어 재사용 대신 직접 구성. `cmd_brief --evening`이 쓰던 `ag`엔 이 필드가 없었어서 `attach_event_notes(v, ag)` 호출을 한 줄 추가
- `retro_material`에 `automation_kpi`(`office_kpis`에서 뽑은 전체 runs/fails/rate + 가장 실패가 많은 `worst_team`) 추가, `retro_prompt`엔 fails>0일 때만 patterns에서 안정성을 언급하라는 조건절 추가. `journal_material`엔 오늘 첫 일정의 날씨 요약을 `weather_today`로 얹어 일지가 "비 오는 날"을 말할 재료를 갖게 함
- `today_human`: `suggestions.count`/`retro_questions` 중 하나라도 있으면 "준비 제안 N건 대기 · 회고 질문 M개" 한 줄, `week_plan`이 있으면 최대 2개까지 "이번 주 계획: a, b" 한 줄을 추가하고 출력 줄 수 캡을 6→8로 올림
- `cmd_init`: 사람용 출력에만 "다음: `brain.py doctor`로 점검 → 캘린더 연결(`calendar add ics …`) → 대시보드(`serve`)" 안내 한 줄 추가(`--json`은 그대로 `vault`/`created`만)
- 신규 테스트 19건(`test_evening`+4, `test_retro`+2×3클래스, `test_journal`+2, `test_brain`+7). 전체 스위트 410 통과
- `tests/test_widget_cli.py` 10건: CLI 우회 vs API 게이트 유지, add/move/rename/remove/pause/resume/run --dry, brain-* 퇴사 거부, 홈 밖 경로 거부, `--json` 파싱. 전체 스위트 371 통과(기존 361 + 신규 10)
## v0.29 참석자 → 사람 노트

- `people_index(notes)`: person 타입 노트를 정규화 이름(공백 제거·소문자·끝의 님/씨/선생님/팀장/부장/대표 제거) 키로, 프론트매터 `email:`이 있으면 그 로컬파트도 보조 키로 인덱싱. `attach_event_notes`가 이 인덱스와 `link_graph` 인접 집합을 한 번만 만들어, 참석자가 있고 7일 이내인 일정마다 `e["people"] = [{name, path, matched, mentions}]`(이메일 로컬파트도 매칭, mentions는 그 사람 노트에 링크된 노트 수)를 붙인다
- `person_action(vault, body)`(`POST /api/person`, 다른 쓰기 라우트와 같은 토큰 게이트): `{action:"create", name, event_key?}`로 참석자 이름의 사람 노트를 찾거나(정규화 이름으로 중복 방지) 새로 만들고, `event_key`가 있으면 그 일정 노트(없으면 새로 생성)의 프론트매터 `people` 목록에 위키링크를 추가. `person_note_payload(vault, path, agenda)`(`GET /api/person?path=`)는 `dash_note`에 이 사람이 참석자로 매칭된 일정 목록(`events`)을 덧붙인다(`dash_note` 자체는 그대로 둠)
- `kakao_brief`의 "내일 일정 없음" 문장에서, 내일 첫 일정에 매칭된 참석자가 있으면 " (이름 외 N, 기록 N건)"을 덧붙인다(200자 예산 안에서)
- 보드(`web/index.html`) 일정 패널의 「참석」 행을 칩으로: 매칭된 참석자는 사람 노트로 바로 열리는 버튼, 안 매칭되면 이름 옆에 「사람 노트 만들기」 버튼(클릭 시 `/api/person` 호출 후 그 자리에서 matched/path만 갈아 끼움)
- 테스트 `tests/test_people_link.py` 7건 추가(정규화, 이름/이메일 인덱싱, attach 매칭/비매칭, 생성·중복방지·일정 링크, kakao_brief, HTTP 토큰 게이트). 전체 스위트 314 통과
## v0.29 보드 단축키·명령 팔레트

- `web/index.html`(JS): `/`·`⌘/Ctrl K`로 검색창 포커스(텍스트 선택 포함), `g` 다음 `t/h/w/a/d/l/j/p`(1.2초 이내 체인)로 오늘·할 일·이번 주·자동화·결정·타임라인·일지·프로젝트 섹션 스크롤, `n`은 할 일 추가 입력창 포커스, `r`은 새로 고침, `t`는 테마 전환(`toggleTheme()`로 분리), `?`는 도움말 시트. 입력창(input/textarea/select)에 포커스가 있거나 노트 패널·위젯 모달이 열려 있으면 모두 무시
- 명령 팔레트: 검색창에 `>`를 입력하면(또는 `⌘/Ctrl⇧P`) 노트 검색 대신 명령 목록(오늘로·할 일로·…·코어/사무실/리포트 열기·테마 전환·새로 고침·"할 일 추가: <텍스트>")을 부분일치로 필터링해 보여주고, 기존 화살표 이동·Enter로 그대로 실행. "할 일 추가"는 기존 `taskPost`를 재사용, 별도 "메모" 명령은 적합한 캡처용 API가 없어 넣지 않음
- 접근성: 도움말 시트는 `role="dialog"` + `<dl>` 목록, 토스트(`#scToast`)는 `aria-live="polite"`(alert 미사용), Esc로 검색 결과·팔레트·도움말 모두 닫힘(패널 Esc 동작은 그대로 유지). 토픽바에 "?" 버튼 추가, 390px 폭에서도 레이아웃 유지 확인
## v0.29 Apple Notes 가져오기

- 새 어댑터 `scripts/apple_notes.py`: `fetch_notes(folders, limit, timeout)`가 reminders.py와 같은 패턴(JXA `osascript -l JavaScript`, `-1743` 권한 힌트, `SECOND_BRAIN_APPLE_NOTES_CMD`로 테스트 대체)으로 Notes.app의 계정→폴더→노트를 읽는다. `html_to_markdown()`은 표준 라이브러리 `HTMLParser`로 헤딩/굵게·기울임/리스트/줄바꿈/링크/엔티티를 변환하고, `<img>`/`<object>`는 "(첨부 N개)" 한 줄로 남긴다
- `brain.py import --apple-notes [--folder 이름 …] [--since YYYY-MM-DD] [--dry-run]`: `import_apple_notes()`(`import_path` 옆에 추가)가 노트를 `type: note`, `tags: [apple-notes, 폴더슬러그]`, `imported_from: "apple-notes:<id>"`, `source_modified: <수정일>`로 저장. 같은 id가 이미 있으면 메모 앱의 수정 시각이 저장된 값보다 최신일 때만 본문을 갱신("갱신"), 아니면 "건너뜀"; 새 id면 "생성". Claude 호출 없음(정제는 `enrich`로 별도 안내)
- `write_note`가 그대로 캐시 무효화·`build_index`를 처리하고, `--dry-run`이면 아무것도 쓰지 않는다. 권한 오류(-1743)는 사람 문구+종료 코드 2로 보고
- `tests/test_apple_notes.py` 19건(HTML 변환 11건 + 생성/재실행 스킵/수정 갱신/폴더·since 필터/권한 오류/dry-run 8건). 전체 스위트 436 통과(기존 417 + 신규 19)
## v0.29 검색 연산자·패싯

- `parse_query(q) -> (terms, filters)` 신규: `type:`(반복 OR) · `tag:`(반복 AND, 대소문자 무시) · `project:` · `since:`/`until:`(절대 YYYY-MM-DD 또는 `7d`/`2w`/`3m` 상대) · `has:summary|revisit` · `status:open|decided|superseded` · `is:orphan`(`link_graph` 재사용) · `-단어`(제외) · `"정확한 문구"`(제목·본문 부분일치)를 뽑고, 값이 이상하면 예외 없이 조용히 버린다(실시간 검색 중 타이핑 도중 에러가 나지 않도록). `dash_search`가 filters로 후보를 먼저 좁힌 뒤 남은 terms만 기존 BM25(`search`, 이제 `notes=` 인자로 후보 주입 가능)로 채점, terms가 없으면 생성일 역순
- `dash_search`는 여전히 list이지만(`SearchHits(list)`) `.facets`(`types`/`tags` 상위 8 카운트, limit 적용 전 매치 기준)와 `.applied`(파싱된 filters)를 얹는다 — 기존 호출부·hit 딕셔너리 키(`path`/`title`/`type`/`snippets`/`summary`/`score`)는 그대로. `/api/search`는 `{hits, facets, applied}`로 응답 모양이 바뀜(대시보드·기존 테스트 갱신). `cmd_search`는 `--type`/`--tag`/`--project`/`--since` 플래그를 같은 연산자 문법으로 변환해 query에 덧붙이고, 결과 뒤에 "타입: decision 3 · note 2 | 태그: ai 4, 보안 2" 패싯 줄을 출력(`--json`에는 `facets`/`applied` 키로)
- 보드 검색 결과 드롭다운 맨 위에 패싯 줄 추가: 활성 필터는 제거 가능한 칩(✕, `data-rmchip`), 타입별 카운트 칩(`data-chip`, 클릭 시 `type:x`를 쿼리에 덧붙여 재검색) — 화살표 이동은 `[data-path],[data-cmd]"`만 대상이라 칩은 자동으로 건너뜀. 입력창 아래엔 포커스 시(입력 전) 연산자 힌트 한 줄(`#searchHint`)이 뜨고 타이핑하면 사라짐. `>` 명령 팔레트는 그대로
- 신규 `tests/test_search_ops.py` 38건(parse_query 단위·연산자 조합·facets·filters-only 정렬·하위호환·cmd_search 패싯 줄·`/api/search` 응답 모양). 기존 `test_serve.py`의 `test_search_matches_cli_shape`만 새 응답 모양(`d["hits"]`)에 맞춰 갱신. 전체 스위트 455 통과(기존 417 + 신규 38)
- 검증: `python3 -m unittest tests.test_serve`(21건 통과, 백엔드 무변경), `node --check`로 스크립트 문법 확인, Playwright로 `/` 포커스·`>할 일` 팔레트·`g`→`d` 스크롤·`?` 다이얼로그 열림/Esc 닫힘을 데모 서버(7796)에서 실측 확인
## v0.29 여행 모드

- `is_trip(e)`: 이틀 이상 이어지는 종일 일정, 또는 제목이 "제주도"처럼 지명 그 자체(place title)이거나 여행/출장/휴가 표현이면 여행으로 인식. `attach_event_notes`가 today/upcoming에서 여행마다 `e["trip"] = {days, dates, children, weather}`를 붙이고(`dates`는 exclusive end 보정, `weather`는 날짜별 최대 5건 오프라인 안전 조회), 그 기간 안에 시작하는 다른 일정(항공편 등)의 키를 `children`에 담고 그 일정에는 `e["parent_trip"]`을 붙인다(≤60개 일정 기준 O(n²))
- `prepare_prompt`가 여행이면 예약:/짐:/서류: 접두어 체크리스트(6~10개, `trip.days`·날짜별 날씨 반영)와 출발일 동선만 요청하는 변형 프롬프트로 바뀌고, `_norm_suggestion(raw, e, trip=True)`는 준비 항목 상한을 6→10으로 올린다. `_event_note_payload`는 기존 `steps`에 더해 `days: {날짜: [단계…]}` 그룹을 non-breaking으로 추가
- 보드(`renderEventBody`): 여행 일정엔 유형 배지 옆에 「여행 n일」 배지와, 헤더 아래 날짜별 탭(D1 10/03 · D2 10/04 …, 탭마다 날씨 요약)이 붙어 그날의 하위 일정·동선을 보여준다(하위 일정은 `data-eid`로 클릭 이동). 동선 추가 폼은 선택된 탭 날짜를 기본값으로 하는 날짜 선택자를 얻고 POST에 `day`를 함께 보낸다. 하위 일정(예: 항공편) 자신의 패널에는 "↑ {여행 제목} 여행의 일부" 줄과 부모로 이동하는 버튼이 뜬다
- 테스트 `tests/test_trip.py` 6건: 지명형 여행 인식 vs 평범한 종일 일정, 하위/부모 연결, 여행 일수·날씨 길이 상한, 여행용 prepare_prompt(짐·일수 포함), `_norm_suggestion` 여행 상한 10, `_event_note_payload["days"]` 그룹핑. 전체 스위트 404 통과(기존 398 + 신규 6)
## v0.29 대시보드 상주 에이전트

- `AGENT_SPECS["serve"]`(`com.secondbrain.serve`, `serve --port 7777`) 추가: `agent_plist`가 `keepalive` 플래그를 보면 `RunAtLoad: True` + `KeepAlive: {"SuccessfulExit": False}`(비정상 종료 시에만 재시작, `ThrottleInterval` 10초)로 만들고 `StartCalendarInterval`/`StartInterval`은 넣지 않음. `agents install`(이름 없이)은 기본적으로 serve를 건드리지 않음 — `agents install serve`로 이름을 직접 줘야 설치되어 새 사용자가 모르는 사이 상주 프로세스가 깔리지 않게 함. `--port N`으로 설치 포트 재정의 가능
- 위젯도 자동 등록되지만(`brain-serve`, "대시보드 서버", 운영팀) 다른 에이전트처럼 "발송 완료" 류 패턴이 아니라 `cmd_serve`가 찍는 시작 줄(`http://127.0.0.1:<port>`)을 `ok_pattern`으로 판정하고, 로그가 tick마다 갱신되지 않으므로 `stale_minutes`는 생략(경과 판정 비활성)
- `agents_status()`가 serve 항목엔 `urllib.request.urlopen("http://127.0.0.1:<port>/api/session")`으로 실제 응답 여부를 찔러 `reachable`/`port`를 더 붙임(포트는 설치된 plist에서 읽거나 기본값). `doctor_report`에도 "대시보드" 항목으로 같은 프로브를 추가(불통이면 `agents install serve` 또는 `brain.py serve` 안내)
- `skills/brain-view/SKILL.md`: 서버를 새로 띄우기 전에 먼저 `agents status --json`으로 상주 서버가 있는지/응답하는지 확인 — 있으면 URL만 안내, 없으면(macOS) 상주 등록을 한 번 제안하고, 그것도 아니면 기존 임시 `serve --open` + 백그라운드 실행 흐름으로 폴백
## v0.29 코어 음성 액션

- `web/core.html`의 `answer(q)`에 백엔드 무변경으로 4가지 손대지 않는 음성 동작 추가: 준비 제안 채택/무시("제주도 제안 채택해"·"제안 다 받아"·"수잔 제안 무시해", `POST /api/suggestion`), 준비 항목 체크("여권 체크"·"체크: 모바일 탑승권"·"여권 챙겼어", `POST /api/event-note` check), 준비 남은 항목 안내("내일/제주도 준비 뭐 남았어" — 기존 준비 규칙에 내일/오늘 날짜 우선 매칭만 보강), 할 일 완료("할 일 완료: 보고서"·"보고서 했어", `POST /api/task` check). 이벤트 대상은 기존 준비 규칙과 같은 낱말-겹침 매칭을 재사용, 변경 후 `load()`로 `S.today` 새로 고침
- 안전장치: 전체 채택/전체 무시("다·전부·모두")만 `S.pendingAction`(20초 만료)에 담아 먼저 "N건을 모두 채택할까요?"로 확인받고 "응/그래/네/좋아"로 실행, "아니/취소"로 취소. 단일 일정 채택·체크박스 하나는 바로 실행
- 검증: `node`로 `<script>` 블록 `new Function` 문법 확인, `python3 -m unittest tests.test_core -q` 통과(백엔드 무변경). Playwright로 데모 서버(7798)에서 "팀 주간회의 제안 채택해"(자막에 "적었어요", `/api/today` 제안 2→1) → "제안 다 무시해"(확인 질문) → "응"(1→0) 실측 확인
## v0.29 보드 「사람」 섹션

- `dash_people(vault, agenda=None, today=None)`(`GET /api/people`): 사람 노트마다 `link_graph` 인접 수(`mentions`)·`attach_event_notes`가 붙인 `people` 필드로 매칭된 가장 가까운 일정(`next_event`)·그 사람 노트에 링크된 최근 노트 최대 3개(`recent_notes`/`last_note`)를 모아 next_event 임박 순(없으면 mentions 내림차순)으로 정렬. `unmatched_attendees`는 매칭 안 된 참석자를 정규화 이름으로 묶어 다음 일정·건수를 붙인다. 라우트는 `dash_tasks`/`/api/agenda`와 같은 패턴으로 `agenda_cache.get(14)` 사본에 `attach_event_notes`를 한 번 더 돌려 `people` 필드를 채운 뒤 넘긴다
- 보드에 「사람」 섹션 추가(프로젝트 다음): 사람 카드 격자(이름 클릭 → 노트 패널, D-n 배지, 기록 건수, 최근 노트 링크)와 옆 칸에 「아직 노트가 없는 참석자」 목록(행마다 「사람 노트 만들기」 버튼이 기존 `POST /api/person` create 액션을 재사용). `boot()`과 `refreshLive()` 양쪽에서 `loadPeople()`로 불러 최신 상태를 유지
- 테스트 `tests/test_people_section.py` 3건: 매칭된 사람(기록·다음 만남·최근 노트)과 미매칭 참석자, 사람 노트가 하나도 없는 볼트, `GET /api/people` 응답 모양. 전체 스위트 426 통과(기존 423 + 신규 3)
## v0.30 코어 설정 시트

- HUD 레일에 톱니(`#settingsBtn`) 버튼 추가, 도움말 시트와 같은 `role="dialog"` + `aria-modal` + Esc 닫힘 + Tab 포커스 트랩(닫으면 열기 전 포커스로 복귀) 패턴으로 `#cfgSheet` 구현. 항목: 음성 선택(`getVoices()` ko-KR 우선, 없으면 전체 + 들어보기 미리듣기) · 말 속도(0.8~1.4) · 음높이(0.8~1.2) · 열 때 브리핑 자동 재생 · 선제 알림 켜기/끄기 · 비서 이름(`POST /api/config`) · 자막 크기(작게/보통/크게) · 설정 초기화
- 저장 키: `brain-core-voice`·`brain-core-rate`·`brain-core-pitch`·`brain-core-autobrief`·`brain-core-proactive`·`brain-core-caption`(모두 `localStorage`, `try/catch`로 프라이빗 모드 방어). `pickVoice()`가 저장된 음성명을 우선 매칭(음성 목록은 비동기라 `onvoiceschanged`에서도 재적용), `speak()`의 발화 속도·음높이는 하드코딩 대신 저장값을 읽고, `proactive()`는 선제 알림 토글이 꺼져 있으면 즉시 리턴
- 자동 브리핑은 첫 상호작용(기존 "화면을 한 번 누르면 소리가 켜져요" 게이트) 이후 `S.today`가 준비된 시점에 페이지당 1회만 `briefLines()`를 말하도록 `maybeAutobrief()`로 처리(클릭이 로딩보다 먼저 와도/늦게 와도 정확히 한 번)
- 명령 도움말: `answer()`의 정규식을 하나씩 대조해 실제로 존재하는 21개 음성 명령(오늘·이번 주·내일·다음 일정·자동화·할 일·할 일 추가·결정·회고·브리핑·메일·준비·일지·회사 현황·누가 일해·기억해·제안 채택·무시·체크·할 일 완료·보드·사무실·이름 변경·남은 할 일 내일로)만 예시 문장과 함께 나열
- 검증: `node --check`로 `<script>` 문법 확인, `python3 -m unittest tests.test_core -q` 통과(백엔드 무변경). Playwright로 데모 서버(7799)에서 톱니 클릭 → 다이얼로그 노출(`role=dialog`) → 말 속도 슬라이더 조작 시 `localStorage["brain-core-rate"]` 갱신 → Esc로 `aria-hidden=true` 복귀 실측 확인
## v0.30 볼트 lint

- 새 CLI `brain.py lint [--fix] [--json]`: import·Obsidian 편집·자동화로 볼트가 커지면서 생기는 데이터 품질 문제 17개 코드(frontmatter-missing·type-invalid·type-folder-mismatch·created-invalid·title-missing/duplicate·tags-not-list·link-broken·link-self·orphan-old·event-key-mismatch·event-past-unchecked·journal-date-mismatch·summary-too-long·stem-collision·bom-or-crlf·imported-unenriched)를 점검. `link-broken`은 `relink()`가 쓰는 `_relink_candidates` 해석기를 그대로 재사용해 고칠 수 있는지(`_`→`-`·슬러그화로 유일하게 찾아지는지) 판정한다 — 판정 로직을 중복 구현하지 않음
- `lint_vault(vault, today=None)`은 순수 조회(`{"issues","counts","notes"}`)이고, `--fix`는 별도의 `fix_lint_issues(vault, issues)`가 fixable(`tags-not-list`·`link-self`·`link-broken`·`journal-date-mismatch`·`bom-or-crlf`)만 노트당 `write_note` 한 번으로 고친다. 파일 삭제는 절대 하지 않으며, 고친 뒤에는 `build_index` + `git_commit`이 따라붙고 다시 점검해 남은 이슈로 종료 코드(0=이슈 없음, 2=남음)를 정한다
- `doctor_report`에 "볼트 점검" 항목 한 줄 추가(`lint_vault` 크래시가 doctor 전체를 죽이지 않도록 try/except로 감쌈), fix 안내는 `` `brain.py lint --fix` ``
- `skills/brain-doctor/SKILL.md`에 "볼트 점검 ✗ → lint --json으로 코드 보여주고, fixable만 승인 후 --fix" 단계 추가
- 신규 테스트 `tests/test_lint.py` 8건(코드별 검출·개수, report-only 항목은 fixable=False, --fix가 고칠 것만 고치고 재점검하면 이슈가 줄어듦, 파일 미삭제, CLI 종료 코드·`--json`, 깨끗한 볼트는 이슈 0, doctor 연동). 전체 스위트 441 통과(기존 433 + 신규 8)
## v0.30 빠른 캡처

- `capture_note(vault, body)`(`POST /api/capture`, `brain.py capture "<텍스트>"`) 추가: `remember_chat` 옆에 위치, `{text(1~4000자), type?(note|idea|source, 기본 note), title?, tags?(최대 5개), project?}`를 받아 `create_note`로 노트 한 장을 만들고 `build_index`·`git_commit`까지 처리. 텍스트가 http(s) URL로 시작하면 타입 지정이 없을 때 자동으로 `source`가 되고 제목은 host+path 꼬리에서, `source` 프론트매터에는 URL 그대로 들어간다. v0.29에서 "적합한 캡처용 API가 없어" 보류했던 팔레트의 「메모」 명령이 이걸로 채워진다
- 보드(`web/index.html`) 명령 팔레트: `>` 입력 후 타이핑한 텍스트마다 기존 "할 일 추가: <텍스트>" 옆에 "메모: <텍스트>"·"아이디어: <텍스트>"·"링크: <텍스트>" 세 후보가 함께 뜨고, 고르면 `POST /api/capture`(각각 note/idea/source)로 저장 후 토스트("기록했어요: 「제목」")와 함께 `openNote`로 새 노트를 바로 연다. topbar에 「+ 기록」 버튼을 추가해 `openCommandPalette("메모: ")`로 검색창에 `>메모: `를 채워 포커스한다
- 코어(`web/core.html`) `answer(q)`에 정규식 `/^(메모|기록)(해|해줘)?\s*[:：]?\s*(.+)/` 규칙 한 줄과 `quickCapture(text)` 헬퍼를 추가: "메모해: 텍스트"·"기록: 텍스트" 형태를 잡아 `/api/capture`로 보내고 "「제목」으로 기록했어요."라고 말한다
- 테스트 `tests/test_capture.py` 10건(제목 규칙, idea, URL→source 제목/프론트매터, 명시 제목 유지, 태그·프로젝트, 빈 값/과다 길이/잘못된 타입 검증, HTTP 토큰 게이트, CLI 경로 출력). 전체 스위트 465 통과(기존 455 + 신규 10)
## v0.30 HTML 내보내기

- `brain.py export --html <out.html> [--since] [--type] [--project] [--no-body]`: 외부 자산 없이 인라인 CSS(A4 인쇄용, 결정마다 페이지 나눔)만 쓰는 단일 HTML로 볼트(또는 필터링한 일부)를 내보낸다. `export_html()`이 목차(타입·프로젝트별 개수)·결정(상태 배지·상황·결정·이유·되돌아볼 날)·프로젝트 허브·일지·회고·노트·사람·일정 노트 섹션을 만들고, `md_to_html()`(작은 서버사이드 렌더러: 헤딩·문단·목록·체크박스(☐/☑ 텍스트)·볼드/이탤릭·인라인 코드·링크·인용, 그 외는 escape)로 본문을 그린다
- `[[stem]]` 위키링크는 이번 내보내기에 포함된 노트면 `#n-<stem>` 인페이지 앵커로, 아니면 흐린 텍스트(`wikilink-broken`)로 남는다. 본문 20,000자 초과 시 잘라내고 안내 문구를 붙여(500개 노트 기준 ~2MB 목표) 파일 크기를 억제한다. 검색창 하나짜리 선택적 클라이언트 필터 스크립트 외 자바스크립트는 불필요
- 테스트 `tests/test_export.py` 10건: 섹션·제목 포함, 위키링크 앵커 해석/부재, `--since`·`--type`·`--project` 필터, `--no-body` 본문 생략, `html.parser` 정합성(`<h2>` 개수=섹션 개수), 외부 자산 없음(사용자 링크만 예외), CLI/JSON, 잘못된 타입 오류. 전체 스위트 465 통과(기존 455 + 신규 10)
