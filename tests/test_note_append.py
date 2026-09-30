"""보드 노트 패널 「바로 기록」: note_append_action(memo/tag/todo) + /api/note-append."""
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


class NoteAppendTest(unittest.TestCase):
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

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _note(self, ntype, title):
        path = brain.create_note(self.vault, ntype, title)
        return path.relative_to(self.vault).as_posix()

    # ── memo ──────────────────────────────────────────────────────────
    def test_memo_appends_under_section_and_creates_if_missing(self):
        rel = self._note("note", "메모테스트")
        res = brain.note_append_action(self.vault, {"action": "memo", "path": rel, "text": "첫 메모"})
        self.assertTrue(res["ok"])
        body = res["note"]["body"]
        self.assertIn("## 메모", body)
        self.assertIn("첫 메모", body)
        # 타임스탬프 형식 YYYY-MM-DD HH:MM
        import re
        self.assertRegex(body, r"- \d{4}-\d{2}-\d{2} \d{2}:\d{2}  첫 메모")

    def test_second_memo_appends_below_first(self):
        rel = self._note("note", "메모두번째")
        brain.note_append_action(self.vault, {"action": "memo", "path": rel, "text": "첫 번째"})
        res = brain.note_append_action(self.vault, {"action": "memo", "path": rel, "text": "두 번째"})
        body = res["note"]["body"]
        self.assertEqual(body.count("## 메모"), 1)
        i1, i2 = body.index("첫 번째"), body.index("두 번째")
        self.assertLess(i1, i2)

    def test_memo_on_decision_note_goes_to_memo_not_decision_or_reason(self):
        rel = self._note("decision", "결정테스트")
        res = brain.note_append_action(self.vault, {"action": "memo", "path": rel, "text": "추가 고려사항"})
        body = res["note"]["body"]
        self.assertIn("## 메모", body)
        self.assertIn("추가 고려사항", body)
        # 결정/이유 절은 그대로(빈 채) 유지되고, 메모 절 안에만 텍스트가 들어감
        dec_idx = body.index("## 결정")
        reason_idx = body.index("## 이유")
        memo_idx = body.index("## 메모")
        self.assertGreater(memo_idx, reason_idx)
        self.assertGreater(reason_idx, dec_idx)

    def test_memo_on_journal_note(self):
        rel = self._note("journal", "일지테스트")
        res = brain.note_append_action(self.vault, {"action": "memo", "path": rel, "text": "오늘의 기록"})
        body = res["note"]["body"]
        self.assertIn("## 메모", body)
        self.assertIn("오늘의 기록", body)
        self.assertIn("## 오늘", body)  # 기존 절 유지

    # ── todo ──────────────────────────────────────────────────────────
    def test_todo_creates_section(self):
        rel = self._note("idea", "할일테스트")
        res = brain.note_append_action(self.vault, {"action": "todo", "path": rel, "text": "검토하기"})
        body = res["note"]["body"]
        self.assertIn("## 할 일", body)
        self.assertIn("- [ ] 검토하기", body)

    # ── tag ───────────────────────────────────────────────────────────
    def test_tag_dedupe_strip_hash_and_limit(self):
        rel = self._note("note", "태그테스트")
        brain.note_append_action(self.vault, {"action": "tag", "path": rel, "text": "#일지"})
        res = brain.note_append_action(self.vault, {"action": "tag", "path": rel, "text": "일지"})  # 중복
        tags = res["note"]["frontmatter"]["tags"]
        self.assertEqual(tags.count("일지"), 1)
        # 12개 한도
        for i in range(20):
            res = brain.note_append_action(self.vault, {"action": "tag", "path": rel, "text": f"태그{i}"})
        self.assertLessEqual(len(res["note"]["frontmatter"]["tags"]), 12)

    # ── 검증 ──────────────────────────────────────────────────────────
    def test_rejects_traversal_path(self):
        with self.assertRaises(brain.BrainError):
            brain.note_append_action(self.vault, {"action": "memo", "path": "../etc/passwd.md", "text": "x"})

    def test_rejects_brain_md_and_inbox_md(self):
        with self.assertRaises(brain.BrainError):
            brain.note_append_action(self.vault, {"action": "memo", "path": "BRAIN.md", "text": "x"})
        with self.assertRaises(brain.BrainError):
            brain.note_append_action(self.vault, {"action": "memo", "path": "inbox.md", "text": "x"})

    def test_rejects_empty_text(self):
        rel = self._note("note", "빈텍스트")
        with self.assertRaises(brain.BrainError):
            brain.note_append_action(self.vault, {"action": "memo", "path": rel, "text": "   "})

    def test_rejects_unknown_action(self):
        rel = self._note("note", "알수없는액션")
        with self.assertRaises(brain.BrainError):
            brain.note_append_action(self.vault, {"action": "delete", "path": rel, "text": "x"})

    # ── HTTP ──────────────────────────────────────────────────────────
    def test_api_note_append(self):
        rel = self._note("note", "API테스트")
        srv = brain.make_server(self.vault, 0, quiet=True)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/session", timeout=5) as r:
                tok = json.loads(r.read().decode("utf-8"))["token"]
            payload = json.dumps({"action": "memo", "path": rel, "text": "API로 남긴 메모"}).encode("utf-8")
            req = urllib.request.Request(base + "/api/note-append", data=payload, method="POST",
                                         headers={"Content-Type": "application/json", "X-Brain-Token": tok})
            with urllib.request.urlopen(req, timeout=10) as r:
                res = json.loads(r.read().decode("utf-8"))
            self.assertTrue(res["ok"])
            self.assertIn("API로 남긴 메모", res["note"]["body"])
            # 토큰 없이 요청 → 403
            req2 = urllib.request.Request(base + "/api/note-append", data=payload, method="POST",
                                          headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req2, timeout=10)
            self.assertEqual(ctx.exception.code, 403)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
