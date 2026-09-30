---
name: brain-review
description: 세컨드브레인 볼트의 주간 리뷰. 최근 노트 요약, 되돌아볼 날짜가 된 결정, 고아 노트, 링크 제안을 정리하고 BRAIN.md 인덱스를 갱신한다.
when_to_use: 사용자가 "이번 주 정리해줘", "브레인 리뷰", "주간 리뷰", "최근에 뭐 기록했지"라고 할 때 또는 /second-brain:brain-review 호출 시.
argument-hint: "[--days N]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py *) Bash(python3 ~/.local/k-skill-cron/notify_kakao.py *) Bash(test *) Read
---

# 주간 리뷰

1. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py review --days <N, 기본 7> --semantic --json` (--semantic은 Claude가 내용상 관련 쌍을 덧붙임, 이유가 「의미:」로 시작. 느리거나 실패하면 빼고 다시)
   주간 회고 노트를 남기고 싶다고 하면 `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py retro --days <N>` → journal/YYYY/날짜-weekly.md에 이번 주·눈에 띄는 것·되돌아볼 질문 3개·다음 주. 질문은 사용자에게 그대로 던지고 답을 받으면 그 노트 「메모」로 남긴다.
2. 결과를 네 절로 보여준다.
   - 신규 노트: 타입별로 묶어 각 1줄(제목 + [[파일명]]).
   - 되돌아볼 결정: revisit 도래 결정마다 결정 1줄 + "지금 이 결정 유지할까요, 바꿀까요?" 질문 1줄. 바꾼다고 하면 brain-decide 절차로 새 결정을 만들고 supersede.
   - 고아 노트: 어디에도 링크되지 않은 노트 목록.
   - 링크 제안: 연결 후보 쌍과 이유 1줄. 사용자가 승인한 쌍만 `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py link A B` 실행.
3. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py index` 로 BRAIN.md 갱신.
4. `test -f ~/.local/k-skill-cron/notify_kakao.py` 가 성공하면 "요약을 카톡으로 보낼까요?"라고 제안만 한다. 승인 시에만 `python3 ~/.local/k-skill-cron/notify_kakao.py "<5줄 이내 요약>"` 실행. 자동 전송 금지.
5. 사용자가 리뷰 내용을 인쇄하거나 브레인을 안 쓰는 사람에게 공유하고 싶어 하면(예: "내보내기", "한 파일로", "인쇄해서 주고 싶어") `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py export --html <경로> [--since ...] [--type ...] [--project ...]`로 외부 자산 없는 단일 HTML을 만들어 안내한다.

해당 절에 항목이 없으면 "없음" 한 단어로 둔다. 볼트가 없으면(종료 코드 3) init 을 제안한다.
