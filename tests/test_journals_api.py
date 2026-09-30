"""보드 「일지」: dash_journals가 하루/주간 일지를 최신순으로 나눠 반환, 질문 체크박스 상태·줄번호, /api/journals HTTP 라우트."""
import contextlib
import io
import json
import os
import sys
import threading
import tempfile
import unittest
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


class JournalsApiTest(unittest.TestCase):
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
        self.today = date.today()
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])

        # 하루 일지 (어제)
        self.day1 = (self.today - timedelta(days=1)).isoformat()
        j1_body = ("# " + self.day1 + " 일지\n\n## 오늘\n"
                   "- 첫 번째 줄\n- 두 번째 줄\n- 세 번째 줄\n\n## 잘한 것\n- 미루지 않았다\n\n## 내일 첫 일\n- 점검\n")
        brain.create_note(self.vault, "journal", self.day1 + " 일지", tags=["일지"], body=j1_body, created=self.day1,
                           extra={"journal_date": self.day1, "summary": "요약 하나"})

        # 하루 일지 (오늘, 더 최신)
        self.day2 = self.today.isoformat()
        j2_body = ("# " + self.day2 + " 일지\n\n## 오늘\n- 오늘 줄 하나\n\n## 잘한 것\n\n## 내일 첫 일\n")
        brain.create_note(self.vault, "journal", self.day2 + " 일지", tags=["일지"], body=j2_body, created=self.day2,
                           extra={"journal_date": self.day2, "summary": "오늘 요약"})

        # 주간 회고 (질문 3개, 모두 미체크)
        self.rday = self.today.isoformat()
        r_body = ("# " + self.rday + " 주간 회고\n\n2026-09-23 ~ " + self.rday + " · 노트 3 · 결정 1 · 일지 2\n\n"
                  "## 이번 주\n- 이번 주 한 일 A\n- 이번 주 한 일 B\n\n"
                  "## 되돌아볼 질문\n- [ ] 질문 하나\n- [x] 질문 둘 (체크됨)\n- [ ] 질문 셋\n")
        self.retro_path = brain.create_note(self.vault, "journal", self.rday + " 주간 회고", tags=["회고"], body=r_body,
                                            created=self.rday,
                                            extra={"journal_kind": "weekly", "journal_date": self.rday, "summary": "회고 요약"})

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_dash_journals_shape_and_order(self):
        out = brain.dash_journals(self.vault, limit=14)
        self.assertEqual(set(out.keys()), {"daily", "weekly"})
        self.assertEqual(len(out["daily"]), 2)
        self.assertEqual(len(out["weekly"]), 1)
        # 최신순: 오늘 일지가 어제 일지보다 먼저
        self.assertEqual(out["daily"][0]["date"], self.day2)
        self.assertEqual(out["daily"][1]["date"], self.day1)
        d0 = out["daily"][0]
        self.assertEqual(set(d0.keys()), {"path", "title", "date", "summary", "lines"})
        self.assertEqual(d0["summary"], "오늘 요약")
        self.assertEqual(d0["lines"], ["오늘 줄 하나"])
        d1 = out["daily"][1]
        self.assertEqual(d1["lines"], ["첫 번째 줄", "두 번째 줄", "세 번째 줄"])

    def test_weekly_questions(self):
        out = brain.dash_journals(self.vault, limit=14)
        w0 = out["weekly"][0]
        self.assertEqual(set(w0.keys()), {"path", "title", "date", "summary", "lines", "questions"})
        self.assertEqual(w0["lines"], ["이번 주 한 일 A", "이번 주 한 일 B"])
        qs = w0["questions"]
        self.assertEqual(len(qs), 3)
        self.assertEqual([q["text"] for q in qs], ["질문 하나", "질문 둘 (체크됨)", "질문 셋"])
        self.assertEqual([q["done"] for q in qs], [False, True, False])
        # 줄 번호는 note.body(프론트매터 제외) 기준이어야 함 — event-note check가 그대로 쓸 수 있게
        n = brain.Note(self.vault, self.retro_path)
        body_lines = n.body.split("\n")
        for q in qs:
            self.assertIn(q["text"], body_lines[q["line"]])

    def test_limit_bounds(self):
        out = brain.dash_journals(self.vault, limit=1)
        self.assertEqual(len(out["daily"]), 1)
        self.assertEqual(out["daily"][0]["date"], self.day2)  # 최신 하나만
        out2 = brain.dash_journals(self.vault, limit=999)
        self.assertLessEqual(len(out2["daily"]), 60)

    def test_http_route(self):
        srv = brain.make_server(self.vault, 0, quiet=True, today=self.today)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/journals", timeout=5) as r:
                d = json.loads(r.read().decode("utf-8"))
            self.assertEqual(len(d["daily"]), 2)
            self.assertEqual(len(d["weekly"]), 1)
            with urllib.request.urlopen(base + "/api/journals?limit=1", timeout=5) as r:
                d1 = json.loads(r.read().decode("utf-8"))
            self.assertEqual(len(d1["daily"]), 1)
            self.assertEqual(d1["daily"][0]["date"], self.day2)

            # 회고 질문 체크는 기존 event-note check 라우트로 토글된다
            with urllib.request.urlopen(base + "/api/session", timeout=5) as r:
                tok = json.loads(r.read().decode("utf-8"))["token"]
            q0_line = d["weekly"][0]["questions"][0]["line"]
            req = urllib.request.Request(
                base + "/api/event-note",
                data=json.dumps({"action": "check", "path": d["weekly"][0]["path"], "line": q0_line, "done": True}).encode("utf-8"),
                method="POST", headers={"Content-Type": "application/json", "X-Brain-Token": tok})
            with urllib.request.urlopen(req, timeout=10):
                pass
            with urllib.request.urlopen(base + "/api/journals", timeout=5) as r:
                d2 = json.loads(r.read().decode("utf-8"))
            self.assertTrue(d2["weekly"][0]["questions"][0]["done"])
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
