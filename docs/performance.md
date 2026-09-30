# 성능 — 대시보드 벤치마크·캐시 (v0.31)

목표: 노트 1,000~2,000개짜리 볼트에서도 로컬 대시보드가 즉각 반응해야 한다. 이 문서는
측정 방법, 최적화 전/후 수치, 새로 추가한 캐시 3종과 무효화 방식, `scripts/bench.py`
실행법을 정리한다.

## 1. 측정 방법

`scripts/bench.py`가 임시 `HOME`(실제 `~/brain`·`~/.config`·`~/.cache`는 절대 건드리지
않음) 아래에 합성 볼트를 만든다:

- 노트 N개(기본 1000): 결정 30·일지 60(그중 9개는 주간 회고)·사람 20·프로젝트 40·
  이벤트 50 + 나머지는 note/idea/source/meeting 혼합. 태그·위키링크·요약을 섞고,
  일부 노트에는 "인프라 비용" 관련 문장을 의도적으로 넣어 자유어 검색이 실제로
  히트를 만들게 했다.
- 위젯 10개(모두 `kind: log`), 각 로그는 최근 7일치 날짜 줄을 담아 `widget_history`가
  실제로 파일을 읽고 정규식으로 스캔하는 비용이 드러나게 했다.
- `config.json`의 `calendar.sources`는 빈 배열, `SECOND_BRAIN_OFFLINE=1`·
  `SECOND_BRAIN_NO_LAUNCHCTL=1`·`SECOND_BRAIN_JOBS_DIR`(빈 디렉터리)로 네트워크·
  launchd·백그라운드 작업 스캔을 모두 차단.

각 함수는 1회 warm-up 후 5회 반복 실행해 **중앙값(ms)**을 표로 찍는다(`--out`으로 JSON
저장도 가능). warm-up이 프로세스 캐시를 채우므로, 이 수치는 "서버가 켜진 채로 대시보드를
계속 새로고침할 때"의 실제 체감 성능이다.

```bash
python3 scripts/bench.py --notes 1000 --out bench-1000.json
python3 scripts/bench.py --notes 300 --out bench-300.json
```

## 2. 전/후 수치 (같은 머신, 3회 실행 중앙값)

### 300 노트

| 함수 | 최적화 전 | 최적화 후 |
|---|---:|---:|
| dash_search(자유어 "인프라 비용") | 54.31 ms | 9.05 ms |
| dash_report | 36.98 ms | 13.40 ms |
| dash_office | 24.09 ms | 0.47 ms |
| office_kpis | 23.60 ms | 0.08 ms |
| dash_today | 23.37 ms | 23.87 ms |
| dash_summary | 9.79 ms | 9.38 ms |
| lint_vault | 42.88 ms | 41.38 ms |
| build_index | 8.08 ms | 7.50 ms |

### 1,000 노트

| 함수 | 최적화 전 | 최적화 후 | 요구 기준 |
|---|---:|---:|---|
| dash_search(자유어 "인프라 비용") | 235.72 ms | 26.11 ms | < 80 ms |
| dash_search(연산자 "type:decision since:90d") | 19.38 ms | 20.43 ms | < 80 ms |
| dash_today | 71.50 ms | 69.27 ms | < 150 ms |
| dash_summary | 29.53 ms | 26.74 ms | < 200 ms |
| dash_report | 59.35 ms | 37.53 ms | < 200 ms |
| dash_office | 23.95 ms | 0.53 ms | — |
| office_kpis | 23.57 ms | 0.09 ms | — |
| office_schedule | 0.08 ms | 0.07 ms | — |
| lint_vault | 142.87 ms | 136.81 ms | — |
| build_index | 24.62 ms | 22.93 ms | — |
| load_notes(cold) | 139.93 ms | 141.51 ms | — |
| load_notes(warm) | 17.36 ms | 18.06 ms | — |

모든 요구 기준(1,000노트: `dash_today<150ms`, `dash_search<80ms`, `dash_summary`/
`dash_report<200ms`)을 여유 있게 충족한다. `load_notes`는 애초에 이번 작업의 대상이
아니라(이미 v0.x부터 지문 캐시가 있음) 전/후가 거의 동일하다.

## 3. 무엇을 캐시했는가

### 3.1 `cached_link_graph(vault, notes=None)` — 링크 그래프

`link_graph(notes)` 자체는 이미 저렴하다(1000노트 기준 ~2ms). 문제는 `build_index`·
`lint_vault`·`dash_summary`·`dash_people`·`attach_event_notes`·`retro_material`·
`semantic_link_suggestions` 등 여러 함수가 **한 요청 흐름 안에서 또는 폴링마다 반복
해서** `link_graph`를 다시 계산한다는 점이다.

**처음 시도한 방식(실패)**: 볼트 지문(`_vault_note_signature`, `os.walk`+`stat`)을
캐시 키로 다시 계산해서 비교했더니, 1000노트 기준 지문 스캔 자체가 ~15ms로
`link_graph` 계산(~2ms)보다 훨씬 비쌌다 — 캐시 적중 확인 비용이 캐시로 아끼는 비용보다
커서 오히려 `dash_summary`·`dash_people`·`build_index`·`lint_vault`가 최대 90%까지
**느려지는** 역효과가 났다(벤치로 바로 잡아냄).

**최종 방식**: 지문을 따로 재계산하지 않고, 호출부가 `cached_link_graph` 직전에 이미
불러 둔 `load_notes(vault)`가 `_NOTES_CACHE`에 남겨 둔 지문을 그냥 읽는다. `load_notes`는
어차피 항상 먼저 호출되므로(모든 호출부가 `notes = load_notes(vault)` 다음에
`link_graph(notes)`를 부르는 패턴이었다) 지문 스캔을 요청당 두 번째로 또 하지 않는 것이
핵심이다. 캐시 적중 시 dict 조회 한 번뿐이라 순수 이익만 남는다.

### 3.2 `cached_search_docs(vault)` — 검색 토큰 인덱스

`search()`(BM25-lite)는 매 호출마다 후보 노트 전체의 title/tags/summary/body를
`tokenize()`로 다시 토큰화했다. 대시보드 검색창은 타이핑마다(또는 폴링마다) 같은
볼트에 대해 반복 호출되므로, 노트별 `(tf Counter, length)`를 볼트 지문 기준으로
캐시해서 재사용한다. 지문도 3.1과 같은 이유로 `_NOTES_CACHE`에서 읽는다(재스캔 없음).

여기에 더해 `dash_search`가 자유어 검색 시 facet(타입·태그 집계)을 만들려고 **후보
전체**에 대해 `search()`를 호출하던 게(즉 `limit`을 후보 수만큼 넘겨 사실상 무제한)
BM25 점수와 무관하게 **스니펫(`_snippet`, 본문을 줄 단위로 다시 토큰화)까지 후보
전체에 대해** 계산하고 있었다. 이게 프로파일에서 가장 큰 잔여 비용이었다. `search()`에
`with_snippet=False` 옵션을 추가해 점수만 매기고, `dash_search`가 정렬·`limit` 자르기
**다음에** 살아남은 최대 20개에만 스니펫을 계산하도록 바꿨다. 1000노트 자유어 검색이
이 두 가지(토큰화 캐시 + 스니펫 지연)로 235ms → 26ms(약 9배)가 됐다.

### 3.3 `widget_history(w, days, today)` — 위젯 로그 이력

`office_kpis`·`dash_report`·`retro_material`이 위젯마다 로그 파일(최대 2MB 꼬리)을
직접 읽어 날짜별 줄 수·실패 수를 다시 센다. `(source, mtime_ns, size, days, today,
fail_pattern)` 키로 60초 캐시한다(`_WIDGET_HISTORY_CACHE`). mtime/size가 키에 있어
로그가 새로 쓰이면 60초를 기다리지 않고 즉시 무효화되고, 60초는 그냥 캐시가 무한히
쌓이지 않게 하는 상한이다. 위젯 10개짜리 벤치에서 `dash_office`/`office_kpis`가
~24ms → ~0.1~0.5ms로 떨어졌다(로그 크기·요청 빈도에 비례해 실서비스에서 더 크게
체감될 항목).

## 4. 무효화는 어떻게 되는가

세 캐시 모두 키에 **내용이 바뀌면 자동으로 달라지는 값**을 쓴다 — 그래서 서버를 다시
켜지 않아도 편집이 바로 반영된다.

- `cached_link_graph`/`cached_search_docs`: 키가 `load_notes`의 볼트 지문(노트마다
  `(경로, mtime_ns, size)`)이라, 노트를 만들거나/고치거나/지우면 다음 호출에서 지문이
  달라져 자동 미스가 난다. `write_note()`(모든 노트 쓰기의 단일 통로)가 부르는
  `invalidate_notes_cache()`도 이제 `_LINK_GRAPH_CACHE`·`_SEARCH_INDEX_CACHE`를 같이
  비워서 메모리를 계속 작게 둔다.
- `widget_history`: 키에 로그 파일의 `mtime_ns`·`size`가 그대로 들어가므로, 로그에 한
  줄만 추가돼도 다음 호출은 무조건 새로 읽는다. 60초 TTL은 순수 상한선일 뿐 정확성에는
  영향이 없다.
- `SECOND_BRAIN_NO_NOTE_CACHE=1`이면 위 세 캐시(및 기존 노트 캐시)가 전부 꺼진다(테스트·
  디버깅용). `tests/test_perf_cache.py`가 편집 즉시 반영·캐시 재사용·env 비활성화를
  각각 확인한다.

## 5. 전체 테스트

```bash
cd /Users/lhj1010/repo/project/second-brain-wt/perf-bench
python3 -m unittest discover -s tests -q
```
`Ran 580 tests in ~16s` / `OK` (기존 571 + 신규 `tests/test_perf_cache.py` 9건).
