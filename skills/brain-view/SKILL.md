---
name: brain-view
description: 사용자가 '대시보드 보여줘', '브레인 열어', '코어 화면', '자비스 켜줘', '그래프로 보고 싶어', '뷰어' 라고 하거나 `/second-brain:brain-view`를 치면 로컬 대시보드 서버를 띄우고 URL을 안내
when_to_use: 볼트를 한눈에(요약·링크 그래프·타임라인·결정·프로젝트) 보고 싶을 때. /second-brain:brain-view 호출 시.
argument-hint: "[--port N] [--demo]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/brain.py *) Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/brain.py" *) Bash(test *) Bash(lsof -ti tcp:*) Bash(kill *)
---

# 대시보드 열기

서버는 127.0.0.1 에만 바인딩되는 로컬 서버다. 볼트 쓰기는 일정 노트(메모·준비 체크)만, 페이지 세션 토큰이 있어야 한다.

화면은 셋이다. `/office`는 **사무실**(자동화 하나가 직원 하나, 팀별 방, 지금 도는 직원은 타이핑, 실패는 붉은 램프, Claude 배경 작업은 「Claude 작업실」에 앉아 지금 하는 일을 말풍선으로). `/`는 **보드**(오늘 보고·시간표·이번 주·그래프·자동화·결정·타임라인·프로젝트), `/core`는 **코어**(살아 있는 구체 + 최소 HUD, 브리핑을 음성으로 읽고 마이크·입력창으로 질문). 사용자가 '코어', '자비스', '말하는 화면'이라고 하면 `http://127.0.0.1:7777/core`, '사무실', '직원들', '일하는 모습'이라고 하면 `http://127.0.0.1:7777/office`를 안내한다. 비서 이름은 `brain.py config set assistant_name 이름`.

1. 먼저 상주 서버 여부를 확인: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/brain.py" agents status --json` 을 읽어 `name == "serve"` 항목을 찾는다.
   - `installed`가 true 이고 `reachable`이 true 면 이미 로그인 시 자동 실행·재시작되는 상태다. 서버를 새로 띄우지 말고 바로 `http://127.0.0.1:<port>`, `/core`, `/office` URL만 안내한다(2~5단계 생략).
   - `installed`가 true 인데 `reachable`이 false 면(막 재부팅됐거나 죽었다가 KeepAlive가 아직 안 올린 경우) 잠깐 뒤 다시 `agents status --json` 한 번만 재확인하고, 계속 안 뜨면 2단계 임시 실행으로 넘어간다.
   - `installed`가 false 이고 macOS라면, "로그인할 때마다 자동으로 켜두고 싶으면 `agents install serve` 로 상주시킬 수 있어요. 지금 등록할까요?"라고 한 번 제안한다. 사용자가 동의하면 `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/brain.py" agents install serve` (포트 지정 시 `--port N` 추가)를 실행해 등록하고, 그 결과로 안내되는 URL을 알려준다(2~5단계 생략). 사용자가 원치 않거나 macOS가 아니면 2단계로 넘어간다.
2. (임시 실행 — 상주 서버가 없거나 사용자가 상주를 원치 않을 때) 볼트 확인: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/brain.py" index --head 1` 의 stdout 이 비어 있으면 볼트가 없는 것이다.
3. 서버 실행 — 반드시 Bash `run_in_background: true` 로 실행한다(포그라운드로 돌리면 대화가 멈춘다).
   - 볼트 있음: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/brain.py" serve --open`
   - 볼트 없음: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/brain.py" serve --open --demo`
   - 사용자가 포트를 지정하면 `--port N` 을 붙인다.
4. 백그라운드 출력의 첫 줄(`http://127.0.0.1:7777` 형식)을 읽어 URL을 안내한다. 브라우저가 자동으로 안 열렸으면 그 URL을 직접 열라고 말한다.
   - `--demo` 로 띄웠다면 "가공된 샘플 데이터예요. 실제 볼트는 `/second-brain:brain-setup` 으로 만들 수 있어요."라고 반드시 밝힌다.
   - 포트 바인딩 실패(종료 코드 2, "바인딩하지 못했습니다")면 `--port 7778` 로 한 번만 재시도한다.
5. 종료 방법 안내: "끝나면 '대시보드 꺼줘'라고 하세요." 요청 시 실행한 백그라운드 작업을 중지하거나, 없으면 `lsof -ti tcp:<포트>` 로 PID 를 찾아 `kill <PID>` 한다(다른 프로세스는 건드리지 않는다). 상주 서버(`agents install serve`)로 띄운 경우는 임시 프로세스가 아니므로 끄지 말고, 원하면 `agents remove serve` 안내로 대신한다.

데모 볼트 위치는 `~/.cache/second-brain/demo` 이며 실행할 때마다 새로 만들어진다.
