---
name: brain-doctor
description: 세컨드브레인 설치·연결 상태를 점검한다(볼트·노트·정제 대기·볼트 점검·Python·Claude CLI·캘린더·캘린더 주소 파일·카톡 헬퍼·자동화 위젯·알림 에이전트·캐시 폴더). ✗ 항목마다 고치는 법을 안내하고, brain.py 명령으로 고칠 수 있는 것은 승인받아 대신 실행해 준다.
when_to_use: 사용자가 "점검해줘", "왜 안 돼", "비서 상태 확인", "세컨드브레인 헬스체크", "설정 뭐가 빠졌어"라고 할 때 또는 /second-brain:brain-doctor 호출 시.
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py *) Read
---

# 점검

1. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py doctor --json`을 실행한다. 종료 코드 2는 "고칠 게 있다"는 뜻이고 오류·크래시가 아니다(0=모두 정상, 2=✗ 항목 있음, 3=볼트 없음 계열의 다른 오류).
2. 결과의 `items`(순서대로 볼트·노트·정제 대기·볼트 점검·Python·Claude CLI·캘린더·캘린더 주소 파일·카톡 헬퍼·자동화 위젯·알림 에이전트·캐시 폴더 중 해당되는 것)를 ✓/✗ 표로 보여준다: 이름 · detail, ✗면 그 옆에 fix도 함께.
3. ✗ 항목마다 fix를 보고 하나씩 처리한다(한 항목에 한 번만 묻는다):
   - fix가 `brain.py` 명령이면(`config init-widgets`, `agents install`/`agents install --force`, `enrich`, `calendar add ics ... --url-file ...`, `calendar test`, `config set ask_cmd ...`, `config set kakao_cmd ...` 등) "지금 실행할까요?"라고 묻고, 승인해야만 그 명령을 그대로 실행한다.
   - `enrich`는 Claude 토큰을 쓰는 명령이라 다른 fix보다 한 번 더 분명히 확인받는다("정제에는 Claude 호출이 들어가요, 실행할까요?"). 명시적 "네"가 없으면 절대 실행하지 않는다.
   - fix가 외부 설치·수동 설정(Claude CLI 설치 후 PATH, 구글 캘린더 비공개 ICS 주소를 파일에 저장 등)이면 안내만 하고 대신 실행하지 않는다.
4. "볼트 점검"이 ✗면 `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py lint --json`을 실행해 어떤 코드가 몇 건인지 보여준다(예: link-broken 3건, tags-not-list 1건). 그중 `fixable: true`인 항목이 있으면 "`lint --fix`로 고칠까요?"라고 물어 승인받은 뒤에만 `lint --fix`를 실행한다. `frontmatter-missing`·`type-invalid`·`title-duplicate`처럼 고칠 수 없는 항목은 안내만 하고 대신 손대지 않는다.
5. "자동화 위젯"이 ✗면(문제 위젯 목록이 detail에 있음) `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py widgets`로 각 위젯 상태(ok/warn/fail/stale/missing/unknown)를 보여주고, 대시보드 보드의 「자동화」 섹션에서 직접 보라고 안내한다. 이미 끝난 자동화라면 `~/.config/second-brain/widgets.json`에서 그 위젯에 `"state": "paused"`를 주면 다음 점검부터 문제 목록에서 빠진다고 알려준다(파일은 직접 열어 고치도록 안내, 대신 편집하지 않는다).
6. 모든 항목이 ✓면 "모두 정상이에요"로 끝낸다.

금지: doctor가 보여주지 않는 항목을 지어내지 않는다. 체크박스 토글처럼 CLI에 없는 명령을 만들지 않는다. `enrich`를 승인 없이 실행하지 않는다.
