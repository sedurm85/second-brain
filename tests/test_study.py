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
if "piece 규칙" in p:
    print(json.dumps({"piece": {"title": "조각", "lead": "명제", "points": [{"h": "h", "body": "b"}], "views": {"CISSP": "x", "CISM": "y", "CISA": "z"},
                                "scene": {"title": "장면", "steps": ["1", "2", "3"], "why": "왜"}}}, ensure_ascii=False)); sys.exit(0)
if "[기사 목록]" in p:
    print(json.dumps({"news": [{"i": i, "title": "뉴스 " + str(i), "summary": ["a", "b", "c"], "tech": "기술", "ask": "질문", "unit": 1} for i in range(3)]}, ensure_ascii=False)); sys.exit(0)
if "커뮤니티가 고른 정답" in p:
    batch = json.loads(p.rsplit("[문제]", 1)[1])
    print(json.dumps([{"n": q["n"], "why": "해설 " + str(q["n"]), "doubt": "의심" if q["n"] == 2 else ""} for q in batch], ensure_ascii=False)); sys.exit(0)
if "[검수 대상]" in p:
    print(json.dumps({"question_fixes": [{"i": 0, "problem": "복수 정답", "stem": "고친 문제", "options": ["가", "나", "다", "라"], "answer": 2, "explanation": "고친 해설", "trap": "MOST"},
                                         {"i": 99, "stem": "범위 밖", "options": ["a", "b", "c", "d"], "answer": 0}],
                      "concept_fixes": [{"find": "## 핵심 한 줄", "replace": "## 핵심 한 줄 (검수)", "why": "표시"}, {"find": "없는 문장입니다", "replace": "x", "why": "무시"}],
                      "verdict": "대체로 정확"}, ensure_ascii=False)); sys.exit(0)
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
        self.assertEqual(study.load_progress(self.vault)["field"]["unit"], 1)  # 읽음 게이트: 새 조각만 추가, 아직 진도 전진 전
        study.mark_piece_read(self.vault, self.today)  # 5번째 조각을 읽으면 5//5 → 다음 단원
        self.assertEqual(study.load_progress(self.vault)["field"]["unit"], 2)

    def test_daily_questions_advance_and_cooldown(self):
        self._fill(2)  # 2-1, 2-2 채움(각 12문제)
        qs = study.all_questions(self.vault)
        p = study.load_progress(self.vault)
        ids, cur = study._daily_question_ids(self.vault, p, qs, self.today)  # 처음엔 현재 장만
        self.assertEqual(cur, "2-1")
        self.assertTrue(all(i.startswith("2-1#") for i in ids))
        for q in [x for x in qs.values() if x["chapter"] == "2-1"]:  # 2-1 전부 정답
            study.record_answer(self.vault, q["id"], q["answer"], today=self.today)
        p = study.load_progress(self.vault)
        ids2, cur2 = study._daily_question_ids(self.vault, p, qs, self.today)
        self.assertEqual(cur2, "2-2")  # 자동 진도로 다음 장이 열림
        self.assertIn("2-1", study.load_progress(self.vault)["read"])  # 마스터한 장은 읽음 처리
        self.assertTrue(all(i.startswith("2-2#") for i in ids2))  # 새 장의 새 문제가 나옴
        y = self.today - study.timedelta(days=1)  # 쿨다운: 어제 나온 문제는 모은다
        study._write(study.daily_path(self.vault, y), {"questions": ["2-2#1", "2-2#2"]})
        self.assertEqual(study._recent_shown(self.vault, self.today), {"2-2#1", "2-2#2"})

    def test_review_pace_evening_weekly(self):
        r = self._fill(1)  # 채우면 바로 검수까지
        self.assertEqual(r["filled"][0]["review"]["questions_fixed"], [1])
        ch = study.load_chapter(self.vault, "2-1")
        self.assertEqual((ch["questions"][0]["stem"], ch["questions"][0]["answer"]), ("고친 문제", 2))
        self.assertEqual(len(ch["questions"]), 12)  # 번호 유지, 삭제 없음
        self.assertTrue(ch["concept_md"].startswith("## 핵심 한 줄 (검수)"))
        self.assertIn("복수 정답", (self.vault / "study" / "review-log.md").read_text(encoding="utf-8"))
        self.assertEqual(len(study.UNITS), 11)
        self.assertEqual(study.UNITS[-1]["title"], "AI 보안")
        p = study.load_progress(self.vault)
        self.assertEqual(p["plan"]["target"], "2027-02-28")
        p["plan"]["start"] = "2026-11-13"
        pc = study.pace(p, date(2026, 11, 13))
        self.assertEqual((pc["chapters_expected"], pc["status"], pc["mock_from"]), (0, "계획대로", "2027-02-14"))
        pc = study.pace(p, date(2026, 12, 29))  # 진도 기간의 절반쯤
        self.assertEqual(pc["chapters_expected"], 10)
        self.assertEqual(pc["status"], "뒤처짐")
        self.assertEqual(study.pace(p, date(2027, 2, 20))["phase"], "모의고사 기간")
        self.assertEqual(study.evening_part(self.vault, self.today)[0], 1)  # 오늘 안 함 → 독려
        study.record_answer(self.vault, "2-1#1", 2, today=self.today)
        pr, txt = study.evening_part(self.vault, self.today)
        self.assertEqual(pr, 3)
        self.assertIn("1문제 100%", txt)
        w = study.weekly_report(self.vault, self.today + timedelta(days=1))
        self.assertIn("[공부방 주간] 지난주 1일 · 1문제 100%", w)
        self.assertLessEqual(len(w), 200)
        with self.assertRaises(study.StudyError):
            study.set_target(self.vault, "내년")

    def test_question_sets(self):
        html = self.home / "set.html"
        html.write_text("""<h1>테스트 시험 — 커뮤니티 정답</h1>
<h2>Question 1 (Topic 1)</h2><div class='q'>지문 하나</div><div class='q'>무엇을 고르나?</div>
<div class='choice'><b>A.</b> 가</div><div class='choice star'><b>B.</b> 나  ⭐</div><div class='choice'><b>C.</b> 다</div>
<p class='ans'>커뮤니티 정답: B</p><p class='dist'>투표 분포: B 9 (90%) · A 1</p><p class='src'><a href='https://example.com/1'>원문</a></p><hr>
<h2>Question 2 (Topic 1)</h2><div class='q'>두 개 고르기</div>
<div class='choice star'><b>A.</b> 하나 ⭐</div><div class='choice'><b>B.</b> 둘</div><div class='choice star'><b>C.</b> 셋 ⭐</div><div class='choice'><b>D.</b> 넷</div><div class='choice'><b>E.</b> 다섯</div>
<p class='ans'>커뮤니티 정답: AC</p><p class='dist'>투표 분포: AC 5</p><hr>""", encoding="utf-8")
        r = study.import_set(self.vault, html, "test-set")
        self.assertEqual((r["questions"], r["multi"], r["title"]), (2, 1, "테스트 시험 — 커뮤니티 정답"))
        s = study.load_set(self.vault, "test-set")
        self.assertEqual(s["questions"][0]["stem"], "지문 하나\n\n무엇을 고르나?")
        self.assertEqual(s["questions"][1]["answer"], ["A", "C"])
        self.assertEqual(len(s["questions"][1]["options"]), 5)
        self.assertFalse(study.record_set_answer(self.vault, "test-set", 2, ["A"], today=self.today)["ok"])
        self.assertTrue(study.record_set_answer(self.vault, "test-set", 2, ["c", "a"], today=self.today)["ok"])
        self.assertEqual(study.load_progress(self.vault)["boxes"]["test-set#2"]["box"], 1)
        e = study.explain_set(brain, self.vault, "test-set")
        self.assertEqual((e["explained"], e["remaining"]), (2, 0))
        study.import_set(self.vault, html, "test-set")  # 다시 가져와도 해설 유지
        q2 = study.set_payload(self.vault, "test-set", self.today)["questions"][1]
        self.assertEqual((q2["why"], q2["doubt"], q2["last"], q2["in_box"]), ("해설 2", "의심", True, True))
        ls = study.overview(self.vault, self.today)["sets"][0]
        self.assertEqual((ls["total"], ls["tried"], ls["right"], ls["explained"], ls["wrong"]), (2, 1, 1, 2, 1))
        with self.assertRaises(study.StudyError):
            study.set_path(self.vault, "../x")
        with self.assertRaises(study.StudyError):
            study.record_set_answer(self.vault, "test-set", 9, ["A"])

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
            req = urllib.request.Request(base + "/api/study/answer", data=json.dumps({"id": "2-1#1", "choice": 2}).encode(), method="POST",
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
