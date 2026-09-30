"""코어 화면: /core 라우트, /api/session 이름, /api/ask(가짜 명령으로)."""
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_ASK_CMD")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        for d in ("notes", "decisions", "projects", "people"):
            (self.vault / d).mkdir(parents=True)
        (self.vault / "BRAIN.md").write_text("# BRAIN\n", encoding="utf-8")
        (self.vault / "inbox.md").write_text("- [ ] 테스트 할 일\n", encoding="utf-8")
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault), "assistant_name": "자비스",
                                                         "calendar": {"sources": []}}), encoding="utf-8")
        # 가짜 답변 명령: 표준입력(프롬프트)을 받아 고정 답을 낸다
        fake = self.home / "fake_ask.sh"
        fake.write_text("#!/bin/sh\ncat >/dev/null\necho '네, 오늘은 할 일 하나만 챙기면 돼요.'\n", encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        os.environ["SECOND_BRAIN_ASK_CMD"] = str(fake)
        self.srv = brain.make_server(self.vault, 0, quiet=True)
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()

    def test_core_route_and_name(self):
        st, ct, body = self.get("/core")
        self.assertEqual(st, 200)
        self.assertIn("text/html", ct)
        self.assertIn("세컨드브레인 코어", body.decode("utf-8"))
        st, _, body = self.get("/api/session")
        self.assertEqual(json.loads(body)["assistant_name"], "자비스")

    def test_ask_requires_token_then_answers(self):
        _, _, body = self.get("/api/session")
        tok = json.loads(body)["token"]
        payload = json.dumps({"text": "오늘 뭐부터 하면 좋아?"}).encode("utf-8")
        req = urllib.request.Request(self.base + "/api/ask", data=payload, method="POST", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 403)
        req = urllib.request.Request(self.base + "/api/ask", data=payload, method="POST",
                                     headers={"Content-Type": "application/json", "X-Brain-Token": tok})
        with urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read().decode("utf-8"))
        self.assertEqual(res["answer"], "네, 오늘은 할 일 하나만 챙기면 돼요.")
        self.assertTrue(res["via"].endswith("fake_ask.sh"))

    def test_ask_empty_and_missing_cmd(self):
        with self.assertRaises(brain.BrainError):
            brain.ask_assistant("   ", {})
        os.environ["SECOND_BRAIN_ASK_CMD"] = str(self.home / "nope-cmd")
        with self.assertRaises(brain.BrainError):
            brain.ask_assistant("안녕", {})


if __name__ == "__main__":
    unittest.main()
