"""brain.py widget <action> CLI: cli=True로 allow_hire/allow_run 게이트 건너뛰기, API 경로는 그대로 게이트."""
import contextlib
import io
import json
import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


class WidgetCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT",
                                                     "SECOND_BRAIN_LAUNCH_AGENTS", "SECOND_BRAIN_RUN_DRY",
                                                     "SECOND_BRAIN_JOBS_DIR", "SECOND_BRAIN_NO_LAUNCHCTL")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ["SECOND_BRAIN_RUN_DRY"] = "1"
        os.environ["SECOND_BRAIN_NO_LAUNCHCTL"] = "1"
        os.environ["SECOND_BRAIN_JOBS_DIR"] = str(self.home / "nojobs")
        agents = self.home / "agents"
        agents.mkdir()
        os.environ["SECOND_BRAIN_LAUNCH_AGENTS"] = str(agents)
        logs = self.home / ".local" / "k-skill-cron"
        logs.mkdir(parents=True)
        (logs / "scout.log").write_text("len: 1\nrecruit scout ok\n", encoding="utf-8")
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        for d in ("notes", "decisions", "projects", "people"):
            (self.vault / d).mkdir(parents=True)
        (self.vault / "BRAIN.md").write_text("# BRAIN\n", encoding="utf-8")
        (self.vault / "inbox.md").write_text("", encoding="utf-8")
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault), "calendar": {"sources": []}}), encoding="utf-8")
        self.wpath = cfgdir / "widgets.json"
        self.write_widgets(False, False)

    def write_widgets(self, allow_hire, allow_run, extra=None):
        widgets = [
            {"id": "brain-brief", "title": "비서 브리핑", "kind": "log", "source": "~/.local/k-skill-cron/scout.log"},
        ]
        if extra:
            widgets.extend(extra)
        self.wpath.write_text(json.dumps({"allow_commands": False, "allow_run": allow_run, "allow_hire": allow_hire,
                                          "widgets": widgets}, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def run_cli(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = brain.main(argv)
        return rc, buf.getvalue()

    # -- add: CLI가 allow_hire를 건너뛰고, API 경로는 그대로 막힘 --------

    def test_cli_add_bypasses_allow_hire(self):
        rc, out = self.run_cli(["widget", "add", "채용스카우트", "~/.local/k-skill-cron/scout.log",
                                "--ok", "ok", "--fail", "fail", "--team", "채용팀"])
        self.assertEqual(rc, brain.EXIT_OK)
        self.assertIn("채용 완료", out)
        cfg = json.loads(self.wpath.read_text(encoding="utf-8"))
        hit = next(w for w in cfg["widgets"] if w["id"] == "채용스카우트")
        self.assertEqual(hit["team"], "채용팀")
        self.assertEqual(hit["status"]["ok_pattern"], "ok")

    def test_api_add_still_refused_without_allow_hire(self):
        with self.assertRaises(brain.BrainError) as cm:
            brain.widget_action({"action": "add", "title": "API직원", "source": "~/.local/k-skill-cron/scout.log"}, [])
        self.assertIn("allow_hire", str(cm.exception))

    # -- list / show ------------------------------------------------------

    def test_list_and_show_contain_new_widget(self):
        self.run_cli(["widget", "add", "채용스카우트", "~/.local/k-skill-cron/scout.log", "--team", "채용팀"])
        rc, out = self.run_cli(["widget", "list"])
        self.assertEqual(rc, brain.EXIT_OK)
        self.assertIn("채용스카우트", out)
        self.assertIn("채용팀", out)
        rc, out = self.run_cli(["widget", "show", "채용스카우트"])
        self.assertEqual(rc, brain.EXIT_OK)
        self.assertIn("채용스카우트", out)

    def test_list_filters_by_team(self):
        self.run_cli(["widget", "add", "A", "~/.local/k-skill-cron/scout.log", "--team", "팀A"])
        self.run_cli(["widget", "add", "B", "~/.local/k-skill-cron/scout.log", "--team", "팀B"])
        rc, out = self.run_cli(["widget", "list", "--team", "팀A"])
        self.assertEqual(rc, brain.EXIT_OK)
        self.assertIn("팀A", out)
        self.assertNotIn("팀B", out)

    # -- move / rename / remove -------------------------------------------

    def test_move_rename_remove(self):
        self.run_cli(["widget", "add", "채용스카우트", "~/.local/k-skill-cron/scout.log"])
        rc, out = self.run_cli(["widget", "move", "채용스카우트", "인사팀"])
        self.assertEqual(rc, brain.EXIT_OK)
        self.assertIn("인사팀", out)
        rc, out = self.run_cli(["widget", "rename", "채용스카우트", "채용 스카우트"])
        self.assertEqual(rc, brain.EXIT_OK)
        cfg = json.loads(self.wpath.read_text(encoding="utf-8"))
        hit = next(w for w in cfg["widgets"] if w["id"] == "채용스카우트")
        self.assertEqual(hit["title"], "채용 스카우트")
        self.assertEqual(hit["team"], "인사팀")
        rc, out = self.run_cli(["widget", "remove", "채용스카우트"])
        self.assertEqual(rc, brain.EXIT_OK)
        cfg = json.loads(self.wpath.read_text(encoding="utf-8"))
        self.assertNotIn("채용스카우트", [w["id"] for w in cfg["widgets"]])

    def test_remove_brain_prefixed_refused_even_with_cli(self):
        rc, out = self.run_cli(["widget", "remove", "brain-brief"])
        self.assertEqual(rc, brain.EXIT_INPUT)

    # -- pause / resume -----------------------------------------------------

    def test_pause_resume_status(self):
        self.run_cli(["widget", "add", "채용스카우트", "~/.local/k-skill-cron/scout.log"])
        rc, _ = self.run_cli(["widget", "pause", "채용스카우트"])
        self.assertEqual(rc, brain.EXIT_OK)
        rc, out = self.run_cli(["widget", "show", "채용스카우트", "--json"])
        self.assertEqual(rc, brain.EXIT_OK)
        data = json.loads(out)
        self.assertEqual(data["status"], "paused")
        rc, _ = self.run_cli(["widget", "resume", "채용스카우트"])
        self.assertEqual(rc, brain.EXIT_OK)
        rc, out = self.run_cli(["widget", "show", "채용스카우트", "--json"])
        data = json.loads(out)
        self.assertNotEqual(data["status"], "paused")

    # -- run --dry -----------------------------------------------------------

    def test_run_dry_without_allow_run(self):
        (self.home / "agents" / "com.test.scout.plist").write_bytes(plistlib.dumps({
            "Label": "com.test.scout", "ProgramArguments": ["/bin/bash", "/x/scout.sh"],
            "StandardOutPath": str(self.home / ".local/k-skill-cron/scout.log"),
        }))
        self.run_cli(["widget", "add", "채용스카우트", "~/.local/k-skill-cron/scout.log"])
        rc, out = self.run_cli(["widget", "run", "채용스카우트", "--dry"])
        self.assertEqual(rc, brain.EXIT_OK)
        self.assertIn("미리보기", out)

    # -- 검증: 홈 밖 경로 · JSON 출력 파싱 -----------------------------------

    def test_add_rejects_source_outside_home(self):
        rc, _ = self.run_cli(["widget", "add", "밖", "/etc/passwd"])
        self.assertEqual(rc, brain.EXIT_INPUT)
        cfg = json.loads(self.wpath.read_text(encoding="utf-8"))
        self.assertNotIn("밖", [w["id"] for w in cfg["widgets"]])

    def test_json_output_parses(self):
        rc, out = self.run_cli(["widget", "add", "채용스카우트", "~/.local/k-skill-cron/scout.log", "--json"])
        self.assertEqual(rc, brain.EXIT_OK)
        data = json.loads(out)
        self.assertTrue(data["ok"])
        self.assertEqual(data["action"], "add")


if __name__ == "__main__":
    unittest.main()
