"""신규 사용자 첫 10분 온보딩 회귀 테스트(2026-09-30 QA).

브랜드 뉴 볼트(빈 HOME)에서 help → today → doctor → init → new → search → show →
review → index → capture → task → event → journal/retro/prepare(dry-run) → lint →
export → backup → restore --list → widget list → agents status → calendar list →
notify test → reminders list 순서로 실행해 크래시(Traceback)가 없는지, --json이
유효한 JSON인지, 종료 코드가 0/2 중 기대한 값인지 확인한다.

이 QA에서 고친 두 가지의 회귀도 함께 검증한다:
- reminders list/test: 기본(꺼짐) 상태면 macOS 미리알림을 실제로 조회하지 않고
  "꺼져 있어요" 안내만 하고 EXIT_OK로 끝난다(이전엔 무조건 fetch_reminders를 불렀다).
- agents status: SECOND_BRAIN_NO_LAUNCHCTL=1 환경에서도 미설치 에이전트는
  installed=False·state="not loaded"로 일관되게 나온다(이전엔 "미설치 · loaded"
  처럼 모순된 상태를 보여줬다).
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


def _run(argv):
    """brain.main(argv)을 실행하고 (종료코드, stdout+stderr)을 돌려준다.

    brain.log()는 안내문(예: "볼트가 없어…")을 stderr로 보내므로 stdout만 보면
    놓친다. 예외가 나면(=크래시) 그대로 올린다.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = brain.main(argv)
    return rc, buf.getvalue()


class OnboardingFirstTenMinutesTest(unittest.TestCase):
    ENV_KEYS = ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_ASK_CMD",
                "SECOND_BRAIN_JOBS_DIR", "SECOND_BRAIN_LAUNCH_AGENTS", "CLAUDE_CONFIG_DIR",
                "SECOND_BRAIN_NO_LAUNCHCTL", "SECOND_BRAIN_OFFLINE")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in self.ENV_KEYS}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.home / ".claude-x")  # 실제 클로드 작업 기록과 분리
        os.environ["SECOND_BRAIN_LAUNCH_AGENTS"] = str(self.home / "LaunchAgents")
        os.environ["SECOND_BRAIN_NO_LAUNCHCTL"] = "1"
        os.environ["SECOND_BRAIN_OFFLINE"] = "1"
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ.pop("SECOND_BRAIN_ASK_CMD", None)
        os.environ.pop("SECOND_BRAIN_JOBS_DIR", None)
        self.vault = self.home / "brain"

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _json(self, argv):
        rc, out = _run(argv)
        return rc, json.loads(out)

    def test_help_grouped_and_korean(self):
        rc, out = _run([])
        self.assertEqual(rc, brain.EXIT_INPUT)
        for group in ("자주 쓰는 것", "비서", "자동화", "관리"):
            self.assertIn(group, out)
        self.assertIn("brain.py", out)

    def test_today_before_vault_no_crash(self):
        rc, out = _run(["today"])
        self.assertEqual(rc, brain.EXIT_OK)
        self.assertIn("볼트가 없어", out)

    def test_doctor_fresh_machine_no_crash(self):
        rc, out = _run(["doctor"])
        self.assertEqual(rc, brain.EXIT_INPUT)
        self.assertIn("볼트", out)
        rc2, data = self._json(["doctor", "--json"])
        self.assertEqual(rc2, brain.EXIT_INPUT)
        self.assertFalse(data["ok"])

    def test_full_first_ten_minutes_sequence(self):
        """스크립트에 나온 순서를 그대로 실행: 크래시 없이 기대한 종료 코드만 나오면 통과."""
        steps = [
            (["init"], brain.EXIT_OK),
            (["new", "--type", "idea", "--title", "첫 아이디어", "--body", "테스트"], brain.EXIT_OK),
            (["search", "아이디어"], brain.EXIT_OK),
        ]
        for argv, want in steps:
            rc, out = _run(argv)
            self.assertEqual(rc, want, f"{argv} -> {rc}\n{out}")

        notes = brain.load_notes(self.vault)
        idea_path = next(str(n.path) for n in notes if n.title == "첫 아이디어")

        steps2 = [
            (["show", idea_path], brain.EXIT_OK),
            (["review"], brain.EXIT_OK),
            (["index"], brain.EXIT_OK),
            (["capture", "빠른 메모"], brain.EXIT_OK),
            (["task", "add", "우유", "--tomorrow"], brain.EXIT_OK),
            (["task", "list"], brain.EXIT_OK),
            (["event", "todo", "2026-10-03|테스트", "여권"], brain.EXIT_OK),
            (["journal", "--dry-run"], brain.EXIT_OK),
            (["retro", "--dry-run"], brain.EXIT_OK),
            (["prepare", "--dry-run"], brain.EXIT_OK),
            (["export", "--html", str(self.home / "out.html")], brain.EXIT_OK),
            (["backup"], brain.EXIT_OK),
            (["restore", "--list"], brain.EXIT_OK),
            (["widget", "list"], brain.EXIT_OK),
            (["agents", "status"], brain.EXIT_OK),
            (["calendar", "list"], brain.EXIT_OK),
            (["notify", "test"], brain.EXIT_OK),
            (["reminders", "list"], brain.EXIT_OK),
        ]
        for argv, want in steps2:
            rc, out = _run(argv)
            self.assertEqual(rc, want, f"{argv} -> {rc}\n{out}")

        # 회귀: init 샘플 노트의 예시 문구가 더 이상 가짜 [[파일]] 링크를 만들지 않으므로
        # 신규 사용자가 아무것도 안 건드려도 lint는 깨끗해야 한다.
        rc, out = _run(["lint"])
        self.assertEqual(rc, brain.EXIT_OK, out)
        self.assertTrue((self.home / "out.html").is_file())

    def test_all_json_outputs_are_valid_json(self):
        _run(["init"])
        _run(["new", "--type", "idea", "--title", "아이디어", "--body", "x"])
        _run(["task", "add", "우유", "--tomorrow"])
        _run(["event", "todo", "2026-10-03|테스트", "여권"])
        commands = [
            ["today", "--json"],
            ["doctor", "--json"],
            ["search", "아이디어", "--json"],
            ["review", "--json"],
            ["task", "list", "--json"],
            ["event", "show", "2026-10-03|테스트", "--json"],
            ["journal", "--dry-run", "--json"],
            ["retro", "--dry-run", "--json"],
            ["prepare", "--dry-run", "--json"],
            ["lint", "--json"],
            ["backup", "--json"],
            ["restore", "--list", "--json"],
            ["widget", "list", "--json"],
            ["widgets", "--json"],
            ["agents", "status", "--json"],
            ["calendar", "list", "--json"],
            ["notify", "test", "--json"],
            ["reminders", "list", "--json"],
        ]
        for argv in commands:
            rc, out = _run(argv)
            self.assertIn(rc, (brain.EXIT_OK, brain.EXIT_INPUT), f"{argv} -> {rc}\n{out}")
            try:
                json.loads(out)
            except ValueError as e:
                self.fail(f"{argv} 의 --json 출력이 유효한 JSON이 아님: {e}\n{out}")

    def test_agents_status_not_contradictory_when_uninstalled(self):
        """회귀: SECOND_BRAIN_NO_LAUNCHCTL=1이어도 미설치 에이전트는 not loaded여야 한다."""
        for rec in brain.agents_status():
            self.assertFalse(rec["installed"])
            self.assertEqual(rec["state"], "not loaded")

    def test_reminders_disabled_by_default_does_not_call_fetch(self):
        """회귀: 미리알림을 켠 적 없으면 실제 조회(osascript) 없이 안내만 하고 끝나야 한다."""
        called = []
        orig = brain.reminders_mod.fetch_reminders

        def _spy(*a, **k):
            called.append((a, k))
            return orig(*a, **k)

        brain.reminders_mod.fetch_reminders = _spy
        try:
            rc, out = _run(["reminders", "list"])
        finally:
            brain.reminders_mod.fetch_reminders = orig
        self.assertEqual(rc, brain.EXIT_OK)
        self.assertEqual(called, [])
        self.assertIn("꺼져", out)

        rc2, data = self._json(["reminders", "test", "--json"])
        self.assertEqual(rc2, brain.EXIT_OK)
        self.assertFalse(data["enabled"])
        self.assertIsNone(data["error"])


if __name__ == "__main__":
    unittest.main()
