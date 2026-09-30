---
name: brain-import
description: Obsidian 볼트, Claude Code 메모리 디렉토리, 마크다운 폴더, 애플 메모(Notes.app), URL을 세컨드브레인 볼트 형식으로 가져온다. 원본은 보존하고 중복은 건너뛴다.
when_to_use: 사용자가 "기존 노트 가져와", "옵시디언 볼트 옮겨줘", "클로드 메모리 브레인에 넣어", "이 폴더 import", "애플 메모 가져와", "메모 앱 노트 옮겨줘"라고 할 때 또는 /second-brain:brain-import 호출 시.
argument-hint: "<경로|URL|애플메모>"
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

## 애플 메모(Notes.app)인 경우
"애플 메모 가져와", "메모 앱 노트 옮겨줘"처럼 경로 없이 메모 앱을 지목하면:
1. 먼저 미리보기: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py import --apple-notes --dry-run` (특정 폴더만 원하면 `--folder 이름`을 여러 번, 최근 것만이면 `--since YYYY-MM-DD`).
2. 최초 실행이면 메모 앱 접근 권한 창이 뜰 수 있다고 안내한다.
3. 생성/갱신/건너뜀 수를 보여주고 "진행할까요?"라고 한 번 묻는다.
4. 승인 시 `--dry-run` 없이 실행. 이미 가져온 노트는 `imported_from`(`apple-notes:<id>`) 기준으로 다시 만들지 않고, 메모 앱에서 수정된 것만 본문을 갱신한다.
5. 결과 요약 + "`brain.py enrich`로 제목·태그를 더 정리할 수 있다"고 힌트. 메모 앱 원본은 읽기만 하고 수정하지 않는다.

볼트가 없으면(종료 코드 3) init 을 먼저 제안한다.
