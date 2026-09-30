"""데모 모드: 비서 샘플(할 일·일정 노트·캘린더·위젯)이 만들어지고 CONFIG_OVERRIDES로 서버가 그것을 쓴다."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


class DemoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_JOBS_DIR", "SECOND_BRAIN_OFFLINE")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ["SECOND_BRAIN_JOBS_DIR"] = str(self.home / "nojobs")
        os.environ["SECOND_BRAIN_OFFLINE"] = "1"  # 데모 캘린더에 위치가 많다 - 날씨 실제 네트워크 금지
        brain.CONFIG_OVERRIDES.clear()

    def tearDown(self):
        brain.CONFIG_OVERRIDES.clear()
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_demo_vault_has_assistant_samples(self):
        today = date(2026, 9, 30)
        v = brain.build_demo_vault(today=today)
        self.assertTrue((v / "_demo" / "calendar.ics").is_file())
        self.assertTrue((v / "_demo" / "widgets.json").is_file())
        tasks = brain.parse_tasks(v, today)
        self.assertGreaterEqual(len(tasks), 4)
        b = brain.bucket_tasks(tasks, today)
        self.assertEqual(b["counts"] if "counts" in b else {k: len(b[k]) for k in ("today", "week", "someday", "waiting")}, {"today": 1, "week": 1, "someday": 1, "waiting": 1})
        exact, ranged = brain.event_notes_index(v)
        self.assertIn("2026-09-30|팀 주간회의", exact)
        self.assertEqual(len(ranged), 1)  # 제주 출장(여러 날)
        trip = exact["2026-10-03|제주 출장"]
        steps = brain.parse_steps(trip.body, "2026-10-03")
        self.assertEqual([(s["day"], s["time"]) for s in steps], [("2026-10-03", "07:20"), ("2026-10-03", "09:25"), ("2026-10-03", "13:00"), ("2026-10-04", "10:00")])
        # _demo 폴더는 노트로 읽히지 않음
        self.assertFalse(any("_demo" in n.rel for n in brain.load_notes(v)))

    def test_server_uses_overrides(self):
        today = date(2026, 9, 30)
        v = brain.build_demo_vault(today=today)
        brain.CONFIG_OVERRIDES.update(brain.demo_overrides(v))
        self.assertEqual(brain.load_config()["assistant_name"], "데모 비서")
        srv = brain.make_server(v, 0, today=today, quiet=True)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/widgets", timeout=5) as r:
                ws = json.loads(r.read().decode("utf-8"))
            self.assertEqual({w["id"]: w["status"] for w in ws}, {"backup": "ok", "price": "ok", "scrape": "fail", "growth": "ok"})
            with urllib.request.urlopen(base + "/api/agenda?days=7", timeout=5) as r:
                ag = json.loads(r.read().decode("utf-8"))
            self.assertEqual(ag["sources"][0]["name"], "데모 캘린더")
            titles = {e["title"] for e in ag["today"]}
            self.assertIn("팀 주간회의", titles)
            meet = next(e for e in ag["today"] if e["title"] == "팀 주간회의")
            self.assertEqual(meet["note"]["total"], 2)
            trip = next(e for e in ag["upcoming"] if e["title"] == "제주 출장")
            self.assertEqual(len(trip["note"]["steps"]), 4)
            with urllib.request.urlopen(base + "/api/today", timeout=5) as r:
                t = json.loads(r.read().decode("utf-8"))
            self.assertIn("팀 주간회의", t["agenda"]["sentence"])
            self.assertEqual(t["tasks"]["counts"]["waiting"], 1)
            with urllib.request.urlopen(base + "/api/office", timeout=5) as r:
                d = json.loads(r.read().decode("utf-8"))
            self.assertEqual({tm["name"] for tm in d["teams"]}, {"운영팀", "생활팀", "커리어팀", "콘텐츠팀"})
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
