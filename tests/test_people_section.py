"""보드 「사람」 섹션: dash_people·GET /api/people.

test_people_link.py와 같은 최소 볼트 셋업(오프라인, ICS 캘린더)을 쓰되,
참석자는 배우자(사람 노트 있음) + 미확인친구(사람 노트 없음)로 바꿔서
매칭/미매칭 두 갈래를 모두 확인한다.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
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
UID:e1
DTSTART;TZID=Asia/Seoul:20261002T190000
DTEND;TZID=Asia/Seoul:20261002T210000
SUMMARY:저녁약속
ATTENDEE;CN=배우자:mailto:spouse@example.com
ATTENDEE;CN=미확인친구:mailto:friend@example.com
END:VEVENT
END:VCALENDAR
"""


class PeopleSectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(os.path.realpath(self.tmp.name))  # macOS /var→/private/var 심볼릭 링크 우회
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_OFFLINE")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ["SECOND_BRAIN_OFFLINE"] = "1"  # 날씨는 이 테스트와 무관 - 실제 네트워크 금지
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
        self.now = datetime(2026, 9, 30, 9, 0, tzinfo=KST)  # 저녁약속(10/2)까지 D-2

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def cli(self, argv):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(brain.main(argv), 0)

    def agenda(self, days=14):
        ag = agenda.collect_agenda(brain.load_config(), now=self.now, days=days)
        brain.attach_event_notes(self.vault, ag)
        return ag

    def test_dash_people_matched_person_and_unmatched_attendee(self):
        self.cli(["new", "--type", "person", "--title", "배우자"])
        self.cli(["new", "--type", "note", "--title", "배우자와 저녁 회고", "--people", "배우자"])
        r = brain.dash_people(self.vault, self.agenda(), today=self.now.date())

        persons = {p["title"]: p for p in r["people"]}
        self.assertIn("배우자", persons)
        spouse = persons["배우자"]
        self.assertGreaterEqual(spouse["mentions"], 1)
        self.assertIsNotNone(spouse["next_event"])
        self.assertEqual(spouse["next_event"]["title"], "저녁약속")
        self.assertEqual(spouse["next_event"]["days_left"], 2)
        recent_titles = {n["title"] for n in spouse["recent_notes"]}
        self.assertIn("배우자와 저녁 회고", recent_titles)
        self.assertIsNotNone(spouse["last_note"])
        self.assertEqual(spouse["last_note"]["title"], "배우자와 저녁 회고")

        by_name = {u["name"]: u for u in r["unmatched_attendees"]}
        self.assertIn("미확인친구", by_name)
        self.assertEqual(by_name["미확인친구"]["next_event"]["title"], "저녁약속")
        self.assertNotIn("배우자", by_name)  # 매칭된 사람은 미확인 목록에 안 나온다

    def test_dash_people_empty_vault_has_no_people(self):
        r = brain.dash_people(self.vault, self.agenda(), today=self.now.date())
        self.assertEqual(r["people"], [])
        by_name = {u["name"]: u for u in r["unmatched_attendees"]}
        self.assertIn("미확인친구", by_name)
        self.assertIn("배우자", by_name)  # 사람 노트가 없으면 배우자도 미확인으로 남는다

    def test_people_http_route(self):
        self.cli(["new", "--type", "person", "--title", "배우자"])
        self.cli(["new", "--type", "note", "--title", "배우자와 저녁 회고", "--people", "배우자"])
        srv = brain.make_server(self.vault, 0, today=self.now.date(), quiet=True)
        th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        th.start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/people", timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("people", data)
            self.assertIn("unmatched_attendees", data)
            titles = {p["title"] for p in data["people"]}
            self.assertIn("배우자", titles)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
