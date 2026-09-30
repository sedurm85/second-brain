"""참석자 → 사람 노트: people_index 정규화, attach_event_notes의 people 필드,
POST/GET /api/person, kakao_brief의 매칭 인물 요약."""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import agenda  # noqa: E402
import brain  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
# 배우자: 기존 event notes 테스트와 같은 이름으로 unittest discover 시 재사용 가능한 최소 샘플
ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:m1
DTSTART;TZID=Asia/Seoul:20261001T100000
DTEND;TZID=Asia/Seoul:20261001T110000
SUMMARY:킥오프미팅
ATTENDEE;CN=김영희:mailto:younghee@example.com
ATTENDEE;CN=이름없음:mailto:noone@example.com
ATTENDEE:mailto:carol@example.com
END:VEVENT
BEGIN:VEVENT
UID:m2
DTSTART;VALUE=DATE:20261005
DTEND;VALUE=DATE:20261006
SUMMARY:엄마생일
END:VEVENT
END:VCALENDAR
"""


class PeopleLinkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(os.path.realpath(self.tmp.name))  # macOS /var→/private/var 심볼릭 링크 우회(HTTP 경로 검증과 일치시킴)
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
        self.now = datetime(2026, 9, 30, 9, 0, tzinfo=KST)  # 킥오프미팅(10/1)까지 D-1(내일)

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

    def new_person(self, title):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(brain.main(["new", "--type", "person", "--title", title]), 0)

    def test_normalize_person_name(self):
        self.assertEqual(brain.normalize_person_name("김영희"), brain.normalize_person_name("김영희 님"))
        for suffix in ("님", "씨", "선생님", "팀장", "부장", "대표"):
            self.assertEqual(brain.normalize_person_name(f"김영희{suffix}"), "김영희")
        self.assertEqual(brain.normalize_person_name(" 김 영희 "), "김영희")

    def test_people_index_by_name_and_email_local_part(self):
        self.new_person("김영희")
        brain.create_note(self.vault, "person", "캐롤", extra={"email": "carol@example.com"})
        idx = brain.people_index(brain.load_notes(self.vault))
        self.assertEqual(idx[brain.normalize_person_name("김영희")].title, "김영희")
        self.assertEqual(idx[brain.normalize_person_name("김영희 님")].title, "김영희")
        self.assertEqual(idx["carol"].title, "캐롤")

    def test_attach_event_notes_matched_and_unmatched(self):
        self.new_person("김영희")
        brain.create_note(self.vault, "person", "캐롤", extra={"email": "carol@example.com"})
        ag = self.agenda()
        e = next(x for x in ag["upcoming"] if x["title"] == "킥오프미팅")
        self.assertEqual(e["days_left"], 1)
        by_name = {p["name"]: p for p in e["people"]}
        self.assertEqual(len(by_name), 3)
        self.assertTrue(by_name["김영희"]["matched"])
        self.assertEqual(by_name["김영희"]["path"], "people/김영희.md")
        self.assertEqual(by_name["김영희"]["mentions"], 0)
        self.assertFalse(by_name["이름없음"]["matched"])
        self.assertIsNone(by_name["이름없음"]["path"])
        self.assertTrue(by_name["carol@example.com"]["matched"])  # 이메일 로컬파트 매칭
        self.assertEqual(by_name["carol@example.com"]["path"], "people/캐롤.md")
        # 참석자 없는 일정에는 people 키를 붙이지 않는다
        birthday = next(x for x in ag["upcoming"] if x["title"] == "엄마생일")
        self.assertIsNone(birthday.get("people"))

    def test_person_action_create_dedupes_and_links_event(self):
        key = "2026-10-01|킥오프미팅"
        r1 = brain.person_action(self.vault, {"action": "create", "name": "이름없음", "event_key": key})
        self.assertTrue(r1["created"])
        self.assertEqual(r1["path"], "people/이름없음.md")
        en, created_again = brain.find_or_create_event_note(self.vault, key)
        self.assertFalse(created_again)  # person_action이 이미 만들어 뒀어야 함
        self.assertIn("[[이름없음]]", brain.as_list(en.meta.get("people")))
        r2 = brain.person_action(self.vault, {"action": "create", "name": "이름없음"})
        self.assertFalse(r2["created"])
        self.assertEqual(r2["path"], r1["path"])

    def test_person_action_bad_input(self):
        with self.assertRaises(brain.BrainError):
            brain.person_action(self.vault, {"action": "create", "name": "  "})
        with self.assertRaises(brain.BrainError):
            brain.person_action(self.vault, {"action": "delete", "name": "김영희"})
        with self.assertRaises(brain.BrainError):
            brain.person_action(self.vault, {"action": "create", "name": "김영희", "event_key": "킥오프미팅"})

    def test_kakao_brief_mentions_matched_person(self):
        self.new_person("김영희")
        ag = agenda.collect_agenda(brain.load_config(), now=self.now, days=7)  # attach는 dash_today가 함
        t = brain.dash_today(self.vault, today=self.now.date(), now=self.now, widgets=[], agenda=ag)
        self.assertIn("김영희", t["kakao"])
        self.assertIn("기록 0건", t["kakao"])
        self.assertLessEqual(len(t["kakao"]), brain.KAKAO_MAX)

    def test_person_http_routes(self):
        self.new_person("김영희")
        srv = brain.make_server(self.vault, 0, today=self.now.date(), quiet=True)
        th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        th.start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/session", timeout=5) as r:
                sess = json.loads(r.read().decode("utf-8"))
            payload = json.dumps({"action": "create", "name": "이름없음", "event_key": "2026-10-01|킥오프미팅"}).encode("utf-8")
            req = urllib.request.Request(base + "/api/person", data=payload, method="POST", headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(cm.exception.code, 403)  # 토큰 없이 쓰기 금지
            req = urllib.request.Request(base + "/api/person", data=payload, method="POST",
                                         headers={"Content-Type": "application/json", "X-Brain-Token": sess["token"]})
            with urllib.request.urlopen(req, timeout=5) as r:
                res = json.loads(r.read().decode("utf-8"))
            self.assertTrue(res["ok"])
            self.assertTrue(res["created"])
            with urllib.request.urlopen(base + "/api/person?path=" + urllib.parse.quote("people/김영희.md"), timeout=5) as r:
                pn = json.loads(r.read().decode("utf-8"))
            self.assertEqual(pn["type"], "person")
            self.assertTrue(any(ev["key"] == "2026-10-01|킥오프미팅" for ev in pn["events"]))
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
