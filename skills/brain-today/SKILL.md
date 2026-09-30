---
name: brain-today
description: 개인 비서 브리핑. 되돌아볼 결정, 멈춘 자동화(위젯 fail/stale), inbox 할 일, 이번 주 신규 기록을 3~6줄로 알려준다.
when_to_use: 사용자가 "오늘 뭐 있어", "비서", "브리핑 해줘", "상태 알려줘", "자비스", "오늘 할 일"이라고 할 때 또는 /second-brain:brain-today 호출 시.
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py *) Bash(python3 ~/.local/k-skill-cron/notify_kakao.py *) Bash(test *)
---

# 오늘 브리핑

1. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py today --json`
2. 결과를 3~6줄로 말한다. 첫 줄은 `greeting` + 날짜·요일, 그다음은 아래 순서대로, 항목이 없는 줄은 뺀다.
   - 되돌아볼 결정(`revisit`): 제목 + `days_left`(0이면 "오늘", 음수면 "N일 지남"). 최대 3개.
   - 멈춘 자동화(`top_widgets` 중 status `fail`/`stale`): 제목 + summary 핵심만. `missing`은 "파일 없음"으로 짧게.
   - inbox 할 일(`inbox`): 개수 + 앞 3개.
   - 이번 주 신규(`this_week`): 노트 N · 결정 M.
3. 위젯이 하나도 없으면(`widgets_total`이 0) 마지막에 한 줄 안내: "`brain.py config init-widgets`로 예시 widgets.json을 만들면 내 자동화 상태도 같이 볼 수 있어요."
4. `test -f ~/.local/k-skill-cron/notify_kakao.py` 가 성공하면 "카톡으로도 보낼까요?"라고 한 번만 묻는다. 승인하면 결과의 `kakao`(200자 이내) 값을 그대로 `python3 ~/.local/k-skill-cron/notify_kakao.py "<kakao>"`로 보낸다. 묻지 않고 보내지 않는다.

숫자·제목은 JSON에 있는 값만 쓰고 추측하지 않는다. 볼트가 없어도 위젯 브리핑은 동작하며, 이때 "볼트가 아직 없어요 — brain-setup으로 만들 수 있어요" 한 줄을 붙인다.
