# 날씨 모듈 통합 가이드

`scripts/weather.py`는 stdlib만 쓰는 독립 모듈이다(Open-Meteo, API 키 불필요). 아직 `brain.py`는
이 모듈을 import하지 않는다 — 머지 충돌을 피하려고 별도 워크트리(`wt/weather`)에서 모듈+테스트만
먼저 만들었다. 아래는 담당자가 `brain.py`에 실제로 연결할 때 그대로 옮길 수 있는 지점이다.

## 0. import

```python
import weather  # scripts/ 가 sys.path에 있으므로 다른 모듈과 동일하게 import
```

## 1. `attach_event_notes(vault, ag)` — scripts/brain.py:3011

이 함수는 `ag["today"]/["upcoming"]/["current"]`의 각 일정 dict `e`에 `note`/`related`/`prep`을
붙인다(3011~3050행 부근). 같은 루프(예: `related`를 붙이는 3029행 for문) 안에서, `location`이 있고
`days_left <= 7`인 일정에 날씨를 붙인다:

```python
cache_dir = agenda_mod.cache_dir() / "weather"
for e in (ag.get("today") or []) + [x for x in (ag.get("upcoming") or []) if (x.get("days_left") or 0) <= 7]:
    e["weather"] = None
    if e.get("location"):
        try:
            e["weather"] = weather.weather_for(e["location"], e["start"][:10], cache_dir)
        except Exception:  # noqa: BLE001 - 날씨는 읽기 전용 보조 정보, 실패해도 일정 표시는 막지 않는다
            e["weather"] = None
```

- `location`이 없으면 시도하지 않는다(제목에서 장소를 추측하는 로직은 오탐 위험이 커서 넣지 않았다 —
  필요하면 별도 검토).
- `agenda_mod.cache_dir()`는 `scripts/agenda.py:50`에 이미 있는 `~/.cache/second-brain` 반환 함수이니
  그 아래 `weather/` 서브폴더만 추가하면 된다.

## 2. `kakao_brief(t)` / `today_human(t)` — scripts/brain.py:2239, 2396

`kakao_brief`는 2247행 근처에서 `tomorrow = [e for e in (ag.get("upcoming") or []) if e.get("days_left") == 1]`로
내일 일정을 이미 뽑는다. 그중 `location`이 있고(=위 1번에서 `weather`가 채워진) 첫 건으로 한 줄 추가:

```python
tomorrow_located = [e for e in tomorrow if e.get("weather")]
if tomorrow_located:
    parts.append(weather.weather_sentence([e["weather"] for e in tomorrow_located]))
```

`today_human`(2396행)도 같은 `ag["sentence"]` 다음 줄에 동일하게 붙이면 사람용 브리핑에도 노출된다.
카톡은 200자 제한(`KAKAO_MAX`)이 있으니 새 줄은 `weather_sentence`가 이미 짧게(20자 내외) 만들어 준다.

## 3. 보드 이벤트 패널 — web/index.html:1429 `renderEventBody(e)`

`e.location` 행(1433행, `<tr><th>어디</th>...`) 바로 다음에 「날씨」 행을 추가한다(이 파일은 이번
워크트리에서 건드리지 않았다 — 담당자가 직접 수정):

```js
(e.weather ? '<tr><th>날씨</th><td>' + esc(e.weather.summary) +
  (e.weather.umbrella ? ' ☔' : '') + '</td></tr>' : "")
```

`e.weather`는 1번에서 `attach_event_notes`가 채운 값이 `/api/today` 응답에 그대로 실려 온다는 전제다
(현재 이벤트 dict를 JSON으로 그대로 내보내는 구조라 별도 직렬화 작업은 필요 없을 것으로 보이나,
API 응답을 만드는 지점에서 필드가 잘리지 않는지 확인 필요).

## 4. `prepare_prompt(e, note, related)` — scripts/brain.py:4331

Claude에게 준비 항목(우산/겉옷)을 제안하게 하려면 `ctx` dict에 날씨 한 줄을 추가한다:

```python
ctx = {"title": e["title"], "when": when, "location": e.get("location") or "",
       "description": (e.get("description") or "")[:600],
       "weather": (e.get("weather") or {}).get("summary") or "",
       "umbrella": bool((e.get("weather") or {}).get("umbrella")),
       ...}
```

그리고 프롬프트 규칙 문장에 한 줄 추가: `"날씨가 궂으면(umbrella=true) 우산을, cold/hot이면 겉옷/무더위 대비를 준비 항목에 넣어도 된다."`
`_norm_suggestion`(4344행)은 이미 임의 조언(`memo`)까지 받아 저장하므로 별도 스키마 변경은 불필요.

## 캐시 위치

CLI 기본 캐시는 `$XDG_CACHE_HOME/second-brain/weather` (없으면 `~/.cache/second-brain/weather`)다.
`brain.py`에서 붙일 때는 기존 `agenda_mod.cache_dir()`(=`~/.cache/second-brain`) 아래
`weather/` 서브폴더를 넘기면 다른 위젯 캐시와 같은 뿌리를 공유한다(위 1번 예시 참고).

## 주의

- `weather_for`/`weather_sentence`는 예외를 던지지 않지만, 호출하는 쪽(`attach_event_notes`,
  `kakao_brief` 등)에서 방어적으로 `except Exception`을 씌워 두면 날씨 API 문제가 일정·카톡
  기능 전체를 막는 사고를 원천적으로 피한다.
- Open-Meteo 지오코딩은 "서울"/"제주도"/"김포공항" 같은 구어체 지명을 못 찾는 경우가 있다
  (정식 행정명 "서울특별시"/"제주시", 정식 시설명 "김포국제공항"은 찾는다). `location` 필드에
  캘린더 참석자가 적는 텍스트가 그대로 들어가므로, 실제 배포 후 못 찾는 사례가 잦으면 흔한
  구어체→정식명 별칭 테이블을 추가하는 것을 고려(이번 작업 범위에는 포함하지 않았다).
