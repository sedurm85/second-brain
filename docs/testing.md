# 테스트 가이드

이 프로젝트의 테스트는 `tests/` 아래에 있고, 두 층으로 나뉜다.

- `tests/*.py`: 표준 라이브러리 `unittest`만 쓰는 단위·통합 테스트(순수 함수, 스레드 서버 + `urllib`). CI에서 항상 돈다.
- `tests/e2e/*.py`: playwright(+chromium)로 실제 브라우저를 띄워 `web/*.html`을 검증하는 브라우저 회귀 테스트. **로컬 전용** — playwright가 없는 CI에서는 자동으로 skip된다.

## 단위 테스트 실행

```bash
python3 -m unittest discover -s tests -q
```

`tests/`에는 `tests/e2e/`도 포함되므로(`__init__.py`가 있는 패키지라 `discover`가 재귀적으로 들어간다),
위 한 줄이 단위 테스트와 e2e 테스트를 모두 대상으로 한다. playwright가 없으면 e2e 쪽은 자동으로 skip 처리되고
나머지 단위 테스트만 실행된다 — 그래서 CI에서도 항상 초록불이다.

## e2e(브라우저) 스위트 실행

로컬에서 실제로 브라우저를 띄워 검증하려면 먼저 playwright와 chromium을 설치한다(이 문서는 안내만 한다 —
이 저장소 CI 환경에는 설치하지 않는다):

```bash
pip install playwright
playwright install chromium
```

설치 후에는 위와 같은 `python3 -m unittest discover -s tests -q` 한 줄로 단위 테스트와 함께 실행되거나,
e2e만 따로 돌리려면:

```bash
python3 -m unittest discover -s tests.e2e -q   # 또는: python3 -m unittest tests.e2e.test_board -v
```

각 테스트 파일(`test_board.py`/`test_office.py`/`test_core.py`/`test_report.py`)은 클래스당 한 번씩
`python3 scripts/brain.py serve --demo --port <임의 포트>`를 서브프로세스로 띄운다. 이때 `HOME`·
`XDG_CACHE_HOME`을 임시 디렉터리로 돌리고 `SECOND_BRAIN_OFFLINE=1`·`SECOND_BRAIN_NO_LAUNCHCTL=1`·
`SECOND_BRAIN_JOBS_DIR=<임시 빈 폴더>`를 지정하므로, **실제 `~/brain`·`~/.config`·`~/.cache`는 절대
건드리지 않는다.**

### 뭘 확인하나

- **board(`/`)**: 히어로 보고 3문장 이상, 내비(일지/사람/리포트), `/`가 검색창을 포커스, 명령 팔레트(`>할 일`),
  `?` 도움말 열기/Esc 닫기, 「다가오는 일정」 클릭 → 브레인 제안 패널 → 채택 시 `/api/today` 제안 건수 감소,
  할 일 추가, 노트 패널 메모 남기기, 개요 통계 타일 5개, 사람 카드, 390px에서 가로 스크롤 없음.
- **office(`/office`)**: 방 2개 이상·`.kpi`·`#shiftStrip`, 데스크 클릭 → 상세 패널(「이번 주 한 줄」 버튼),
  390px에서 가로 스크롤 없음 + 상세 패널이 하단 바텀시트(`position: fixed; bottom: 0`)로 바뀜.
- **core(`/core`)**: "오늘 뭐 있어" → 자막에 "일정", "회사 현황" → "성공률" 또는 "첫 기록", 톱니 → 설정
  다이얼로그, "팀 주간회의 제안 채택해" 음성 명령이 실제로 `/api/today` 제안 건수를 줄이는지.
- **report(`/report`)**: `<h2>` 5개 이상, 인쇄용 `@media print` 스타일 존재.

가장 값어치 있는 검증은 **모든 테스트가 `page.on("pageerror")`로 잡힌 처리되지 않은 JS 예외가 0건인지**를
확인한다는 점이다(`console.error` 로그도 함께 잡는다) — 셀렉터가 우연히 맞아도 화면이 실제로는 깨져 있는
경우(예: 이벤트 핸들러 안에서 예외가 나서 나머지 로직이 멈춘 경우)를 잡아낸다.

### 건너뛰기(skip) 동작 확인

playwright 부재 상황을 로컬에서 흉내내려면:

```bash
SECOND_BRAIN_E2E_DISABLE=1 python3 -m unittest discover -s tests -q
```

`Ran N tests ... OK (skipped=16)` 처럼 e2e 16건이 모두 skip으로 표시되고 나머지는 그대로 통과해야 한다.
실제 CI에서는 이 환경변수 없이도, playwright가 `import`되지 않으므로 같은 skip이 자연히 일어난다.
