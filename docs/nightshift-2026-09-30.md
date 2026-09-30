# 야간 자율 근무 로그 (2026-09-30, ~24:00까지)

관리자(Claude 세션) + 워커(서브에이전트, 각자 git worktree 브랜치) 체계. 워커는 한 과제만 맡고 테스트 통과 후 브랜치에 커밋, 푸시는 관리자가 검수 후.

## 체계
- 브랜치: `wt/<과제>` ↔ `../second-brain-wt/<과제>` worktree
- 관리자 검수: `git rebase main` → 전체 테스트 → ff 머지 → 버전·SPEC → push·tag → 플러그인 캐시 동기화 → 서버 재시작
- 원칙: 볼트 쓰기는 사람 승인, 테스트가 실제 launchctl·볼트를 건드리지 않음, 시크릿 금지

## 백로그
- [x] CI(GitHub Actions, Python 3.9~3.13 매트릭스) + 3.9 호환 — 워커 완료·머지 17:26, 실제 3.9 회귀는 없었음(agenda/mailer에 future import 이미 있음)
- [x] 보드 「일지」 섹션 — 머지 17:34 (/api/journals, 데모 일지·회고, 테스트 189)(/api/journals, 일지·회고 목록·요약·패널) + 데모 데이터에 일지·준비 제안
- [x] load_notes 캐시 — 워커 완료·머지 17:29: dash_today 145.6ms → 29.7ms(300노트), 지문=stat(mtime,size), write_note에서 무효화, 테스트 185
- [x] 코어 「준비」 규칙: 질문에 든 일정 제목으로 매칭, 진짜 질문은 Claude로 (관리자, 17:25)
- [x] 코어 "오늘 일지 읽어줘" (관리자, 17:25)
- [x] docs/vault-format.md에 journal·events·suggestions·allow_run 반영 (관리자, 17:33)
- [x] 서버 보안 점검(관리자, 17:33): 쓰기=세션 토큰(compare_digest)+Host 루프백, 경로=절대·..·볼트 밖 심볼릭 링크 거부·.md만, CORS 헤더 없음(교차 출처 읽기 차단), 본문 64KB 제한. 추가 조치 불필요
- [ ] 데모 미리보기 이미지 재캡처(관리자, Orca)

## 로드맵 (관리자 기획, 17:22)
우선순위 기준: 비서가 "먼저 말해 주는" 가치 > 키 없이 동작 > 사람 승인 원칙 유지.

1. **날씨**: 일정 장소(location) 기준 예보를 Open-Meteo(지오코딩+예보, 키 불필요)로 → 일정 카드 「그날 날씨」, 아침 브리핑 「내일 비 예보, 우산」. 여행(제주도)·항공(김포)에 바로 쓸모
2. **복구**: `backup`만 있고 `restore`가 없음 → `restore <zip> [--to 경로] [--dry-run]`(덮어쓰기 전 현재 볼트 자동 백업)
3. **스킬**: brain-journal("오늘 일지 써줘", "이번 주 회고", "회고 질문 답할게"), brain-doctor("점검해줘") → 대화에서 바로
4. 보드 「이번 주 계획」: 회고의 `## 다음 주` 체크박스를 회고 질문처럼 노출
5. 사무실 팀 KPI 띠: 팀별 7일 성공률·실패 수를 방 헤더에
6. macOS 알림 센터 폴백: 카톡 헬퍼가 없어도 `remind`가 osascript로 알림
7. 주간 리포트 페이지 `/report`: 회고+일지+자동화 7일을 인쇄용 한 장으로
8. 검색 결과에 요약 스니펫 우선 표시

## 사이클 기록

### 사이클 1 (17:18~)
- 워커 3명 투입: wt/ci(CI+3.9 호환), wt/journal-board(보드 일지 섹션+데모), wt/perf-cache(load_notes 캐시)
- 관리자: 코어 준비 규칙·일지 읽기 수정 → Orca로 실제 확인("제주도 준비 뭐 있어" → 제주도 일정+제안 6개, "오늘 일지 읽어줘" → 5줄)

### 사이클 2 (17:22~)
- 워커 추가 투입: wt/office-mobile(사무실 모바일·접근성), wt/weather(날씨 모듈), wt/restore(복구 명령), wt/skills-journal(스킬 2종)
- 17:26 wt/ci 검수·머지(180 OK), GitHub Actions 첫 실행 감시 중

### 사이클 3 (17:27~)
- 워커 추가: wt/notify-fallback(알림 채널 통합·macOS 알림 센터 폴백, `notify test`), wt/report-page(/report 인쇄용 주간 리포트)
- 동시 가동 워커 8명. 관리자는 완료 알림마다 리베이스→테스트→머지→푸시, 3건 이상 쌓이면 버전·캐시 동기화·서버 재시작
- 17:28 CI 첫 실행 전부 실패 → 원인 2개: (1) 3.9~3.11 f-string 식 안 백슬래시 1곳(kakao_brief 동선), (2) CI 러너 UTC라 KST 기대 문장 실패 → 워크플로우 TZ=Asia/Seoul. 수정 푸시, 재실행 감시
- 17:30 wt/office-mobile 머지(SPEC.md 충돌은 양쪽 절 병합 스크립트로 해소, playwright 390px scrollWidth=390 실측), wt/skills-journal 머지(스킬 11개)
- 17:31 v0.27.0 태그·플러그인 캐시 동기화·서버 재시작(캐시·모바일 반영)
- 17:32 CI 재실행 전 매트릭스 통과(ubuntu 3.9~3.13·macOS 3.12/3.13)
- 17:34 wt/journal-board·wt/weather(Open-Meteo 모듈, 테스트 24)·wt/restore(복구·zip-slip 방어) 머지 → 테스트 218. 날씨 지오코딩 한계: '김포공항'·'제주도' 같은 구어 지명은 못 찾음 → 통합 워커에 별칭·정규화 과제 포함

### 사이클 4 (17:35~)
- 워커 추가: wt/weather-integration(일정·브리핑·준비 제안에 날씨 붙이기 + 지명 정규화), wt/board-plan(회고 「다음 주」 체크박스 카드 + 검색 결과 요약 스니펫)
- 가동 중: notify-fallback, report-page, office-kpi
- 17:38 wt/report-page 머지(brain.py 라우트 충돌은 양쪽 유지로 해소, 테스트 226), wt/notify-fallback 머지(notify() 통합·macOS 알림 센터 폴백·`notify test`, 테스트 239). 서버 재시작
- 가동 중: office-kpi, weather-integration, board-plan, reminders
- 17:39 /report 실제 화면 확인(기간·숫자 띠·이번 주·결정 카드·질문 체크). 시각 표기 오류 정정(앞선 기록의 18:xx는 실제 17:2x~17:3x)
- 17:48 wt/board-plan 머지(이번 주 계획 카드·검색 요약, 테스트 247), wt/office-kpi 머지(팀 KPI 띠·회사 전체 성공률, 테스트 253)

### 사이클 5 (17:45~)
- 워커 추가: wt/note-append(보드 노트 패널에서 메모·태그·할 일 기록), wt/hire(사무실 직원 채용·부서 이동·퇴사, allow_hire 게이트), wt/docs-arch(아키텍처 문서·기획 진행표)
- 가동 중: weather-integration, reminders, note-append, hire, docs-arch
- 17:52 wt/note-append(노트 패널 메모·태그·할 일, 테스트 264)·wt/docs-arch(architecture.md·v2-plan 진행표)·wt/reminders(맥 미리알림 읽기, both-added 충돌 양쪽 유지, 테스트 274) 머지. 사무실·리포트 미리보기 갱신
- 가동 중: weather-integration, hire, md-checkbox
- 17:58 wt/hire 머지(직원 채용·이동·퇴사, allow_hire 게이트, Playwright 흐름 실측, 테스트 289), wt/md-checkbox 머지(노트 본문 체크박스 토글). 미리알림 실기기 test는 20초 초과 — 권한 창을 헤드리스에서 못 띄움, 사용자가 터미널에서 `reminders test` 1회 필요

### 사이클 6 (17:50~)
- 워커 추가: wt/core-voice(회사 현황·누가 일해·미리알림·선제 알림), wt/edge-tests(파서 경계 테스트·버그 사냥), wt/demo-rich(데모 캐시 격리·전 기능 데이터)
- 가동 중: weather-integration(장기), core-voice, edge-tests, demo-rich
- 18:05 wt/weather-integration 머지(import 충돌 양쪽 유지, 지명 정규화·일정 카드 날씨·브리핑 우산, 테스트 307), wt/core-voice 머지(회사 현황·누가 일해·선제 알림). v0.28.0 태그
- 18:08 릴리스 v0.28.0 발행(테스트 307). 실제 캘린더 「제주도」에 날씨 붙음(이슬비 24% · 16~23°). 사용자 widgets.json에 allow_hire 켬

### 사이클 7 (18:08~)
- 워커 추가: wt/people-link(참석자 ↔ people/ 노트, 사람 노트 만들기, 브리핑에 이름), wt/office-schedule(크론·launchd 파싱 → 오늘 근무표 띠·다음 근무)
- 가동 중: edge-tests, demo-rich, people-link, office-schedule
- 다음 후보: 여행 모드(여러 날 일정 → 짐 목록·일자별 동선), 보드 단축키(Cmd+K), 21:30 저녁 마감 실행 검증
- 18:14 wt/edge-tests 머지(경계 테스트 54개, `_tail_lines` 초대형 한 줄 버그 수정, 테스트 361). 워커 추가: wt/widget-cli(widget add/list/… CLI + brain-office 스킬)
- 18:20 wt/office-schedule(오늘 근무표·다음 근무, 테스트 378)·wt/demo-rich(데모 캐시 격리·전 기능 데이터, 381)·wt/widget-cli(widget CLI·brain-office 스킬, 391) 머지. 관리자: brain-evening 위젯 소스를 launchd 로그로 교정(다음 근무 21:30으로 정상화), 같은 로그 크론 2줄 슬롯 버그 수정, 데모 미리보기 재캡처

### 사이클 8 (18:20~)
- 워커 추가: wt/board-shortcuts(보드 단축키·명령 팔레트), wt/brief-polish(저녁 마감에 내일 날씨, 회고에 자동화 신뢰도)
- 가동 중: people-link, board-shortcuts, brief-polish
- 18:26 wt/people-link 머지(참석자↔사람 노트·/api/person·브리핑 이름, 테스트 398). 워커 추가: wt/trip-mode(여러 날 일정 → 여행 인식·날짜 탭·짐 목록 제안). 21:30 저녁 마감은 launchd가 repo main의 brain.py를 직접 실행하므로 main은 항상 테스트 통과 상태 유지
- 18:27 wt/board-shortcuts(단축키·명령 팔레트, Playwright 실측)·wt/brief-polish(저녁 날씨·회고 KPI·init 안내, 테스트 417) 머지

### 사이클 9 (18:27~)
- 워커 추가: wt/core-actions(음성으로 제안 채택·체크·할 일 완료), wt/serve-agent(대시보드 상주 launchd), wt/apple-notes(Apple Notes 가져오기), wt/search-ops(검색 연산자 type:/tag:/since: + 보드 필터)
- 가동 중: trip-mode, core-actions, serve-agent, apple-notes, search-ops
- 사용자 약속: 자정까지 워커 3명 이상 상시 가동, 멈추지 않기
- 18:36 wt/trip-mode 머지(여행 인식·날짜 탭·짐 목록, 테스트 423). 관리자: 코어 자유 질문 맥락 확장(준비 제안·회고 질문·계획), 제주도 제안을 여행 모드로 재생성(예약·짐·서류 분류), 야간 체계를 메모리에 기록. 워커 추가: wt/people-section(보드 「사람」 섹션)
- 의미 링크 제안 10쌍 재확인(변동 없음) — 볼트 링크 적용은 사용자 승인 원칙 유지, 목록은 보드 리뷰에서 확인 가능
- 18:45 wt/serve-agent 머지(테스트 433) → 실제 설치: 대시보드가 launchd 상주(com.secondbrain.serve, 포트 7777 응답). 이후 서버 반영은 `launchctl kickstart -k gui/$UID/com.secondbrain.serve`. v0.29.0 태그·릴리스
- 18:37 wt/core-actions 머지(음성으로 제안 채택·체크·할 일 완료, 전체 작업은 확인 후). 워커 추가: wt/vault-lint(볼트 점검·안전 수정), wt/core-settings(코어 음성·속도·자동 브리핑 설정)
- 가동 중: apple-notes, search-ops, people-section, vault-lint, core-settings
- 18:50 wt/apple-notes 머지(테스트 452). 관리자: 실제 Apple Notes 14건 가져오기(백업 후) + enrich 정제 실행
- 18:48 wt/people-section(보드 「사람」)·wt/core-settings(음성·속도·자막·자동 브리핑)·wt/search-ops(검색 연산자·패싯, 38 테스트) 머지 → 테스트 493. 워커 추가: wt/quick-capture(보드·코어 빠른 캡처), wt/export-html(볼트 한 파일 내보내기). 가동 중: vault-lint, quick-capture, export-html
- 18:55 관리자: 참석자에서 본인 계정 제외(캘린더 이름·config me), 검색 연산자·doctor 실제 볼트 확인(노트 107·요약 101, 에이전트 7/7). 워커 추가: wt/overview-stats(개요 통계·12주 활동 차트·일지 연속)
- 19:00 wt/vault-lint 머지(17개 점검 코드·5개 자동 수정, 테스트 501). 실제 볼트 lint: 이슈 6(오래된 고아 4, 예시 텍스트의 [[파일]] 2 — 둘 다 사용자 판단 영역, 자동 수정 대상 아님). 워커 추가: wt/widget-edit(사무실 직원 설정 편집)
- 19:05 wt/quick-capture 머지(both-added 충돌 양쪽 유지, 테스트 511). 관리자: `brain.py ask` CLI 추가(512). 실수 1건: 테스트 실패에도 커밋된 체인(tail 종료코드) → 즉시 보정, lessons.md 기록. 워커 추가: wt/obsidian-init. 가동 중: export-html, overview-stats, widget-edit, obsidian-init
- 19:10 wt/export-html 머지(테스트 522). 실제 볼트 내보내기 107노트 260KB, Orca로 열어 목차·결정·앵커 62개 확인. 워커 추가: wt/inpage-remind(보드 토스트·코어 음성 화면 내 알림). 가동 중: overview-stats, widget-edit, obsidian-init, inpage-remind
- 19:16 wt/overview-stats 머지(테스트 533, 실볼트: 링크 194·고아 6·요약 94%). 워커 추가: wt/agenda-gaps(일정 사이 여유·이동 경고). 가동 중: widget-edit, obsidian-init, inpage-remind, agenda-gaps
- 19:22 wt/widget-edit 머지(직원 설정 편집·패턴 시험, 테스트 556). v0.30.0 태그·릴리스
- 19:30 wt/obsidian-init 머지(테스트 571) → 실볼트에 `obsidian init` 적용(.obsidian 설정·템플릿 4개, 노트 무변경). 워커 추가: wt/perf-bench(1000노트 벤치·느린 API 캐시)
- 19:35 wt/inpage-remind 머지(테스트 573, /api/remind-peek). 워커 추가: wt/e2e-suite(Playwright 회귀 스위트, 없으면 skip), wt/monthly-retro(월간 회고·매월 1일 에이전트). 가동 중: agenda-gaps, onboarding-qa, perf-bench, e2e-suite, monthly-retro
- 19:40 wt/agenda-gaps 머지(빠듯/이동 경고, 테스트 576). 가동 중: onboarding-qa, perf-bench, e2e-suite, monthly-retro
- 19:45 wt/onboarding-qa 머지(결함 2건 수정: 미리알림 enabled 가드·agents status 모순, --help 4단 그룹, 테스트 583). 워커 추가: wt/board-staff-brief(보드 자동화 섹션에서 실패 원인 한 줄), wt/ask-guard(Claude 호출 동시성·시간당 상한)
- 19:50 wt/monthly-retro 머지(테스트 596) → 실제 설치(매월 1일 9:10, 내일 첫 실행) + 관리자가 9월 월간 회고를 지금 생성. 가동 중: perf-bench, e2e-suite, board-staff-brief, ask-guard
- 19:58 wt/e2e-suite 머지(Playwright 16건, 총 612, 실제 버그 0). 워커 추가: wt/decision-review(되돌아볼 결정 유지/변경/종결 UI), wt/board-mobile(보드 390px 섹션별 폴리시). 가동 중: perf-bench, board-staff-brief, ask-guard, decision-review, board-mobile
- 19:33 wt/board-staff-brief 머지(보드 자동화 행 「왜 실패했어?」). 가동 중: perf-bench, ask-guard, decision-review, board-mobile. 21:30 저녁 마감 에이전트 실행 로그 확인 예정
- 19:52 wt/perf-bench 머지(검색 236→26ms, office 24→0.5ms @1000노트, 테스트 621). 워커 추가: wt/office-mobile-2(사무실 390px 재점검), wt/kakao-budget(200자 카톡 우선순위·잘림 보장). 가동 중: ask-guard, decision-review, board-mobile, office-mobile-2, kakao-budget
- 20:00 wt/ask-guard 머지(시간당 40회 상한·동시성 락·사용량 로그, retro 호출부 충돌은 두 변경 합성). 가동 중: decision-review, board-mobile, office-mobile-2, kakao-budget
