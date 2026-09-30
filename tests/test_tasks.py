"""할 일 체계: 태그 파서, 4묶음, 파생 할 일, 쓰기(add/check/move/remove), API, CLI."""
import contextlib
import io
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
INBOX = """# inbox

- [ ] 세무사 답장 @waiting(세무사) @since(2026-09-25)
- [ ] 보고서 초안 @due(2026-09-29)
- [ ] 치과 예약 @due(2026-10-02) @project(건강)
- [ ] 아침브리핑 재개 여부 @someday
- [ ] 태그 없는 것
- [x] 끝난 일 @due(2026-09-28)
- 그냥 글머리
"""


class TaskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault), "calendar": {"sources": []}}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])
        (self.vault / "inbox.md").write_text(INBOX, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["new", "--type", "decision", "--title", "곧 되돌아볼 결정", "--revisit", "2026-10-03", "--created", "2026-09-20"])

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_parse_and_buckets(self):
        ts = brain.parse_tasks(self.vault, TODAY)
        self.assertEqual(len(ts), 6)
        m = {t["text"]: t for t in ts}
        self.assertEqual(m["세무사 답장"]["waiting"], "세무사")
        self.assertEqual(m["치과 예약"]["project"], "건강")
        self.assertTrue(m["끝난 일"]["done"])
        b = brain.bucket_tasks(ts, TODAY)
        self.assertEqual([t["text"] for t in b["today"]], ["보고서 초안"])  # 마감 지남 → 오늘
        self.assertEqual(b["today"][0]["days_left"], -1)
        self.assertEqual([t["text"] for t in b["week"]], ["치과 예약"])
        self.assertEqual({t["text"] for t in b["someday"]}, {"아침브리핑 재개 여부", "태그 없는 것"})
        self.assertEqual(b["waiting"][0]["waiting_days"], 5)
        self.assertEqual([t["text"] for t in b["done_recent"]], ["끝난 일"])

    def test_derived_and_dash(self):
        widgets = [{"id": "dongtan", "title": "동탄 실거래 (매일 9:10)", "status": "fail", "state": "active", "summary": "502"},
                   {"id": "ok1", "title": "정상", "status": "ok", "state": "active"},
                   {"id": "paused1", "title": "멈춤", "status": "paused", "state": "paused"}]
        agenda = {"today": [], "upcoming": [{"title": "제주도", "start": "2026-10-03", "key": "2026-10-03|제주도", "days_left": 3,
                                             "note": {"path": "events/x.md", "total": 2, "done": 1, "checklist": [{"line": 4, "done": True, "text": "렌터카"}, {"line": 5, "done": False, "text": "상비약"}]}}]}
        b = brain.dash_tasks(self.vault, TODAY, widgets, agenda)
        kinds_today = [(t["kind"], t["text"]) for t in b["today"]]
        self.assertIn(("automation", "실패 확인: 동탄 실거래"), kinds_today)
        self.assertIn(("task", "보고서 초안"), kinds_today)
        week = [(t["kind"], t["text"]) for t in b["week"]]
        self.assertIn(("decision", "결정 되돌아보기: 곧 되돌아볼 결정"), week)
        self.assertIn(("prep", "제주도 준비: 상비약"), week)
        self.assertEqual(b["counts"]["waiting"], 1)

    def test_write_actions(self):
        r = brain.task_action(self.vault, {"action": "add", "text": "새 할 일", "due": "tomorrow"}, TODAY)
        self.assertIn("@due(2026-10-01)", r["text"])
        r2 = brain.task_action(self.vault, {"action": "add", "text": "기다림", "waiting": "회계사"}, TODAY)
        self.assertIn("@waiting(회계사) @since(2026-09-30)", r2["text"])
        ts = {t["text"]: t for t in brain.parse_tasks(self.vault, TODAY)}
        brain.task_action(self.vault, {"action": "check", "line": ts["새 할 일"]["line"], "done": True}, TODAY)
        brain.task_action(self.vault, {"action": "move", "line": ts["치과 예약"]["line"], "to": "someday"}, TODAY)
        brain.task_action(self.vault, {"action": "move", "line": ts["태그 없는 것"]["line"], "to": "today"}, TODAY)
        ts = {t["text"]: t for t in brain.parse_tasks(self.vault, TODAY)}
        self.assertTrue(ts["새 할 일"]["done"])
        self.assertTrue(ts["치과 예약"]["someday"] and ts["치과 예약"]["due"] is None)
        self.assertEqual(ts["치과 예약"]["project"], "건강")  # 다른 태그는 유지
        self.assertEqual(ts["태그 없는 것"]["due"], "2026-09-30")
        brain.task_action(self.vault, {"action": "remove", "line": ts["기다림"]["line"]}, TODAY)
        self.assertNotIn("기다림", {t["text"] for t in brain.parse_tasks(self.vault, TODAY)})
        with self.assertRaises(brain.BrainError):
            brain.task_action(self.vault, {"action": "check", "line": 0}, TODAY)  # 제목 줄
        with self.assertRaises(brain.BrainError):
            brain.task_action(self.vault, {"action": "move", "line": ts["새 할 일"]["line"], "to": "nowhere"}, TODAY)

    def test_cli_and_api(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(brain.main(["task", "add", "CLI 할 일", "--tomorrow"]), 0)
        self.assertIn("@due(", buf.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(brain.main(["task", "list"]), 0)
        self.assertIn("[기다림 1]", buf.getvalue())
        self.assertIn("세무사 답장", buf.getvalue())
        srv = brain.make_server(self.vault, 0, today=TODAY, quiet=True)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/tasks", timeout=5) as r:
                b = json.loads(r.read().decode("utf-8"))
            self.assertEqual(b["counts"]["waiting"], 1)
            with urllib.request.urlopen(base + "/api/session", timeout=5) as r:
                tok = json.loads(r.read().decode("utf-8"))["token"]
            req = urllib.request.Request(base + "/api/task", data=json.dumps({"action": "add", "text": "API 할 일", "someday": True}).encode("utf-8"),
                                         method="POST", headers={"Content-Type": "application/json", "X-Brain-Token": tok})
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertTrue(json.loads(r.read().decode("utf-8"))["ok"])
            with urllib.request.urlopen(base + "/api/today", timeout=5) as r:
                t = json.loads(r.read().decode("utf-8"))
            self.assertIn("tasks", t)
            self.assertEqual(t["tasks"]["counts"]["someday"], 3)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
