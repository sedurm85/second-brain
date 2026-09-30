# 야간 자율 근무 로그 (2026-09-30, ~24:00까지)

관리자(Claude 세션) + 워커(서브에이전트, 각자 git worktree 브랜치) 체계. 워커는 한 과제만 맡고 테스트 통과 후 브랜치에 커밋, 푸시는 관리자가 검수 후.

## 체계
- 브랜치: `wt/<과제>` ↔ `../second-brain-wt/<과제>` worktree
- 관리자 검수: `git rebase main` → 전체 테스트 → ff 머지 → 버전·SPEC → push·tag → 플러그인 캐시 동기화 → 서버 재시작
- 원칙: 볼트 쓰기는 사람 승인, 테스트가 실제 launchctl·볼트를 건드리지 않음, 시크릿 금지

## 백로그
- [x] CI(GitHub Actions, Python 3.9~3.13 매트릭스) + 3.9 호환 — 워커 완료·머지 17:26, 실제 3.9 회귀는 없었음(agenda/mailer에 future import 이미 있음)
- [x] 보드 「일지」 섹션 — 머지 18:12 (/api/journals, 데모 일지·회고, 테스트 189)(/api/journals, 일지·회고 목록·요약·패널) + 데모 데이터에 일지·준비 제안
- [x] load_notes 캐시 — 워커 완료·머지 17:52: dash_today 145.6ms → 29.7ms(300노트), 지문=stat(mtime,size), write_note에서 무효화, 테스트 185
- [x] 코어 「준비」 규칙: 질문에 든 일정 제목으로 매칭, 진짜 질문은 Claude로 (관리자, 17:25)
- [x] 코어 "오늘 일지 읽어줘" (관리자, 17:25)
- [x] docs/vault-format.md에 journal·events·suggestions·allow_run 반영 (관리자, 17:33)
- [x] 서버 보안 점검(관리자, 17:33): 쓰기=세션 토큰(compare_digest)+Host 루프백, 경로=절대·..·볼트 밖 심볼릭 링크 거부·.md만, CORS 헤더 없음(교차 출처 읽기 차단), 본문 64KB 제한. 추가 조치 불필요
- [ ] 데모 미리보기 이미지 재캡처(관리자, Orca)

## 로드맵 (관리자 기획, 17:40)
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

### 사이클 2 (17:40~)
- 워커 추가 투입: wt/office-mobile(사무실 모바일·접근성), wt/weather(날씨 모듈), wt/restore(복구 명령), wt/skills-journal(스킬 2종)
- 17:26 wt/ci 검수·머지(180 OK), GitHub Actions 첫 실행 감시 중

### 사이클 3 (17:30~)
- 워커 추가: wt/notify-fallback(알림 채널 통합·macOS 알림 센터 폴백, `notify test`), wt/report-page(/report 인쇄용 주간 리포트)
- 동시 가동 워커 8명. 관리자는 완료 알림마다 리베이스→테스트→머지→푸시, 3건 이상 쌓이면 버전·캐시 동기화·서버 재시작
- 17:50 CI 첫 실행 전부 실패 → 원인 2개: (1) 3.9~3.11 f-string 식 안 백슬래시 1곳(kakao_brief 동선), (2) CI 러너 UTC라 KST 기대 문장 실패 → 워크플로우 TZ=Asia/Seoul. 수정 푸시, 재실행 감시
- 17:55 wt/office-mobile 머지(SPEC.md 충돌은 양쪽 절 병합 스크립트로 해소, playwright 390px scrollWidth=390 실측), wt/skills-journal 머지(스킬 11개)
- 18:00 v0.27.0 태그·플러그인 캐시 동기화·서버 재시작(캐시·모바일 반영)
- 18:05 CI 재실행 전 매트릭스 통과(ubuntu 3.9~3.13·macOS 3.12/3.13)
- 18:12 wt/journal-board·wt/weather(Open-Meteo 모듈, 테스트 24)·wt/restore(복구·zip-slip 방어) 머지 → 테스트 218. 날씨 지오코딩 한계: '김포공항'·'제주도' 같은 구어 지명은 못 찾음 → 통합 워커에 별칭·정규화 과제 포함

### 사이클 4 (18:15~)
- 워커 추가: wt/weather-integration(일정·브리핑·준비 제안에 날씨 붙이기 + 지명 정규화), wt/board-plan(회고 「다음 주」 체크박스 카드 + 검색 결과 요약 스니펫)
- 가동 중: notify-fallback, report-page, office-kpi
