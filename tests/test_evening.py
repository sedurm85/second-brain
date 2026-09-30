"""저녁 마감·carry·설정 API·관련 기록·ask 컨텍스트."""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402

TODAY = date(2026, 9, 30)


class EveningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_ASK_CMD", "SECOND_BRAIN_OFFLINE")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ["SECOND_BRAIN_OFFLINE"] = "1"  # 저녁 마감 테스트는 날씨와 무관 - 실제 네트워크 금지
        ics = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:i1\nDTSTART;TZID=Asia/Seoul:20261002T140000\nDTEND;TZID=Asia/Seoul:20261002T153000\nSUMMARY:CJ 면접 준비\nLOCATION:온라인\nEND:VEVENT\n"
               "BEGIN:VEVENT\nUID:t1\nDTSTART;VALUE=DATE:20261001\nDTEND;VALUE=DATE:20261002\nSUMMARY:엄마생일\nEND:VEVENT\nEND:VCALENDAR\n")
        (self.home / "cal.ics").write_text(ics, encoding="utf-8")
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        fake = self.home / "fake_ask.sh"
        fake.write_text("#!/bin/sh\ncat > \"$HOME/prompt.txt\"\necho '기억에 CJ 면접 노트가 있어요.'\n", encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        os.environ["SECOND_BRAIN_ASK_CMD"] = str(fake)
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault), "calendar": {"sources": [{"kind": "ics", "name": "t", "path": str(self.home / "cal.ics")}]}}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])
            brain.main(["new", "--type", "note", "--title", "CJ올넷 AX마스터 면접 예상 질문", "--body", "CJ 면접에서 나올 질문: 보안 조직 설계, ISMS-P 경험", "--created", "2026-09-20"])
        (self.vault / "inbox.md").write_text("# inbox\n\n- [ ] 보고서 초안 @due(2026-09-29)\n- [ ] 치과 예약 @due(2026-09-30) @project(건강)\n- [ ] 세무사 답장 @waiting(세무사)\n- [ ] 언젠가 @someday\n- [x] 끝 @due(2026-09-30)\n", encoding="utf-8")

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_carry_and_evening(self):
        r = brain.task_action(self.vault, {"action": "carry"}, TODAY)
        self.assertEqual((r["moved"], r["to"]), (2, "2026-10-01"))
        ts = {t["text"]: t for t in brain.parse_tasks(self.vault, TODAY)}
        self.assertEqual(ts["보고서 초안"]["due"], "2026-10-01")
        self.assertEqual(ts["치과 예약"]["project"], "건강")
        self.assertIsNone(ts["세무사 답장"]["due"])  # 기다림은 그대로
        t = brain.dash_today(self.vault, today=TODAY, widgets=[], agenda={"today": [], "upcoming": []})
        msg = brain.evening_brief(t, [{"title": "엄마생일", "all_day": True, "start": "2026-10-01"}])
        self.assertIn("오늘 할 일은 다 끝났어요", msg)
        self.assertIn("내일 종일 엄마생일", msg)
        self.assertLessEqual(len(msg), 200)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(brain.main(["task", "carry"]), 0)
        self.assertIn("옮길 것이 없어요", buf.getvalue())

    def test_evening_weather_umbrella(self):
        t = brain.dash_today(self.vault, today=TODAY, widgets=[], agenda={"today": [], "upcoming": []})
        tomorrow = [{"title": "엄마생일", "all_day": True, "start": "2026-10-01",
                     "weather": {"place": "제주", "summary": "비 80% · 18~22°", "umbrella": True, "cold": False, "hot": False}}]
        msg = brain.evening_brief(t, tomorrow)
        self.assertIn("우산", msg)
        self.assertIn("제주 비 80%", msg)
        self.assertLessEqual(len(msg), 200)

    def test_evening_weather_cold_wording(self):
        t = brain.dash_today(self.vault, today=TODAY, widgets=[], agenda={"today": [], "upcoming": []})
        tomorrow = [{"title": "출근", "all_day": False, "start": "2026-10-01T09:00",
                     "weather": {"place": "서울", "summary": "맑음 · -2~3°", "umbrella": False, "cold": True, "hot": False}}]
        msg = brain.evening_brief(t, tomorrow)
        self.assertIn("겉옷", msg)
        self.assertNotIn("우산", msg)

    def test_evening_no_weather_when_not_notable(self):
        t = brain.dash_today(self.vault, today=TODAY, widgets=[], agenda={"today": [], "upcoming": []})
        tomorrow = [{"title": "산책", "all_day": False, "start": "2026-10-01T09:00",
                     "weather": {"place": "서울", "summary": "맑음 · 18~22°", "umbrella": False, "cold": False, "hot": False}}]
        msg = brain.evening_brief(t, tomorrow)
        self.assertNotIn("우산", msg)
        self.assertNotIn("겉옷", msg)
        self.assertNotIn("더위", msg)

    def test_cmd_brief_evening_calls_attach_event_notes(self):
        """cmd_brief --evening은 attach_event_notes를 적용해 내일 일정에 weather/note 필드를 붙여야 한다."""
        calls = []
        orig = brain.attach_event_notes

        def spy(vault, ag):
            calls.append(vault)
            return orig(vault, ag)

        brain.attach_event_notes = spy
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = brain.main(["brief", "--evening", "--json"])
            self.assertEqual(code, 0)
        finally:
            brain.attach_event_notes = orig
        # dash_today가 오늘 agenda(7일)에 한 번, --evening 분기가 내일 agenda(2일)에 한 번 더 호출한다.
        self.assertEqual(len(calls), 2)
        for c in calls:
            self.assertEqual(os.path.realpath(c), os.path.realpath(self.vault))

    def test_related_and_ask_context(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        import agenda
        now = datetime(2026, 9, 30, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        ag = agenda.collect_agenda(brain.load_config(), now=now, days=7)
        brain.attach_event_notes(self.vault, ag)
        cj = next(e for e in ag["upcoming"] if e["title"].startswith("CJ"))
        self.assertTrue(cj["prep"])
        self.assertEqual(cj["related"][0]["title"], "CJ올넷 AX마스터 면접 예상 질문")
        birthday = next(e for e in ag["upcoming"] if e["title"] == "엄마생일")
        self.assertFalse(birthday["prep"])
        t = brain.dash_today(self.vault, today=TODAY, agenda=ag, widgets=[])
        res = brain.ask_assistant("CJ 면접 때 뭐 준비하면 좋아?", t, self.vault)
        self.assertIn("CJ올넷 AX마스터 면접 예상 질문", res["memory_used"])
        prompt = (self.home / "prompt.txt").read_text(encoding="utf-8")
        self.assertIn("[기억]", prompt)
        self.assertIn("ISMS-P", prompt)

    def test_config_api(self):
        srv = brain.make_server(self.vault, 0, today=TODAY, quiet=True)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/session", timeout=5) as r:
                tok = json.loads(r.read().decode("utf-8"))["token"]
            req = urllib.request.Request(base + "/api/config", data=json.dumps({"assistant_name": "자비스"}).encode("utf-8"), method="POST",
                                         headers={"Content-Type": "application/json", "X-Brain-Token": tok})
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertEqual(json.loads(r.read().decode("utf-8"))["changed"], {"assistant_name": "자비스"})
            self.assertEqual(brain.load_config()["assistant_name"], "자비스")
            req = urllib.request.Request(base + "/api/config", data=json.dumps({"vault": "/tmp/x"}).encode("utf-8"), method="POST",
                                         headers={"Content-Type": "application/json", "X-Brain-Token": tok})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(cm.exception.code, 400)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    import urllib.error  # noqa: F401
    unittest.main()
