---
name: brain-today
description: 개인 비서 브리핑. 오늘 일정(구글 ICS·맥 캘린더), 되돌아볼 결정, 멈춘 자동화(위젯 fail/stale), inbox 할 일, 이번 주 신규 기록을 3~7줄로 알려준다.
when_to_use: 사용자가 "오늘 뭐 있어", "오늘 일정", "이번 주 일정", "비서", "브리핑 해줘", "상태 알려줘", "자비스", "오늘 할 일"이라고 할 때 또는 /second-brain:brain-today 호출 시.
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py *) Bash(python3 ~/.local/k-skill-cron/notify_kakao.py *) Bash(test *)
---

# 오늘 브리핑

1. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py today --json`
2. 결과를 3~7줄로 말한다. 첫 줄은 `greeting` + 날짜·요일, 그다음은 아래 순서대로, 항목이 없는 줄은 뺀다.
   - 오늘 일정(`agenda.today`): 시간 순으로 `HH:MM 제목(@장소)`, 종일은 "종일". `agenda.conflicts`가 있으면 "겹침" 한마디. `agenda.sources`에 status가 ok가 아닌 것이 있고 오늘 일정이 비어 있으면 그 `error`/`hint`를 한 줄로 전한다(캘린더 미연결 안내). "이번 주"를 물으면 `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py agenda --json`의 `upcoming`을 날짜별로.
   - 되돌아볼 결정(`revisit`): 제목 + `days_left`(0이면 "오늘", 음수면 "N일 지남"). 최대 3개.
   - 멈춘 자동화(`top_widgets` 중 status `fail`/`stale`): 제목 + summary 핵심만. `missing`은 "파일 없음"으로 짧게.
   - inbox 할 일(`inbox`): 개수 + 앞 3개.
   - 준비 제안(`suggestions.count`): "채택을 기다리는 준비 제안 N건 — 보드의 일정 카드에서 채택/무시" 한 줄. 회고 질문(`retro_questions`)이 있으면 첫 질문을 그대로 읽어 준다.
   - 이번 주 신규(`this_week`): 노트 N · 결정 M.
3. 위젯이 하나도 없으면(`widgets_total`이 0) 마지막에 한 줄 안내: "`brain.py config init-widgets`로 예시 widgets.json을 만들면 내 자동화 상태도 같이 볼 수 있어요."
4. `test -f ~/.local/k-skill-cron/notify_kakao.py` 가 성공하면 "카톡으로도 보낼까요?"라고 한 번만 묻는다. 승인하면 `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py brief --kakao`를 실행한다(200자 요약을 헬퍼로 발송). 묻지 않고 보내지 않는다.
5. 캘린더 연결 요청("구글 캘린더 붙여줘")이면: 사용자가 구글 캘린더 설정 → 해당 캘린더 → "iCal 형식의 비공개 주소"를 복사해 `~/.config/second-brain/google.ics.url` 파일 첫 줄에 직접 저장하도록 안내한다(주소는 비밀이므로 대화에 붙이지 말라고 말한다). 파일이 생기면 `brain.py calendar add ics 구글 --url-file ~/.config/second-brain/google.ics.url` → `brain.py calendar test`. 맥 캘린더 앱에 계정이 붙어 있으면 `brain.py calendar add eventkit 맥` 뒤 권한 허용 안내.

숫자·제목은 JSON에 있는 값만 쓰고 추측하지 않는다. 볼트가 없어도 위젯 브리핑은 동작하며, 이때 "볼트가 아직 없어요 — brain-setup으로 만들 수 있어요" 한 줄을 붙인다.
