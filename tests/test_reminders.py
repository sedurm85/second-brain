"""미리알림 어댑터(reminders.py)와 brain.py 연결 테스트. 실제 osascript는 부르지 않는다(가짜 스크립트로 대체)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import textwrap
import unittest
import unittest.mock
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402
import reminders  # noqa: E402

TODAY = date(2026, 9, 30)

FIXTURE_REMINDERS = [
    {"id": "r1", "title": "우유 사기", "list": "장보기", "due": "2026-09-30", "due_time": None,
     "priority": 0, "notes": "", "completed": False, "url": ""},
    {"id": "r2", "title": "보고서 제출", "list": "회사", "due": "2026-10-03", "due_time": "14:00",
     "priority": 1, "notes": "월간 보고서", "completed": False, "url": ""},
    {"id": "r3", "title": "책 읽기", "list": "개인", "due": None, "due_time": None,
     "priority": 0, "notes": "", "completed": False, "url": ""},
    {"id": "r4", "title": "완료된 일", "list": "장보기", "due": "2026-09-20", "due_time": None,
     "priority": 0, "notes": "", "completed": True, "url": ""},
]

_FIXTURE_JSON = json.dumps({"reminders": FIXTURE_REMINDERS}, ensure_ascii=False)
SUCCESS_SCRIPT = "import sys\nsys.stdout.write(" + repr(_FIXTURE_JSON) + ")\n"

PERMISSION_SCRIPT = textwrap.dedent("""
    import sys
    sys.stderr.write("execution error: 미리알림에 접근할 수 없습니다. (-1743)\\n")
    sys.exit(1)
""")


class ReminderTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in
                     ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_REMINDERS_CMD", "SECOND_BRAIN_OFFLINE")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ.pop("SECOND_BRAIN_OFFLINE", None)
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.home / "brain")}), encoding="utf-8")

        self.success_script = self.home / "fake_success.py"
        self.success_script.write_text(SUCCESS_SCRIPT, encoding="utf-8")
        self.permission_script = self.home / "fake_permission.py"
        self.permission_script.write_text(PERMISSION_SCRIPT, encoding="utf-8")
        reminders.LAST_ERROR = None

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def use_success(self):
        os.environ["SECOND_BRAIN_REMINDERS_CMD"] = f"{sys.executable} {self.success_script}"

    def use_permission_error(self):
        os.environ["SECOND_BRAIN_REMINDERS_CMD"] = f"{sys.executable} {self.permission_script}"


class FetchRemindersTest(ReminderTestBase):
    def test_fetch_excludes_completed_and_shapes_fields(self):
        self.use_success()
        items = reminders.fetch_reminders(force=True)
        self.assertEqual(len(items), 3)  # 완료된 r4는 제외
        by_id = {r["id"]: r for r in items}
        self.assertEqual(by_id["r1"]["due"], "2026-09-30")
        self.assertIsNone(by_id["r1"]["due_time"])
        self.assertEqual(by_id["r2"]["due_time"], "14:00")
        self.assertIsNone(by_id["r3"]["due"])
        self.assertNotIn("r4", by_id)
        self.assertIsNone(reminders.LAST_ERROR)

    def test_permission_error_returns_empty_and_hint(self):
        self.use_permission_error()
        items = reminders.fetch_reminders(force=True)
        self.assertEqual(items, [])
        self.assertIsNotNone(reminders.LAST_ERROR)
        self.assertIn("-1743", reminders.LAST_ERROR)
        self.assertIn("미리알림", reminders.LAST_ERROR)

    def test_cache_ttl(self):
        self.use_success()
        first = reminders.fetch_reminders(force=True)
        self.assertEqual(len(first), 3)
        mtime = reminders.cache_path().stat().st_mtime
        # 캐시가 신선한 동안은 osascript 명령이 깨져도 캐시를 그대로 쓴다
        self.use_permission_error()
        cached = reminders.fetch_reminders()  # force=False
        self.assertEqual(len(cached), 3)
        self.assertIsNone(reminders.LAST_ERROR)
        # TTL을 넘기면 다시 조회를 시도하고(깨진 스크립트라 실패) 실패로 남는다
        with unittest.mock.patch.object(reminders.time, "time", return_value=mtime + reminders.CACHE_SEC + 5):
            stale = reminders.fetch_reminders()
        self.assertEqual(stale, [])
        self.assertIsNotNone(reminders.LAST_ERROR)

    def test_lists_filter(self):
        self.use_success()
        items = reminders.fetch_reminders(lists=["회사"], force=True)
        self.assertEqual([r["id"] for r in items], ["r2"])


class ReminderConfigCliTest(ReminderTestBase):
    def test_on_off_cli(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(brain.main(["reminders", "on", "--lists", "장보기,회사"]), brain.EXIT_OK)
        cfg = brain.load_config()
        self.assertEqual(cfg["reminders"], {"enabled": True, "lists": ["장보기", "회사"]})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(brain.main(["reminders", "off"]), brain.EXIT_OK)
        cfg = brain.load_config()
        self.assertFalse(cfg["reminders"]["enabled"])

    def test_cli_test_action_reports_count_or_error(self):
        self.use_success()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(brain.main(["reminders", "on"]), brain.EXIT_OK)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(brain.main(["reminders", "test", "--json"]), brain.EXIT_OK)
        out = json.loads(buf.getvalue())
        self.assertEqual(out["count"], 3)
        self.assertIsNone(out["error"])

    def test_cli_test_action_permission_error(self):
        self.use_permission_error()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(brain.main(["reminders", "on"]), brain.EXIT_OK)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = brain.main(["reminders", "test", "--json"])
        self.assertEqual(code, brain.EXIT_INPUT)
        out = json.loads(buf.getvalue())
        self.assertIn("-1743", out["error"])


class DashTasksIntegrationTest(ReminderTestBase):
    def setUp(self):
        super().setUp()
        cfg = brain.load_config()
        cfg["reminders"] = {"enabled": True, "lists": []}
        brain.save_config(cfg)

    def test_dash_tasks_buckets_reminders_and_counts(self):
        self.use_success()
        b = brain.dash_tasks(None, today=TODAY, widgets=[], agenda={})
        rem_today = [x for x in b["today"] if x.get("kind") == "reminder"]
        rem_week = [x for x in b["week"] if x.get("kind") == "reminder"]
        rem_someday = [x for x in b["someday"] if x.get("kind") == "reminder"]
        self.assertEqual([x["text"] for x in rem_today], ["우유 사기"])
        self.assertEqual([x["text"] for x in rem_week], ["보고서 제출"])
        self.assertEqual([x["text"] for x in rem_someday], ["책 읽기"])
        self.assertEqual(b["counts"]["today"], 1)
        self.assertEqual(b["counts"]["week"], 1)
        self.assertEqual(b["counts"]["someday"], 1)
        self.assertEqual(rem_today[0]["source"], "장보기")
        self.assertEqual(rem_today[0]["rid"], "r1")

    def test_disabled_means_no_fetch(self):
        cfg = brain.load_config()
        cfg["reminders"]["enabled"] = False
        brain.save_config(cfg)
        with unittest.mock.patch.object(brain.reminders_mod, "fetch_reminders") as mock_fetch:
            out = brain.reminder_tasks(brain.load_config(), TODAY)
        mock_fetch.assert_not_called()
        self.assertEqual(out, [])

    def test_task_action_rejects_check_on_reminder(self):
        vault = self.home / "brain"
        vault.mkdir(parents=True, exist_ok=True)
        (vault / "inbox.md").write_text("# inbox\n\n", encoding="utf-8")
        with self.assertRaises(brain.BrainError) as ctx:
            brain.task_action(vault, {"action": "check", "kind": "reminder", "rid": "r1", "done": True}, today=TODAY)
        self.assertIn("미리알림 앱", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
