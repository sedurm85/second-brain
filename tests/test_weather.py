"""weather.py 단위 테스트. 실제 네트워크는 절대 쓰지 않는다(FETCH 훅을 가짜로 교체)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import weather  # noqa: E402


def _geo_result(name="김포시", lat=37.6153, lon=126.7159, country="대한민국", admin1="경기도", cc="KR"):
    return {
        "results": [
            {
                "name": name,
                "latitude": lat,
                "longitude": lon,
                "country": country,
                "country_code": cc,
                "admin1": admin1,
            }
        ]
    }


def _forecast_result(dates, codes, tmax, tmin, rain_prob, rain_mm):
    return {
        "daily": {
            "time": dates,
            "weather_code": codes,
            "temperature_2m_max": tmax,
            "temperature_2m_min": tmin,
            "precipitation_probability_max": rain_prob,
            "precipitation_sum": rain_mm,
        }
    }


class FakeFetch:
    """url -> 반환값(dict) 매핑, 없으면 예외. 호출된 url을 기록해 검증에 쓴다."""

    def __init__(self, mapping=None, raise_always=False):
        self.mapping = mapping or {}
        self.raise_always = raise_always
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if self.raise_always:
            raise OSError("network down")
        for key, value in self.mapping.items():
            if key in url:
                if value is None:
                    raise OSError(f"no fixture for {url}")
                return value
        raise OSError(f"unexpected url: {url}")


class WeatherTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self._tmp.name) / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._orig_fetch = weather.FETCH
        self._orig_offline = os.environ.get("SECOND_BRAIN_OFFLINE")
        if "SECOND_BRAIN_OFFLINE" in os.environ:
            del os.environ["SECOND_BRAIN_OFFLINE"]

    def tearDown(self):
        weather.FETCH = self._orig_fetch
        self._tmp.cleanup()
        if self._orig_offline is None:
            os.environ.pop("SECOND_BRAIN_OFFLINE", None)
        else:
            os.environ["SECOND_BRAIN_OFFLINE"] = self._orig_offline


class DescribeTests(unittest.TestCase):
    def test_known_codes(self):
        self.assertEqual(weather.describe(0), "맑음")
        self.assertEqual(weather.describe(1), "대체로 맑음")
        self.assertEqual(weather.describe(2), "구름 많음")
        self.assertEqual(weather.describe(3), "흐림")
        self.assertEqual(weather.describe(45), "안개")
        self.assertEqual(weather.describe(51), "이슬비")
        self.assertEqual(weather.describe(61), "비")
        self.assertEqual(weather.describe(80), "소나기")
        self.assertEqual(weather.describe(71), "눈")
        self.assertEqual(weather.describe(95), "뇌우")

    def test_unknown_code(self):
        self.assertEqual(weather.describe(12345), "알 수 없음")
        self.assertEqual(weather.describe(None), "알 수 없음")


class NormalizeQueryTests(unittest.TestCase):
    def test_strip_parens(self):
        self.assertEqual(weather._normalize_place("김포공항(서울)"), "김포공항")
        self.assertEqual(weather._normalize_place("  제주도  "), "제주도")

    def test_fallback_suffix(self):
        cands = weather.candidates("김포공항")
        self.assertIn("김포", cands)

    def test_fallback_multi_token(self):
        cands = weather.candidates("강남 삼성점 근처")
        self.assertIn("강남 삼성점", cands)


class CandidatesTests(unittest.TestCase):
    def test_airport_alias_then_generic_then_locality(self):
        self.assertEqual(weather.candidates("김포공항"), ["김포공항", "김포국제공항", "김포"])

    def test_island_do_alias_and_si(self):
        self.assertEqual(weather.candidates("제주도"), ["제주도", "제주", "제주시"])

    def test_multi_token_airport_with_extra_token(self):
        self.assertEqual(weather.candidates("인천공항 T2"), ["인천공항 T2", "인천국제공항", "인천"])

    def test_generic_airport_not_in_alias_dict(self):
        # 별칭 사전에 없어도 "X공항" 일반 규칙이 커버해야 한다.
        cands = weather.candidates("여수공항")
        self.assertEqual(cands, ["여수공항", "여수국제공항", "여수"])

    def test_facility_suffix_strips_to_locality(self):
        self.assertIn("강남", weather.candidates("강남역"))
        self.assertIn("판교테크노밸리", weather.candidates("판교테크노밸리점"))

    def test_admin_suffix_strips_to_locality(self):
        self.assertIn("화성", weather.candidates("화성시"))
        self.assertIn("수원", weather.candidates("수원구"))

    def test_last_resort_two_char_marked_only_when_nothing_else_matches(self):
        cands = weather.candidates("없는곳")
        self.assertEqual(cands, ["없는곳", "없는"])
        # 규칙이 하나라도 맞으면 마지막 수단(2글자)은 추가되지 않는다
        self.assertNotIn("동탄역"[:2], weather.candidates("동탄역")[2:])

    def test_no_last_resort_for_short_names(self):
        # 한글 3자 미만이면 마지막 수단도 시도하지 않는다(원문만 반환)
        self.assertEqual(weather.candidates("동탄"), ["동탄"])

    def test_no_duplicates(self):
        cands = weather.candidates("제주도")
        self.assertEqual(len(cands), len(set(cands)))


class GeocodeTests(WeatherTestBase):
    def test_full_query_hit_prefers_kr(self):
        non_kr = {
            "results": [
                {"name": "Jeju", "latitude": 1.0, "longitude": 1.0, "country": "X", "country_code": "US"},
                {"name": "제주도", "latitude": 33.4996, "longitude": 126.5312, "country": "대한민국", "country_code": "KR", "admin1": "제주"},
            ]
        }
        fetch = FakeFetch({"name=%EC%A0%9C%EC%A3%BC%EB%8F%84": non_kr})
        weather.FETCH = fetch
        result = weather.geocode("제주도", self.cache_dir)
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "제주도")
        self.assertEqual(result["country"], "대한민국")

    def test_fallback_query_used_when_full_query_misses(self):
        fetch = FakeFetch(
            {
                "name=%EA%B9%80%ED%8F%AC%EA%B3%B5%ED%95%AD": {"results": []},
                "name=%EA%B9%80%ED%8F%AC": _geo_result(name="김포시"),
            }
        )
        weather.FETCH = fetch
        result = weather.geocode("김포공항", self.cache_dir)
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "김포시")
        # 전체 쿼리 먼저 시도하고 그다음 fallback을 시도했는지 확인
        self.assertEqual(len(fetch.calls), 2)

    def test_last_resort_candidate_marks_approx(self):
        fetch = FakeFetch(
            {
                "name=%EC%97%86%EB%8A%94%EA%B3%B3": {"results": []},
                "name=%EC%97%86%EB%8A%94": _geo_result(name="없는"),
            }
        )
        weather.FETCH = fetch
        result = weather.geocode("없는곳", self.cache_dir)
        self.assertIsNotNone(result)
        self.assertTrue(result.get("approx"))

    def test_normal_hit_does_not_mark_approx(self):
        fetch = FakeFetch({"name=%EC%A0%9C%EC%A3%BC%EB%8F%84": _geo_result(name="제주도")})
        weather.FETCH = fetch
        result = weather.geocode("제주도", self.cache_dir)
        self.assertIsNotNone(result)
        self.assertFalse(result.get("approx"))

    def test_cache_write_then_read_without_network(self):
        fetch = FakeFetch({"name=%EC%A0%9C%EC%A3%BC%EB%8F%84": _geo_result(name="제주도")})
        weather.FETCH = fetch
        first = weather.geocode("제주도", self.cache_dir)
        self.assertIsNotNone(first)
        self.assertEqual(len(fetch.calls), 1)
        # 캐시 파일이 생겼는지
        cache_path = self.cache_dir / "geocode.json"
        self.assertTrue(cache_path.exists())
        # 다시 호출해도 네트워크를 또 타지 않아야 함
        second = weather.geocode("제주도", self.cache_dir)
        self.assertEqual(second, first)
        self.assertEqual(len(fetch.calls), 1)

    def test_miss_cached_and_retried_after_7_days(self):
        # "없는곳"은 한글 3자라 다른 규칙이 안 맞으면 최후 수단으로 앞 2글자("없는")도
        # 시도한다 - 두 후보 모두 미스로 고정해 둔다.
        fetch = FakeFetch(
            {
                "name=%EC%97%86%EB%8A%94%EA%B3%B3": {"results": []},
                "name=%EC%97%86%EB%8A%94": {"results": []},
            }
        )
        weather.FETCH = fetch
        result = weather.geocode("없는곳", self.cache_dir)
        self.assertIsNone(result)
        self.assertEqual(len(fetch.calls), 2)
        # 바로 다시 조회하면 캐시된 미스라 네트워크를 타지 않음
        result2 = weather.geocode("없는곳", self.cache_dir)
        self.assertIsNone(result2)
        self.assertEqual(len(fetch.calls), 2)
        # 캐시 타임스탬프를 8일 전으로 돌리면 재시도해야 함
        cache_path = self.cache_dir / "geocode.json"
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        data["없는곳"]["ts"] -= 8 * 86400
        cache_path.write_text(json.dumps(data), encoding="utf-8")
        result3 = weather.geocode("없는곳", self.cache_dir)
        self.assertIsNone(result3)
        self.assertEqual(len(fetch.calls), 4)

    def test_network_failure_returns_none(self):
        weather.FETCH = FakeFetch(raise_always=True)
        result = weather.geocode("아무곳", self.cache_dir)
        self.assertIsNone(result)

    def test_offline_env_skips_network(self):
        os.environ["SECOND_BRAIN_OFFLINE"] = "1"
        fetch = FakeFetch({})
        weather.FETCH = fetch
        result = weather.geocode("제주도", self.cache_dir)
        self.assertIsNone(result)
        self.assertEqual(len(fetch.calls), 0)

    def test_offline_env_still_reads_existing_cache(self):
        fetch = FakeFetch({"name=%EC%A0%9C%EC%A3%BC%EB%8F%84": _geo_result(name="제주도")})
        weather.FETCH = fetch
        weather.geocode("제주도", self.cache_dir)
        os.environ["SECOND_BRAIN_OFFLINE"] = "1"
        weather.FETCH = FakeFetch({})
        result = weather.geocode("제주도", self.cache_dir)
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "제주도")


class ForecastTests(WeatherTestBase):
    def test_parses_daily_fields(self):
        payload = _forecast_result(
            ["2026-10-01", "2026-10-02"],
            [61, 0],
            [24.0, 19.0],
            [18.0, 10.0],
            [70, 0],
            [5.0, 0.0],
        )
        weather.FETCH = FakeFetch({"latitude=37.5": payload})
        result = weather.forecast(37.5, 127.0, self.cache_dir)
        self.assertEqual(len(result["days"]), 2)
        d0 = result["days"][0]
        self.assertEqual(d0["date"], "2026-10-01")
        self.assertEqual(d0["code"], 61)
        self.assertEqual(d0["tmax"], 24.0)
        self.assertEqual(d0["tmin"], 18.0)
        self.assertEqual(d0["rain_prob"], 70)
        self.assertEqual(d0["rain_mm"], 5.0)

    def test_ttl_3_hours_serves_cache(self):
        payload = _forecast_result(["2026-10-01"], [0], [20.0], [10.0], [0], [0.0])
        fetch = FakeFetch({"latitude=37.5": payload})
        weather.FETCH = fetch
        weather.forecast(37.5, 127.0, self.cache_dir)
        self.assertEqual(len(fetch.calls), 1)
        weather.forecast(37.5, 127.0, self.cache_dir)
        self.assertEqual(len(fetch.calls), 1)  # 캐시 히트, 네트워크 안 탐

    def test_ttl_expiry_refetches(self):
        payload = _forecast_result(["2026-10-01"], [0], [20.0], [10.0], [0], [0.0])
        fetch = FakeFetch({"latitude=37.5": payload})
        weather.FETCH = fetch
        weather.forecast(37.5, 127.0, self.cache_dir)
        cache_path = self.cache_dir / "forecast-37.5000-127.0000.json"
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        data["_ts"] -= 4 * 3600  # 4시간 전으로 되돌려 TTL 만료시킴
        cache_path.write_text(json.dumps(data), encoding="utf-8")
        weather.forecast(37.5, 127.0, self.cache_dir)
        self.assertEqual(len(fetch.calls), 2)

    def test_network_failure_returns_empty_when_no_cache(self):
        weather.FETCH = FakeFetch(raise_always=True)
        result = weather.forecast(37.5, 127.0, self.cache_dir)
        self.assertEqual(result["days"], [])

    def test_offline_env_returns_empty_without_cache(self):
        os.environ["SECOND_BRAIN_OFFLINE"] = "1"
        fetch = FakeFetch({})
        weather.FETCH = fetch
        result = weather.forecast(37.5, 127.0, self.cache_dir)
        self.assertEqual(result["days"], [])
        self.assertEqual(len(fetch.calls), 0)


class WeatherForTests(WeatherTestBase):
    def _stub(self, geo_name="김포시", lat=37.6153, lon=126.7159):
        geo_payload = _geo_result(name=geo_name, lat=lat, lon=lon)
        fc_payload = _forecast_result(
            ["2026-10-02", "2026-10-03"],
            [61, 0],
            [24.0, 30.5],
            [18.0, 2.0],
            [70, 0],
            [5.0, 0.0],
        )
        weather.FETCH = FakeFetch(
            {
                "name=%EA%B9%80%ED%8F%AC": geo_payload,
                "latitude=37.6153": fc_payload,
            }
        )

    def test_summary_and_umbrella(self):
        self._stub()
        result = weather.weather_for("김포", "2026-10-02", self.cache_dir)
        self.assertIsNotNone(result)
        self.assertEqual(result["place"], "김포시")
        self.assertTrue(result["umbrella"])
        self.assertIn("비 70%", result["summary"])
        self.assertIn("18~24", result["summary"])

    def test_hot_and_cold_flags(self):
        self._stub()
        result = weather.weather_for("김포", "2026-10-03", self.cache_dir)
        self.assertIsNotNone(result)
        self.assertTrue(result["hot"])  # tmax 30.5 >= 30
        self.assertTrue(result["cold"])  # tmin 2.0 <= 5
        self.assertFalse(result["umbrella"])

    def test_out_of_window_date_returns_none(self):
        self._stub()
        result = weather.weather_for("김포", "2030-01-01", self.cache_dir)
        self.assertIsNone(result)

    def test_place_not_found_returns_none(self):
        weather.FETCH = FakeFetch({"name=": {"results": []}})
        result = weather.weather_for("존재하지않는곳", "2026-10-02", self.cache_dir)
        self.assertIsNone(result)


class WeatherSentenceTests(unittest.TestCase):
    def test_umbrella_sentence(self):
        items = [{"place": "김포", "code": 61, "tmin": 18.0, "tmax": 24.0, "rain_prob": 70, "umbrella": True}]
        self.assertEqual(weather.weather_sentence(items), "내일 김포 비 70%, 우산 챙기세요")

    def test_no_umbrella_sentence(self):
        items = [{"place": "제주", "code": 0, "tmin": 19.0, "tmax": 25.0, "rain_prob": 0, "umbrella": False}]
        self.assertEqual(weather.weather_sentence(items), "내일 제주 맑음 19~25°")

    def test_empty_items(self):
        self.assertEqual(weather.weather_sentence([]), "")


if __name__ == "__main__":
    unittest.main()
