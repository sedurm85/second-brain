"""주간 리포트: dash_report 형태·개수·순서·결정 발췌·회고 질문, /report·/api/report HTTP."""
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


class ReportTest(unittest.TestCase):
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
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault), "assistant_name": "자비스",
                                                         "calendar": {"sources": []}}), encoding="utf-8")
        self.today = date.today()
        d = lambda k: (self.today - timedelta(days=k)).isoformat()
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])
            brain.main(["new", "--type", "note", "--title", "리포트 새 노트", "--tags", "리포트", "--project", "세컨드브레인",
                        "--body", "리포트 페이지를 만드는 중", "--created", d(2)])
            brain.main(["new", "--type", "idea", "--title", "인쇄용 한 장 아이디어", "--body", "A4 한 장으로 정리", "--created", d(1)])
            brain.main(["new", "--type", "decision", "--title", "리포트는 서버가 만든다", "--revisit", d(-5), "--created", d(3),
                        "--body", "# 리포트는 서버가 만든다\n\n## 상황\n프런트에서 집계할지 고민\n\n## 결정\nbrain.py가 재료를 만든다\n\n## 이유\n테스트하기 쉽다\n"])
            brain.main(["new", "--type", "note", "--title", "오래된 무관한 노트", "--body", "x", "--created", d(40)])
        # 일지 하나 (일반)
        jdir = self.vault / "journal" / str(self.today.year)
        jdir.mkdir(parents=True)
        (jdir / f"{d(1)}.md").write_text(
            f"---\ntitle: {d(1)} 일지\ntype: journal\ncreated: {d(1)}\ntags: [일지]\njournal_date: {d(1)}\nsummary: 리포트 페이지 작업\n---\n"
            f"# {d(1)} 일지\n\n## 오늘\n- 리포트 API를 만들었다.\n- 페이지 레이아웃을 짰다.\n", encoding="utf-8")
        # 주간 회고 (weekly)
        (jdir / f"{d(0)}-weekly.md").write_text(
            f"---\ntitle: {d(0)} 주간 회고\ntype: journal\ncreated: {d(0)}\ntags: [회고]\njournal_kind: weekly\njournal_date: {d(0)}\nsummary: 리포트 만든 한 주\n---\n"
            f"# {d(0)} 주간 회고\n\n{d(2)} ~ {d(0)} · 노트 2 · 결정 1 · 일지 1\n\n"
            "## 이번 주\n- 리포트 API를 만들었다.\n- 결정 하나를 남겼다.\n\n"
            "## 눈에 띄는 것\n- 리포트 작업에 집중했다.\n\n"
            "## 되돌아볼 질문\n- [x] 서버 집계 방식은 유지할까요?\n- [ ] 인쇄 레이아웃은 A4로 충분할까요?\n\n"
            "## 다음 주\n- [ ] 결정 revisit 검토\n", encoding="utf-8")

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_dash_report_shape_and_counts(self):
        r = brain.dash_report(self.vault, 7, self.today, widgets=[])
        for key in ("since", "until", "counts", "new_notes", "decisions", "journals", "retro",
                    "automation", "tasks", "projects"):
            self.assertIn(key, r)
        self.assertEqual(r["until"], self.today.isoformat())
        c = r["counts"]
        self.assertEqual(c["decisions"], 1)
        self.assertEqual(c["journals"], 2)  # 일반 일지 1 + 주간 회고 1
        self.assertEqual(c["events"], 0)
        self.assertGreaterEqual(c["notes"], 2)

        titles = [n["title"] for n in r["new_notes"]]
        self.assertIn("리포트 새 노트", titles)
        self.assertIn("리포트는 서버가 만든다", titles)  # decision도 new_notes에 포함
        self.assertNotIn("오래된 무관한 노트", titles)
        for n in r["new_notes"]:
            self.assertNotIn(n["type"], ("journal", "event"))
        # 새 기록 최신순
        created = [n["created"] for n in r["new_notes"]]
        self.assertEqual(created, sorted(created, reverse=True))

    def test_decisions_excerpt(self):
        r = brain.dash_report(self.vault, 7, self.today, widgets=[])
        self.assertEqual(len(r["decisions"]), 1)
        d = r["decisions"][0]
        self.assertEqual(d["title"], "리포트는 서버가 만든다")
        self.assertEqual(d["decision"], "brain.py가 재료를 만든다")
        self.assertEqual(d["why"], "테스트하기 쉽다")
        self.assertEqual(d["status"], "open")
        self.assertIsNotNone(d["revisit"])

    def test_journals_and_retro(self):
        r = brain.dash_report(self.vault, 7, self.today, widgets=[])
        self.assertEqual(len(r["journals"]), 1)  # 주간 회고는 제외
        self.assertIn("리포트 API를 만들었다.", r["journals"][0]["lines"])
        self.assertEqual(r["journals"][0]["summary"], "리포트 페이지 작업")

        retro = r["retro"]
        self.assertIsNotNone(retro)
        self.assertIn("리포트 API를 만들었다.", retro["week"])
        self.assertEqual(retro["patterns"], ["리포트 작업에 집중했다."])
        qs = retro["questions"]
        self.assertEqual(len(qs), 2)
        self.assertTrue(qs[0]["done"])
        self.assertFalse(qs[1]["done"])
        self.assertEqual(qs[1]["text"], "인쇄 레이아웃은 A4로 충분할까요?")
        nxt = retro["next_week"]
        self.assertEqual(len(nxt), 1)
        self.assertFalse(nxt[0]["done"])

    def test_retro_none_when_no_weekly_in_window(self):
        r = brain.dash_report(self.vault, 1, self.today - timedelta(days=10), widgets=[])
        self.assertIsNone(r["retro"])

    def test_projects_and_tasks(self):
        r = brain.dash_report(self.vault, 7, self.today, widgets=[])
        proj_names = [p[0] for p in r["projects"]]
        self.assertIn("세컨드브레인", proj_names)
        self.assertIn("open", r["tasks"])
        self.assertIn("done_recent", r["tasks"])
        self.assertIsInstance(r["tasks"]["open"], dict)

    def test_automation_excludes_paused_and_counts_log_widgets(self):
        log = self.home / "job.log"
        today_iso = self.today.isoformat()
        log.write_text(f"{today_iso} 12:00 ok\n{today_iso} 13:00 Error boom\n", encoding="utf-8")
        widgets = [
            {"id": "w1", "title": "잡 실행기", "kind": "log", "team": "운영팀", "state": "active",
             "status": "fail", "source": str(log), "status_cfg": {"fail_pattern": "Error"}},
            {"id": "w2", "title": "멈춘 위젯", "kind": "log", "team": "운영팀", "state": "paused",
             "status": "paused", "source": str(log)},
        ]
        r = brain.dash_report(self.vault, 7, self.today, widgets=widgets)
        ids = [a["id"] for a in r["automation"]]
        self.assertIn("w1", ids)
        self.assertNotIn("w2", ids)
        w1 = next(a for a in r["automation"] if a["id"] == "w1")
        self.assertEqual(w1["runs"], 2)
        self.assertEqual(w1["fails"], 1)
        self.assertEqual(w1["team"], "운영팀")


class ReportHttpTest(unittest.TestCase):
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
        for d in ("notes", "decisions", "projects", "people"):
            (self.vault / d).mkdir(parents=True)
        (self.vault / "BRAIN.md").write_text("# BRAIN\n", encoding="utf-8")
        (self.vault / "inbox.md").write_text("- [ ] 테스트 할 일\n", encoding="utf-8")
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault), "calendar": {"sources": []}}), encoding="utf-8")
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

    def test_report_page(self):
        st, ct, body = self.get("/report")
        self.assertEqual(st, 200)
        self.assertIn("text/html", ct)
        self.assertIn("리포트", body.decode("utf-8"))

    def test_api_report(self):
        st, ct, body = self.get("/api/report?days=7")
        self.assertEqual(st, 200)
        self.assertIn("application/json", ct)
        r = json.loads(body)
        for key in ("since", "until", "counts", "new_notes", "decisions", "journals", "retro",
                    "automation", "tasks", "projects"):
            self.assertIn(key, r)


if __name__ == "__main__":
    unittest.main()
