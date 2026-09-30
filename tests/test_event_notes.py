"""일정 노트(event 타입): 부착·메모·준비 체크리스트·쓰기 API 토큰·CLI."""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
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


class EventNoteTest(unittest.TestCase):
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

    def test_rich_fields_key_days_left_and_tomorrow_sentence(self):
        ag = self.agenda()
        ke = next(e for e in ag["upcoming"] if e["title"].startswith("대한항공"))
        self.assertEqual(ke["key"], "2026-10-03|대한항공 KE1355")
        self.assertEqual(ke["days_left"], 3)
        self.assertIn("예약번호 ABC123", ke["description"])
        self.assertEqual(ke["attendees"], ["배우자"])
        self.assertEqual(ke["url"], "https://example.com/booking")
        self.assertIsNone(ke["note"])
        s = agenda.agenda_sentence(ag)
        self.assertIn("내일은 종일 엄마생일", s)

    def test_memo_todo_check_and_attach(self):
        key = "2026-10-03|제주도"
        r = brain.event_note_action(self.vault, {"action": "todo", "key": key, "text": "렌터카 예약", "end": "2026-10-05"})
        self.assertTrue(r["created"])
        r = brain.event_note_action(self.vault, {"action": "todo", "key": key, "text": "숙소 체크인 시간 확인"})
        self.assertFalse(r["created"])
        r = brain.event_note_action(self.vault, {"action": "memo", "key": key, "text": "16:25 KE1355, 공항 2시간 전 출발"})
        note = r["note"]
        self.assertEqual((note["done"], note["total"]), (0, 2))
        self.assertIn("공항 2시간 전 출발", note["memo"])
        line = note["checklist"][0]["line"]
        r = brain.event_note_action(self.vault, {"action": "check", "path": note["path"], "line": line, "done": True})
        self.assertEqual((r["note"]["done"], r["note"]["total"]), (1, 2))
        # 파일 구조: 준비 절에 체크박스, 메모 절에 타임스탬프 줄
        text = (self.vault / note["path"]).read_text(encoding="utf-8")
        self.assertIn("## 준비\n- [x] 렌터카 예약\n- [ ] 숙소 체크인 시간 확인", text)
        self.assertIn("## 메모\n- ", text)
        self.assertIn("event_key: 2026-10-03|제주도", text)
        self.assertTrue(note["path"].startswith("events/2026/2026-10-03-"))
        # 부착: 정확 키 + 기간(end 10-05) 안의 같은 이름
        ag = self.agenda()
        jeju = next(e for e in ag["upcoming"] if e["title"] == "제주도")
        self.assertEqual(jeju["note"]["total"], 2)
        ke = next(e for e in ag["upcoming"] if e["title"].startswith("대한항공"))
        self.assertIsNone(ke["note"])  # 이름이 다르면 붙지 않음

    def test_bad_inputs(self):
        with self.assertRaises(brain.BrainError):
            brain.event_note_action(self.vault, {"action": "memo", "key": "제주도", "text": "x"})
        with self.assertRaises(brain.BrainError):
            brain.event_note_action(self.vault, {"action": "memo", "key": "2026-10-03|제주도", "text": "  "})
        with self.assertRaises(brain.BrainError):
            brain.event_note_action(self.vault, {"action": "fly", "key": "2026-10-03|제주도"})

    def test_cli_event(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(brain.main(["event", "memo", "2026-10-03|제주도", "렌터카는 공항 앞 A사"]), 0)
        self.assertIn("새 일정 노트 생성 후 메모 추가", buf.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(brain.main(["event", "todo", "2026-10-03|제주도", "선글라스"]), 0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(brain.main(["event", "show", "2026-10-03|제주도"]), 0)
        self.assertIn("- [ ] 선글라스", buf.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(brain.main(["event", "show", "2026-10-09|없는 일정"]), 0)
        self.assertIn("아직 메모가 없는", buf.getvalue())

    def test_post_requires_token_and_writes(self):
        srv = brain.make_server(self.vault, 0, today=self.now.date(), quiet=True)
        th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        th.start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/session", timeout=5) as r:
                sess = json.loads(r.read().decode("utf-8"))
            self.assertEqual(sess["token"], srv.token)
            self.assertTrue(sess["writable"])
            payload = json.dumps({"action": "memo", "key": "2026-10-03|제주도", "text": "토큰 테스트"}).encode("utf-8")
            req = urllib.request.Request(base + "/api/event-note", data=payload, method="POST", headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(cm.exception.code, 403)
            req = urllib.request.Request(base + "/api/event-note", data=payload, method="POST",
                                         headers={"Content-Type": "application/json", "X-Brain-Token": sess["token"]})
            with urllib.request.urlopen(req, timeout=5) as r:
                res = json.loads(r.read().decode("utf-8"))
            self.assertTrue(res["ok"])
            self.assertIn("토큰 테스트", res["note"]["memo"])
            with urllib.request.urlopen(base + "/api/agenda?days=7", timeout=5) as r:
                ag = json.loads(r.read().decode("utf-8"))
            jeju = next(e for e in ag["upcoming"] if e["title"] == "제주도")
            self.assertIn("토큰 테스트", jeju["note"]["memo"])
            with urllib.request.urlopen(base + "/api/today", timeout=5) as r:
                t = json.loads(r.read().decode("utf-8"))
            self.assertIn("upcoming", t["agenda"])
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
