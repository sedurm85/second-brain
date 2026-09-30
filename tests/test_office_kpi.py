"""사무실 팀 KPI: 7일 실행·실패·성공률·일별 집계, 기본 팀 라벨, /api/office 응답."""
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

TODAY = date(2026, 9, 30)


class OfficeKpiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_JOBS_DIR")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ["SECOND_BRAIN_JOBS_DIR"] = str(self.home / "nojobs")
        brain.CONFIG_OVERRIDES.clear()
        logs = self.home / ".local"
        logs.mkdir()
        # 알파팀: 28일 성공 2회, 29일 성공 1 + 실패 1, 30일 성공 1 (총 실행 5, 실패 1)
        (logs / "alpha1.log").write_text(
            "\n".join(["2026-09-28 10:00 run ok", "2026-09-28 15:00 run ok", "2026-09-29 09:00 run ok", "2026-09-30 09:00 run ok"]) + "\n",
            encoding="utf-8")
        (logs / "alpha2.log").write_text("2026-09-29 22:00 Traceback boom\n", encoding="utf-8")
        # 베타팀: 27일 성공 1회 (총 실행 1, 실패 0)
        (logs / "beta1.log").write_text("2026-09-27 08:00 run ok\n", encoding="utf-8")
        # 팀 미지정, id도 기본 팀 접두어에 안 걸림 -> team_for 기본값(운영팀)에 집계
        (logs / "orphan.log").write_text("2026-09-30 11:00 run ok\n", encoding="utf-8")
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.home / "brain"), "calendar": {"sources": []}}), encoding="utf-8")
        (cfgdir / "widgets.json").write_text(json.dumps({"widgets": [
            {"id": "alpha-1", "title": "알파1", "kind": "log", "source": "~/.local/alpha1.log", "team": "알파팀"},
            {"id": "alpha-2", "title": "알파2", "kind": "log", "source": "~/.local/alpha2.log", "team": "알파팀", "status": {"fail_pattern": "Traceback"}},
            {"id": "alpha-paused", "title": "알파(멈춤)", "kind": "log", "source": "~/.local/alpha1.log", "team": "알파팀", "state": "paused"},
            {"id": "beta-1", "title": "베타1", "kind": "log", "source": "~/.local/beta1.log", "team": "베타팀"},
            {"id": "orphan", "title": "무소속", "kind": "log", "source": "~/.local/orphan.log"},
        ]}, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_team_kpis(self):
        ws = brain.collect_widgets()
        k = brain.office_kpis(ws, today=TODAY)
        alpha = k["teams"]["알파팀"]
        self.assertEqual(alpha["runs"], 5)
        self.assertEqual(alpha["fails"], 1)
        self.assertEqual(alpha["rate"], 80)  # (5-1)/5 * 100
        self.assertEqual(alpha["members"], 2)  # paused 위젯은 제외
        self.assertEqual(len(alpha["days"]), 7)
        beta = k["teams"]["베타팀"]
        self.assertEqual((beta["runs"], beta["fails"], beta["rate"], beta["members"]), (1, 0, 100, 1))

    def test_default_team_label_matches_dash_office(self):
        ws = brain.collect_widgets()
        k = brain.office_kpis(ws, today=TODAY)
        d = brain.dash_office(ws, ps_lines=[], cron_lines=[])
        room_names = {t["name"] for t in d["teams"]}
        self.assertIn("운영팀", room_names)
        self.assertIn("운영팀", k["teams"])
        self.assertEqual(k["teams"]["운영팀"]["runs"], 1)

    def test_paused_excluded(self):
        ws = brain.collect_widgets()
        k = brain.office_kpis(ws, today=TODAY)
        # 알파팀 members 2 (alpha-1, alpha-2) - alpha-paused는 제외됐으므로 runs에 중복 반영 안 됨
        self.assertEqual(k["teams"]["알파팀"]["runs"], 5)

    def test_total_aggregation(self):
        ws = brain.collect_widgets()
        k = brain.office_kpis(ws, today=TODAY)
        total = k["total"]
        self.assertEqual(total["runs"], 5 + 1 + 1)  # 알파 5 + 베타 1 + 운영팀(orphan) 1
        self.assertEqual(total["fails"], 1)
        self.assertEqual(total["members"], 4)  # paused 제외한 4개 위젯
        self.assertEqual(len(total["days"]), 7)

    def test_zero_runs_rate_is_none(self):
        k = brain.office_kpis([{"id": "x", "kind": "log", "source": "", "team": "빈팀"}], today=TODAY)
        self.assertEqual(k["teams"]["빈팀"]["runs"], 0)
        self.assertIsNone(k["teams"]["빈팀"]["rate"])

    def test_office_route_has_kpis(self):
        vault = self.home / "brain"
        for d in ("notes", "decisions", "projects", "people"):
            (vault / d).mkdir(parents=True)
        (vault / "BRAIN.md").write_text("# B\n", encoding="utf-8")
        (vault / "inbox.md").write_text("", encoding="utf-8")
        srv = brain.make_server(vault, 0, today=TODAY, quiet=True)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/office", timeout=5) as r:
                d = json.loads(r.read().decode("utf-8"))
            self.assertIn("kpis", d)
            self.assertIn("teams", d["kpis"])
            self.assertIn("total", d["kpis"])
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
