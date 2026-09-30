---
name: brain-office
description: 자동화 위젯(사무실 직원)을 대시보드를 열지 않고 대화로 채용·이동·이름변경·퇴사·정지·재개·실행·요약 확인한다. widgets.json의 allow_hire·allow_run 없이도 사용자 자신의 터미널 권한으로 처리한다.
when_to_use: 사용자가 "사무실 상태", "직원 채용", "자동화 등록해줘", "OO 로그 직원으로 붙여줘", "OO 직원 쉬게 해", "부서 이동", "퇴사 처리", "누가 일하고 있어", "직원 한 줄 요약"이라고 할 때 또는 /second-brain:brain-office 호출 시.
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py *) Bash(test *) Bash(ls *) Read
---

# 사무실 (자동화 직원)

CLI는 사용자 자신의 터미널이라 대시보드의 `allow_hire`/`allow_run` 승인 버튼 없이도 바로 채용·이동·퇴사·실행할 수 있다. 그 대신 매 단계 무엇을 할지 먼저 말하고 확인받는다.

## (a) 사무실 상태 보기

`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py widget list --json` 으로 전체 위젯을 받아 팀별로 묶어 요약한다(팀 없으면 "무소속"). 각 줄은 `상태 · 제목 · (마지막 결과 요약)`. paused는 따로 "멈춤"으로 접어 보여준다. 특정 팀만 궁금하면 `widget list --team <팀>`.

"누가 일하고 있어" 같은 질문이면 status가 ok인 항목 위주로 짧게 답한다.

## (b) 직원 채용 (자동화를 위젯으로 등록)

1. 빠진 정보(제목, 로그/상태 파일 경로, 팀)를 순서대로 묻는다. 경로는 `~` 또는 절대경로만 되고, 홈(`$HOME`) 밖이나 `..`가 들어가면 거부된다고 먼저 설명한다.
2. `Bash(test -f <경로>)` 로 파일이 실제로 있는지 확인한다. 없으면 만들어지는 시점(예: 다음 크론 실행)을 물어보고, 지금은 없다고 알린다.
3. `Read` 로 로그 파일의 마지막 몇 줄을 읽고, 성공/실패를 구분할 만한 단어나 패턴을 골라 `--ok`/`--fail` 정규식을 제안한다(예: "완료"/"OK" → `--ok "완료|OK"`, "실패"/"Error" → `--fail "실패|Error"`). 사용자가 다르게 원하면 그대로 따른다.
4. 확정되면:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py widget add "<제목>" "<경로>" --kind log --team "<팀>" --ok "<패턴>" --fail "<패턴>"
   ```
   kind는 로그가 아니면 json · csv · markdown 중에서 고른다(명령 실행형은 채용으로 못 만든다).
5. `widget show <id>` 로 방금 등록된 위젯의 평가 상태를 확인해 결과를 보여준다.

## (c) 이동 · 이름변경 · 정지 · 재개 · 퇴사

- 부서 이동: `widget move <id> <팀>`
- 이름 변경: `widget rename <id> "<새 제목>"`
- 쉬게 하기(경고 대상에서 뺌, 기록은 유지): `widget pause <id>`
- 다시 켜기: `widget resume <id>`
- 퇴사(위젯 등록만 삭제, 로그 파일은 그대로 남음): 실행 전에 "정말 `<id>`를 퇴사 처리할까요? 로그 파일은 지워지지 않아요"로 한 줄 확인부터 받고, 승인 후에만 `widget remove <id>` 실행. `brain-` 로 시작하는 비서 에이전트는 여기서 못 지운다(`agents remove` 안내).

## (d) 한 줄 요약 (팀장 브리핑)

`widget brief <id> [--force]` — 최근 로그를 Claude가 읽어 "이번 주 한 일 / 문제 / 기분"을 한 줄씩 만든다. 하루 한 번 캐시되므로 다시 부르려면 `--force`가 필요하다는 것과, 이 호출은 Claude API를 한 번 더 쓴다는 것을 실행 전에 말해준다.

## (e) 마무리 원칙

- 자동화가 끝났거나 더 안 쓰면 지우지 말고 `pause`로 접어 두라고 권한다(이력·로그가 남아 있어야 나중에 다시 볼 수 있다).
- 지금 실행(`widget run <id> [--dry]`)은 크론의 `>> 로그` 경로 또는 launchd `StandardOutPath`가 위젯 source와 같아야 실행 방법을 찾는다. 모르면 `--dry`로 먼저 무엇을 실행할지 보여준다.
- `--json`이 필요하면 아무 액션에나 붙여서 원본 데이터를 받을 수 있다.
