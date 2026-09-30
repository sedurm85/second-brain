"""weather.py를 brain.py에 연결한 지점들의 통합 테스트. 실제 네트워크는 쓰지 않는다
(weather.FETCH를 가짜로 바꿔치기). docs/weather-integration.md의 삽입 지점이 실제로
attach_event_notes/kakao_brief/today_human/prepare_prompt까지 이어지는지 확인한다."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import agenda  # noqa: E402
import brain  # noqa: E402
import weather  # noqa: E402

KST = ZoneInfo("Asia/Seoul")

# w1: 내일(2026-10-01) 김포공항발 리무진 - 위치 있음, 비 80% -> 우산
# w2: 2026-10-03 종일 "제주도" - 위치 없이 제목이 장소 이름, 맑음
# w3: 2026-10-02 "동네카페 미팅" - 위치가 지오코딩이 절대 못 찾는 이름 -> weather None이어야 함
ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:w1
DTSTART;TZID=Asia/Seoul:20261001T090000
DTEND;TZID=Asia/Seoul:20261001T110000
SUMMARY:공항 리무진
LOCATION:김포공항
END:VEVENT
BEGIN:VEVENT
UID:w2
DTSTART;VALUE=DATE:20261003
DTEND;VALUE=DATE:20261004
SUMMARY:제주도
END:VEVENT
BEGIN:VEVENT
UID:w3
DTSTART;TZID=Asia/Seoul:20261002T140000
DTEND;TZID=Asia/Seoul:20261002T150000
SUMMARY:동네카페 미팅
LOCATION:아무도모르는가상의동네
END:VEVENT
END:VCALENDAR
"""


def _geo(name, lat, lon):
    return {"results": [{"name": name, "latitude": lat, "longitude": lon,
                         "country": "대한민국", "country_code": "KR", "admin1": "-"}]}


def _fc(date_iso, code, tmax, tmin, rain_prob, rain_mm):
    return {"daily": {"time": [date_iso], "weather_code": [code],
                      "temperature_2m_max": [tmax], "temperature_2m_min": [tmin],
                      "precipitation_probability_max": [rain_prob], "precipitation_sum": [rain_mm]}}


def fetch_stub(url):
    """weather.FETCH 대체: name=/latitude= 파라미터로 분기, 매핑 없으면 예외(네트워크 실패 흉내)."""
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    if "name" in qs:
        name = qs["name"][0]
        if name == "김포국제공항":
            return _geo("김포국제공항", 37.5660, 126.8008)
        if name == "제주시":
            return _geo("제주시", 33.5097, 126.5219)
        raise OSError(f"no fixture for geocode: {name}")
    if "latitude" in qs:
        lat = qs["latitude"][0]
        if lat.startswith("37.566"):
            return _fc("2026-10-01", 61, 15.0, 8.0, 80, 4.0)  # 김포: 비 80% -> 우산
        if lat.startswith("33.5097") or lat.startswith("33.509"):
            return _fc("2026-10-03", 0, 24.0, 17.0, 5, 0.0)  # 제주: 맑음
        raise OSError(f"no fixture for forecast: {lat}")
    raise OSError(f"unexpected url: {url}")


class WeatherIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_OFFLINE")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ.pop("SECOND_BRAIN_OFFLINE", None)
        (self.home / "cal.ics").write_text(ICS, encoding="utf-8")
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        (cfgdir / "config.json").write_text(json.dumps({
            "vault": str(self.vault),
            "calendar": {"sources": [{"kind": "ics", "name": "t", "path": str(self.home / "cal.ics")}]},
        }), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])
        self.now = datetime(2026, 9, 30, 9, 0, tzinfo=KST)
        self._orig_fetch = weather.FETCH
        weather.FETCH = fetch_stub

    def tearDown(self):
        weather.FETCH = self._orig_fetch
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def agenda(self):
        ag = agenda.collect_agenda(brain.load_config(), now=self.now, days=7)
        brain.attach_event_notes(self.vault, ag)
        return ag

    def test_located_event_gets_weather_summary_and_umbrella(self):
        ag = self.agenda()
        limo = next(e for e in ag["upcoming"] if e["title"] == "공항 리무진")
        self.assertIsNotNone(limo["weather"])
        self.assertIn("비", limo["weather"]["summary"])
        self.assertTrue(limo["weather"]["umbrella"])

    def test_all_day_place_title_gets_weather_without_location(self):
        ag = self.agenda()
        jeju = next(e for e in ag["upcoming"] if e["title"] == "제주도")
        self.assertFalse(jeju.get("location"))
        self.assertIsNotNone(jeju["weather"])
        self.assertIn("맑음", jeju["weather"]["summary"])
        self.assertFalse(jeju["weather"]["umbrella"])

    def test_unresolvable_location_yields_none_without_breaking_agenda(self):
        ag = self.agenda()
        cafe = next(e for e in ag["upcoming"] if e["title"] == "동네카페 미팅")
        self.assertIsNone(cafe["weather"])
        # 날씨가 실패해도 다른 필드(제목·장소)는 그대로 살아 있어야 한다
        self.assertEqual(cafe["location"], "아무도모르는가상의동네")
        self.assertEqual(len(ag["upcoming"]), 3)

    def test_weather_key_survives_into_dash_today(self):
        t = brain.dash_today(self.vault, today=self.now.date(), now=self.now)
        limo = next(e for e in t["agenda"]["upcoming"] if e["title"] == "공항 리무진")
        self.assertIsNotNone(limo["weather"])
        self.assertTrue(limo["weather"]["umbrella"])

    def test_kakao_brief_mentions_umbrella(self):
        t = brain.dash_today(self.vault, today=self.now.date(), now=self.now)
        self.assertIn("우산", t["kakao"])
        self.assertLessEqual(len(t["kakao"]), 200)

    def test_today_human_has_weather_line(self):
        t = brain.dash_today(self.vault, today=self.now.date(), now=self.now)
        human = brain.today_human(t)
        self.assertIn("날씨:", human)
        self.assertIn("김포국제공항", human)

    def test_prepare_prompt_contains_weather(self):
        t = brain.dash_today(self.vault, today=self.now.date(), now=self.now)
        limo = next(e for e in t["agenda"]["upcoming"] if e["title"] == "공항 리무진")
        prompt = brain.prepare_prompt(limo, None, [])
        self.assertIn('"weather"', prompt)
        self.assertIn("우산", prompt)  # 날씨 궂으면 우산 제안하라는 규칙 문장


if __name__ == "__main__":
    unittest.main()
