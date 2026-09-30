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
