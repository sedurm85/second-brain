"""retro: 재료(새 노트·결정과 이유·되돌아볼 결정·일지·고아), 가짜 Claude로 회고 노트(journal/YYYY/날짜-weekly.md) + 질문 3개, 중복·--force, 빈 기간, review --semantic 링크 제안 병합."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


class RetroTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_ASK_CMD", "SECOND_BRAIN_JOBS_DIR")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ["SECOND_BRAIN_JOBS_DIR"] = str(self.home / "nojobs")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault), "calendar": {"sources": []}}), encoding="utf-8")
        self.today = date.today()
        d = lambda k: (self.today - timedelta(days=k)).isoformat()
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])
            brain.main(["new", "--type", "note", "--title", "Orca 브라우저 캡처 요령", "--tags", "orca,자동화", "--body", "스크롤 화면은 검게 나온다", "--created", d(2)])
            brain.main(["new", "--type", "idea", "--title", "사무실 직원 속마음", "--tags", "대시보드", "--body", "MZ 문구", "--created", d(1)])
            brain.main(["new", "--type", "decision", "--title", "대시보드는 로컬 서버로 띄운다", "--revisit", d(-3), "--created", d(3),
                        "--body", "# 대시보드는 로컬 서버로 띄운다\n\n## 상황\n외부 호스팅 고민\n\n## 결정\n127.0.0.1 전용 서버\n\n## 이유\n개인 데이터가 나가지 않는다\n"])
            brain.main(["new", "--type", "note", "--title", "오래된 무관한 노트", "--body", "x", "--created", d(40)])
        # 일지 하나
        jp = self.vault / "journal" / str(self.today.year)
        jp.mkdir(parents=True)
        (jp / f"{d(1)}.md").write_text(f"---\ntitle: {d(1)} 일지\ntype: journal\ncreated: {d(1)}\ntags: [일지]\njournal_date: {d(1)}\n---\n# {d(1)} 일지\n\n## 오늘\n- 사무실에 속마음 말풍선을 넣었다.\n- 준비 제안 기능을 만들었다.\n", encoding="utf-8")
        fake = self.home / "fake_claude.py"
        fake.write_text('''import sys, json
p = sys.stdin.read()
if "[후보]" in p:
    cands = json.loads(p.rsplit("[후보]", 1)[1])
    cat = [l.split(" | ")[0] for l in p.split("[카탈로그 stem | 제목 | 요약]")[1].split("[후보]")[0].strip().split("\\n")]
    out = []
    for c in cands:
        others = [s for s in cat if s != c["stem"] and s not in c["linked"] and "사무실" in s]
        if others and "orca" in c["stem"]:
            out.append({"a": c["stem"], "b": others[0], "reason": "같은 대시보드 작업"})
    out.append({"a": "없는-stem", "b": cat[0], "reason": "버려야 함"})
    print(json.dumps(out, ensure_ascii=False)); sys.exit(0)
mat = json.loads(p.rsplit("[재료]", 1)[1])
out = {"week": ["Orca 캡처 요령과 사무실 속마음 아이디어를 적었다.", "대시보드를 로컬 서버로 띄우기로 결정했다.", "일지 " + str(mat["counts"]["journals"]) + "편을 썼다."],
       "patterns": ["대시보드 관련 기록이 이번 주 대부분이다."],
       "questions": ["로컬 서버 결정은 외부에서 볼 필요가 생겨도 유지할 건가요?", "고아 노트를 어디에 연결할 수 있을까요?", "다음 주 가장 먼저 닫을 미결은 무엇인가요?"],
       "next_week": ["되돌아볼 결정 1건 검토"], "kakao": "이번 주 노트 2·결정 1, 대시보드에 집중"}
print("```json\\n" + json.dumps(out, ensure_ascii=False) + "\\n```")
''', encoding="utf-8")
        os.environ["SECOND_BRAIN_ASK_CMD"] = sys.executable + " " + str(fake)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_material(self):
        mat = brain.retro_material(self.vault, 7, self.today, widgets=[])
        self.assertFalse(mat["empty"])
        titles = [n["title"] for n in mat["new_notes"]]
        self.assertIn("Orca 브라우저 캡처 요령", titles)
        self.assertNotIn("오래된 무관한 노트", titles)
        self.assertEqual(mat["decisions"][0]["decision"], "127.0.0.1 전용 서버")
        self.assertEqual(mat["decisions"][0]["why"], "개인 데이터가 나가지 않는다")
        self.assertEqual(len(mat["revisit_due"]), 1)  # 3일 뒤 revisit → 14일 창 안
        self.assertEqual(mat["journals"][0]["lines"][0], "사무실에 속마음 말풍선을 넣었다.")
        self.assertEqual(mat["counts"]["journals"], 1)
        self.assertIn("1인칭", brain.retro_prompt(mat))

    def test_cli_and_force(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = brain.main(["retro"])
        self.assertEqual(rc, 0, buf.getvalue())
        rel = f"journal/{self.today.year}/{self.today.isoformat()}-weekly.md"
        self.assertIn(rel, buf.getvalue())
        n = brain.Note(self.vault, self.vault / rel)
        self.assertEqual(n.type, "journal")
        self.assertEqual(n.meta["journal_kind"], "weekly")
        self.assertEqual(n.meta["summary"], "이번 주 노트 2·결정 1, 대시보드에 집중")
        self.assertIn("## 되돌아볼 질문\n- [ ] 로컬 서버 결정은", n.body)
        self.assertEqual(n.body.count("- [ ] "), 4)  # 질문 3 + 다음 주 1
        self.assertIn("## 고아 노트", n.body)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertNotEqual(brain.main(["retro"]), 0)  # 같은 날 중복은 에러
            self.assertEqual(brain.main(["retro", "--force"]), 0)
        self.assertEqual(len([x for x in brain.load_notes(self.vault) if x.type == "journal" and x.stem.endswith("-weekly")]), 1)
        # 일지 재료에서 주간 회고는 '일지'로 세지 않음
        mat = brain.retro_material(self.vault, 7, self.today, widgets=[])
        self.assertEqual(mat["counts"]["journals"], 1)

    def test_empty(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = brain.main(["retro", "--days", "1", "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertIn("회고 재료", buf.getvalue())

    def test_semantic_review(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = brain.main(["review", "--semantic", "--json"])
        self.assertEqual(rc, 0)
        r = json.loads(buf.getvalue())
        sem = [s for s in r["link_suggestions"] if s["reason"].startswith("의미:")]
        self.assertEqual(len(sem), 1)
        self.assertTrue(sem[0]["a"].endswith("orca-브라우저-캡처-요령.md"))
        self.assertEqual(r["semantic"], 1)
        self.assertFalse(any("없는-stem" in s["a"] for s in r["link_suggestions"]))
        # 제안을 실제 연결하면 다음 리뷰에선 빠짐
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["link", sem[0]["a"], sem[0]["b"]])
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                brain.main(["review", "--semantic", "--json"])
        r2 = json.loads(buf.getvalue())
        self.assertEqual([s for s in r2["link_suggestions"] if s["reason"].startswith("의미:")], [])

    def test_agent_spec(self):
        self.assertEqual(brain.AGENT_SPECS["retro"]["calendar"], {"Weekday": 1, "Hour": 9, "Minute": 0})


if __name__ == "__main__":
    unittest.main()


class RetroQuestionsTest(RetroTest):
    def test_questions_exposed_and_checkable(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            brain.main(["retro"])
        t = brain.dash_today(self.vault, today=self.today, widgets=[], agenda={"today": [], "upcoming": []})
        qs = t["retro_questions"]
        self.assertEqual(len(qs), 3)
        self.assertTrue(qs[0]["text"].startswith("로컬 서버 결정은"))
        self.assertTrue(qs[0]["path"].endswith("-weekly.md"))
        # 보드가 쓰는 체크 액션으로 첫 질문을 닫으면 다음 today에서 빠진다
        brain.event_note_action(self.vault, {"action": "check", "path": qs[0]["path"], "line": qs[0]["line"], "done": True})
        t2 = brain.dash_today(self.vault, today=self.today, widgets=[], agenda={"today": [], "upcoming": []})
        self.assertEqual(len(t2["retro_questions"]), 2)
        self.assertNotIn("로컬 서버", " ".join(q["text"] for q in t2["retro_questions"]))
        # 15일 전 회고는 안 보임
        self.assertEqual(brain.retro_questions(brain.load_notes(self.vault), self.today + timedelta(days=15)), [])


class WeekPlanTest(RetroTest):
    def test_plan_exposed_and_checkable(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            brain.main(["retro"])
        t = brain.dash_today(self.vault, today=self.today, widgets=[], agenda={"today": [], "upcoming": []})
        wp = t["week_plan"]
        self.assertEqual(len(wp), 1)
        self.assertEqual(wp[0]["text"], "되돌아볼 결정 1건 검토")
        self.assertTrue(wp[0]["path"].endswith("-weekly.md"))
        # 보드가 쓰는 체크 액션으로 계획을 닫으면 다음 today에서 빠진다
        brain.event_note_action(self.vault, {"action": "check", "path": wp[0]["path"], "line": wp[0]["line"], "done": True})
        t2 = brain.dash_today(self.vault, today=self.today, widgets=[], agenda={"today": [], "upcoming": []})
        self.assertEqual(t2["week_plan"], [])
        # 15일 전 회고는 안 보임
        self.assertEqual(brain.week_plan(brain.load_notes(self.vault), self.today + timedelta(days=15)), [])
