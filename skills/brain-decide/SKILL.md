---
name: brain-decide
description: 대화에서 내린 결정을 ADR(의사결정 기록) 형식으로 세컨드브레인 볼트 decisions/에 저장하고, 관련 노트와 링크하며, 이전 결정을 뒤집는 경우 superseded로 표시한다.
when_to_use: 사용자가 "결정했어", "정했어", "이렇게 가자", "A로 하자", "이걸로 확정", "결정 기록해줘"라고 할 때 또는 /second-brain:brain-decide 호출 시.
argument-hint: "[결정 내용]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py *) Read Write
---

# 결정 기록 (ADR)

1. 대화에서 아래 항목을 뽑아 본문을 만든다. 대화에 없는 선택지·이유는 지어내지 말고 "(대화에서 언급 없음)"으로 둔다.
   ```
   ## 상황
   ## 고려한 선택지
   - 선택지 A — 왜 아닌지 한 줄
   ## 결정
   ## 이유
   ## 되돌아볼 날짜
   ## 관련 노트
   ```
2. 관련 노트 찾기: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py search "<결정 핵심어>" --limit 3 --json`. 상위 3개를 `## 관련 노트`에 `[[파일명]]`으로 적는다.
3. 되돌아볼 날짜: 사용자가 말하지 않으면 오늘+90일(YYYY-MM-DD).
4. 저장: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py new --type decision --title "<결정 한 줄>" --tags a,b [--project P] --revisit <날짜> --body-file <파일>`
5. 2단계에서 찾은 노트마다 `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py link <새 결정 경로> <노트 경로>`.
6. 검색 결과에 같은 주제의 기존 decision이 있고 이번 결정이 그것을 뒤집으면 사용자에게 한 번 확인 후 `decide --supersede <기존 경로> <새 경로>`. 기존 파일은 삭제하지 않는다.
7. 출력: 결정 파일 경로, 되돌아볼 날짜, 링크한 노트 수, (해당 시) 대체한 결정 1줄.

볼트가 없으면(종료 코드 3) `init` 을 한 번 제안한다.
