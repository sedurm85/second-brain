---
name: brain-journal
description: 오늘 하루를 Claude가 5줄 일지로 써서 journal/YYYY/YYYY-MM-DD.md에 남기고, 지난 한 주를 주간 회고(journal/YYYY/YYYY-MM-DD-weekly.md)로 되돌아보며 코칭 질문에 답한 내용을 노트로 남긴다.
when_to_use: 사용자가 "오늘 일지 써줘", "오늘 뭐 했지", "일지 읽어줘", "이번 주 회고", "주간 회고 써줘", "회고 질문", "지난주 어땠어"라고 할 때 또는 /second-brain:brain-journal 호출 시.
argument-hint: "[읽기|회고]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py *) Bash(python3 ~/.local/k-skill-cron/notify_kakao.py *) Bash(date *) Bash(test *) Read
---

# 일지·회고

## 오늘 일지

1. 사용자가 "뭐가 담길지 먼저 보고 싶다"고 하면 `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py journal --dry-run --json`으로 재료(오늘 만든 노트·일정·완료한 할 일·자동화 문제 등)만 먼저 보여준다. 그렇지 않으면 바로 2로 간다.
2. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py journal --json`을 실행한다.
   - 오류가 "오늘 일지가 이미 있어요: ... (--force로 다시)"이면 그대로 전하고 "다시 쓸까요?"라고 물은 뒤, 승인해야만 `--force`를 붙여 다시 실행한다. 되묻지 않고 바로 `--force`를 쓰지 않는다.
   - 재료가 없어 쓰지 않았다는 응답("N 기록이 없어 일지를 쓰지 않았어요")은 그대로 전한다.
3. 성공하면 결과의 `lines`(오늘 한 일 3~5줄)를 그대로 불릿으로 보여주고, 이어서 `kakao` 한 줄을 "카톡: ..." 형식으로 보여준다. 노트 경로도 한 줄 알린다.
4. `test -f ~/.local/k-skill-cron/notify_kakao.py`가 성공하면 "카톡으로도 보낼까요?"라고 한 번만 묻는다. 승인해야만 처음부터 `journal --kakao`(재작성이 필요하면 `--force --kakao`)로 다시 실행해 발송한다. 묻지 않고 보내지 않는다.

## 일지 읽어줘

- "오늘" 일지면 오늘 날짜를 셸에서 계산해 경로를 만든다: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py show journal/$(date +%Y)/$(date +%F).md`
- 특정 날짜를 말하면(예: "지난주 화요일") 그 날짜로 같은 패턴의 경로를 만들어 `show`한다: `journal/YYYY/YYYY-MM-DD.md`
- 없는 날짜면 `show`가 내는 오류를 그대로 전한다(추측해서 다른 날짜를 보여주지 않는다).

## 주간 회고

1. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py retro --json` (기간을 다르게 말하면 `--days N`)을 실행한다.
   - 오류가 "오늘 회고가 이미 있어요: ... (--force로 다시)"이면 그대로 전하고 "다시 쓸까요?"라고 물은 뒤, 승인해야만 `--force`를 붙여 다시 실행한다.
   - 기록이 없어 쓰지 않았다는 응답은 그대로 전한다.
2. 성공하면 `week`(이번 주 한 일)를 불릿으로 보여준 뒤, `questions`(되돌아볼 질문, 정확히 3개)를 한 번에 나열하지 말고 하나씩 순서대로 묻는다.
3. 사용자가 질문에 답하면:
   - 답을 요약·의역하지 않고 사용자가 말한 원문 그대로 다음으로 노트를 만든다(본문은 "질문 원문 + 빈 줄 + 답 원문"): `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py new --type note --title "<질문 앞 30자> — 답" --tags 회고,답 --body $'<질문 원문>\n\n<사용자 답 원문>'`
   - 그 노트를 방금 만든 회고 노트와 연결한다: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py link <새 노트 경로> <회고 노트 경로>`
   - 회고 노트의 그 질문 체크박스는 CLI로 토글하는 명령이 없다. "보드에서 그 질문을 체크하면 완료로 표시돼요"라고 안내만 하고, 임의로 체크된 것처럼 말하거나 새 명령을 지어내지 않는다.
4. 질문 3개를 다 처리한 뒤에만 `kakao` 한 줄을 "카톡: ..." 형식으로 보여준다.
5. `test -f ~/.local/k-skill-cron/notify_kakao.py`가 성공하면 "카톡으로도 보낼까요?"라고 한 번만 묻는다. 승인해야만 처음부터 `retro --kakao`(재작성이 필요하면 `--force --kakao`)로 다시 실행해 발송한다.

## 공통 규칙

- 사용자의 답변·재료 문장은 절대 의역하지 않는다. 있는 그대로 옮긴다.
- 모든 응답은 해요체.
- 카톡은 위 두 승인 지점을 제외하면 절대 자동으로 보내지 않는다.
- 재료·결과에 없는 사실을 만들지 않는다. `journal`/`retro`가 내는 숫자·문장만 쓴다.
