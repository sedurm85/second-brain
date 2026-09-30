# 야간 자율 근무 로그 (2026-09-30, ~24:00까지)

관리자(Claude 세션) + 워커(서브에이전트, 각자 git worktree 브랜치) 체계. 워커는 한 과제만 맡고 테스트 통과 후 브랜치에 커밋, 푸시는 관리자가 검수 후.

## 체계
- 브랜치: `wt/<과제>` ↔ `../second-brain-wt/<과제>` worktree
- 관리자 검수: `git rebase main` → 전체 테스트 → ff 머지 → 버전·SPEC → push·tag → 플러그인 캐시 동기화 → 서버 재시작
- 원칙: 볼트 쓰기는 사람 승인, 테스트가 실제 launchctl·볼트를 건드리지 않음, 시크릿 금지

## 백로그
- [ ] CI(GitHub Actions, Python 3.9~3.13 매트릭스) + 3.9 호환(`datetime | None` 표기 등)
- [ ] 보드 「일지」 섹션(/api/journals, 일지·회고 목록·요약·패널) + 데모 데이터에 일지·준비 제안
- [ ] load_notes 반복 호출 캐시(볼트 mtime 기준) + /api/today 지연 측정
- [ ] 코어 「준비」 규칙: 질문에 든 일정 제목으로 매칭, 진짜 질문은 Claude로 (관리자)
- [ ] 코어 "오늘 일지 읽어줘" (관리자)
- [ ] docs/vault-format.md에 journal·events·suggestions·allow_run 반영
- [ ] 서버 POST 엔드포인트·경로 처리 보안 점검(읽기 전용 보고)
- [ ] 데모 미리보기 이미지 재캡처(관리자, Orca)

## 사이클 기록
