---
name: brain-event
description: 캘린더 일정에 메모·준비 항목을 남기고 보여준다. 일정마다 볼트에 「일정 노트」(events/)가 생겨 대시보드 상세 패널과 아침 브리핑에 함께 나온다.
when_to_use: 사용자가 "~일정에 메모", "~에 준비할 것 추가", "제주도 여행 준비 목록", "그 일정 메모 보여줘"처럼 특정 일정에 무언가를 적거나 볼 때.
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py *)
---

# 일정 노트

1. 어느 일정인지 찾는다: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py agenda --days 30 --json` 의 `today`/`upcoming`에서 제목·날짜가 맞는 항목의 `key`(형식 `YYYY-MM-DD|제목`)를 쓴다. 후보가 둘 이상이면 날짜를 한 번 확인한다. 캘린더에 없는 일정이면 사용자가 말한 날짜와 제목으로 키를 만든다.
2. 메모: `brain.py event memo "<key>" "<사용자가 말한 원문>"` (여러 줄이면 임시 파일 + `--body-file`). 요약하지 말고 원문 그대로.
3. 준비 항목: `brain.py event todo "<key>" "<항목>"` 을 항목마다 한 번씩.
4. 여러 날 일정(여행 등)은 첫 호출에 `--end YYYY-MM-DD`를 붙이면 같은 이름의 날짜들에 한 노트가 함께 붙는다. 장소는 `--location`.
4-1. 동선: `brain.py event step "<key>" "14:00 집 출발 (자가용 50분)"` — 시각·내용·괄호 안 소요분. 여러 날 계획은 `--day YYYY-MM-DD`로 날짜를 지정. 동선은 그날 보드 시간표에 겹쳐 보이고 `brain.py remind --kakao`(10분마다 launchd)가 시각 10분 전에 카톡으로 알린다. 사용자가 "여행 일정 넣어줘"라며 여러 줄을 말하면 한 줄씩 step으로, 준비물은 todo로 나눈다.
5. 보여주기: `brain.py event show "<key>"`.
6. 출력은 두 줄: 저장된 노트 경로 + "대시보드에서 그 일정을 누르면 같은 메모가 보여요" 또는 준비 진행(n/m).

금지: 노트 파일 직접 편집(항상 brain.py 경유), 임의 요약, 사용자가 말하지 않은 준비 항목 추가.
