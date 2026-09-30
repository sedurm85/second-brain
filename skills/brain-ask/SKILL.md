---
name: brain-ask
description: 세컨드브레인 볼트를 검색해 과거 기록·아이디어·결정의 이유를 [[파일]] 인용과 함께 답한다. 기록에 없으면 "기록 없음"이라고 답하고 추측하지 않는다.
when_to_use: 사용자가 "그때 왜 그렇게 했지", "언제 정했지", "뭐라고 했지", "예전에 정한 게 뭐였지", "brain에서 찾아", "기록 있어?", "전에 메모한 거"처럼 과거 기록을 물을 때 또는 /second-brain:brain-ask 호출 시.
argument-hint: "<질문>"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py *) Read
---

# 볼트에 묻기

1. 질문에서 핵심어 2~5개를 뽑는다(조사·의문사 제외). 기간·프로젝트·타입 단서가 있으면 필터로 쓴다.
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py search "<핵심어>" --limit 8 --json [--type decision] [--project P] [--since YYYY-MM-DD]`
   결과가 넓으면 검색어 안에 연산자를 직접 섞어 좁혀도 된다: `type:decision|note|idea|source|meeting|event|journal|project|person`(반복하면 OR), `tag:xxx`(반복하면 AND), `project:xxx`, `since:7d`/`since:2026-01-01`, `until:...`, `has:summary`, `has:revisit`, `status:open|decided|superseded`, `is:orphan`(링크 없는 노트), `-단어`(제외), `"정확한 문구"`(제목·본문에 그대로 있어야 함).
2. 결과가 0건이면 동의어·영문 표기로 한 번만 재검색한다.
3. 상위 3~5개를 `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py show <경로>` 로 읽는다.
4. 종합 답변 규칙:
   - 모든 주장 뒤에 `[[파일명]]` 인용. 인용 없는 문장 금지.
   - decision 노트가 있으면 "당시 이유"를 먼저 쓰고, 상태가 superseded면 대체한 결정도 함께 밝힌다.
   - 기록에 없는 부분은 "기록 없음"이라고 쓰고 추측·일반 지식으로 메우지 않는다.
   - 여러 기록이 충돌하면 날짜와 함께 둘 다 제시한다.
5. 답변 끝에 "새 노트로 저장할까요?" 같은 제안은 붙이지 않는다.

볼트가 없으면(종료 코드 3) "아직 볼트가 없어요. '기억해둬'로 첫 노트를 만들면 시작돼요." 한 줄로 답한다.
