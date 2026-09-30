"""journal: 재료 수집(새 노트·완료 할 일·일정 메모·자동화 사고), 프롬프트, 가짜 Claude로 노트 작성(journal/YYYY/날짜.md), 중복 방지·--force, evening --journal 카톡 결합, 빈 날은 안 씀."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


class JournalTest(unittest.TestCase):
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
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])
            brain.main(["new", "--type", "idea", "--title", "사무실 직원 속마음 말풍선", "--body", "직원마다 상태 문구", "--created", self.today.isoformat()])
            brain.main(["new", "--type", "decision", "--title", "일지는 21:30에 자동으로 쓴다", "--created", self.today.isoformat()])
            brain.main(["task", "add", "enrich 마무리", "--due", self.today.isoformat()])
            brain.main(["task", "add", "일지 기능 테스트"])
        tasks = brain.parse_tasks(self.vault, self.today)
        brain.task_action(self.vault, {"action": "check", "line": tasks[0]["line"], "done": True}, self.today)
        brain.event_note_action(self.vault, {"action": "memo", "key": f"{self.today.isoformat()}|점검", "text": "노트북 배터리 교체 문의"})
        fake = self.home / "fake_claude.py"
        fake.write_text('''import sys, json
p = sys.stdin.read()
mat = json.loads(p.rsplit("[재료]", 1)[1])
titles = [n["title"] for n in mat["new_notes"] if n["type"] == "idea"]
out = {"today": ["「" + titles[0] + "」 아이디어를 적었다.", "결정 하나를 기록했다.", "할 일 " + str(len(mat["tasks_done"])) + "개를 끝냈다."],
       "win": "미루던 enrich를 끝냈다", "tomorrow": mat["tasks_left"][0] if mat["tasks_left"] else "", "kakao": "아이디어 1·결정 1·할 일 1 완료"}
print("결과:\\n```json\\n" + json.dumps(out, ensure_ascii=False) + "\\n```")
''', encoding="utf-8")
        os.environ["SECOND_BRAIN_ASK_CMD"] = sys.executable + " " + str(fake)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_material_and_prompt(self):
        mat = brain.journal_material(self.vault, self.today, widgets=[{"id": "dongtan", "title": "동탄", "status": "fail", "state": "active", "summary": "조회 실패", "kind": "log"}])
        self.assertFalse(mat["empty"])
        self.assertTrue({n["type"] for n in mat["new_notes"]}.issuperset({"idea", "decision"}))  # 안내 노트는 init 날짜가 오늘이라 note도 섞일 수 있음
        self.assertEqual(mat["tasks_done"], ["enrich 마무리"])
        self.assertEqual(mat["tasks_left"], ["일지 기능 테스트"])  # 마감 없는 것은 today 아님 → 비어 있을 수도
        self.assertTrue(any("노트북 배터리" in m for m in mat["event_memos"]))
        self.assertEqual(mat["automation_issues"], ["동탄: 조회 실패"])
        p = brain.journal_prompt(mat)
        self.assertIn("[재료]", p)
        self.assertIn("1인칭", p)

    def test_cli_write_and_force(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = brain.main(["journal"])
        self.assertEqual(rc, 0, buf.getvalue())
        rel = f"journal/{self.today.year}/{self.today.isoformat()}.md"
        self.assertIn(rel, buf.getvalue())
        n = brain.Note(self.vault, self.vault / rel)
        self.assertEqual(n.type, "journal")
        self.assertEqual(n.meta["journal_date"], self.today.isoformat())
        self.assertEqual(n.meta["summary"], "아이디어 1·결정 1·할 일 1 완료")
        self.assertIn("## 오늘\n- 「사무실 직원 속마음 말풍선」 아이디어를 적었다.", n.body)
        self.assertIn("## 잘한 것\n- 미루던 enrich를 끝냈다", n.body)
        # 같은 날 다시: 에러, --force면 갱신
        with self.assertRaises(brain.BrainError):
            brain.make_journal(self.vault, self.today)
        r = brain.make_journal(self.vault, self.today, force=True)
        self.assertFalse(r["created"])
        self.assertEqual(len([x for x in brain.load_notes(self.vault) if x.type == "journal"]), 1)
        # 인덱스·검색에 잡힘
        hits = brain.dash_search(self.vault, "일지", 5)
        self.assertTrue(any(h["type"] == "journal" for h in hits))

    def test_empty_day(self):
        far = date(2020, 1, 1)
        r = brain.make_journal(self.vault, far)
        self.assertTrue(r["empty"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = brain.main(["journal", "--date", "2020-01-01"])
        self.assertEqual(rc, 0)
        self.assertIn("일지를 쓰지 않았어요", buf.getvalue())

    def test_evening_with_journal(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = brain.main(["brief", "--evening", "--journal"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("저녁 마감", out)
        self.assertIn("일지: 아이디어 1·결정 1·할 일 1 완료", out)
        self.assertTrue((self.vault / f"journal/{self.today.year}/{self.today.isoformat()}.md").exists())
        self.assertEqual(brain.AGENT_SPECS["evening"]["args"], ["brief", "--evening", "--journal", "--kakao"])

    def test_dry_run(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = brain.main(["journal", "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertIn("일지 재료", buf.getvalue())
        self.assertFalse((self.vault / "journal").exists())


if __name__ == "__main__":
    unittest.main()
