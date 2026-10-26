"""커리어 탭: 포지션·연락처 추가/수정/상태 변경/기록, 기한 집계·카톡 한 줄, career/가 보드·검색에서 빠지는지, HTTP(토큰), CLI, 스카우트 읽기."""
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
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402
import career  # noqa: E402


class CareerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        h = Path(self.tmp.name)
        self.home = h
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_JOBS_DIR",
                                                    "SECOND_BRAIN_OFFLINE", "SECOND_BRAIN_SCOUT_RESULT", "SECOND_BRAIN_SCOUT_REPORTS")}
        os.environ.update(HOME=str(h), XDG_CACHE_HOME=str(h / ".cache"), SECOND_BRAIN_JOBS_DIR=str(h / "nojobs"), SECOND_BRAIN_OFFLINE="1",
                          SECOND_BRAIN_SCOUT_RESULT=str(h / "scout.md"), SECOND_BRAIN_SCOUT_REPORTS=str(h / "reports"))
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        (h / ".config" / "second-brain").mkdir(parents=True)
        self.vault = h / "brain"
        (h / ".config" / "second-brain" / "config.json").write_text(json.dumps({"vault": str(self.vault), "calendar": {"sources": []}}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])
        self.today = date.today()

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.tmp.cleanup()

    def test_position_lifecycle_and_isolation(self):
        p = career.add_position(brain, self.vault, {"company": "가나다테크", "role": "보안 총괄", "score": 72, "source": "헤드헌터",
                                                    "next_action": "JD 요청", "next_due": self.today.isoformat(), "fit": "규제 대응 경험 직결"})
        self.assertEqual(p["status"], "발견")
        self.assertEqual(p["sections"]["핏 분석"], "규제 대응 경험 직결")
        self.assertTrue((self.vault / "career" / "positions" / f"{p['id']}.md").is_file())
        p2 = career.update_position(brain, self.vault, p["id"], {"status": "보류", "revisit_trigger": "통근 개선 시"}, "연봉 밴드 낮음")
        self.assertEqual(p2["status"], "보류")
        self.assertEqual(p2["revisit_trigger"], "통근 개선 시")
        self.assertIn("상태 발견 → 보류 · 연봉 밴드 낮음", p2["logs"][-1]["text"])
        with self.assertRaises(career.CareerError):
            career.update_position(brain, self.vault, p["id"], {"status": "애매"})
        with self.assertRaises(career.CareerError):
            career.update_position(brain, self.vault, p["id"], {"score": 140})
        with self.assertRaises(career.CareerError):
            career._find(self.vault, "position", "../../etc")
        # 보드·검색·인덱스에 섞이지 않는다
        self.assertFalse(any("가나다테크" in n.title for n in brain.load_notes(self.vault)))
        self.assertEqual([h for h in brain.dash_search(self.vault, "가나다테크", 5)], [])
        brain.build_index(self.vault)
        self.assertNotIn("가나다테크", (self.vault / "BRAIN.md").read_text(encoding="utf-8"))
        ov = career.overview(brain, self.vault)
        self.assertEqual(ov["counts"]["보류"], 1)
        career.delete_position(brain, self.vault, p["id"])
        self.assertEqual(career.list_positions(brain, self.vault), [])

    def test_contacts_due_and_brief(self):
        c = career.add_contact(brain, self.vault, {"name": "김헤드", "firm": "갤리언", "channel": "LinkedIn",
                                                   "reply_due": (self.today + timedelta(days=1)).isoformat()})
        self.assertEqual(c["title"], "김헤드 (갤리언)")
        p = career.add_position(brain, self.vault, {"company": "여기", "role": "DevOps 팀장", "contact": c["id"],
                                                    "next_action": "역제안 메일", "next_due": (self.today - timedelta(days=1)).isoformat()})
        self.assertEqual(p["contact"], c["id"])
        due = career.due_items(brain, self.vault, self.today, within=1)
        self.assertEqual([x["kind"] for x in due], ["position", "contact"])  # 지난 것이 먼저
        self.assertIn("커리어 여기 역제안 메일 지남", career.due_sentence(due))
        t = brain.dash_today(self.vault, today=self.today, widgets=[], agenda={"today": [], "upcoming": []})
        self.assertEqual(len(t["career_due"]), 2)
        self.assertIn("커리어", t["kakao"])
        self.assertLessEqual(len(t["kakao"]), brain.KAKAO_MAX)
        # 보류·종료는 기한 집계에서 빠진다
        career.update_position(brain, self.vault, p["id"], {"status": "안함"})
        career.update_contact(brain, self.vault, c["id"], {"status": "종료"}, "패스 회신")
        self.assertEqual(career.due_items(brain, self.vault, self.today, within=1), [])
        self.assertEqual(career._contact_payload(brain, career._find(self.vault, "contact", c["id"]), self.today, True)["logs"][-1]["text"], "패스 회신")

    def test_profile_and_scout(self):
        self.assertIn("킬러 수치", career.read_profile(self.vault))
        career.save_profile(brain, self.vault, "# 자산\n\n## 킬러 수치\n- IT 비용 30% 절감\n")
        self.assertIn("30%", career.read_profile(self.vault))
        (self.home / "scout.md").write_text("# 결과\n- ⭐ 가나다 · 보안 리드 https://example.com/1\n- 라마바 · 플랫폼장\n", encoding="utf-8")
        (self.home / "reports").mkdir()
        (self.home / "reports" / "2026-10-20.md").write_text("# 10/20 리포트\n본문", encoding="utf-8")
        s = career.scout_overview(brain)
        self.assertEqual(len(s["lines"]), 2)
        self.assertEqual(s["lines"][0]["url"], "https://example.com/1")
        self.assertEqual(s["reports"][0]["title"], "10/20 리포트")
        self.assertIn("본문", career.read_report(brain, "2026-10-20.md")["text"])
        with self.assertRaises(career.CareerError):
            career.read_report(brain, "../../x.md")

    def test_http_and_cli(self):
        srv = brain.make_server(self.vault, 0, quiet=True)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/career", timeout=5) as r:
                self.assertIn("커리어", r.read().decode("utf-8"))
            with urllib.request.urlopen(base + "/api/session", timeout=5) as r:
                tok = json.loads(r.read().decode("utf-8"))["token"]

            def post(path, body, token=tok):
                req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                             headers={"Content-Type": "application/json", "X-Brain-Token": token})
                with urllib.request.urlopen(req, timeout=10) as r:
                    return json.loads(r.read().decode("utf-8"))
            with self.assertRaises(urllib.error.HTTPError) as cm:
                post("/api/career/position", {"action": "add", "company": "x"}, token="bad")
            self.assertEqual(cm.exception.code, 403)
            r = post("/api/career/position", {"action": "add", "company": "샘플사", "role": "CISO", "score": "61"})
            pid = r["position"]["id"]
            r = post("/api/career/position", {"action": "status", "id": pid, "status": "진행"})
            self.assertEqual(r["position"]["status"], "진행")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                post("/api/career/position", {"action": "status", "id": pid, "status": "모름"})
            self.assertEqual(cm.exception.code, 400)
            with urllib.request.urlopen(base + "/api/career", timeout=5) as resp:
                d = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(d["counts"]["진행"], 1)
            self.assertIn("보류", d["statuses"])
        finally:
            srv.shutdown()
            srv.server_close()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(brain.main(["career", "add", "--company", "CLI사", "--role", "본부장", "--score", "55"]), 0)
        pid = [p["id"] for p in career.list_positions(brain, self.vault) if p["company"] == "CLI사"][0]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(brain.main(["career", "status", pid, "보류", "--note", "통근"]), 0)
            self.assertEqual(brain.main(["career", "set", pid, "next_action=재확인", f"next_due={self.today.isoformat()}"]), 0)
            self.assertEqual(brain.main(["career", "contact", "add", "--name", "박리크", "--firm", "서치펌"]), 0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            brain.main(["career", "list", "--status", "보류"])
        self.assertIn("CLI사", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
