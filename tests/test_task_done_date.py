"""체크할 때 @done(날짜)가 붙고, 일지는 '오늘 완료'만 넣는다(날짜 없는 옛 체크·지난 완료는 제외)."""
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


class TaskDoneDateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        h = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_JOBS_DIR", "SECOND_BRAIN_OFFLINE")}
        os.environ.update(HOME=str(h), XDG_CACHE_HOME=str(h / ".cache"), SECOND_BRAIN_JOBS_DIR=str(h / "nojobs"), SECOND_BRAIN_OFFLINE="1")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        (h / ".config" / "second-brain").mkdir(parents=True)
        self.vault = h / "brain"
        (h / ".config" / "second-brain" / "config.json").write_text(json.dumps({"vault": str(self.vault), "calendar": {"sources": []}}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])
        inbox = self.vault / "inbox.md"
        inbox.write_text(inbox.read_text(encoding="utf-8") + "- [x] 옛날에 끝낸 여행 준비 @due(2026-10-02)\n- [ ] 오늘 끝낼 일\n", encoding="utf-8")

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.tmp.cleanup()

    def test_check_adds_done_date_and_journal_uses_today_only(self):
        today = date.today()
        t = [x for x in brain.parse_tasks(self.vault, today) if x["text"] == "오늘 끝낼 일"][0]
        brain.task_action(self.vault, {"action": "check", "line": t["line"], "done": True}, today)
        self.assertIn(f"@done({today.isoformat()})", (self.vault / "inbox.md").read_text(encoding="utf-8"))
        mat = brain.journal_material(self.vault, today, widgets=[], agenda={"today": [], "upcoming": []})
        self.assertEqual(mat["tasks_done"], ["오늘 끝낼 일"])  # 날짜 없는 옛 체크는 매일 다시 나오지 않음
        later = brain.journal_material(self.vault, today + timedelta(days=1), widgets=[], agenda={"today": [], "upcoming": []})
        self.assertEqual(later["tasks_done"], [])
        # 체크 해제하면 @done 제거
        brain.task_action(self.vault, {"action": "check", "line": t["line"], "done": False}, today)
        self.assertNotIn("@done(", (self.vault / "inbox.md").read_text(encoding="utf-8").split("오늘 끝낼 일")[1].split("\n")[0])


if __name__ == "__main__":
    unittest.main()
