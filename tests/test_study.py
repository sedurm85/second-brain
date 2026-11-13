"""공부방: 장 채우기(가짜 Claude) · 하루치 · 채점과 오답 상자(1·3·7일) · 출제 모드 · 모의고사 판정 · 화면 API · 뉴스 저장 · 아침 한 줄."""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402
import study  # noqa: E402

FEED = [{"source": "데일리시큐", "title": f"기사 {i}", "url": f"https://example.com/{i}", "date": date.today().isoformat(), "summary": f"요약 {i}"} for i in range(5)]


class StudyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_ASK_CMD", "SECOND_BRAIN_JOBS_DIR", "SECOND_BRAIN_CLAUDE_UNLIMITED")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ["SECOND_BRAIN_JOBS_DIR"] = str(self.home / "nojobs")
        os.environ["SECOND_BRAIN_CLAUDE_UNLIMITED"] = "1"
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault), "calendar": {"sources": []}}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])
        fake = self.home / "fake_claude.py"
        fake.write_text('''import sys, json
p = sys.stdin.read()
if "[기사 목록]" in p:
    print(json.dumps({"news": [{"i": i, "title": "뉴스 " + str(i), "summary": ["a", "b", "c"], "tech": "기술", "ask": "질문", "unit": 1} for i in range(3)],
                      "piece": {"title": "조각", "lead": "명제", "points": [{"h": "h", "body": "b"}], "views": {"CISSP": "x", "CISM": "y", "CISA": "z"},
                                "scene": {"title": "장면", "steps": ["1", "2", "3"], "why": "왜"}}}, ensure_ascii=False)); sys.exit(0)
qs = [{"stem": "문제 " + str(i), "options": ["가", "나", "다", "라"], "answer": i % 4, "explanation": "해설", "trap": "BEST"} for i in range(12)]
qs.append({"stem": "깨진 문제", "options": ["가"], "answer": 9})
print(json.dumps({"concept_md": "## 핵심 한 줄\\n" + "개념 " * 500, "terms": [{"ko": "거버넌스", "en": "Governance", "desc": "방향"}], "questions": qs}, ensure_ascii=False))
''', encoding="utf-8")
        os.environ["SECOND_BRAIN_ASK_CMD"] = sys.executable + " " + str(fake)
        self.today = date.today()

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _fill(self, n=2):
        return study.fill(brain, self.vault, None, n)

    def test_fill_order_and_validation(self):
        r = self._fill(2)
        self.assertEqual([x["chapter"] for x in r["filled"]], ["2-1", "2-2"])  # 실무감 1단원 장부터
        self.assertEqual(r["filled"][0]["questions"], 12)  # 보기 4개가 아닌 문제는 버림
        self.assertEqual(r["remaining"], len(study.ORDER) - 2)
        self.assertEqual(len(study.all_questions(self.vault)), 24)
        self.assertIn("study", brain.SKIP_DIRS)
        with self.assertRaises(study.StudyError):
            study.load_chapter(self.vault, "9-9")

    def test_answers_boxes_and_quiz_modes(self):
        self._fill(2)
        q = study.all_questions(self.vault)["2-1#2"]  # answer 1
        r = study.record_answer(self.vault, q["id"], 0, today=self.today)
        self.assertFalse(r["ok"])
        p = study.load_progress(self.vault)
        self.assertEqual(p["boxes"][q["id"]], {"box": 0, "due": (self.today + timedelta(days=1)).isoformat()})
        self.assertEqual(study.due_wrong(p, self.today), [])
        self.assertEqual(study.due_wrong(p, self.today + timedelta(days=1)), [q["id"]])
        w = study.make_quiz(self.vault, "wrong", today=self.today)
        self.assertEqual([x["id"] for x in w["questions"]], [q["id"]])
        study.record_answer(self.vault, q["id"], 1, today=self.today)
        self.assertEqual(study.load_progress(self.vault)["boxes"][q["id"]]["box"], 1)
        study.record_answer(self.vault, q["id"], 1, today=self.today)
        study.record_answer(self.vault, q["id"], 1, today=self.today)
        self.assertNotIn(q["id"], study.load_progress(self.vault)["boxes"])  # 세 번 맞히면 졸업
        self.assertEqual(len(study.make_quiz(self.vault, "quick", seed=1)["questions"]), 5)
        ch = study.make_quiz(self.vault, "chapter", "2-2", seed=1)
        self.assertEqual({x["chapter"] for x in ch["questions"]}, {"2-2"})
        self.assertEqual(len(ch["questions"]), 10)
        self.assertEqual(study.make_quiz(self.vault, "mini", seed=1)["minutes"], 48)
        with self.assertRaises(study.StudyError):
            study.make_quiz(self.vault, "full")  # 150개 미만
        with self.assertRaises(study.StudyError):
            study.record_answer(self.vault, "없음#1", 0)

    def test_daily_overview_and_readiness(self):
        self._fill(1)
        doc = study.make_daily(brain, self.vault, self.today, feed=FEED)
        self.assertEqual([n["url"] for n in doc["news"]], [f"https://example.com/{i}" for i in range(3)])  # 링크는 피드 원문에서
        self.assertEqual(len(doc["questions"]), 5)
        self.assertTrue(all(i.startswith("2-1#") for i in doc["questions"]))
        self.assertEqual(study.make_daily(brain, self.vault, self.today, feed=[])["made"], doc["made"])  # 이미 있으면 그대로
        self.assertEqual(study.load_progress(self.vault)["field"]["covered"][0]["title"], "조각")
        self.assertIn("공부방: 뉴스 3 · 1단원", study.brief_line(self.vault, self.today))
        study.record_answer(self.vault, doc["questions"][0], 0, today=self.today)
        study.mark_read(self.vault, "2-1", today=self.today)
        o = study.overview(self.vault, self.today)
        self.assertEqual(o["streak"], 1)
        self.assertEqual(len(o["daily"]["answered"]), 1)
        self.assertEqual(o["domains"][1]["read"], 1)
        self.assertIsNone(o["current_chapter"])  # 채운 장(2-1)을 다 읽음
        for _ in range(3):
            study.record_mock(self.vault, "mini", 24, 30, today=self.today)
        self.assertTrue(study.overview(self.vault, self.today)["readiness"]["ready"])
        n = study.save_news_note(brain, self.vault, self.today, 0)
        self.assertTrue((self.vault / n["path"]).is_file())

    def test_field_unit_advances(self):
        p = study.load_progress(self.vault)
        p["field"]["covered"] = [{"date": "2026-01-0" + str(i), "unit": 1, "title": f"t{i}"} for i in range(1, 5)]
        study.save_progress(self.vault, p)
        study.make_daily(brain, self.vault, self.today, feed=FEED)
        self.assertEqual(study.load_progress(self.vault)["field"]["unit"], 2)

    def test_http_routes(self):
        self._fill(1)
        srv = brain.make_server(self.vault, 0, quiet=True, today=self.today)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/study", timeout=5) as r:
                self.assertIn("공부방", r.read().decode("utf-8"))
            with urllib.request.urlopen(base + "/api/study", timeout=5) as r:
                self.assertEqual(json.loads(r.read())["chapters_ready"], 1)
            with urllib.request.urlopen(base + "/api/study/chapter?id=2-1", timeout=5) as r:
                self.assertEqual(json.loads(r.read())["terms"][0]["en"], "Governance")
            with urllib.request.urlopen(base + "/api/session", timeout=5) as r:
                tok = json.loads(r.read())["token"]
            req = urllib.request.Request(base + "/api/study/answer", data=json.dumps({"id": "2-1#1", "choice": 0}).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "X-Brain-Token": tok})
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertTrue(json.loads(r.read())["ok"])
            try:
                urllib.request.urlopen(base + "/api/study/quiz?mode=nope", timeout=5)
                self.fail("400이어야 함")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 400)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_cli(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(brain.main(["study", "fill", "--limit", "1"]), 0)
            self.assertEqual(brain.main(["study", "status"]), 0)
        self.assertIn("2-1 채움", buf.getvalue())
        self.assertIn("장 1/19 준비", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
