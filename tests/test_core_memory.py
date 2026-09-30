"""코어 대화 기억: ask → core_log 기록·최근 대화 맥락, remember → 노트, /api/remember, 일지 재료 core_chat. 사무실 직원 한 줄(staff brief): Claude 요약·하루 캐시·force, widget_action brief."""
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


class CoreMemoryTest(unittest.TestCase):
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
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])
        # 가짜 Claude: 호출 횟수를 파일에 남기고, 프롬프트 종류에 따라 답한다
        self.counter = self.home / "calls.txt"
        fake = self.home / "fake_claude.py"
        fake.write_text(f'''import sys, json
p = sys.stdin.read()
open({str(self.counter)!r}, "a").write("x")
if "[기록]" in p:
    ctx = json.loads(p.rsplit("[기록]", 1)[1])
    print(json.dumps({{"did": "이번 주 " + str(ctx["runs_7d"]) + "번 돌며 카톡을 보냈다", "issue": "문제 없음" if not ctx["fails_7d"] else "실패 " + str(ctx["fails_7d"]) + "회", "mood": "순조로움"}}, ensure_ascii=False)); sys.exit(0)
if "[최근 대화]" in p:
    recent = json.loads(p.rsplit("[최근 대화]", 1)[1].rsplit("[질문]", 1)[0])
    q = p.rsplit("[질문]", 1)[1].strip()
    print("직전 대화 " + str(len(recent)) + "건을 참고했어요. " + q + "에 대한 답이에요."); sys.exit(0)
print("?")
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

    def _today(self):
        return brain.dash_today(self.vault, today=self.today, widgets=[], agenda={"today": [], "upcoming": []})

    def test_ask_logs_and_context(self):
        t = self._today()
        r1 = brain.ask_assistant("내일 뭐 있어", t, self.vault)
        self.assertIn("직전 대화 0건", r1["answer"])
        r2 = brain.ask_assistant("그럼 준비물은?", t, self.vault)
        self.assertIn("직전 대화 1건", r2["answer"])
        chat = brain.core_chat_today(self.today)
        self.assertEqual([c["q"] for c in chat], ["내일 뭐 있어", "그럼 준비물은?"])
        self.assertTrue(chat[0]["a"].startswith("직전 대화"))
        # 일지 재료에 포함
        mat = brain.journal_material(self.vault, self.today, widgets=[])
        self.assertEqual(len(mat["core_chat"]), 2)
        self.assertFalse(mat["empty"])
        # 다른 날은 비어 있음
        self.assertEqual(brain.core_chat_today(date(2020, 1, 1)), [])

    def test_remember_and_api(self):
        res = brain.remember_chat(self.vault, {"question": "제주도 갈 때 뭐 챙기지?", "answer": "여권은 필요 없고 신분증·보조배터리를 챙기세요."})
        self.assertTrue(res["path"].startswith("notes/"))
        self.assertEqual(res["title"], "제주도 갈 때 뭐 챙기지")
        n = brain.Note(self.vault, self.vault / res["path"])
        self.assertEqual(n.tags[:2], ["코어", "대화"])
        self.assertIn("## 질문\n제주도 갈 때 뭐 챙기지?", n.body)
        self.assertIn("## 답\n여권은 필요 없고", n.body)
        with self.assertRaises(brain.BrainError):
            brain.remember_chat(self.vault, {"question": "", "answer": "x"})
        srv = brain.make_server(self.vault, 0, quiet=True, today=self.today)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/session", timeout=5) as r:
                tok = json.loads(r.read().decode("utf-8"))["token"]
            req = urllib.request.Request(base + "/api/remember", data=json.dumps({"question": "회고는 언제?", "answer": "월요일 9시에 써요.", "title": "회고 시각"}).encode("utf-8"),
                                         method="POST", headers={"Content-Type": "application/json", "X-Brain-Token": tok})
            with urllib.request.urlopen(req, timeout=10) as r:
                j = json.loads(r.read().decode("utf-8"))
            self.assertEqual(j["title"], "회고 시각")
            hits = brain.dash_search(self.vault, "회고", 5)
            self.assertTrue(any(h["title"] == "회고 시각" for h in hits))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_staff_brief_cache(self):
        logp = self.home / "market.log"
        d = self.today.isoformat()
        logp.write_text("".join(f"{d} 10:00 len: 148 카톡 발송 OK\n" for _ in range(3)) + f"{d} 15:00 Traceback 실패\n", encoding="utf-8")
        w = {"id": "marketset", "title": "마켓세트 카톡", "kind": "log", "source": str(logp), "status": "ok", "state": "active", "summary": "len: 148",
             "status_cfg": {"fail_pattern": "Traceback|실패"}}
        b = brain.staff_brief(w, today=self.today)
        self.assertFalse(b["cached"])
        self.assertEqual(b["did"], "이번 주 4번 돌며 카톡을 보냈다")
        self.assertEqual(b["issue"], "실패 1회")
        self.assertEqual(b["mood"], "순조로움")
        self.assertEqual(self.counter.read_text(), "x")
        b2 = brain.staff_brief(w, today=self.today)
        self.assertTrue(b2["cached"])
        self.assertEqual(self.counter.read_text(), "x")  # 캐시라 호출 없음
        b3 = brain.widget_action({"action": "brief", "id": "marketset", "force": True}, [w])
        self.assertEqual((b3["action"], b3["cached"]), ("brief", False))
        self.assertEqual(self.counter.read_text(), "xx")
        with self.assertRaises(brain.BrainError):
            brain.widget_action({"action": "brief", "id": "ghost"}, [w])


if __name__ == "__main__":
    unittest.main()
