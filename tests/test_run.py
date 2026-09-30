"""지금 실행·멈춤·재개: 명령 매핑(크론·launchd), dry 실행, 상태 토글, API 권한(allow_run)."""
import json
import os
import plistlib
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_LAUNCH_AGENTS", "SECOND_BRAIN_RUN_DRY", "SECOND_BRAIN_JOBS_DIR")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ["SECOND_BRAIN_RUN_DRY"] = "1"
        os.environ["SECOND_BRAIN_JOBS_DIR"] = str(self.home / "nojobs")
        agents = self.home / "agents"
        agents.mkdir()
        os.environ["SECOND_BRAIN_LAUNCH_AGENTS"] = str(agents)
        (agents / "com.test.gen.plist").write_bytes(plistlib.dumps({"Label": "com.test.gen", "ProgramArguments": ["/bin/bash", "/x/gen.sh"],
                                                                     "StandardOutPath": str(self.home / ".local/naver-publish/gen.log")}))
        logs = self.home / ".local" / "k-skill-cron"
        logs.mkdir(parents=True)
        (self.home / ".local" / "naver-publish").mkdir()
        for n in ("marketset.log", "dongtan_trades.log"):
            (logs / n).write_text("len: 1\n", encoding="utf-8")
        (self.home / ".local" / "naver-publish" / "gen.log").write_text("ok\n", encoding="utf-8")
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        for d in ("notes", "decisions", "projects", "people"):
            (self.vault / d).mkdir(parents=True)
        (self.vault / "BRAIN.md").write_text("# BRAIN\n", encoding="utf-8")
        (self.vault / "inbox.md").write_text("", encoding="utf-8")
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault), "calendar": {"sources": []}}), encoding="utf-8")
        self.wpath = cfgdir / "widgets.json"
        self.write_widgets(False)
        self.cron = [
            "0 10 * * 1-5 /opt/miniconda3/bin/python3 /Users/x/.local/k-skill-cron/marketset_kakao.py >> " + str(self.home / ".local/k-skill-cron/marketset.log") + " 2>&1",
            "10 9 * * * /opt/miniconda3/bin/python3 /Users/x/.local/k-skill-cron/dongtan_trades_monitor.py >> " + str(self.home / ".local/k-skill-cron/dongtan_trades.log") + " 2>&1",
        ]

    def write_widgets(self, allow_run):
        self.wpath.write_text(json.dumps({"allow_commands": False, "allow_run": allow_run, "widgets": [
            {"id": "marketset", "title": "마켓세트", "kind": "log", "source": "~/.local/k-skill-cron/marketset.log", "status": {"ok_pattern": "len:"}},
            {"id": "dongtan-trades", "title": "동탄", "kind": "log", "source": "~/.local/k-skill-cron/dongtan_trades.log"},
            {"id": "gen-daily", "title": "카페 글 생성", "kind": "log", "source": "~/.local/naver-publish/gen.log"},
            {"id": "orphan", "title": "실행법 없음", "kind": "log", "source": "~/.local/k-skill-cron/marketset.log"},
        ]}, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_commands_and_dry_run(self):
        ws = brain.collect_widgets()
        cmds = brain.widget_commands(ws, cron_lines=self.cron)
        self.assertEqual(cmds["marketset"]["kind"], "cron")
        self.assertTrue(cmds["marketset"]["cmd"].endswith("marketset_kakao.py"))
        self.assertEqual(cmds["gen-daily"], {"kind": "launchd", "label": "com.test.gen"})
        self.assertEqual(cmds["orphan"]["kind"], "cron")  # 같은 로그를 쓰는 위젯도 실행법을 안다
        r = brain.run_widget("dongtan-trades", ws, cron_lines=self.cron)
        self.assertTrue(r["dry"])
        self.assertIn("dongtan_trades_monitor.py", r["cmd"])
        with self.assertRaises(brain.BrainError):
            brain.run_widget("nope", ws, cron_lines=self.cron)

    def test_state_toggle(self):
        brain.set_widget_state("marketset", "paused")
        cfg = json.loads(self.wpath.read_text(encoding="utf-8"))
        self.assertEqual(next(w for w in cfg["widgets"] if w["id"] == "marketset")["state"], "paused")
        ws = brain.collect_widgets()
        self.assertEqual(next(w for w in ws if w["id"] == "marketset")["status"], "paused")
        brain.set_widget_state("marketset", "active")
        with self.assertRaises(brain.BrainError):
            brain.set_widget_state("marketset", "weird")
        with self.assertRaises(brain.BrainError):
            brain.set_widget_state("ghost", "paused")

    def test_api_permissions(self):
        srv = brain.make_server(self.vault, 0, quiet=True)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/session", timeout=5) as r:
                tok = json.loads(r.read().decode("utf-8"))["token"]
            def post(body):
                req = urllib.request.Request(base + "/api/widget", data=json.dumps(body).encode("utf-8"), method="POST",
                                             headers={"Content-Type": "application/json", "X-Brain-Token": tok})
                with urllib.request.urlopen(req, timeout=10) as r:
                    return json.loads(r.read().decode("utf-8"))
            with self.assertRaises(urllib.error.HTTPError) as cm:
                post({"action": "run", "id": "marketset"})
            self.assertEqual(cm.exception.code, 400)
            self.assertIn("allow_run", json.loads(cm.exception.read().decode("utf-8"))["error"])
            res = post({"action": "pause", "id": "marketset"})
            self.assertEqual(res["state"], "paused")
            with urllib.request.urlopen(base + "/api/widgets", timeout=5) as r:
                ws = json.loads(r.read().decode("utf-8"))
            m = next(w for w in ws if w["id"] == "marketset")
            self.assertEqual((m["status"], m["runnable"]), ("paused", False))
            self.write_widgets(True)
            srv.widget_cache.clear()
            res = post({"action": "run", "id": "gen-daily", "dry": True})
            self.assertEqual((res["kind"], res["label"], res["dry"]), ("launchd", "com.test.gen", True))
            with urllib.request.urlopen(base + "/api/office", timeout=5) as r:
                d = json.loads(r.read().decode("utf-8"))
            self.assertEqual(d["runnable"].get("gen-daily"), "launchd")
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
