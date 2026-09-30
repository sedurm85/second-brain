# 온보딩 QA — 신규 사용자 첫 10분 (2026-09-30)

브랜드 뉴 사용자 관점으로 `python3 scripts/brain.py`를 처음부터 끝까지 따라가며 크래시·불친절한 메시지·기본값 문제를 찾아 고쳤다. 매 명령은 `HOME=$(mktemp -d)` + `XDG_CACHE_HOME=$HOME/.cache` + `SECOND_BRAIN_OFFLINE=1` + `SECOND_BRAIN_NO_LAUNCHCTL=1`로 격리한 임시 볼트에서 실행했다(실제 `~/brain`·`~/.config`·`~/.cache`는 손대지 않음).

## 격리 관련 메모(다른 QA 작업자를 위한 기록)

작업 중 이 세션의 셸에 실제 `CLAUDE_CONFIG_DIR`(사용자 개인 Claude 설정)이 이미 잡혀 있어서, `journal --dry-run` 같은 명령이 `jobs_dir()`의 `CLAUDE_CONFIG_DIR` 폴백을 타고 실제 배경 작업 상태를 읽어온 적이 있었다(쓰기는 없었음, 읽기만). 이후 모든 명령에 `unset CLAUDE_CONFIG_DIR SECOND_BRAIN_VAULT SECOND_BRAIN_JOBS_DIR SECOND_BRAIN_ASK_CMD SECOND_BRAIN_LAUNCH_AGENTS`를 추가해 완전히 격리했다. 또한 `export HOME=$(mktemp -d) XDG_CACHE_HOME=$HOME/.cache ...`처럼 한 줄에 몰아 쓰면 `$HOME`이 갱신 전 값(진짜 홈)으로 먼저 펼쳐져 `XDG_CACHE_HOME`이 실제 `~/.cache`를 가리키는 셸 함정이 있다 — `HOME` 대입과 그걸 참조하는 대입은 반드시 별도 줄(또는 별도 변수)로 나눠야 한다. 브라우저 표시상 영향은 없었음(mkdir만 발생, mtime 확인으로 검증).

## 친절도 표(단계 · 무슨 일이 있었는지 · 평점 0~3 · 수정/아이디어)

| 단계 | 무슨 일이 있었는지 | 평점 | 수정/아이디어 |
|---|---|---|---|
| `brain.py` (인자 없음) | 36개 서브커맨드가 평평하게 나열, 한국어·설명은 좋지만 처음 보면 압도적 | 2 | **수정**: 최상위 `epilog`에 그룹 묶음(자주 쓰는 것/비서/자동화/관리) 4줄 추가, `formatter_class=RawDescriptionHelpFormatter`로 줄바꿈 보존. 개별 서브커맨드 `-h` 출력은 그대로 |
| `today` (볼트 없음) | "볼트가 없어 위젯만 보여줍니다: <경로>" + 저녁 인사 + 위젯 안내, 무섭지 않고 exit 0 | 0 | 수정 불필요 |
| `doctor` (새 기기) | ✗ 항목(볼트·캘린더·카톡·위젯)마다 실행 가능한 fix 명령이 붙어 있음. 다만 `SECOND_BRAIN_NO_LAUNCHCTL=1`에서 "알림 에이전트"·"agents status"가 **미설치인데도 "loaded"** 로 나오는 자기모순 발견 | 2 | **수정**: `agents_status()`가 `installed`(plist 파일 존재)를 함께 확인해야만 `state="loaded"`로 판정하게 가드 추가. `doctor`의 "대시보드 응답함"은 로컬 7777 포트를 실제로 GET하는 설계라 이 QA 환경에 기존에 떠 있던 서버에 반응한 것 — 알고 있는 한계, 수정 안 함(아래 "안 한 것" 참고) |
| `init` | "볼트 준비 완료" + 생성 목록 + "다음: doctor → calendar → serve" 힌트 | 0 | 수정 불필요 |
| `lint` (init 직후, 아무것도 안 건드림) | 샘플 가이드 노트 본문의 예시 문구 `[[파일]]`이 실제 위키링크로 파싱되어 **신규 사용자가 첫 lint에서 바로 이슈 1건(link-broken)을 봄** — 자기가 만들지도 않은 문제. 테스트 코드에 이미 이 현상을 우회하는 주석이 있었음(`tests/test_lint.py`) | 3 | **수정**: `SAMPLE_BODY`에서 `[[파일]]`을 "관련 노트를 링크로 인용해 답해요"로 바꿔 실제 위키링크 표기를 없앰. 이제 `init` 후 바로 `lint`하면 "이슈 없음" |
| `reminders list`/`test` (연결 켠 적 없음, 기본 꺼짐 문서화돼 있음) | 문서상 "기본 꺼짐"인데 실제로는 `enabled` 플래그를 확인 안 하고 그냥 `fetch_reminders`를 호출 — 오프라인이 아니면 **맥 미리알림 권한 창까지 뜰 수 있는** 설계 결함 | 3(치명적) | **수정**: `cmd_reminders`에 `rcfg.get("enabled")` 가드 추가. 꺼져 있으면 fetch를 아예 부르지 않고 "미리알림 연결이 꺼져 있어요. `reminders on`으로 켜세요" 안내만 하고 EXIT_OK |
| new/capture/search/show/review/index/task/event/journal --dry-run/retro --dry-run/prepare --dry-run/export/backup/restore --list/widget list/widgets/calendar list/notify test | 모두 크래시 없음, 해요체·설명 일관, `--json`은 전부 유효한 JSON(`json.tool` 통과) | 0 | 수정 불필요 |
| `serve --demo --port 7801` + curl `/`, `/api/today` | 200 OK, `/api/today` 유효 JSON, 데모 캐시가 실제 `~/.cache/second-brain`과 분리됨을 로그로 직접 확인, kill로 정상 종료 | 0 | 수정 불필요 |
| skills/*/SKILL.md 12개 전수 확인 | 참조하는 모든 서브커맨드(agenda/agents/brief/calendar/config/doctor/enrich/event/export/import/index/journal/link/lint/new/remind/retro/review/search/serve/show/today/widget/widgets)와 플래그가 `build_parser()`와 전부 일치. **드리프트 없음** | 0 | 수정 불필요(확인만) |
| `ask`/`enrich`/`journal`(non-dry) | 태스크 지시대로 **실행하지 않음**(SECOND_BRAIN_ASK_CMD 미설정 시 실제 `claude` CLI를 부르는 명령) | - | 해당 없음 |
| `obsidian status` | `build_parser()`에 그런 서브커맨드가 없음(존재 확인만, 스크립트 오타 아니었음) | - | 존재하지 않는 명령이라 QA 대상에서 제외 |

## 실행한 수정 요약 (scripts/brain.py)

1. 최상위 `--help` epilog에 4단 그룹 묶음 추가(자주 쓰는 것/비서/자동화/관리) + `RawDescriptionHelpFormatter`.
2. `agents_status()`: `SECOND_BRAIN_NO_LAUNCHCTL=1`에서도 미설치 에이전트는 `state="not loaded"`로 일관되게(이전엔 "미설치 · loaded" 모순).
3. `cmd_reminders`: `list`/`test`가 `enabled=False`면 실제 조회(osascript) 없이 "꺼져 있어요" 안내만 하고 종료(이전엔 무조건 조회 시도 — 오프라인 모드가 아니면 권한 프롬프트까지 뜰 수 있었음).
4. `SAMPLE_BODY`(init 샘플 노트): 예시 위키링크 `[[파일]]`을 없애 신규 사용자의 첫 `lint`가 깨끗하게 나오도록.

회귀 테스트: `tests/test_onboarding.py` 신규 7건(도움말 그룹핑, 볼트 없는 today, fresh doctor, 전체 온보딩 시퀀스 크래시 없음, 모든 --json 유효성, agents status 비모순, reminders 기본꺼짐 가드). 전체 스위트 `python3 -m unittest discover -s tests -q` → **563 통과**(기존 556 + 신규 7).

## 안 고친 것 top 5 (이유 포함)

1. **doctor의 "대시보드"/"알림 에이전트" 항목이 로컬 머신 상태(7777 포트, 실제 launchd)에 영향받음** — 이 QA 환경에서 이미 떠 있는 실서버에 반응해 "응답함"이 나옴. 격리하려면 doctor에 포트/launchctl probe를 완전히 끄는 별도 플래그가 필요한데, 이건 진단 명령의 핵심 기능(실제로 떠 있는지 확인)을 훼손하는 더 큰 설계 변경이라 이번 범위(작고 로컬한 수정)를 벗어남. `tests/test_doctor.py`도 이미 이 항목을 조건부로만 확인하도록 만들어져 있어 기존에 알려진 한계.
2. **~40개 서브커맨드를 실제 argparse 그룹(서브파서 그룹)으로 나누기** — epilog 텍스트로 묶음만 보여주는 대신 `usage:`/`positional arguments:` 목록 자체를 그룹별로 묶으려면 argparse 기본 포매터를 커스텀 클래스로 갈아엎어야 해서(Python 3.9 호환 유지하며 `_format_action` 오버라이드) "작고 로컬한 수정" 범위를 넘어감. 지금은 epilog 힌트로 충분히 완화됐다고 판단.
3. **`reminders`/`calendar` 같은 "기본 꺼짐" 기능들이 각자 다른 방식으로 꺼짐을 표현** (`reminders`는 `enabled` 플래그, `calendar`는 그냥 "소스 없음") — 일관된 "미설정" UX 패턴으로 통일할 수 있지만 여러 명령에 걸친 리팩터라 다른 워커의 영역(agenda 간극)과 겹칠 수 있어 보류.
4. **`doctor`의 종료 코드가 어떤 항목이 실패했는지와 무관하게 항상 2** (볼트가 없어도 3이 아니라 2) — `brain.py` 최상위 epilog는 "3 볼트 없음"을 언급하지만 doctor 자체는 절대 3을 반환하지 않는다. 의미상 크게 헷갈리진 않지만(코드 주석·기존 테스트가 이미 이 동작을 전제) 종료 코드 스킴을 바꾸면 `require_vault()`를 쓰는 다른 모든 명령과의 일관성을 다시 검토해야 해서 보류.
5. **`journal`/`enrich`/`ask`/`prepare`(non-dry) 실제 호출 경로는 테스트 못 함** — 태스크 지침상 `SECOND_BRAIN_ASK_CMD` 없이 실제 `claude` CLI를 부르는 경로라 실행 금지. dry-run 경로만 확인했으므로 Claude 응답을 파싱하는 이후 로직(요약 추출, 재료 조립 이후)은 이번 QA 범위 밖.

## 커밋

- `agents_status`/`cmd_reminders`/`SAMPLE_BODY`/help epilog 수정 + 회귀 테스트: 커밋 로그 참고(브랜치 `wt/onboarding-qa`).
