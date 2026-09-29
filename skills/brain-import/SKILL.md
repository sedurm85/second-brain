---
name: brain-import
description: Obsidian 볼트, Claude Code 메모리 디렉토리, 마크다운 폴더, URL을 세컨드브레인 볼트 형식으로 가져온다. 원본은 보존하고 중복은 건너뛴다.
when_to_use: 사용자가 "기존 노트 가져와", "옵시디언 볼트 옮겨줘", "클로드 메모리 브레인에 넣어", "이 폴더 import"라고 할 때 또는 /second-brain:brain-import 호출 시.
argument-hint: "<경로|URL>"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py *) Bash(ls *) Read WebFetch
---

# 가져오기

인자: `$ARGUMENTS`

## 경로인 경우
1. 경로가 없으면 사용할 수 있는 예시를 안내하고 멈춘다: Obsidian 볼트 폴더, 마크다운 폴더, Claude Code 메모리 디렉토리(`~/.claude*/projects/*/memory/`).
2. 먼저 미리보기: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py import <경로> --dry-run`
3. 가져올 파일 수·건너뛸 중복 수·타입 분포를 요약해 보여주고 "진행할까요?"라고 한 번 묻는다.
4. 승인 시 `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py import <경로>` 실행 후 `index` 로 BRAIN.md 갱신.
5. 결과: 가져온 수, 건너뛴 수, 오류 파일(있으면 경로). 원본 폴더는 수정되지 않았다고 명시한다.

## URL인 경우
brain-capture의 URL 절차를 따른다: WebFetch로 제목·핵심 3줄 → `new --type source --title "<제목>" --source <URL> --body-file <파일>`.

볼트가 없으면(종료 코드 3) init 을 먼저 제안한다.
