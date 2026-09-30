"""여행 모드: 다중일 종일 일정을 여행으로 인식하고 하위 일정·짐 목록·날짜별 동선을 부착한다."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import agenda  # noqa: E402
import brain  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:j1
DTSTART;VALUE=DATE:20261003
DTEND;VALUE=DATE:20261004
SUMMARY:제주도
END:VEVENT
BEGIN:VEVENT
UID:j2
DTSTART;TZID=Asia/Seoul:20261003T162500
DTEND;TZID=Asia/Seoul:20261003T173500
SUMMARY:대한항공 KE1355
LOCATION:김포공항
DESCRIPTION:예약번호 ABC123\\n좌석 12A
ATTENDEE;CN=배우자:mailto:x@example.com
URL:https://example.com/booking
END:VEVENT
BEGIN:VEVENT
UID:t1
DTSTART;VALUE=DATE:20261001
DTEND;VALUE=DATE:20261002
SUMMARY:엄마생일
END:VEVENT
END:VCALENDAR
"""


class TripModeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_OFFLINE")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ["SECOND_BRAIN_OFFLINE"] = "1"  # 여행 모드 테스트는 날씨 실패를 허용 - 실제 네트워크 금지
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

    def tearDown(self):
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

    def test_is_trip_place_title_vs_plain_event(self):
        ag = self.agenda()
        jeju = next(e for e in ag["upcoming"] if e["title"] == "제주도")
        mom = next(e for e in ag["upcoming"] if e["title"] == "엄마생일")
        self.assertTrue(brain.is_trip(jeju))
        self.assertFalse(brain.is_trip(mom))
        # 여행이 아닌 종일 일정은 trip 필드가 붙지 않는다
        self.assertIsNone(mom.get("trip"))
        self.assertIn("trip", jeju)

    def test_children_and_parent_trip_linked(self):
        ag = self.agenda()
        jeju = next(e for e in ag["upcoming"] if e["title"] == "제주도")
        ke = next(e for e in ag["upcoming"] if e["title"].startswith("대한항공"))
        self.assertIn(ke["key"], jeju["trip"]["children"])
        self.assertEqual(ke["parent_trip"], jeju["key"])
        # 여행 자신은 스스로의 하위 일정이 아니다
        self.assertNotIn(jeju["key"], jeju["trip"]["children"])

    def test_trip_days_and_weather_list_bounded(self):
        ag = self.agenda()
        jeju = next(e for e in ag["upcoming"] if e["title"] == "제주도")
        trip = jeju["trip"]
        self.assertGreaterEqual(trip["days"], 1)
        self.assertEqual(len(trip["dates"]), trip["days"])
        self.assertEqual(trip["dates"][0], "2026-10-03")
        # 오프라인이면 날씨 조회가 비어도 되지만, 길이는 항상 days(최대 5) 이내
        self.assertLessEqual(len(trip["weather"]), min(trip["days"], 5))

    def test_prepare_prompt_trip_variant_mentions_packing_and_days(self):
        ag = self.agenda()
        jeju = next(e for e in ag["upcoming"] if e["title"] == "제주도")
        prompt = brain.prepare_prompt(jeju, jeju.get("note"), jeju.get("related"))
        self.assertIn("짐", prompt)
        self.assertIn(str(jeju["trip"]["days"]), prompt)
        self.assertIn("trip_days", prompt)

    def test_norm_suggestion_trip_cap_raised_to_ten(self):
        ag = self.agenda()
        jeju = next(e for e in ag["upcoming"] if e["title"] == "제주도")
        raw = {"prep": [f"짐: 물건{i}" for i in range(12)]}
        normal = brain._norm_suggestion(raw, jeju, trip=False)
        trip = brain._norm_suggestion(raw, jeju, trip=True)
        self.assertEqual(len(normal), 6)
        self.assertEqual(len(trip), 10)

    def test_event_note_payload_days_grouping(self):
        key = "2026-10-03|제주도"
        brain.event_note_action(self.vault, {"action": "step", "key": key, "text": "10:00 렌터카 인수", "end": "2026-10-04"})
        r = brain.event_note_action(self.vault, {"action": "step", "key": key, "text": "09:00 공항 도착", "day": "2026-10-04"})
        note = r["note"]
        self.assertIn("2026-10-03", note["days"])
        self.assertIn("2026-10-04", note["days"])
        self.assertEqual(note["days"]["2026-10-03"][0]["text"], "렌터카 인수")
        self.assertEqual(note["days"]["2026-10-04"][0]["text"], "공항 도착")


if __name__ == "__main__":
    unittest.main()
