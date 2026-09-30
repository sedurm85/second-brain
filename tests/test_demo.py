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
        self.assertGreaterEqual(len(tasks), 5)
        b = brain.bucket_tasks(tasks, today)
        # today=2: 마감이 오늘인 것 1개 + 연체(지난 마감) 1개
        self.assertEqual(b["counts"] if "counts" in b else {k: len(b[k]) for k in ("today", "week", "someday", "waiting")}, {"today": 2, "week": 1, "someday": 1, "waiting": 1})
        overdue = [t for t in b["today"] if t.get("due") and t["due"] < today.isoformat()]
        self.assertEqual(len(overdue), 1)  # 연체 @due 확인
        self.assertEqual(len(b["waiting"]), 1)  # @waiting 확인
        exact, ranged = brain.event_notes_index(v)
        self.assertIn("2026-09-30|팀 주간회의", exact)
        self.assertEqual(len(ranged), 1)  # 제주 출장(여러 날)
        trip = exact["2026-10-03|제주 출장"]
        steps = brain.parse_steps(trip.body, "2026-10-03")
        self.assertEqual([(s["day"], s["time"]) for s in steps], [("2026-10-03", "07:20"), ("2026-10-03", "09:25"), ("2026-10-03", "13:00"), ("2026-10-04", "10:00")])
        # _demo 폴더는 노트로 읽히지 않음
        self.assertFalse(any("_demo" in n.rel for n in brain.load_notes(v)))
        # 제주 출장에 장소가 붙어 있어야 날씨 캐시가 의미 있음
        self.assertIn("LOCATION:김포국제공항", (v / "_demo" / "calendar.ics").read_text(encoding="utf-8"))
        # 그래프 구조: 사람·프로젝트 노트가 결정에서 직접 연결됨
        g = brain.dash_graph(v)
        node_type = {n["id"]: n["type"] for n in g["nodes"]}
        edge_type_pairs = [{node_type.get(e["source"]), node_type.get(e["target"])} for e in g["edges"]]
        self.assertIn({"decision", "person"}, edge_type_pairs)
        self.assertIn({"decision", "project"}, edge_type_pairs)

    def test_demo_cache_seeded(self):
        """suggestions.json·staff_briefs.json·core_log.jsonl·weather 캐시가 채워진다."""
        today = date(2026, 9, 30)
        v = brain.build_demo_vault(today=today)
        sugg = brain.load_suggestions()
        self.assertEqual(len(brain.pending_suggestions(sugg, today)), 2)
        for s in sugg.values():
            self.assertEqual(s["status"], "pending")
            self.assertTrue(s["items"])
        briefs = json.loads(brain.staff_briefs_path().read_text(encoding="utf-8"))
        self.assertEqual(set(briefs), {"backup", "scrape"})
        for rec in briefs.values():
            self.assertIn("did", rec)
            self.assertIn("mood", rec)
        core_lines = brain.core_log_path().read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(core_lines), 3)
        for ln in core_lines:
            d = json.loads(ln)
            self.assertTrue(d["q"] and d["a"])
        weather_dir = brain.agenda_mod.cache_dir() / "weather"
        self.assertTrue((weather_dir / "geocode.json").is_file())
        forecasts = list(weather_dir.glob("forecast-*.json"))
        self.assertEqual(len(forecasts), 1)

    def test_demo_widget_history_covers_a_week(self):
        today = date(2026, 9, 30)
        v = brain.build_demo_vault(today=today)
        brain.CONFIG_OVERRIDES.update(brain.demo_overrides(v))
        by_id = {w["id"]: w for w in brain.collect_widgets()}
        h = brain.widget_history(by_id["backup"], days=7, today=today)
        self.assertGreater(h["total_runs"], 0)
        scrape_h = brain.widget_history(by_id["scrape"], days=7, today=today)
        self.assertGreater(scrape_h["total_fails"], 0)
        kpis = brain.office_kpis(brain.collect_widgets(), today=today)
        self.assertGreater(kpis["total"]["runs"], 0)

    def test_serve_demo_isolates_real_cache(self):
        """cmd_serve --demo가 쓰는 setup_demo_isolated()는 (테스트 기준) '실제' 캐시 경로 A를 건드리지 않고
        데모 볼트 안의 별도 캐시 경로에만 suggestions.json 등을 써야 한다."""
        real_cache = self.home / ".cache-real-A"
        real_cache.mkdir(parents=True)
        os.environ["XDG_CACHE_HOME"] = str(real_cache)
        before = sorted(p.relative_to(real_cache).as_posix() for p in real_cache.rglob("*"))
        self.assertEqual(before, [])
        v = brain.setup_demo_isolated()
        after = sorted(p.relative_to(real_cache).as_posix() for p in real_cache.rglob("*"))
        self.assertEqual(after, [])  # A에는 아무것도 안 생김
        demo_cache = Path(v) / "_demo" / "cache_home" / "second-brain"
        self.assertTrue((demo_cache / "suggestions.json").is_file())
        self.assertTrue((demo_cache / "core_log.jsonl").is_file())
        self.assertTrue((demo_cache / "staff_briefs.json").is_file())
        self.assertTrue((demo_cache / "weather" / "geocode.json").is_file())

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
            self.assertEqual(t["suggestions"]["count"], 2)
            with urllib.request.urlopen(base + "/api/office", timeout=5) as r:
                d = json.loads(r.read().decode("utf-8"))
            self.assertEqual({tm["name"] for tm in d["teams"]}, {"운영팀", "생활팀", "커리어팀", "콘텐츠팀"})
            self.assertGreater(d["kpis"]["total"]["runs"], 0)
            with urllib.request.urlopen(base + "/api/journals", timeout=5) as r:
                j = json.loads(r.read().decode("utf-8"))
            self.assertEqual(len(j["daily"]), 1)
            self.assertEqual(len(j["weekly"]), 1)
            with urllib.request.urlopen(base + "/api/report?days=7", timeout=5) as r:
                rep = json.loads(r.read().decode("utf-8"))
            self.assertGreater(sum(rep["counts"].values()), 0)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
