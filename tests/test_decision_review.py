"""되돌아볼 결정 처리: decision_action(keep/close/change) + /api/decision + CLI `decision`."""
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


class DecisionReviewTest(unittest.TestCase):
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
        self.today = date(2026, 9, 30)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _decision(self, title, revisit=None):
        path = brain.create_note(self.vault, "decision", title, revisit=revisit)
        return path

    # ── keep ──────────────────────────────────────────────────────────
    def test_keep_moves_revisit_forward_and_appends_note(self):
        yesterday = (self.today - timedelta(days=1)).isoformat()
        p = self._decision("결정A", revisit=yesterday)
        rel = p.relative_to(self.vault).as_posix()
        res = brain.decision_action(self.vault, {"action": "keep", "path": rel, "days": 90, "note": "아직 유효"}, self.today)
        self.assertTrue(res["ok"])
        expected = (self.today + timedelta(days=90)).isoformat()
        self.assertEqual(res["revisit"], expected)
        meta, body = brain.parse_frontmatter(p.read_text(encoding="utf-8"))
        self.assertEqual(meta["revisit"], expected)
        self.assertIn("## 되돌아볼 날짜", body)
        self.assertIn(f"- {self.today.isoformat()} 유지 → 다음 검토 {expected}. 아직 유효", body)

    def test_keep_default_days_is_90(self):
        p = self._decision("결정기본", revisit=self.today.isoformat())
        rel = p.relative_to(self.vault).as_posix()
        res = brain.decision_action(self.vault, {"action": "keep", "path": rel}, self.today)
        self.assertEqual(res["revisit"], (self.today + timedelta(days=90)).isoformat())

    # ── close ─────────────────────────────────────────────────────────
    def test_close_sets_decided_and_clears_revisit(self):
        p = self._decision("결정B", revisit=self.today.isoformat())
        rel = p.relative_to(self.vault).as_posix()
        res = brain.decision_action(self.vault, {"action": "close", "path": rel, "note": "끝났음"}, self.today)
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "decided")
        meta, body = brain.parse_frontmatter(p.read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "decided")
        self.assertNotIn("revisit", meta)
        self.assertIn(f"- {self.today.isoformat()} 종결. 끝났음", body)
        notes = brain.load_notes(self.vault)
        self.assertNotIn(rel, [d["path"] for d in brain._revisit_soon(notes, self.today)])

    # ── change ────────────────────────────────────────────────────────
    def test_change_creates_new_decision_and_supersedes_old(self):
        p = self._decision("결정C", revisit=self.today.isoformat())
        rel = p.relative_to(self.vault).as_posix()
        old_num = brain.next_decision_number(self.vault) - 1
        res = brain.decision_action(self.vault, {
            "action": "change", "path": rel,
            "title": "결정C 재검토", "decision": "이렇게 바꾼다", "why": "상황이 바뀌어서",
            "revisit_days": 60,
        }, self.today)
        self.assertTrue(res["ok"])
        new_path = self.vault / res["path"]
        self.assertTrue(new_path.is_file())
        new_num = int(Path(res["path"]).name.split("-", 1)[0])
        self.assertEqual(new_num, old_num + 1)

        old_meta, _ = brain.parse_frontmatter(p.read_text(encoding="utf-8"))
        self.assertEqual(old_meta["status"], "superseded")
        self.assertIn(new_path.stem, old_meta["superseded_by"])

        new_meta, new_body = brain.parse_frontmatter(new_path.read_text(encoding="utf-8"))
        self.assertIn(p.stem, str(new_meta["supersedes"]))
        self.assertEqual(new_meta["status"], "open")
        self.assertEqual(new_meta["revisit"], (self.today + timedelta(days=60)).isoformat())
        self.assertIn("결정C", new_body)  # 「옛 제목」을 다시 검토한 결과
        self.assertIn("이렇게 바꾼다", new_body)
        self.assertIn("상황이 바뀌어서", new_body)

    def test_change_without_revisit_days_leaves_revisit_unset(self):
        p = self._decision("결정D")
        rel = p.relative_to(self.vault).as_posix()
        res = brain.decision_action(self.vault, {
            "action": "change", "path": rel, "title": "결정D 후속", "decision": "d", "why": "w",
        }, self.today)
        new_meta, _ = brain.parse_frontmatter((self.vault / res["path"]).read_text(encoding="utf-8"))
        self.assertNotIn("revisit", new_meta)

    # ── 검증 ──────────────────────────────────────────────────────────
    def test_rejects_non_decision_note(self):
        p = brain.create_note(self.vault, "note", "그냥노트")
        rel = p.relative_to(self.vault).as_posix()
        with self.assertRaises(brain.BrainError):
            brain.decision_action(self.vault, {"action": "keep", "path": rel}, self.today)

    def test_change_requires_title_decision_why(self):
        p = self._decision("결정E")
        rel = p.relative_to(self.vault).as_posix()
        with self.assertRaises(brain.BrainError):
            brain.decision_action(self.vault, {"action": "change", "path": rel, "decision": "d", "why": "w"}, self.today)
        with self.assertRaises(brain.BrainError):
            brain.decision_action(self.vault, {"action": "change", "path": rel, "title": "t", "why": "w"}, self.today)
        with self.assertRaises(brain.BrainError):
            brain.decision_action(self.vault, {"action": "change", "path": rel, "title": "t", "decision": "d"}, self.today)

    def test_rejects_unknown_action(self):
        p = self._decision("결정F")
        rel = p.relative_to(self.vault).as_posix()
        with self.assertRaises(brain.BrainError):
            brain.decision_action(self.vault, {"action": "delete", "path": rel}, self.today)

    def test_rejects_long_note(self):
        p = self._decision("결정G")
        rel = p.relative_to(self.vault).as_posix()
        with self.assertRaises(brain.BrainError):
            brain.decision_action(self.vault, {"action": "keep", "path": rel, "note": "x" * 2001}, self.today)

    def test_rejects_traversal_path(self):
        with self.assertRaises(brain.BrainError):
            brain.decision_action(self.vault, {"action": "keep", "path": "../etc/passwd.md"}, self.today)

    # ── HTTP ──────────────────────────────────────────────────────────
    def test_api_decision_keep_and_no_token(self):
        p = self._decision("API결정", revisit=self.today.isoformat())
        rel = p.relative_to(self.vault).as_posix()
        srv = brain.make_server(self.vault, 0, quiet=True)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/session", timeout=5) as r:
                tok = json.loads(r.read().decode("utf-8"))["token"]
            payload = json.dumps({"action": "keep", "path": rel, "days": 30}).encode("utf-8")
            req = urllib.request.Request(base + "/api/decision", data=payload, method="POST",
                                         headers={"Content-Type": "application/json", "X-Brain-Token": tok})
            with urllib.request.urlopen(req, timeout=10) as r:
                res = json.loads(r.read().decode("utf-8"))
            self.assertTrue(res["ok"])
            self.assertEqual(res["action"], "keep")
            req2 = urllib.request.Request(base + "/api/decision", data=payload, method="POST",
                                          headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req2, timeout=10)
            self.assertEqual(ctx.exception.code, 403)
        finally:
            srv.shutdown()
            srv.server_close()

    # ── CLI ───────────────────────────────────────────────────────────
    def test_cli_keep_close_change_roundtrip(self):
        p1 = self._decision("CLI결정하나", revisit=self.today.isoformat())
        rel1 = p1.relative_to(self.vault).as_posix()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = brain.main(["decision", "keep", rel1, "--days", "45", "--note", "CLI유지", "--json"])
        self.assertEqual(rc, brain.EXIT_OK)
        j = json.loads(out.getvalue())
        self.assertEqual(j["action"], "keep")

        p2 = self._decision("CLI결정둘")
        rel2 = p2.relative_to(self.vault).as_posix()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = brain.main(["decision", "close", rel2, "--note", "CLI종결", "--json"])
        self.assertEqual(rc, brain.EXIT_OK)
        j = json.loads(out.getvalue())
        self.assertEqual(j["status"], "decided")

        p3 = self._decision("CLI결정셋")
        rel3 = p3.relative_to(self.vault).as_posix()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = brain.main(["decision", "change", rel3, "--title", "CLI결정셋 후속",
                              "--decision", "새 결정", "--why", "새 이유", "--revisit-days", "30", "--json"])
        self.assertEqual(rc, brain.EXIT_OK)
        j = json.loads(out.getvalue())
        self.assertEqual(j["action"], "change")
        self.assertTrue((self.vault / j["path"]).is_file())

    def test_cli_change_missing_flags_fails(self):
        p = self._decision("CLI결정넷")
        rel = p.relative_to(self.vault).as_posix()
        with contextlib.redirect_stderr(io.StringIO()):
            rc = brain.main(["decision", "change", rel, "--title", "t"])
        self.assertEqual(rc, brain.EXIT_INPUT)


if __name__ == "__main__":
    unittest.main()
