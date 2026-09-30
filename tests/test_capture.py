"""빠른 캡처(v0.30): capture_note, /api/capture, CLI `capture` — 보드 팔레트·코어 "메모해"에서 텍스트 한 줄 → 노트 한 장."""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


class CaptureNoteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_JOBS_DIR")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ["SECOND_BRAIN_JOBS_DIR"] = str(self.home / "nojobs")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault), "calendar": {"sources": []}}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    # ── capture_note ─────────────────────────────────────────────
    def test_note_title_from_first_line(self):
        res = brain.capture_note(self.vault, {"text": "오늘 배운 것\n\n둘째 줄은 본문에만 들어간다"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["type"], "note")
        self.assertEqual(res["title"], "오늘 배운 것")
        self.assertTrue(res["path"].startswith("notes/"))
        n = brain.Note(self.vault, self.vault / res["path"])
        self.assertEqual(n.type, "note")
        self.assertIn("오늘 배운 것", n.body)
        self.assertIn("둘째 줄은 본문에만 들어간다", n.body)
        self.assertIn("맥락: 보드/코어 빠른 캡처", n.body)

    def test_idea_type(self):
        res = brain.capture_note(self.vault, {"text": "새 자동화 아이디어: 크론을 하나 더 만든다", "type": "idea"})
        self.assertEqual(res["type"], "idea")
        n = brain.Note(self.vault, self.vault / res["path"])
        self.assertEqual(n.type, "idea")

    def test_url_becomes_source_with_host_title(self):
        res = brain.capture_note(self.vault, {"text": "https://example.com/blog/second-brain"})
        self.assertEqual(res["type"], "source")
        n = brain.Note(self.vault, self.vault / res["path"])
        self.assertEqual(n.type, "source")
        self.assertEqual(n.meta.get("source"), "https://example.com/blog/second-brain")
        self.assertIn("example.com", res["title"])
        self.assertIn("second-brain", res["title"])

    def test_url_with_explicit_title_keeps_user_title(self):
        res = brain.capture_note(self.vault, {"text": "https://example.com/x", "title": "내가 정한 제목"})
        self.assertEqual(res["title"], "내가 정한 제목")
        self.assertEqual(res["type"], "source")

    def test_tags_and_project_applied(self):
        res = brain.capture_note(self.vault, {"text": "태그 테스트", "tags": ["a", "b", "c", "d", "e", "f"], "project": "세컨드브레인"})
        n = brain.Note(self.vault, self.vault / res["path"])
        self.assertEqual(n.tags, ["a", "b", "c", "d", "e"])  # 최대 5개
        self.assertEqual(n.project, "세컨드브레인")

    def test_rejects_empty_text(self):
        with self.assertRaises(brain.BrainError):
            brain.capture_note(self.vault, {"text": "   "})

    def test_rejects_too_long_text(self):
        with self.assertRaises(brain.BrainError):
            brain.capture_note(self.vault, {"text": "x" * 4001})

    def test_rejects_unknown_type(self):
        with self.assertRaises(brain.BrainError):
            brain.capture_note(self.vault, {"text": "타입 오류", "type": "meeting"})

    # ── HTTP ─────────────────────────────────────────────────────
    def test_api_capture_requires_token(self):
        srv = brain.make_server(self.vault, 0, quiet=True)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/session", timeout=5) as r:
                tok = json.loads(r.read().decode("utf-8"))["token"]
            payload = json.dumps({"text": "API로 남긴 빠른 캡처", "type": "note"}).encode("utf-8")
            req = urllib.request.Request(base + "/api/capture", data=payload, method="POST",
                                         headers={"Content-Type": "application/json", "X-Brain-Token": tok})
            with urllib.request.urlopen(req, timeout=10) as r:
                j = json.loads(r.read().decode("utf-8"))
            self.assertTrue(j["ok"])
            self.assertTrue((self.vault / j["path"]).is_file())
            # 토큰 없이 요청 → 403
            req2 = urllib.request.Request(base + "/api/capture", data=payload, method="POST",
                                          headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req2, timeout=10)
            self.assertEqual(ctx.exception.code, 403)
        finally:
            srv.shutdown()
            srv.server_close()

    # ── CLI ──────────────────────────────────────────────────────
    def test_cli_capture_prints_path(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            brain.main(["capture", "CLI로 남긴 빠른 캡처"])
        printed = out.getvalue().strip()
        self.assertTrue(printed.startswith("notes/"), printed)
        self.assertTrue((self.vault / printed).is_file())


if __name__ == "__main__":
    unittest.main()
