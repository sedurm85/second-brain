"""날씨 조회 모듈 (second-brain).

Open-Meteo 지오코딩 + 예보 API를 stdlib(urllib)만으로 호출한다. API 키 불필요.
brain.py는 아직 이 모듈을 import하지 않는다(병합 충돌 방지) — docs/weather-integration.md 참고.

읽기 전용: 네트워크 실패·장소를 못 찾는 경우 등 어떤 예외도 호출자에게 던지지 않고
None/빈 값을 돌려준다. `SECOND_BRAIN_OFFLINE=1`이면 네트워크를 건너뛰고 캐시만 쓴다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

USER_AGENT = "second-brain/1.0"
TIMEOUT = 8

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search?name={q}&count=3&language=ko&format=json"
FORECAST_URL = (
    "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
    "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
    "precipitation_probability_max,precipitation_sum&timezone=Asia%2FSeoul&forecast_days={days}"
)

GEOCODE_MISS_RETRY_SECONDS = 7 * 86400  # 미스는 7일 후 재시도
FORECAST_TTL_SECONDS = 3 * 3600  # 예보는 3시간 캐시

# 구어체 지명 -> 정식 지오코딩 검색어. 일반 규칙(candidates())으로 못 커버하는
# 잘 알려진 공항/섬 이름만 최소로 등록한다(임의 지명 매핑 금지).
_ALIASES = {
    "김포공항": "김포국제공항",
    "인천공항": "인천국제공항",
    "제주공항": "제주국제공항",
    "김해공항": "김해국제공항",
    "제주도": "제주",
}

# "X역/X터미널/..." 처럼 시설명을 떼어내면 지오코딩이 아는 동네 이름이 남는 접미사들.
_LOCALITY_SUFFIXES = ("역", "터미널", "점", "본점", "지점", "캠퍼스", "병원", "호텔", "리조트", "공항")
_ADMIN_SUFFIXES = ("시", "군", "구")
_HANGUL_ONLY_RE = re.compile(r"^[가-힣]+$")

_WEATHER_CODES = {
    0: "맑음",
    1: "대체로 맑음",
    2: "구름 많음",
    3: "흐림",
    45: "안개",
    48: "안개",
    51: "이슬비",
    53: "이슬비",
    55: "이슬비",
    56: "이슬비",
    57: "이슬비",
    61: "비",
    63: "비",
    65: "비",
    66: "비",
    67: "비",
    71: "눈",
    73: "눈",
    75: "눈",
    77: "눈",
    80: "소나기",
    81: "소나기",
    82: "소나기",
    85: "눈",
    86: "눈",
    95: "뇌우",
    96: "뇌우",
    99: "뇌우",
}


def _fetch_json(url: str) -> dict:
    """실제 HTTP GET. 실패 시 예외를 던진다(호출부에서 잡는다)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 - 고정 도메인만
        return json.loads(resp.read().decode("utf-8"))


# 테스트에서 weather.FETCH를 가짜 함수로 바꿔치기하기 위한 훅.
FETCH = _fetch_json


def _offline() -> bool:
    return os.environ.get("SECOND_BRAIN_OFFLINE") == "1"


def _load_json_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 캐시 파일 손상/없음은 빈 캐시로 취급
        return {}


def _save_json_cache(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001 - 캐시 쓰기 실패는 무시(다음에 다시 조회될 뿐)
        pass


def _normalize_place(place: str) -> str:
    """검색 전 정규화: 괄호 부분 제거, 공백 정리."""
    s = str(place or "").strip()
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _candidates_meta(q: str) -> list:
    """정규화된 쿼리 -> [(후보 검색어, approx 여부), ...]. 순서=시도 우선순위.

    구어체 지명("김포공항", "제주도", "인천공항 T2")을 지오코딩이 아는 정식 명칭으로
    바꿔볼 후보들을 만든다. 규칙:
    - 원문은 항상 1순위.
    - 잘 알려진 공항/섬은 별칭 사전으로 정식명 추가("김포공항"->"김포국제공항").
    - "X공항" -> "X국제공항"(별칭에 없는 공항도 커버).
    - "X도" -> "X", "X시"("제주도"->"제주", "제주시").
    - "X시"/"X군"/"X구" -> "X".
    - "X역"/"X터미널"/"X점"/... -> "X"(시설명을 떼면 남는 동네 이름).
    - 위 규칙이 하나도 안 맞고 한글 3자 이상이면 최후 수단으로 앞 2글자를
      approx=True로 추가한다(UI에서 "근처" 표시용).
    """
    out = []  # [(cand, approx)]
    seen = set()

    def _add(cand: str, approx: bool = False) -> None:
        cand = (cand or "").strip()
        if cand and cand not in seen:
            seen.add(cand)
            out.append((cand, approx))

    _add(q)
    if not q:
        return out

    first_tok = q.split(" ")[0]
    tokens_to_check = (q,) if first_tok == q else (q, first_tok)

    for tok in tokens_to_check:
        if tok in _ALIASES:
            _add(_ALIASES[tok])

    for tok in tokens_to_check:
        if tok.endswith("공항") and len(tok) > 2 and not tok.endswith("국제공항"):
            _add(tok[:-2] + "국제공항")

    for tok in tokens_to_check:
        if tok.endswith("도") and len(tok) > 1:
            base = tok[:-1]
            _add(base)
            _add(base + "시")

    for tok in tokens_to_check:
        for suf in _ADMIN_SUFFIXES:
            if tok.endswith(suf) and len(tok) > len(suf):
                _add(tok[: -len(suf)])

    for tok in tokens_to_check:
        for suf in _LOCALITY_SUFFIXES:
            if tok.endswith(suf) and len(tok) > len(suf):
                _add(tok[: -len(suf)])
                break

    tokens = q.split(" ")
    if len(tokens) >= 2:
        _add(" ".join(tokens[:2]))

    if len(out) == 1 and len(q) >= 3 and _HANGUL_ONLY_RE.match(q):
        _add(q[:2], approx=True)

    return out


def candidates(place: str) -> list:
    """장소명 -> 지오코딩에 시도해 볼 검색어 후보 목록(순서=우선순위).

    "김포공항" -> ["김포공항", "김포국제공항", "김포"],
    "제주도" -> ["제주도", "제주", "제주시"] 같은 식으로, 원문 먼저 시도하고
    실패할 때만 정규화된 후보로 넘어가도록 순서를 유지한다.
    """
    q = _normalize_place(place)
    return [cand for cand, _approx in _candidates_meta(q)]


def _geocode_lookup(q: str) -> dict | None:
    if not q:
        return None
    url = GEOCODE_URL.format(q=urllib.parse.quote(q))
    try:
        data = FETCH(url)
    except Exception:  # noqa: BLE001 - 네트워크/파싱 실패는 결과 없음으로 처리
        return None
    results = (data or {}).get("results") or []
    if not results:
        return None
    picked = None
    for r in results:
        if str(r.get("country_code") or "").upper() == "KR":
            picked = r
            break
    if picked is None:
        picked = results[0]
    lat, lon = picked.get("latitude"), picked.get("longitude")
    if lat is None or lon is None:
        return None
    return {
        "name": picked.get("name"),
        "lat": lat,
        "lon": lon,
        "country": picked.get("country"),
        "admin1": picked.get("admin1"),
    }


def geocode(place: str, cache_dir) -> dict | None:
    """장소명 -> {"name","lat","lon","country","admin1"}. 못 찾으면 None."""
    q = _normalize_place(place)
    if not q:
        return None
    cache_dir = Path(cache_dir)
    cache_path = cache_dir / "geocode.json"
    cache = _load_json_cache(cache_path)
    now = time.time()
    entry = cache.get(q)
    if entry is not None:
        if entry.get("result") is not None:
            return entry["result"]
        if now - float(entry.get("ts") or 0) < GEOCODE_MISS_RETRY_SECONDS:
            return None
    if _offline():
        return None
    result = None
    for candidate, approx in _candidates_meta(q):
        try:
            result = _geocode_lookup(candidate)
        except Exception:  # noqa: BLE001 - 방어적: 절대 호출자에게 예외를 던지지 않는다
            result = None
        if result:
            if approx:
                result["approx"] = True
            break
    cache[q] = {"result": result, "ts": now}
    _save_json_cache(cache_path, cache)
    return result


def _parse_forecast(data: dict) -> dict:
    daily = (data or {}).get("daily") or {}
    dates = daily.get("time") or []
    codes = daily.get("weather_code") or []
    tmax = daily.get("temperature_2m_max") or []
    tmin = daily.get("temperature_2m_min") or []
    rp = daily.get("precipitation_probability_max") or []
    rmm = daily.get("precipitation_sum") or []
    days = []
    for i, d in enumerate(dates):
        days.append(
            {
                "date": d,
                "code": int(codes[i]) if i < len(codes) and codes[i] is not None else 0,
                "tmax": float(tmax[i]) if i < len(tmax) and tmax[i] is not None else None,
                "tmin": float(tmin[i]) if i < len(tmin) and tmin[i] is not None else None,
                "rain_prob": int(rp[i]) if i < len(rp) and rp[i] is not None else None,
                "rain_mm": float(rmm[i]) if i < len(rmm) and rmm[i] is not None else 0.0,
            }
        )
    return {"days": days, "fetched_at": datetime.now().isoformat(timespec="seconds")}


def _empty_forecast() -> dict:
    return {"days": [], "fetched_at": ""}


def forecast(lat, lon, cache_dir, days: int = 7) -> dict:
    """좌표 -> {"days": [...], "fetched_at": iso}. 3시간 캐시."""
    cache_dir = Path(cache_dir)
    try:
        lat4, lon4 = f"{float(lat):.4f}", f"{float(lon):.4f}"
    except (TypeError, ValueError):
        return _empty_forecast()
    cache_path = cache_dir / f"forecast-{lat4}-{lon4}.json"
    cached = _load_json_cache(cache_path) if cache_path.exists() else None
    now = time.time()
    if cached and now - float(cached.get("_ts") or 0) < FORECAST_TTL_SECONDS:
        return cached.get("data") or _empty_forecast()
    if _offline():
        return (cached or {}).get("data") or _empty_forecast()
    url = FORECAST_URL.format(lat=lat, lon=lon, days=days)
    try:
        data = FETCH(url)
        result = _parse_forecast(data)
    except Exception:  # noqa: BLE001 - 네트워크/파싱 실패 시 기존 캐시(있으면) 반환
        return (cached or {}).get("data") or _empty_forecast()
    _save_json_cache(cache_path, {"_ts": now, "data": result})
    return result


def describe(code: int) -> str:
    """WMO weather_code -> 짧은 한글 설명."""
    try:
        return _WEATHER_CODES.get(int(code), "알 수 없음")
    except (TypeError, ValueError):
        return "알 수 없음"


def weather_for(place: str, date_iso: str, cache_dir) -> dict | None:
    """장소+날짜 -> 요약 dict. 장소를 못 찾거나 예보 범위 밖이면 None."""
    cache_dir = Path(cache_dir)
    try:
        loc = geocode(place, cache_dir)
    except Exception:  # noqa: BLE001
        loc = None
    if not loc:
        return None
    try:
        fc = forecast(loc["lat"], loc["lon"], cache_dir)
    except Exception:  # noqa: BLE001
        return None
    day = next((d for d in fc.get("days") or [] if d.get("date") == date_iso), None)
    if not day:
        return None
    tmax, tmin = day.get("tmax"), day.get("tmin")
    rain_prob = day.get("rain_prob")
    rain_mm = day.get("rain_mm") or 0.0
    umbrella = bool((rain_prob is not None and rain_prob >= 50) or rain_mm >= 3)
    cold = bool(tmin is not None and tmin <= 5)
    hot = bool(tmax is not None and tmax >= 30)
    desc = describe(day.get("code") or 0)
    parts = [f"{desc} {rain_prob}%" if rain_prob else desc]
    if tmin is not None and tmax is not None:
        parts.append(f"{int(round(tmin))}~{int(round(tmax))}°")
    summary = " · ".join(parts)
    return {
        "place": loc.get("name") or place,
        "date": date_iso,
        "summary": summary,
        "code": day.get("code"),
        "tmax": tmax,
        "tmin": tmin,
        "rain_prob": rain_prob,
        "rain_mm": rain_mm,
        "umbrella": umbrella,
        "cold": cold,
        "hot": hot,
        "approx": bool(loc.get("approx")),
    }


def weather_sentence(items: list) -> str:
    """카톡 브리핑 한 줄. items[0](=내일 첫 일정)만 사용."""
    if not items:
        return ""
    it = items[0]
    place = it.get("place") or ""
    desc = describe(it.get("code") or 0)
    tmin, tmax = it.get("tmin"), it.get("tmax")
    rng = f" {int(round(tmin))}~{int(round(tmax))}°" if tmin is not None and tmax is not None else ""
    rain_prob = it.get("rain_prob")
    if it.get("umbrella"):
        bit = f"{desc} {rain_prob}%" if rain_prob else desc
        return f"내일 {place} {bit}, 우산 챙기세요"
    return f"내일 {place} {desc}{rng}"


def default_cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    d = base / "second-brain" / "weather"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="weather.py", description="장소 날씨 조회 (second-brain)")
    p.add_argument("place", help="장소명 (예: 김포공항, 제주도)")
    p.add_argument("date", nargs="?", help="YYYY-MM-DD (기본값: 오늘)")
    p.add_argument("--json", action="store_true", help="dict를 JSON으로 출력")
    args = p.parse_args(argv)
    date_iso = args.date or date.today().isoformat()
    result = weather_for(args.place, date_iso, default_cache_dir())
    if result is None:
        print(f"{args.place} {date_iso} 날씨를 찾을 수 없습니다.")
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{result['place']} {result['date']} {result['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
