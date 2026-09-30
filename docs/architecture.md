# 아키텍처 지도 (기여자용, 2026-09-30 기준 코드 스냅샷)

이 문서는 SPEC.md(기능 변경 로그)나 v2-plan.md(기획)를 대신하지 않는다. "지금 코드가 실제로 어떻게 짜여 있는가"만
`scripts/`·`web/`을 직접 훑어 정리한 지도다. 다른 워크트리에서 동시에 손대고 있는 기능(팀 KPI·날씨 통합·미리알림·
이번 주 계획 등)은 이 스냅샷에 없을 수 있다 — `docs/v2-plan.md`의 「진행 현황」 표를 함께 보면 된다.

전체가 표준 라이브러리 기반 단일 프로세스 CLI+HTTP 서버(`scripts/brain.py`, 5,388줄)이고, 캘린더/메일/날씨만
별도 모듈로 분리돼 있다. 프런트엔드는 빌드 도구 없는 순수 HTML/JS 4장.

## 모듈 지도

### scripts/brain.py — 본체

파일 안 `# ---` 배너는 아래처럼 느슨하게 나뉘어 있지만(코드가 쓰인 순서일 뿐 기능 경계와 정확히 일치하지 않음),
실제 역할 기준으로 다시 묶으면 다음과 같다.

| 영역 | 원본 배너(줄) | 핵심 함수/클래스 |
|---|---|---|
| 프런트매터·설정·경로 | 프론트매터(63) / 설정·경로 안전(264) | YAML 부분집합 파서, `vault_path`, `load_config`/`save_config` |
| 노트 모델 | 노트 모델(359) / slug·경로 규칙(541) | `class Note`, `slugify`, `target_path`, `create_note`, `write_note`, `load_notes`(지문 캐시) |
| 검색 | 검색(656) | `dash_search` (BM25-lite + 최근성 가중) |
| 인덱스·리뷰 | 인덱스(769) / 리뷰·링크·결정 대체(882) / 가져오기(967) | `build_index`(BRAIN.md), `cmd_review`(+`--semantic`), `cmd_decide`, `cmd_link`/`cmd_relink`, `cmd_import`, `semantic_link_suggestions` |
| 대시보드 순수 함수 | 대시보드 데이터 v0.2(1294) | `dash_summary`/`dash_graph`/`dash_note`/`dash_timeline`/`dash_decisions`/`dash_journals`/`dash_projects`/`dash_report` — 전부 `(vault, today, …) → JSON` 순수 함수 |
| 데모 | 데모 볼트(1605) | `build_demo_vault`, `demo_overrides` (전부 가공 데이터, `serve --demo`) |
| 위젯 | 비서 모드 v0.3(1792) 내 일부 | `class WidgetCache`(60초), `collect_widgets`, `evaluate_widget`, `widget_action`, `widget_commands`, `widget_history`, `cmd_widgets` |
| 일정 노트·동선 | 비서 모드 v0.3(1792) 내 일부 | `class AgendaCache`(60초, agenda.py 위 얇은 층), `cmd_event`/`event_note_action`, `cmd_agenda`, `attach_event_notes`, `cmd_prepare`/`prepare_prompt`/`suggestion_action` |
| 할 일 | 비서 모드 v0.3(1792) 내 일부 | `parse_tasks`(inbox.md), `cmd_task`/`task_action`, `dash_tasks` |
| 브리핑·알림 | 비서 모드 v0.3(1792) 내 일부 | `cmd_brief`, `dash_today`, `today_human`/`kakao_brief`, `notify()`(카톡→macOS 알림 센터 폴백), `cmd_notify`, `cmd_remind`, `_notify_log` |
| 서버 | HTTP 서버(2632) | `class DashboardHandler`(`do_GET`/`do_POST`/`_write_allowed`/`_static`), `make_server`, `cmd_serve` |
| CLI·에이전트·기타 | CLI(2902) 이후 전부 | `cmd_mail`, `cmd_journal`/`cmd_retro`/`cmd_enrich`, `cmd_doctor`, `cmd_backup`/`cmd_restore`, `cmd_agents`(`AGENT_SPECS`), `cmd_calendar`, `cmd_config`, `class KoreanParser`(argparse) |

Claude를 부르는 로직(`_run_claude_json`, `ask_assistant`, enrich/prepare/journal/retro/semantic/staff_brief 프롬프트)은
물리적으로는 "CLI" 배너 구간(2902줄 이후)에 흩어져 있지만, 성격이 달라 아래 별도 절로 뺐다.

### scripts/agenda.py (607줄) — 일정 어댑터
- 소스 2종: `ics`(iCalendar 파일/URL, URL은 15분 파일 캐시) / `eventkit`(맥 캘린더 앱, `osascript -l JavaScript`로 JXA 호출, 최초 1회 권한 필요)
- `cache_dir()` — `~/.cache/second-brain/`(XDG_CACHE_HOME로 재정의 가능), brain.py의 캐시 파일들도 이 함수를 공유
- RRULE 반복 일정 확장(`MAX_RRULE_INSTANCES=400`), 표준 스키마 `{id, title, start, end, all_day, location, calendar, source}`

### scripts/mailer.py (288줄) — IMAP 읽기 전용
- 앱 비밀번호 파일(`~/.config/second-brain/mail.pass`, chmod 600)로 IMAP 헤더만 읽음(본문 저장 없음)
- 결과를 `~/.cache/second-brain/mail.json`에 10분 캐시(`CACHE_SEC = 10*60`)
- 분류 3묶음: 답장 필요 / 기다리는 답 / 알아두면 됨

### scripts/weather.py (328줄) — 독립 날씨 모듈, **brain.py 미연동**
- Open-Meteo 지오코딩+예보(API 키 불필요), `SECOND_BRAIN_OFFLINE=1`로 네트워크 완전 차단 가능
- 지오코딩은 미스 포함 영구 캐시(`geocode.json`, 미스는 `GEOCODE_MISS_RETRY_SECONDS` 뒤 재시도), 예보는 좌표별 3시간 캐시(`forecast-<lat4>-<lon4>.json`)
- 이 스냅샷에서는 `brain.py`가 이 모듈을 import하지 않는다. 연결 지점 설계는 `docs/weather-integration.md` 참고, 실제 통합은 다른 워크트리(`wt/weather-integration`)에서 진행 중 — 진행 중(워커).

### web/*.html — 프런트엔드 4장 (빌드 도구 없음, 정적 서빙)
| 파일 | 경로 | 역할 |
|---|---|---|
| `web/index.html`(2,149줄) | `/`, `/index.html` | 메인 보드. 내비: 오늘·할 일·이번 주·지식 그래프·자동화·개요·결정·타임라인·일지·프로젝트 |
| `web/core.html`(428줄) | `/core`, `/core.html` | "코어" 화면. 자유 질문(`/api/ask`), 대화 기억(`/api/remember`) |
| `web/office.html`(421줄) | `/office`, `/office.html` | "사무실" 화면. 위젯을 자동화 직원으로 시각화, 책상 상세·이번 주 한 줄 |
| `web/report.html`(336줄) | `/report`, `/report.html` | 인쇄용 주간 리포트 한 장(`/api/report`), 화면에서 7/14/30일 전환 |

## API 표

모든 POST 요청은 라우트 분기 전에 `_write_allowed()`를 한 번만 통과해야 한다: `X-Brain-Token` 헤더가 서버
기동 시 발급한 세션 토큰(`secrets.compare_digest`)과 일치하고, `Host`가 `127.0.0.1`/`localhost`/`[::1]`(루프백)일 때만
허용한다. 실패 시 403. 토큰은 `GET /api/session`으로만 내려간다.

### GET
| 경로 | 파라미터 | 호출 함수 | 설명 |
|---|---|---|---|
| `/api/office` | — | `dash_office` + `widget_commands` | 사무실 위젯 데이터, `allow_run`이면 실행 가능 명령 목록 |
| `/api/mail` | `force` | `collect_mail_safe` | 메일 3묶음(10분 캐시, force로 즉시 재조회) |
| `/api/widget-history` | `id`, `days`(≤90) | `widget_history` | 위젯 하나의 실행 이력 |
| `/api/tasks` | — | `dash_tasks` | 할 일 4열(오늘/이번 주/언젠가/기다림), 일정 메모 부착 |
| `/web/*` | — | `_static` | web/ 하위 정적 자원(assets 등) |
| `/api/summary` | — | `dash_summary` | 개요 숫자 |
| `/api/graph` | — | `dash_graph` | 지식 그래프 노드·엣지 |
| `/api/search` | `q`, `limit`(≤200) | `dash_search` | BM25-lite 검색 |
| `/api/note` | `path` | `dash_note` | 노트 패널 상세 |
| `/api/timeline` | `days`(≤3650) | `dash_timeline` | 타임라인 |
| `/api/journals` | `limit`(≤60) | `dash_journals` | 일지(하루)·회고(주간) 목록 |
| `/api/report` | `days`(≤90) | `dash_report` | 주간 리포트 데이터 |
| `/api/decisions` | — | `dash_decisions` | 결정 카드 |
| `/api/projects` | — | `dash_projects` | 프로젝트 허브 |
| `/api/widgets` | — | `collect_widgets` + `widget_commands` | 위젯 목록, `runnable` 플래그 |
| `/api/today` | — | `dash_today` | 오늘 화면(보고 문장·시간표·할 일·자동화·회고 질문 등 통합) |
| `/api/suggestions` | — | `pending_suggestions` | 대기 중인 일정 준비 제안 |
| `/api/agenda` | `days`(≤60) | `AgendaCache.get` + `attach_event_notes` | 일정 목록 |
| `/api/session` | — | — | 세션 토큰·`writable`·비서 이름 |

### POST (모두 쓰기 가드 통과 필요)
| 경로 | 호출 함수 | 설명 | 부수효과 |
|---|---|---|---|
| `/api/event-note` | `event_note_action` | 일정 메모/준비/동선 기록 | 일정 캐시 무효화 |
| `/api/remember` | `remember_chat` | 코어 대화를 노트로 저장 | 노트 1개 생성 |
| `/api/suggestion` | `suggestion_action` | 준비 제안 채택/무시 | 일정 캐시 무효화 |
| `/api/widget` | `widget_action` | 위젯 실행/멈춤/재시작/「한 줄」 갱신 | 위젯 캐시 전체 클리어 |
| `/api/task` | `task_action` | inbox.md 할 일 체크·추가 | inbox.md 갱신 |
| `/api/config` | (인라인) | 허용된 설정 키만 변경(현재 `assistant_name`만) | config.json 갱신 |
| `/api/ask` | `ask_assistant` | 코어 자유 질문(헤드리스 Claude) | 없음(볼트 쓰기 없음) |

## 데이터 위치 표

### 볼트 (`~/brain` 기본, `config.json`의 `vault` 또는 `SECOND_BRAIN_VAULT`로 재정의)
| 타입 | 경로 규칙 |
|---|---|
| note | `notes/YYYY/MM/{slug}.md` |
| decision | `decisions/{NNN}-{slug}.md` |
| project | `projects/{slug}.md` |
| person | `people/{slug}.md` |
| event | `events/YYYY/{YYYY-MM-DD}-{slug}.md` |
| journal(하루) | `journal/YYYY/{YYYY-MM-DD}.md` |
| journal(주간 회고) | `journal/YYYY/{YYYY-MM-DD}-weekly.md` |
| 인덱스·할 일 | `BRAIN.md`, `inbox.md` (볼트 루트) |

### `~/.config/second-brain/`
- `config.json` — vault 경로, `ask_cmd`, `calendar.sources`, `mail`, `notify_channels`/`notify_center`, `assistant_name`, `kakao_cmd`
- `widgets.json` — 자동화 위젯 정의, `allow_commands`/`allow_run`
- 사용자가 직접 지정하는 파일들: ICS 비공개 주소 파일(예: `google.ics.url`), 메일 앱 비밀번호 파일(예: `mail.pass`, chmod 600)

### `~/.cache/second-brain/` (`agenda_mod.cache_dir()`, `XDG_CACHE_HOME`로 재정의 가능)
| 파일/폴더 | 용도 |
|---|---|
| `ics-{sha1(url)[:16]}.ics` | 캘린더 ICS 파일 캐시(15분) |
| `mail.json` | 메일 수집 결과 캐시(10분, mailer.py) |
| `core_log.jsonl` | 코어 대화 로그 |
| `staff_briefs.json` | 사무실 직원 「이번 주 한 줄」 캐시(하루 1회) |
| `reminded.json`(`REMIND_STATE`) | 출발·시작 알림 중복 발송 방지 상태 |
| `suggestions.json` | 일정 준비 제안(pending/accepted/dismissed) |
| `agents/` | launchd 에이전트 로그 |
| `backups/` | `backup_vault` 기본 목적지(`brain-YYYYMMDD-HHMMSS.zip`) |
| `weather/geocode.json`, `weather/forecast-{lat4}-{lon4}.json` | weather.py 전용(현재 brain.py와 미연동) |

## 캐시·성능
- **노트 캐시**: `load_notes(vault)`는 볼트 지문(노트 디렉터리를 `os.walk`+`stat`만으로 훑은 `(경로, mtime_ns, size)` 튜플)이 바뀌지 않으면 재파싱하지 않는다. `write_note()`가 모든 노트 쓰기의 단일 통로라 그 안에서 `invalidate_notes_cache()`를 한 번 더 불러 지문 방식 위에 belt-and-braces를 얹었다. `SECOND_BRAIN_NO_NOTE_CACHE=1`로 완전히 끌 수 있다(테스트용).
- **링크 그래프**(v0.31): `cached_link_graph(vault, notes=None)`가 `link_graph()` 결과를 볼트 지문 기준으로 캐시한다. 지문은 따로 재스캔하지 않고 바로 앞에서 호출된 `load_notes(vault)`가 `_NOTES_CACHE`에 채워 둔 값을 그대로 읽는다(요청당 `os.walk`+`stat` 지문 스캔을 두 번 하지 않으려는 것 — 재스캔이 `link_graph` 계산 자체보다 비쌌다). `build_index`/`lint_vault`/`dash_summary`/`dash_people`/`attach_event_notes`/`retro_material`/`semantic_link_suggestions`가 이 캐시를 쓴다.
- **검색 인덱스**(v0.31): `cached_search_docs(vault)`가 노트별 BM25 토큰(`tf`, `length`)을 같은 지문 방식으로 캐시해 `search()`가 매 호출 전체 본문을 재토큰화하지 않게 한다. `dash_search`는 추가로 facet 집계용 전체 후보 채점과 스니펫 계산을 분리해(`with_snippet=False` 후 최종 `limit` 슬라이스에만 스니펫 계산) 자유어 검색을 크게 줄였다. 상세 수치는 [docs/performance.md](performance.md).
- **위젯**: `WidgetCache`, 60초(`WIDGET_CACHE_SEC`). 키는 위젯 설정+idx+allow_commands, 무효화 조건은 소스 mtime 변경 또는 60초 경과.
- **위젯 로그 이력**(v0.31): `widget_history()`가 `(source, mtime_ns, size, days, today, fail_pattern)` 키로 60초 캐시(`_WIDGET_HISTORY_CACHE`). 로그가 새로 쓰이면 mtime/size가 바뀌어 60초를 기다리지 않고 즉시 무효화된다. `office_kpis`/`dash_report`/`retro_material`이 위젯마다 로그를 반복해서 다시 읽던 비용을 없앤다.
- **일정**: 이중 캐시. brain.py의 `AgendaCache`가 프로세스 내 60초 캐시(EventKit 호출 억제용)로 감싸고, 그 안에서 부르는 agenda.py의 ICS 파일 캐시가 15분.
- **메일**: mailer.py `CACHE_SEC = 10 * 60`, 결과를 `mail.json`에 저장, `force=True`로 즉시 재조회.
- **날씨**: 예보 3시간, 지오코딩은 성공 시 영구 캐시·실패(미스)는 일정 시간 뒤 재시도.
- **사무실 직원 한 줄**: `staff_brief()` 하루 1회 캐시(`staff_briefs.json`), 날짜가 바뀌거나 `force=True`면 재생성.

## Claude 호출 원칙

`claude -p`(또는 `SECOND_BRAIN_ASK_CMD`)를 부르는 곳은 `_run_claude_json`(공통 실행기, 타임아웃 기본 240초) 하나를
전부 거친다. 응답은 ` ```json ` 펜스·앞뒤 잡담을 허용해 첫 `[`/`{` ~ 마지막 `]`/`}` 구간만 파싱한다.

| 명령/기능 | 프롬프트 생성 | 볼트에 쓰는가 |
|---|---|---|
| `enrich` | `enrich_prompt` → `apply_enrichment` | **자동으로 씀** — 제목·요약·태그·링크를 노트에 직접 적용 후 `git_commit` |
| `journal` | `journal_prompt`(재료는 `journal_material`) | **자동으로 씀** — `journal/YYYY/날짜.md` 생성/갱신 |
| `retro` | `retro_prompt`(재료는 `retro_material`) → `write_retro` | **자동으로 씀** — `journal/YYYY/날짜-weekly.md` |
| `staff_brief`(사무실 「이번 주 한 줄」) | 인라인 프롬프트 | 볼트 아님, `staff_briefs.json` 캐시에만 씀 |
| `ask_assistant`(코어 `/api/ask`) | 인라인 프롬프트 | 안 씀. 사람이 "기억해" 하면 별도로 `/api/remember`가 노트 생성(Claude 호출 아님, 사용자 승인 경로) |
| `prepare`(`cmd_prepare`) | `prepare_prompt` | **안 씀** — `suggestions.json`에 `pending`으로만 보관, 보드에서 사람이 "채택"해야 `event_note_action`으로 실제 기록 |
| `review --semantic` | `semantic_link_suggestions` | **안 씀** — 관련 노트 쌍을 제안만, 적용은 `brain.py link A B`로 사람이 실행 |

원칙: 노트 본문·요약·태그처럼 "정리" 성격의 결과는 자동 반영하고, 일정 준비·의미 기반 링크처럼 "새 사실을 만들 여지가 있는" 제안은
항상 대기함에 넣고 사람 승인을 거친다.

### env 오버라이드 (`SECOND_BRAIN_*`, 전체 목록)
| 변수 | 용도 |
|---|---|
| `SECOND_BRAIN_VAULT` | 볼트 경로 강제 지정(`config.json`의 `vault`보다 우선) |
| `SECOND_BRAIN_ASK_CMD` | Claude 호출 명령 교체(테스트에서 가짜 스크립트로 대체하는 용도) |
| `SECOND_BRAIN_NO_NOTE_CACHE` | 노트 캐시 완전 비활성화 |
| `SECOND_BRAIN_LAUNCH_AGENTS` | launchd 에이전트 plist 설치 경로 오버라이드 |
| `SECOND_BRAIN_RUN_DRY` | "지금 실행"을 실제로 실행하지 않고 드라이런 |
| `SECOND_BRAIN_JOBS_DIR` | 백그라운드 Claude 작업 상태 디렉터리 오버라이드 |
| `SECOND_BRAIN_NOTIFY_LOG` | 알림 발송 시도를 파일에 한 줄씩 기록(테스트용) |
| `SECOND_BRAIN_NOTIFY_DRY` | 알림을 실제로 보내지 않고 시도할 채널만 ok로 반환 |
| `SECOND_BRAIN_NO_LAUNCHCTL` | 실제 launchd(`launchctl`)를 건드리지 않음(테스트·드라이런) |
| `SECOND_BRAIN_MAIL_PASSWORD` | 메일 앱 비밀번호를 파일 대신 환경변수로 주입(테스트용) |
| `SECOND_BRAIN_OFFLINE`(weather.py) | 날씨 모듈 네트워크 호출 완전 차단 |

이 외에 `CLAUDE_CONFIG_DIR`(launchd 에이전트·백그라운드 실행 시 Claude CLI 설정 디렉터리 전달)도 있으나 `SECOND_BRAIN_` 접두사는 아니다.

## 테스트

```
python3 -m unittest discover -s tests -q
```
이 워크트리에서 실행한 결과: `Ran 239 tests in 10.132s` / `OK`. (SPEC.md 각 항목이 그 시점 기준 테스트 수를 적어 두지만,
다른 워크트리들의 기능이 순차 병합되며 현재 `main` 기준 실제 수는 그보다 많다 — 숫자가 필요하면 항상 직접 재실행해서 확인할 것.)

격리 관례(`tests/test_*.py`가 반복하는 `setUp` 패턴):
- `HOME`, `XDG_CACHE_HOME`을 `tempfile.TemporaryDirectory()`로 임시 디렉터리에 재지정해 실제 `~/brain`·`~/.config`·`~/.cache`를 건드리지 않음
- `SECOND_BRAIN_VAULT`는 지우고 `~/.config/second-brain/config.json`을 직접 써서 볼트 경로를 명시
- launchd를 다루는 테스트는 `SECOND_BRAIN_NO_LAUNCHCTL=1`로 실제 `launchctl` 호출을 막음
- Claude를 부르는 명령을 테스트할 때는 `SECOND_BRAIN_ASK_CMD`를 stdin으로 재료를 받아 고정 JSON을 출력하는 로컬 가짜 스크립트(`sys.executable + " " + str(fake)`)로 바꿔 실제 Claude CLI를 절대 실행하지 않음
- `tearDown`에서 건드린 환경변수를 원래 값으로 복원

성능 벤치마크(합성 볼트 1000노트, 캐시 전/후 수치)는 `scripts/bench.py`로 재현하며 자세한 내용은 [docs/performance.md](performance.md) 참고.

## 야간 자율 근무 체계

관리자(Claude 세션 하나) + 워커(서브에이전트, 각자 `wt/<과제>` 브랜치의 별도 git worktree) 체계로 여러 기능을 동시에
진행한다. 워커는 한 과제만 맡아 테스트 통과까지 확인하고 자기 브랜치에 커밋하며, 관리자가 `git rebase main` →
전체 테스트 → ff 머지 → 버전/SPEC 갱신 → push·tag까지 검수한 뒤에만 `main`에 들어간다. 볼트 쓰기는 항상 사람 승인,
테스트는 실제 `launchctl`·실제 볼트를 건드리지 않는다는 원칙은 이 문서의 「Claude 호출 원칙」·「테스트」 절과 같다.

자세한 사이클별 기록(투입된 워커, 충돌 해소 방식, 실측 테스트 수 변화 등)은
[docs/nightshift-2026-09-30.md](nightshift-2026-09-30.md) 참고.
