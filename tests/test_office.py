"""사무실: 팀 배정, 가동 감지(가짜 ps·crontab), Claude 작업 읽기, /api/office·/office."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


class OfficeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_JOBS_DIR")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        jobs = self.home / "jobs" / "abc12345"
        jobs.mkdir(parents=True)
        (jobs / "state.json").write_text(json.dumps({"state": "working", "detail": "릴스 분석 중", "tempo": "active",
                                                      "children": [{"title": "사업 발굴 결과"}]}), encoding="utf-8")
        (jobs / "timeline.jsonl").write_text('{"detail":"첫 단계"}\n{"detail":"둘째 단계"}\n', encoding="utf-8")
        old = self.home / "jobs" / "old00000"
        old.mkdir()
        (old / "state.json").write_text(json.dumps({"state": "done"}), encoding="utf-8")
        os.utime(old / "state.json", (0, 0))  # 아주 오래됨 → 제외
        os.environ["SECOND_BRAIN_JOBS_DIR"] = str(self.home / "jobs")
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.home / "brain"), "assistant_name": "자비스",
                                                         "calendar": {"sources": []}}), encoding="utf-8")
        logs = self.home / ".local" / "k-skill-cron"
        logs.mkdir(parents=True)
        (logs / "marketset.log").write_text("len: 148\n", encoding="utf-8")
        (logs / "jobscout_manager.log").write_text("=== exit: 0 ===\n", encoding="utf-8")
        (logs / "dongtan_trades.log").write_text("조회 실패 3/3 — 판단 불가\n", encoding="utf-8")
        (cfgdir / "widgets.json").write_text(json.dumps({"allow_commands": False, "widgets": [
            {"id": "marketset", "title": "마켓세트 카톡 (평일 10시)", "kind": "log", "source": "~/.local/k-skill-cron/marketset.log", "status": {"ok_pattern": "len:"}},
            {"id": "jobscout-manager", "title": "채용 매니저 (월·목)", "kind": "log", "source": "~/.local/k-skill-cron/jobscout_manager.log", "status": {"ok_pattern": "exit: 0"}},
            {"id": "dongtan-trades", "title": "동탄 실거래", "kind": "log", "source": "~/.local/k-skill-cron/dongtan_trades.log", "status": {"fail_pattern": "실패"}},
            {"id": "mystery", "title": "정체불명", "kind": "log", "source": "~/.local/k-skill-cron/marketset.log", "team": "비밀팀"},
        ]}, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_team_mapping(self):
        self.assertEqual(brain.team_for({"id": "marketset"}), "생활팀")
        self.assertEqual(brain.team_for({"id": "jobscout-manager"}), "커리어팀")
        self.assertEqual(brain.team_for({"id": "gen-daily"}), "콘텐츠팀")
        self.assertEqual(brain.team_for({"id": "whatever"}), "운영팀")
        self.assertEqual(brain.team_for({"id": "x", "team": "비밀팀"}), "비밀팀")

    def test_running_detection(self):
        ws = brain.collect_widgets()
        cron = [
            "0 10 * * 1-5 /opt/miniconda3/bin/python3 /Users/x/.local/k-skill-cron/marketset_kakao.py >> /Users/x/.local/k-skill-cron/marketset.log 2>&1",
            "17 9 * * 1,4 /Users/x/.local/k-skill-cron/jobscout_manager.sh",
            "# 주석",
        ]
        ps = [
            "  501 00:12 /opt/miniconda3/bin/python3 /Users/x/.local/k-skill-cron/marketset_kakao.py",
            "  502 05:40 /bin/zsh /Users/x/.local/k-skill-cron/jobscout_manager.sh",
            "  503 00:01 grep marketset",
            "  504 01:00 python3 scripts/brain.py serve",
            "  505 04:00 /Applications/Claude.app/Contents/Frameworks/Claude Helper (Renderer).app --enable-features=Flight,dongtan_trades --type=renderer",
        ]
        run = brain.running_widgets(ws, ps_lines=ps, cron_lines=cron)
        self.assertEqual(run["marketset"]["pid"], 501)
        self.assertEqual(run["jobscout-manager"]["etime"], "05:40")
        self.assertNotIn("dongtan-trades", run)
        self.assertEqual(brain.running_widgets(ws, ps_lines=[], cron_lines=cron), {})

    def test_jobs_and_office(self):
        jobs = brain.claude_jobs()
        self.assertEqual([j["id"] for j in jobs], ["abc12345"])
        self.assertEqual(jobs[0]["detail"], "릴스 분석 중")
        self.assertEqual(jobs[0]["title"], "사업 발굴 결과")
        self.assertEqual(jobs[0]["recent"], ["첫 단계", "둘째 단계"])
        d = brain.dash_office(brain.collect_widgets(), ps_lines=[], cron_lines=[])
        names = [t["name"] for t in d["teams"]]
        self.assertEqual(set(names), {"생활팀", "커리어팀", "비밀팀"})
        self.assertEqual(d["assistant_name"], "자비스")
        self.assertTrue(any(e["who"] == "동탄 실거래" and e["bad"] for e in d["events"]))
        self.assertTrue(any(e["who"].startswith("Claude") for e in d["events"]))

    def test_routes(self):
        vault = self.home / "brain"
        vault.mkdir()
        (vault / "inbox.md").write_text("", encoding="utf-8")
        srv = brain.make_server(vault, 0, quiet=True)
        th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        th.start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/office", timeout=5) as r:
                self.assertIn("세컨드브레인 사무실", r.read().decode("utf-8"))
            with urllib.request.urlopen(base + "/api/office", timeout=5) as r:
                d = json.loads(r.read().decode("utf-8"))
            for k in ("teams", "widgets", "running", "jobs", "events", "assistant_name"):
                self.assertIn(k, d)
            self.assertTrue(all("source" in w for w in d["widgets"]))
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
