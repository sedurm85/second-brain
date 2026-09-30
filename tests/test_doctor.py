"""doctor: 볼트 없음/있음, Claude CLI 못 찾음, 캘린더·카톡·위젯 미설정 안내, 정제 대기, 종료 코드."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


class DoctorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_ASK_CMD", "SECOND_BRAIN_NO_LAUNCHCTL", "SECOND_BRAIN_LAUNCH_AGENTS")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ["SECOND_BRAIN_NO_LAUNCHCTL"] = "1"
        os.environ["SECOND_BRAIN_LAUNCH_AGENTS"] = str(self.home / "agents")
        (self.home / "agents").mkdir()
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ["SECOND_BRAIN_ASK_CMD"] = "/nonexistent/claude-x -p"
        self.cfgdir = self.home / ".config" / "second-brain"
        self.cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        (self.cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault), "calendar": {"sources": []}}), encoding="utf-8")

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def by(self, r):
        return {i["name"]: i for i in r["items"]}

    def test_fresh_machine(self):
        r = brain.doctor_report()
        b = self.by(r)
        self.assertFalse(r["ok"])
        self.assertFalse(b["볼트"]["ok"])
        self.assertFalse(b["Claude CLI"]["ok"])
        self.assertIn("ask_cmd", b["Claude CLI"]["fix"])
        self.assertFalse(b["캘린더"]["ok"])
        self.assertIn("calendar add", b["캘린더"]["fix"])
        self.assertFalse(b["카톡 헬퍼"]["ok"])
        self.assertFalse(b["자동화 위젯"]["ok"])
        self.assertTrue(b["Python"]["ok"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = brain.main(["doctor"])
        self.assertEqual(rc, brain.EXIT_INPUT)
        self.assertIn("✗ 볼트", buf.getvalue())

    def test_configured(self):
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])
            brain.main(["new", "--type", "note", "--title", "가져온 메모", "--body", "x"])
        for n in brain.load_notes(self.vault):
            if n.title == "가져온 메모":
                n.meta["imported_from"] = "memory"
                brain.write_note(n.path, n.meta, n.body)
        os.environ["SECOND_BRAIN_ASK_CMD"] = sys.executable + " -c pass"
        (self.cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault), "calendar": {"sources": [{"kind": "ics", "name": "구글", "url_file": str(self.home / "missing.url")}]}}), encoding="utf-8")
        r = brain.doctor_report()
        b = self.by(r)
        self.assertTrue(b["볼트"]["ok"])
        self.assertTrue(b["Claude CLI"]["ok"])
        self.assertTrue(b["캘린더"]["ok"])
        self.assertFalse(b["캘린더 주소 파일"]["ok"])
        self.assertFalse(b["정제 대기"]["ok"])
        self.assertIn("enrich", b["정제 대기"]["fix"])


if __name__ == "__main__":
    unittest.main()
