"""일정 어댑터(agenda.py)와 brain.py 연결 테스트. 네트워크·EventKit은 쓰지 않는다(ICS 파일 소스만)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import agenda  # noqa: E402
import brain  # noqa: E402

KST = ZoneInfo("Asia/Seoul")

ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//KO
X-WR-CALNAME:테스트 캘린더
BEGIN:VEVENT
UID:a1
DTSTART;TZID=Asia/Seoul:20260930T100000
DTEND;TZID=Asia/Seoul:20260930T110000
SUMMARY:팀 주간회의
LOCATION:회의실 A
END:VEVENT
BEGIN:VEVENT
UID:a2
DTSTART;VALUE=DATE:20260930
DTEND;VALUE=DATE:20261001
SUMMARY:종일 행사
END:VEVENT
BEGIN:VEVENT
UID:a3
DTSTART:20260930T050000Z
DTEND:20260930T060000Z
SUMMARY:UTC 회의(14시 KST)
END:VEVENT
BEGIN:VEVENT
UID:r1
DTSTART;TZID=Asia/Seoul:20260901T090000
DTEND;TZID=Asia/Seoul:20260901T093000
RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=20
EXDATE;TZID=Asia/Seoul:20260930T090000
SUMMARY:아침 스탠드업
END:VEVENT
BEGIN:VEVENT
UID:r2
DTSTART;TZID=Asia/Seoul:20260901T150000
DTEND;TZID=Asia/Seoul:20260901T160000
RRULE:FREQ=DAILY;UNTIL=20261003T000000Z
SUMMARY:매일 운동
END:VEVENT
BEGIN:VEVENT
UID:r2
RECURRENCE-ID;TZID=Asia/Seoul:20261001T150000
DTSTART;TZID=Asia/Seoul:20261001T170000
DTEND;TZID=Asia/Seoul:20261001T180000
SUMMARY:매일 운동(옮김)
END:VEVENT
BEGIN:VEVENT
UID:c1
DTSTART;TZID=Asia/Seoul:20260930T103000
DTEND;TZID=Asia/Seoul:20260930T113000
SUMMARY:치과
STATUS:CANCELLED
END:VEVENT
BEGIN:VEVENT
UID:long
DTSTART;TZID=Asia/Seoul:20260930T220000
DTEND;TZID=Asia/Seoul:20261001T010000
SUMMARY:야근 자정 넘김\\, 쉼표 포함
DESCRIPTION:여러 줄
  접힘 테스트
END:VEVENT
END:VCALENDAR
"""


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.day0 = datetime(2026, 9, 30, 0, 0, tzinfo=KST)
        self.end = self.day0 + timedelta(days=7)

    def by_title(self, evs):
        return {e["title"]: e for e in evs}

    def test_basic_timed_allday_utc(self):
        evs = agenda.parse_ics(ICS, self.day0, self.end, source="t", tz=KST)
        m = self.by_title(evs)
        self.assertEqual(m["팀 주간회의"]["start"], "2026-09-30T10:00+09:00")
        self.assertEqual(m["팀 주간회의"]["location"], "회의실 A")
        self.assertEqual(m["팀 주간회의"]["calendar"], "테스트 캘린더")
        self.assertTrue(m["종일 행사"]["all_day"])
        self.assertEqual((m["종일 행사"]["start"], m["종일 행사"]["end"]), ("2026-09-30", "2026-10-01"))
        self.assertEqual(m["UTC 회의(14시 KST)"]["start"], "2026-09-30T14:00+09:00")
        self.assertNotIn("치과", m)  # CANCELLED
        self.assertIn("야근 자정 넘김, 쉼표 포함", m)  # 이스케이프 해제

    def test_recurrence_weekly_byday_exdate(self):
        evs = agenda.parse_ics(ICS, self.day0, self.end, source="t", tz=KST)
        standups = sorted(e["start"] for e in evs if e["title"] == "아침 스탠드업")
        # 창 안 월·수: 09-30(수)는 EXDATE로 제외, 10-05(월) 포함. COUNT=20은 9/1부터 10주 → 11/4까지라 창엔 영향 없음
        self.assertNotIn("2026-09-30T09:00+09:00", standups)
        self.assertIn("2026-10-05T09:00+09:00", standups)

    def test_recurrence_daily_until_and_override(self):
        evs = agenda.parse_ics(ICS, self.day0, self.end, source="t", tz=KST)
        daily = sorted(e["start"] for e in evs if e["title"].startswith("매일 운동"))
        self.assertIn("2026-09-30T15:00+09:00", daily)
        self.assertNotIn("2026-10-01T15:00+09:00", daily)  # RECURRENCE-ID로 대체됨
        self.assertIn("2026-10-01T17:00+09:00", daily)  # 옮긴 회차
        self.assertNotIn("2026-10-04T15:00+09:00", daily)  # UNTIL 10-03 이후 없음
        self.assertIn("2026-10-02T15:00+09:00", daily)

    def test_window_excludes_far_events(self):
        evs = agenda.parse_ics(ICS, self.day0 + timedelta(days=30), self.end + timedelta(days=30), source="t", tz=KST)
        self.assertEqual([e["title"] for e in evs if not e["title"].startswith("아침")], [])

    def test_duration_and_unfold(self):
        self.assertEqual(agenda._parse_duration("PT1H30M"), timedelta(hours=1, minutes=30))
        self.assertEqual(agenda._parse_duration("P2D"), timedelta(days=2))
        lines = agenda._unfold("A:1\r\n b\r\nC:2")
        self.assertEqual(lines, ["A:1b", "C:2"])


class CollectTest(unittest.TestCase):
    """임시 HOME에 config + .ics 파일. EventKit은 호출하지 않는다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        (self.home / "cal.ics").write_text(ICS, encoding="utf-8")
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        (cfgdir / "config.json").write_text(json.dumps({
            "vault": str(self.home / "brain"),
            "calendar": {"sources": [{"kind": "ics", "name": "테스트", "path": str(self.home / "cal.ics")},
                                     {"kind": "ics", "name": "없음", "path": str(self.home / "nope.ics")}]},
        }), encoding="utf-8")
        self.now = datetime(2026, 9, 30, 10, 30, tzinfo=KST)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_collect_today_next_conflicts_sources(self):
        ag = agenda.collect_agenda(brain.load_config(), now=self.now, days=7)
        titles = [e["title"] for e in ag["today"]]
        self.assertIn("팀 주간회의", titles)
        self.assertIn("종일 행사", titles)
        self.assertIn("UTC 회의(14시 KST)", titles)
        self.assertEqual(ag["current"][0]["title"], "팀 주간회의")  # 10:30 진행 중
        self.assertEqual(ag["next"]["title"], "UTC 회의(14시 KST)")
        st = {s["name"]: s["status"] for s in ag["sources"]}
        self.assertEqual(st, {"테스트": "ok", "없음": "missing"})
        self.assertTrue(all("_start" not in e for e in ag["today"] + ag["upcoming"]))
        self.assertIn("2026-10-01", {e["start"][:10] for e in ag["upcoming"]})

    def test_sentences(self):
        ag = agenda.collect_agenda(brain.load_config(), now=self.now, days=7)
        s = agenda.agenda_sentence(ag)
        self.assertTrue(s.startswith("오늘 일정 "))
        self.assertIn("10:00 팀 주간회의", s)
        k = agenda.agenda_kakao(ag)
        self.assertTrue(k.startswith("일정 "))
        self.assertLessEqual(len(k), 90)
        self.assertIn("오늘 일정", agenda.agenda_human(ag))

    def test_no_sources_configured_gives_hint_without_calling_anything(self):
        self.assertEqual(agenda.normalize_sources({}), [])
        ag = agenda.collect_agenda({}, now=self.now, days=7)
        self.assertEqual(ag["today"], [])
        self.assertEqual(ag["sources"][0]["status"], "unconfigured")
        self.assertIn("calendar add ics", ag["sources"][0]["hint"])
        self.assertIsNone(agenda.agenda_sentence(ag))

    def test_dash_today_includes_agenda_and_kakao(self):
        ag = agenda.collect_agenda(brain.load_config(), now=self.now, days=7)
        t = brain.dash_today(None, today=self.now.date(), now=self.now, widgets=[], agenda=ag)
        self.assertEqual(len(t["agenda"]["today"]), len(ag["today"]))
        self.assertIn("일정 ", t["kakao"])
        self.assertLessEqual(len(t["kakao"]), 200)
        self.assertIn("오늘 일정", brain.today_human(t))

    def test_api_agenda_route(self):
        vault = self.home / "brain"
        vault.mkdir()
        (vault / "inbox.md").write_text("", encoding="utf-8")
        srv = brain.make_server(vault, 0, today=self.now.date(), quiet=True)
        th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        th.start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/agenda?days=3", timeout=5) as r:
                ag = json.loads(r.read().decode("utf-8"))
            self.assertEqual(ag["days"], 3)
            self.assertIn("today", ag)
            self.assertEqual({s["name"] for s in ag["sources"]}, {"테스트", "없음"})
            with urllib.request.urlopen(base + "/api/today", timeout=5) as r:
                t = json.loads(r.read().decode("utf-8"))
            self.assertIn("agenda", t)
            self.assertIn("sentence", t["agenda"])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_cli_agenda_and_calendar(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(brain.main(["agenda", "--json"]), 0)
        ag = json.loads(buf.getvalue())
        self.assertIn("today", ag)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(brain.main(["calendar", "list"]), 0)
        self.assertIn("테스트 (ics)", buf.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(brain.main(["calendar", "remove", "없음"]), 0)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(brain.main(["calendar", "test"]), 0)  # 남은 소스가 모두 ok
        (self.home / "g.url").write_text("https://calendar.google.com/calendar/ical/x/private-abc/basic.ics\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(brain.main(["calendar", "add", "ics", "구글", "--url-file", str(self.home / "g.url")]), 0)
        cfg = brain.load_config()
        self.assertEqual([x["name"] for x in cfg["calendar"]["sources"]], ["테스트", "구글"])


if __name__ == "__main__":
    unittest.main()
