# 세컨드브레인 (second-brain)

> A Claude Code plugin that turns "remember this" into plain-markdown notes in a local vault.
> Ask "why did we decide that?" later and it answers with the original decision context, citing [[files]].
> No accounts, no API keys, no servers — Obsidian-compatible markdown on your own disk.

**Claude와 대화하다 "기억해둬"라고 하면 쌓이고, "그때 왜 그렇게 정했지?"라고 물으면 결정의 이유까지 찾아주는 개인 지식 창고.**

## 30초 설치

Claude Code에서 두 줄이면 끝납니다.

```
/plugin marketplace add sedurm85/second-brain
/plugin install second-brain@second-brain
```

첫 "기억해둬" 때 볼트(`~/brain/`)를 만들지 한 번 묻습니다. 필요한 건 Python 3.9+ 하나(macOS·대부분의 Linux 기본 탑재)입니다.


## 비서 모드 (v0.3)

대시보드 맨 위에 **「오늘」**이 생겼습니다. 자비스가 하는 세 가지 — 내 상태를 한 화면에, 물으면 답하고, 먼저 알려주기 — 를 볼트와 내 자동화 위에 얹은 것입니다.

- **오늘**: 인사 · 되돌아볼 결정(D-N) · inbox 할 일 · 자동화 상태 요약
- **자동화 위젯**: `~/.config/second-brain/widgets.json`에 내 크론 로그·상태 JSON·CSV 지표·마크다운 결과·(허용 시) 명령을 등록하면 카드로 뜹니다. 살았는지(ok/fail/stale), 마지막 결과, 추이 스파크라인. 60초 자동 갱신. 끝난 자동화는 `"state": "paused"`로 두면 「멈춤」 묶음에 접혀 들어가 경고를 내지 않습니다
- **"오늘 뭐 있어?"** / `/second-brain:brain-today`: 되돌아볼 결정 → 죽은 자동화 → 할 일 → 이번 주 신규 순으로 3~6줄 브리핑. 카톡 헬퍼가 있으면 전송 제안

```bash
python3 scripts/brain.py config init-widgets   # 예시 widgets.json 생성
python3 scripts/brain.py today                 # 터미널에서 브리핑
```

첫날 이 위젯이 조용히 죽어 있던 크론 3개를 찾아냈습니다. 그게 이 기능의 존재 이유입니다.

## 대시보드 (v0.2)

말로만 쓰는 게 아니라 **보이게** 만들었습니다. `/second-brain:brain-view` 한 번(또는 "대시보드 보여줘")이면 브라우저에 로컬 대시보드가 뜹니다. 외부 서비스·라이브러리 없이 `brain.py serve`가 내 컴퓨터에서만 서빙합니다.

![대시보드](web/preview-live-desktop.png)

- **지식 그래프**: 기록 하나가 점, `[[링크]]`가 선. 타입별 모양·색(결정 ◆, 프로젝트 ○, 노트 ●, 자료 ■, 아이디어 ▲, 회의 ⬡, 사람 ⬠). 드래그·확대, 클릭하면 노트 패널
- **결정 보드**: 열림 / 결정됨 / 대체됨. 되돌아볼 날이 7일 이내면 D-N 배지
- **타임라인**: 최근 30일 기록
- **프로젝트 카드**: 노트·결정 수, 최근 활동
- **검색**: 타이핑하면 즉시 결과, 클릭하면 본문·들어오는/나가는 링크

볼트가 없으면 `python3 scripts/brain.py serve --demo`로 가공 데이터 데모를 볼 수 있습니다(포트 7777).

## 이렇게 씁니다 — 5가지 시나리오

**1. 떠오른 아이디어 저장 (brain-capture)**
```
나: 온보딩 메일을 가입 3일 차에 한 번 더 보내면 어떨까. 기억해둬.
Claude: notes/2026/09/onboarding-day3-mail.md 에 저장했어요.
        다음에 '그때 X 뭐라고 했지?'라고 물으면 찾아드려요.
```

**2. 나중에 읽을 링크 (brain-capture, source)**
```
나: https://example.com/rag-eval 이거 나중에 보게 저장해줘
Claude: notes/2026/09/rag-evaluation-guide.md 에 저장했어요. (제목·핵심 3줄 + 원문 링크)
```

**3. 결정 기록 (brain-decide)**
```
나: 로그 저장소는 OpenSearch 말고 Loki로 가자. 비용이 1/3이고 우리 쿼리는 라벨 검색이 대부분이라.
Claude: decisions/007-log-store-loki.md 에 결정을 기록했어요.
        되돌아볼 날짜 2026-12-29 · 관련 노트 2개 링크
```

**4. 과거 맥락 찾기 (brain-ask)**
```
나: 그때 왜 Loki로 정했지?
Claude: 비용이 OpenSearch의 1/3 수준이고, 주요 쿼리가 라벨 검색이라 전문 검색이
        필요 없다는 게 당시 이유였어요 [[007-log-store-loki]].
        전문 검색이 필요해지면 재검토하기로 했고 [[007-log-store-loki]],
        실제 비용 비교표는 기록 없음.
```

**5. 주간 리뷰 (brain-review)**
```
나: /second-brain:brain-review
Claude: 신규 노트 6개(아이디어 3 · 자료 2 · 회의 1)
        되돌아볼 결정 1건: [[003-weekly-deploy-day]] — 지금 이 결정 유지할까요, 바꿀까요?
        고아 노트 2개 · 링크 제안 3쌍 (승인하면 연결해요)
        BRAIN.md 갱신 완료
```

그 밖에 `/second-brain:brain-import <경로>`(Obsidian 볼트·Claude Code 메모리 가져오기), `/second-brain:brain-setup`(볼트 위치·git 자동 커밋 설정)이 있습니다.

## 볼트 구조

```
~/brain/
├── BRAIN.md                    자동 인덱스 (최근 · 프로젝트별 · 미해결 결정)
│                               → 세션 시작 시 상단 40줄을 Claude가 읽음
├── inbox.md                    빠른 캡처 임시함
├── notes/YYYY/MM/<slug>.md     note · idea · source · meeting
├── decisions/<NNN>-<slug>.md   ADR: 상황 · 선택지 · 결정 · 이유 · 되돌아볼 날짜
├── projects/<slug>.md          프로젝트 허브 (관련 노트·결정 자동 수집)
└── people/<slug>.md            사람과 맥락
```

모든 파일은 프론트매터(title, type, created, tags …) + 자유 마크다운 + `[[위키링크]]`입니다. 뒤집힌 결정도 지우지 않고 `superseded`로 표시해 "왜 바꿨는지"까지 남깁니다.

## Obsidian과 함께 쓰기

Obsidian → **Open folder as vault** → `~/brain` 선택. 그래프 뷰·백링크·검색이 그대로 동작합니다. Obsidian에서 직접 쓴 노트도 프론트매터만 있으면 Claude가 함께 검색합니다. 기존 Obsidian 볼트가 있다면 `/second-brain:brain-import <볼트경로>`로 원본을 건드리지 않고 복사해 올 수 있습니다.

## FAQ

**데이터는 어디에 저장되나요?**
내 컴퓨터의 볼트 폴더(기본 `~/brain/`)에만 있습니다. 플러그인 자체는 외부 서버로 아무것도 보내지 않습니다. (대화 내용은 평소처럼 Claude 모델로 전송됩니다.)

**플러그인을 삭제하면?**
볼트는 플러그인 밖에 있어서 그대로 남습니다. 다시 설치하면 이어서 씁니다.

**여러 컴퓨터에서 쓰려면?**
`/second-brain:brain-setup`에서 git 자동 커밋을 켜고 볼트를 개인 private 저장소에 push하거나, iCloud·Dropbox 폴더로 볼트 위치를 옮기면 됩니다.

**임베딩 / AI 의미 검색은요?**
v0.1은 키워드 기반 검색(제목·태그 가중 + 최근성)입니다. 임베딩 검색은 외부 API 키가 필요해서 "키 0개" 원칙에 맞춰 v0.2 옵션으로 미뤘습니다.

## 영감

hongik.man 릴스 "클로드로 주말 동안 만들 수 있는 AI 프로젝트 3가지" 중 2번(개인 지식 창고)에서 영감을 받았습니다.

## 라이선스

MIT — [LICENSE](LICENSE)
