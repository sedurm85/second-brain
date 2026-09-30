"""위젯 근무 그래프: 날짜별 실행·실패 집계, 라우트."""
import json
import os
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


class HistoryTest(unittest.TestCase):
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
        lines = ["2026-09-28 10:00 run ok", "2026-09-28 15:00 run ok", "2026-09-29 10:00 Traceback boom", "2026-09-30 10:00 run ok", "no date line", "2026-09-01 old"]
        (logs / "a.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.home / "brain"), "calendar": {"sources": []}}), encoding="utf-8")
        (cfgdir / "widgets.json").write_text(json.dumps({"widgets": [{"id": "a", "title": "A", "kind": "log", "source": "~/.local/a.log", "status": {"ok_pattern": "ok", "fail_pattern": "Traceback"}}]}), encoding="utf-8")

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_history_counts(self):
        w = brain.collect_widgets()[0]
        h = brain.widget_history(w, days=7, today=TODAY)
        by = {d["date"]: d for d in h["days"]}
        self.assertEqual((by["2026-09-28"]["runs"], by["2026-09-28"]["fails"]), (2, 0))
        self.assertEqual((by["2026-09-29"]["runs"], by["2026-09-29"]["fails"]), (1, 1))
        self.assertEqual(by["2026-09-30"]["runs"], 1)
        self.assertEqual((h["total_runs"], h["total_fails"]), (4, 1))
        self.assertNotIn("2026-09-01", by)

    def test_route(self):
        vault = self.home / "brain"
        for d in ("notes", "decisions", "projects", "people"):
            (vault / d).mkdir(parents=True)
        (vault / "BRAIN.md").write_text("# B\n", encoding="utf-8")
        (vault / "inbox.md").write_text("", encoding="utf-8")
        srv = brain.make_server(vault, 0, today=TODAY, quiet=True)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/widget-history?id=a&days=7", timeout=5) as r:
                h = json.loads(r.read().decode("utf-8"))
            self.assertEqual(h["total_runs"], 4)
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + "/api/widget-history?id=zzz", timeout=5)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    import urllib.error  # noqa: F401
    unittest.main()
