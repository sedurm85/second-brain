"""prepare: 제안 대상 선별, 가짜 Claude로 제안 생성 → 보류함, /api/today 노출, 채택(고른 항목만 노트에) · 무시, 재실행 건너뜀, 카톡 한 줄."""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402

KST = timezone(timedelta(hours=9))
ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:j2
DTSTART;TZID=Asia/Seoul:20261003T162500
DTEND;TZID=Asia/Seoul:20261003T173500
SUMMARY:대한항공 KE1355
LOCATION:김포공항
DESCRIPTION:예약번호 ABC123
END:VEVENT
BEGIN:VEVENT
UID:t1
DTSTART;VALUE=DATE:20261001
DTEND;VALUE=DATE:20261002
SUMMARY:엄마생일
END:VEVENT
BEGIN:VEVENT
UID:far
DTSTART;VALUE=DATE:20261120
DTEND;VALUE=DATE:20261121
SUMMARY:먼 일정
END:VEVENT
END:VCALENDAR
"""


class PrepareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_ASK_CMD")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        (self.home / "cal.ics").write_text(ICS, encoding="utf-8")
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault),
            "calendar": {"sources": [{"kind": "ics", "name": "t", "path": str(self.home / "cal.ics")}]}}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])
        fake = self.home / "fake_claude.py"
        fake.write_text('''import sys, json
p = sys.stdin.read()
ctx = json.loads(p.rsplit("[일정]", 1)[1])
if "대한항공" in ctx["title"]:
    out = {"prep": ["여권", "모바일 체크인", "보조배터리"], "steps": ["13:30 집 출발 (콜밴)", "14:25 김포공항 도착", "시간모름 라운지"], "memo": "국내선은 신분증만으로도 탑승 가능"}
else:
    out = {"prep": ["케이크 예약", "꽃"], "steps": [], "memo": ""}
print("```json\\n" + json.dumps(out, ensure_ascii=False) + "\\n```")
''', encoding="utf-8")
        os.environ["SECOND_BRAIN_ASK_CMD"] = sys.executable + " " + str(fake)
        self.today = datetime(2026, 9, 30, 9, 0, tzinfo=KST).date()

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _agenda(self):
        ag = dict(brain.agenda_mod.collect_agenda(brain.load_config(), now=datetime(2026, 9, 30, 9, 0, tzinfo=KST), days=7))
        brain.attach_event_notes(self.vault, ag)
        return ag

    def test_targets_and_prompt(self):
        ag = self._agenda()
        tg = brain.suggest_targets(ag, {}, 7)
        self.assertEqual([e["title"] for e in tg], ["엄마생일", "대한항공 KE1355"])  # 먼 일정은 제외
        ke = tg[1]
        p = brain.prepare_prompt(ke, None, [])
        self.assertIn("김포공항", p)
        self.assertIn("예약번호 ABC123", p)
        # 이미 제안한 키는 건너뜀
        self.assertEqual(len(brain.suggest_targets(ag, {ke["key"]: {"status": "dismissed"}}, 7)), 1)

    def test_cli_accept_dismiss(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = brain.main(["prepare", "--days", "7"])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn("준비 제안 2건", buf.getvalue())
        sugg = brain.load_suggestions()
        key = "2026-10-03|대한항공 KE1355"
        self.assertEqual(sugg[key]["status"], "pending")
        kinds = [i["kind"] for i in sugg[key]["items"]]
        self.assertEqual(kinds.count("prep"), 3)
        self.assertEqual(kinds.count("step"), 2)  # 'HH:MM' 형식이 아닌 동선은 버림
        self.assertEqual(kinds.count("memo"), 1)
        # /api/today 노출 + 카톡 한 줄
        t = brain.dash_today(self.vault, today=self.today, widgets=[], agenda=self._agenda())
        self.assertEqual(t["suggestions"]["count"], 2)
        self.assertIn(key, t["suggestions"]["by_key"])
        self.assertIn("준비 제안 2건", t["kakao"])
        # 채택: 여권·체크인·첫 동선·메모만
        res = brain.suggestion_action(self.vault, {"action": "accept", "key": key, "indexes": [0, 1, 3, 5]})
        self.assertEqual((res["status"], res["count"]), ("accepted", 4))
        note = next(n for n in brain.load_notes(self.vault) if n.meta.get("event_key") == key)
        self.assertIn("- [ ] 여권", note.body)
        self.assertIn("- [ ] 모바일 체크인", note.body)
        self.assertNotIn("보조배터리", note.body)
        self.assertIn("- 13:30 집 출발 (콜밴)", note.body)
        self.assertNotIn("14:25", note.body)
        self.assertIn("국내선은 신분증", note.body)
        self.assertEqual(note.meta.get("event_date"), "2026-10-03")
        # 무시
        res = brain.suggestion_action(self.vault, {"action": "dismiss", "key": "2026-10-01|엄마생일"})
        self.assertEqual(res["status"], "dismissed")
        self.assertEqual(brain.pending_suggestions(today=self.today), {})
        with self.assertRaises(brain.BrainError):
            brain.suggestion_action(self.vault, {"action": "accept", "key": "2026-10-01|없음"})
        # 재실행: 둘 다 처리됨 → 대상 없음
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            brain.main(["prepare"])
        self.assertIn("제안할 일정이 없어요", buf.getvalue())

    def test_api(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            brain.main(["prepare"])
        srv = brain.make_server(self.vault, 0, quiet=True, today=self.today)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/suggestions", timeout=5) as r:
                d = json.loads(r.read().decode("utf-8"))
            self.assertEqual(len(d["pending"]), 2)
            with urllib.request.urlopen(base + "/api/session", timeout=5) as r:
                tok = json.loads(r.read().decode("utf-8"))["token"]
            req = urllib.request.Request(base + "/api/suggestion", data=json.dumps({"action": "accept", "key": "2026-10-01|엄마생일"}).encode("utf-8"),
                                         method="POST", headers={"Content-Type": "application/json", "X-Brain-Token": tok})
            with urllib.request.urlopen(req, timeout=10) as r:
                res = json.loads(r.read().decode("utf-8"))
            self.assertEqual(res["count"], 2)
            self.assertEqual(res["note"]["total"], 2)
            with urllib.request.urlopen(base + "/api/suggestions", timeout=5) as r:
                self.assertEqual(len(json.loads(r.read().decode("utf-8"))["pending"]), 1)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_agent_spec(self):
        self.assertIn("prepare", brain.AGENT_SPECS)
        self.assertEqual(brain.AGENT_SPECS["prepare"]["calendar"], {"Hour": 6, "Minute": 40})


if __name__ == "__main__":
    unittest.main()
