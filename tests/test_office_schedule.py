"""오늘 근무표: 크론·launchd 스케줄을 오늘 시각으로 펼친 슬롯 상태(완료/실패/지연/예정)와 다음 근무."""
import json
import os
import plistlib
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402

MON = date(2026, 9, 28)   # 알려진 월요일
SUN = date(2026, 9, 27)   # 알려진 일요일
TZ = datetime.now().astimezone().tzinfo


class ScheduleForTest(unittest.TestCase):
    """schedule_for: 크론 필드(*, 리스트, 범위, 스텝) 파싱과 오늘 요일 필터."""

    def test_weekday_range(self):
        self.assertEqual(brain.schedule_for("0 10 * * 1-5", MON), ["10:00"])
        self.assertEqual(brain.schedule_for("0 10 * * 1-5", SUN), [])

    def test_step_every_10_minutes(self):
        out = brain.schedule_for("*/10 * * * *", MON)
        self.assertEqual(out[:4], ["00:00", "00:10", "00:20", "00:30"])
        self.assertEqual(len(out), 144)

    def test_daily(self):
        self.assertEqual(brain.schedule_for("10 9 * * *", MON), ["09:10"])
        self.assertEqual(brain.schedule_for("10 9 * * *", SUN), ["09:10"])

    def test_weekend_list_and_dow_zero_and_seven(self):
        self.assertEqual(brain.schedule_for("0 7,19 * * 0,6", SUN), ["07:00", "19:00"])
        self.assertEqual(brain.schedule_for("0 7,19 * * 0,6", MON), [])

    def test_dow_list_thursday(self):
        thu = date(2026, 10, 1)
        self.assertEqual(brain.schedule_for("5 9 * * 1,4", thu), ["09:05"])
        self.assertEqual(brain.schedule_for("5 9 * * 1,4", MON), ["09:05"])
        self.assertEqual(brain.schedule_for("5 9 * * 1,4", date(2026, 9, 29)), [])  # 화요일

    def test_full_line_with_command_and_redirect(self):
        line = "0 10 * * 1-5 /opt/miniconda3/bin/python3 /x/marketset.py >> /home/x/marketset.log 2>&1"
        self.assertEqual(brain.schedule_for(line, MON), ["10:00"])

    def test_malformed_line_returns_empty(self):
        self.assertEqual(brain.schedule_for("", MON), [])
        self.assertEqual(brain.schedule_for("0 10 *", MON), [])


class LaunchdCalendarTest(unittest.TestCase):
    """_launchd_calendar_times: StartCalendarInterval dict·list, Weekday 0/7=일요일."""

    def test_single_dict(self):
        self.assertEqual(brain._launchd_calendar_times({"Hour": 7, "Minute": 0}, MON), ["07:00"])

    def test_list_of_dicts(self):
        cal = [{"Hour": 7, "Minute": 0}, {"Hour": 19, "Minute": 30}]
        self.assertEqual(brain._launchd_calendar_times(cal, MON), ["07:00", "19:30"])

    def test_weekday_filter_zero_and_seven_are_sunday(self):
        self.assertEqual(brain._launchd_calendar_times({"Hour": 9, "Minute": 0, "Weekday": 0}, SUN), ["09:00"])
        self.assertEqual(brain._launchd_calendar_times({"Hour": 9, "Minute": 0, "Weekday": 7}, SUN), ["09:00"])
        self.assertEqual(brain._launchd_calendar_times({"Hour": 9, "Minute": 0, "Weekday": 1}, SUN), [])
        self.assertEqual(brain._launchd_calendar_times({"Hour": 9, "Minute": 0, "Weekday": 1}, MON), ["09:00"])

    def test_weekday_list(self):
        self.assertEqual(brain._launchd_calendar_times({"Hour": 9, "Minute": 0, "Weekday": [1, 2, 3, 4, 5]}, MON), ["09:00"])
        self.assertEqual(brain._launchd_calendar_times({"Hour": 9, "Minute": 0, "Weekday": [1, 2, 3, 4, 5]}, SUN), [])


class OfficeScheduleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_LAUNCH_AGENTS", "SECOND_BRAIN_NO_LAUNCHCTL", "SECOND_BRAIN_JOBS_DIR")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ["SECOND_BRAIN_NO_LAUNCHCTL"] = "1"
        os.environ["SECOND_BRAIN_JOBS_DIR"] = str(self.home / "nojobs")
        agents = self.home / "agents"
        agents.mkdir()
        os.environ["SECOND_BRAIN_LAUNCH_AGENTS"] = str(agents)
        self.plist_dir = agents
        (agents / "com.test.calendar.plist").write_bytes(plistlib.dumps({
            "Label": "com.test.calendar", "ProgramArguments": ["/bin/bash", "/x/gen.sh"],
            "StandardOutPath": str(self.home / "gen.log"), "StartCalendarInterval": {"Hour": 10, "Minute": 0, "Weekday": [1, 2, 3, 4, 5]},
        }))
        (agents / "com.test.remind.plist").write_bytes(plistlib.dumps({
            "Label": "com.test.remind", "ProgramArguments": ["/bin/bash", "/x/remind.sh"],
            "StandardOutPath": str(self.home / "remind.log"), "StartInterval": 600,
        }))
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        for d in ("notes", "decisions", "projects", "people"):
            (self.vault / d).mkdir(parents=True)
        (self.vault / "BRAIN.md").write_text("# BRAIN\n", encoding="utf-8")
        (self.vault / "inbox.md").write_text("", encoding="utf-8")
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault), "calendar": {"sources": []}}), encoding="utf-8")
        self.wpath = cfgdir / "widgets.json"
        self.wpath.write_text(json.dumps({"allow_commands": False, "allow_run": False, "widgets": [
            {"id": "gen-daily", "title": "카페 글 생성", "kind": "log", "source": "~/gen.log", "team": "콘텐츠팀"},
            {"id": "remind-widget", "title": "출발·시작 알림", "kind": "log", "source": "~/remind.log", "team": "운영팀"},
            {"id": "marketset", "title": "마켓세트", "kind": "log", "source": "~/marketset.log", "team": "생활팀"},
            {"id": "paused-widget", "title": "쉬는 직원", "kind": "log", "source": "~/marketset.log", "team": "생활팀", "state": "paused"},
        ]}, ensure_ascii=False), encoding="utf-8")
        self.cron = [
            "0 9,11,13 * * 1-5 /opt/miniconda3/bin/python3 /x/marketset.py >> " + str(self.home / "marketset.log") + " 2>&1",
        ]

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _widgets(self, extra=None):
        base = json.loads(self.wpath.read_text(encoding="utf-8"))["widgets"]
        for w in base:
            w.setdefault("state", "active")
        if extra:
            for wid, patch in extra.items():
                hit = next(w for w in base if w["id"] == wid)
                hit.update(patch)
        return base

    def test_launchd_calendar_and_cron_slots_and_interval_band(self):
        now = datetime(2026, 9, 28, 11, 5, tzinfo=TZ)  # 월요일
        out = brain.office_schedule(self._widgets(), cron_lines=self.cron, plist_dir=self.plist_dir, today=MON, now=now)
        ids = {s["id"] for s in out["slots"]}
        self.assertIn("gen-daily", ids)     # launchd calendar
        self.assertIn("marketset", ids)     # cron
        self.assertNotIn("remind-widget", ids)  # interval은 slots가 아니라 intervals
        self.assertNotIn("paused-widget", ids)  # 멈춘 위젯 제외
        self.assertEqual(out["intervals"], [{"id": "remind-widget", "title": "출발·시작 알림", "every_min": 10}])

    def test_today_filters_by_weekday(self):
        now_mon = datetime(2026, 9, 28, 8, 0, tzinfo=TZ)
        out_mon = brain.office_schedule(self._widgets(), cron_lines=self.cron, plist_dir=self.plist_dir, today=MON, now=now_mon)
        self.assertTrue(any(s["id"] == "gen-daily" for s in out_mon["slots"]))
        self.assertTrue(any(s["id"] == "marketset" for s in out_mon["slots"]))
        now_sun = datetime(2026, 9, 27, 8, 0, tzinfo=TZ)
        out_sun = brain.office_schedule(self._widgets(), cron_lines=self.cron, plist_dir=self.plist_dir, today=SUN, now=now_sun)
        self.assertFalse(any(s["id"] == "gen-daily" for s in out_sun["slots"]))   # Mon-Fri만
        self.assertFalse(any(s["id"] == "marketset" for s in out_sun["slots"]))  # Mon-Fri만

    def test_slot_state_done_failed_due_upcoming(self):
        now = datetime(2026, 9, 28, 11, 5, tzinfo=TZ)  # marketset 슬롯: 09:00, 11:00(최근 지난), 13:00(예정)
        # 상태 ok + updated_at이 11:00 이후 → 지난 슬롯 모두 done
        done_widgets = self._widgets({"marketset": {"status": "ok", "updated_at": "2026-09-28T11:02:00+09:00"}})
        out = brain.office_schedule(done_widgets, cron_lines=self.cron, plist_dir=self.plist_dir, today=MON, now=now)
        m = {s["time"]: s["state"] for s in out["slots"] if s["id"] == "marketset"}
        self.assertEqual(m, {"09:00": "done", "11:00": "done", "13:00": "upcoming"})

        # 상태 fail + updated_at 없음 → 가장 최근 지난 슬롯(11:00)만 failed, 그 이전(09:00)은 due
        fail_widgets = self._widgets({"marketset": {"status": "fail"}})
        out2 = brain.office_schedule(fail_widgets, cron_lines=self.cron, plist_dir=self.plist_dir, today=MON, now=now)
        m2 = {s["time"]: s["state"] for s in out2["slots"] if s["id"] == "marketset"}
        self.assertEqual(m2, {"09:00": "due", "11:00": "failed", "13:00": "upcoming"})

    def test_next_shift_picks_soonest_upcoming(self):
        now = datetime(2026, 9, 28, 9, 30, tzinfo=TZ)  # marketset 다음은 11:00, gen-daily 다음은 10:00
        out = brain.office_schedule(self._widgets(), cron_lines=self.cron, plist_dir=self.plist_dir, today=MON, now=now)
        self.assertIsNotNone(out["next"])
        self.assertEqual(out["next"]["id"], "gen-daily")
        self.assertEqual(out["next"]["time"], "10:00")
        self.assertEqual(out["next"]["in_min"], 30)

    def test_no_upcoming_gives_none(self):
        now = datetime(2026, 9, 28, 23, 0, tzinfo=TZ)
        out = brain.office_schedule(self._widgets(), cron_lines=self.cron, plist_dir=self.plist_dir, today=MON, now=now)
        self.assertIsNone(out["next"])

    def test_api_office_has_schedule(self):
        d = brain.dash_office(self._widgets(), cron_lines=self.cron, now=datetime(2026, 9, 28, 9, 0, tzinfo=TZ))
        self.assertIn("schedule", d)
        self.assertIn("slots", d["schedule"])
        self.assertIn("intervals", d["schedule"])
        self.assertIn("next", d["schedule"])
        json.dumps(d)  # 직렬화 가능해야 /api/office 응답으로 나갈 수 있다


if __name__ == "__main__":
    unittest.main()
