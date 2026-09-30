---
name: brain-view
description: 사용자가 '대시보드 보여줘', '브레인 열어', '그래프로 보고 싶어', '뷰어' 라고 하거나 `/second-brain:brain-view`를 치면 로컬 대시보드 서버를 띄우고 URL을 안내
when_to_use: 볼트를 한눈에(요약·링크 그래프·타임라인·결정·프로젝트) 보고 싶을 때. /second-brain:brain-view 호출 시.
argument-hint: "[--port N] [--demo]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py *) Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/brain.py" *) Bash(test *) Bash(lsof -ti tcp:*) Bash(kill *)
---

# 대시보드 열기

서버는 127.0.0.1 에만 바인딩되는 읽기 전용 로컬 서버다. 볼트 파일을 수정하지 않는다.

1. 볼트 확인: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/brain.py" index --head 1` 의 stdout 이 비어 있으면 볼트가 없는 것이다.
2. 서버 실행 — 반드시 Bash `run_in_background: true` 로 실행한다(포그라운드로 돌리면 대화가 멈춘다).
   - 볼트 있음: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/brain.py" serve --open`
   - 볼트 없음: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/brain.py" serve --open --demo`
   - 사용자가 포트를 지정하면 `--port N` 을 붙인다.
3. 백그라운드 출력의 첫 줄(`http://127.0.0.1:7777` 형식)을 읽어 URL을 안내한다. 브라우저가 자동으로 안 열렸으면 그 URL을 직접 열라고 말한다.
   - `--demo` 로 띄웠다면 "가공된 샘플 데이터예요. 실제 볼트는 `/second-brain:brain-setup` 으로 만들 수 있어요."라고 반드시 밝힌다.
4. 포트 바인딩 실패(종료 코드 2, "바인딩하지 못했습니다")면 `--port 7778` 로 한 번만 재시도한다.
5. 종료 방법 안내: "끝나면 '대시보드 꺼줘'라고 하세요." 요청 시 실행한 백그라운드 작업을 중지하거나, 없으면 `lsof -ti tcp:<포트>` 로 PID 를 찾아 `kill <PID>` 한다(다른 프로세스는 건드리지 않는다).

데모 볼트 위치는 `~/.cache/second-brain/demo` 이며 실행할 때마다 새로 만들어진다.
