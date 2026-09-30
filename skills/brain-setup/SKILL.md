---
name: brain-setup
description: 세컨드브레인 볼트 설정(위치, git 자동 커밋, 인덱스 줄 수)과 비서 설정(캘린더 연결, 비서 이름, 카톡 헬퍼, 아침·알림·저녁 launchd 에이전트, 위젯 실행 허용)을 확인·변경하고 볼트를 만든다.
when_to_use: 사용자가 "브레인 설정", "볼트 위치 바꿔", "세컨드브레인 셋업", "캘린더 붙여줘", "아침 브리핑 켜줘", "비서 이름"이라고 할 때 또는 /second-brain:brain-setup 호출 시.
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py *) Read
---

# 설정

0. 먼저 `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py doctor` 로 점검표(볼트·Python·Claude CLI·캘린더·카톡·위젯·알림 에이전트)를 보여준다. ✗ 항목의 → 안내가 곧 할 일이다. 사용자가 "점검해줘", "왜 안 돼"라고 할 때도 이것부터.
1. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py config get` 으로 현재 설정을 표로 보여준다(볼트 경로, git, 인덱스 줄 수). 종료 코드 3이면 볼트가 없다고 알린다.
2. 바꿀 항목을 묻는다. 한 번에 하나씩 처리한다.
   - 볼트 위치: `config set vault <경로>` 후 해당 위치에 볼트가 없으면 `init --vault <경로>`.
   - git 자동 커밋: `init --git` (볼트를 git 저장소로 만들고 저장마다 커밋).
   - 인덱스 줄 수: `config set index_head <N>` (세션 시작 시 BRAIN.md 상단 N줄 주입, 기본 40).
3. 볼트가 없고 사용자가 원하면 `init` (기본 `~/brain/`).
4. 마지막에 안내 3줄:
   - Obsidian에서 보기: Obsidian → "Open folder as vault" → 볼트 폴더 선택. [[링크]]와 그래프 뷰가 그대로 동작한다.
   - 데이터는 이 컴퓨터의 볼트 폴더에만 있고 외부 서버로 보내지 않는다.
   - 플러그인을 지워도 볼트는 남는다(볼트는 플러그인 밖에 있음).

## 위젯 설정 (비서 모드)

대시보드 「오늘」과 brain-today 브리핑에 내 자동화 상태를 붙이려면 위젯을 설정한다.

1. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py config init-widgets` — 예시 `~/.config/second-brain/widgets.json`을 만든다(이미 있으면 덮어쓰지 않음).
2. 파일을 열어 내 로그·상태 파일 경로로 고치도록 안내한다. 필드 요약:
   - 공통: `id`, `title`, `kind`(log · json · csv · markdown · command), `source`(`~` 허용, 홈 밖·`..` 금지)
   - log: `lines`(마지막 N줄), `status.ok_pattern`·`status.fail_pattern`(정규식), `status.stale_minutes`(이 시간 넘게 갱신 없으면 stale)
   - json: `fields`(점 경로 `a.b` 가능) · csv: `x`, `y`(열 이름), `last`(마지막 N행) · markdown: `lines`(첫 N줄)
   - command: `source`가 셸 명령, `timeout_sec`. 최상위 `"allow_commands": true`일 때만 실행한다.
3. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py widgets` 로 각 위젯 상태(ok/warn/fail/stale/missing/unknown)를 확인한다.

자세한 표는 플러그인 `docs/vault-format.md`의 "위젯 설정" 절에 있다.

## 비서 설정 (2차)

한 번에 하나씩, 사용자가 원하는 것만.

- **캘린더**: 구글만 쓰면 구글 캘린더 설정 → 해당 캘린더 → "iCal 형식의 비공개 주소"를 복사해 `~/.config/second-brain/google.ics.url` 첫 줄에 직접 저장하도록 안내한다(주소는 비밀이라 대화에 붙이지 말라고 말한다). 파일이 생기면 `calendar add ics 구글 --url-file ~/.config/second-brain/google.ics.url` → `calendar test`. 맥 캘린더 앱에 계정이 있으면 `calendar add eventkit 맥`(첫 실행 때 권한 허용).
- **비서 이름**: `config set assistant_name <이름>` (코어 화면·자유 질문의 자기 호칭).
- **알림**: 카카오 "나에게 보내기" 헬퍼 스크립트가 있으면 `config set kakao_cmd <경로>`. 없으면 macOS에서는 알림 센터(`osascript`)로 자동 대체되고, 그것도 안 되면 로그에만 남는다고 설명한다. `notify test`로 실제로 어느 채널이 뜨는지 바로 확인시켜준다.
- **Claude가 대신 쓰는 것**: `enrich`(가져온 노트 정제), `prepare`(준비 제안, 06:40 에이전트), `journal`(21:30 마감이 씀), `retro`(월 09:00 에이전트), `review --semantic`. 모두 제안이고 볼트 반영은 사용자가 보드에서 채택하거나 `link A B`를 실행할 때만이라고 설명한다.
- **알림 에이전트(macOS)**: `agents install`(기본 넷: brief remind evening backup) 또는 `agents install prepare retro`처럼 골라서. `--dry-run`으로 만들 파일을 먼저 보여줄 수 있다. `agents status`로 확인, `agents remove`로 제거. 위젯(brain-brief 등)이 자동으로 추가된다. 다른 OS면 cron에 `brain.py brief --kakao`(07:00)·`remind --kakao`(*/10)·`brief --evening --kakao`(21:30)를 직접 등록하도록 안내.
- **지금 실행 버튼**: 보드·사무실에서 자동화를 바로 돌리려면 `~/.config/second-brain/widgets.json`에 `"allow_run": true`. 크론 `>> 로그` 또는 launchd `StandardOutPath`가 위젯 `source`와 같은 것만 실행할 수 있다.
- **화면**: `serve` 뒤 `/`(보드) · `/core`(코어) · `/office`(사무실).
