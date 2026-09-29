---
name: brain-setup
description: 세컨드브레인 볼트 설정(위치, git 자동 커밋, 세션 시작 인덱스 줄 수)을 확인·변경하고 볼트를 만든다.
when_to_use: 사용자가 "브레인 설정", "볼트 위치 바꿔", "세컨드브레인 셋업"이라고 할 때 또는 /second-brain:brain-setup 호출 시.
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py *) Read
---

# 설정

1. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py config get` 으로 현재 설정을 표로 보여준다(볼트 경로, git, 인덱스 줄 수). 종료 코드 3이면 볼트가 없다고 알린다.
2. 바꿀 항목을 묻는다. 한 번에 하나씩 처리한다.
   - 볼트 위치: `config set vault <경로>` 후 해당 위치에 볼트가 없으면 `init --vault <경로>`.
   - git 자동 커밋: `init --git` (볼트를 git 저장소로 만들고 저장마다 커밋).
   - 인덱스 줄 수: `config set index_head <N>` (세션 시작 시 BRAIN.md 상단 N줄 주입, 기본 40).
3. 볼트가 없고 사용자가 원하면 `init` (기본 `~/brain/`).
4. 마지막에 안내 3줄:
   - Obsidian에서 보기: Obsidian → "Open folder as vault" → 볼트 폴더 선택. [[링크]]와 그래프 뷰가 그대로 동작한다.
   - 데이터는 이 컴퓨터의 볼트 폴더에만 있고 외부 서버로 보내지 않는다.
   - 플러그인을 지워도 볼트는 남는다(볼트는 플러그인 밖에 있음).
