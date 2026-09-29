---
name: brain-capture
description: 대화 내용·아이디어·자료·URL을 개인 지식 볼트(세컨드브레인)에 마크다운 노트로 저장한다. 대화 맥락에서 제목·타입(note/idea/source/meeting)·태그·프로젝트를 추론한다.
when_to_use: 사용자가 "기억해둬", "저장해", "메모해", "브레인에 넣어", "이거 기록해", "적어둬"라고 하거나, URL을 주며 "나중에 보게"라고 할 때 자동 실행. 결정 사항("A로 하자", "정했어")은 brain-decide를 쓴다.
argument-hint: "[저장할 내용 또는 URL]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py *) Read Write WebFetch
---

# 볼트에 캡처

1. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py config get` 실행. 종료 코드 3(볼트 없음)이면 "~/brain 에 볼트를 만들까요?"라고 한 번만 묻고, 동의하면 `init` 실행.
2. 저장할 내용은 사용자가 말한 **원문 그대로** 본문에 넣는다. 요약·재작성 금지. 본문 끝에 `맥락:` 절을 두고 이 대화에서 왜 나왔는지 2~3줄 적는다.
3. 타입 추론: 일반 메모 note, 떠오른 발상 idea, 회의·통화 내용 meeting, URL/문서 source.
4. URL이면 WebFetch로 제목과 핵심 3줄을 가져와 본문 상단에 두고, 원문 링크는 `--source URL` 로 넘긴다. 타입은 source.
5. 본문이 여러 줄이면 임시 파일에 쓰고 `--body-file` 사용:
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py new --type <타입> --title "<제목>" --tags a,b [--project P] [--source URL] [--people a,b] --body-file <파일>`
6. 태그는 대화에 명시적으로 드러난 것 최대 3개. 프로젝트는 대화에서 확실할 때만.
7. 출력은 두 줄만: 저장된 파일 경로 1줄 + "다음에 '그때 X 뭐라고 했지?'라고 물으면 찾아드려요" 1줄.

금지: 사용자 확인 없이 태그 남발, 내용 요약해서 저장, 볼트 파일 직접 편집(항상 brain.py 경유).
