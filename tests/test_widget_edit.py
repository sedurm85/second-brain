"""직원 설정 편집: widget_action update(패턴·지연·줄·팀·제목·source·kind), CLI `widget set`, allow_hire 게이트."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


class WidgetEditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_LAUNCH_AGENTS", "SECOND_BRAIN_RUN_DRY", "SECOND_BRAIN_JOBS_DIR")}
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
        (logs / "marketset.log").write_text("len: 1\n", encoding="utf-8")
        (logs / "other.log").write_text("len: 2\n", encoding="utf-8")
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        for d in ("notes", "decisions", "projects", "people"):
            (self.vault / d).mkdir(parents=True)
        (self.vault / "BRAIN.md").write_text("# BRAIN\n", encoding="utf-8")
        (self.vault / "inbox.md").write_text("", encoding="utf-8")
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault), "calendar": {"sources": []}}), encoding="utf-8")
        self.wpath = cfgdir / "widgets.json"
        self.write_widgets(False)

    def write_widgets(self, allow_hire, extra_widgets=None):
        widgets = [
            {"id": "marketset", "title": "마켓세트", "kind": "log", "team": "생활팀",
             "source": "~/.local/k-skill-cron/marketset.log", "status": {"ok_pattern": "len:"}, "lines": 5},
            {"id": "brain-brief", "title": "비서 브리핑", "kind": "log", "source": "~/.local/k-skill-cron/marketset.log"},
        ]
        if extra_widgets:
            widgets.extend(extra_widgets)
        self.wpath.write_text(json.dumps({"allow_commands": False, "allow_run": False, "allow_hire": allow_hire,
                                          "widgets": widgets}, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _cfg_widget(self, wid="marketset"):
        cfg = json.loads(self.wpath.read_text(encoding="utf-8"))
        return next(w for w in cfg["widgets"] if w["id"] == wid)

    # -- allow_hire 게이트 --------------------------------------------------

    def test_update_without_allow_hire_blocked_via_api(self):
        with self.assertRaises(brain.BrainError) as cm:
            brain.widget_action({"action": "update", "id": "marketset", "patch": {"title": "새이름"}}, [])
        self.assertIn("allow_hire", str(cm.exception))

    def test_update_without_allow_hire_allowed_via_cli(self):
        res = brain.widget_action({"action": "update", "id": "marketset", "patch": {"title": "새이름"}}, [], cli=True)
        self.assertTrue(res["ok"])
        self.assertEqual(self._cfg_widget()["title"], "새이름")

    # -- 필드별 업데이트 ------------------------------------------------------

    def test_update_patterns_stale_lines_team_title(self):
        self.write_widgets(True)
        res = brain.widget_action({"action": "update", "id": "marketset", "patch": {
            "title": "마켓세트2", "team": "콘텐츠팀", "ok_pattern": "OK:", "fail_pattern": "FAIL:",
            "stale_minutes": 120, "lines": 8}}, [])
        self.assertTrue(res["ok"])
        hit = self._cfg_widget()
        self.assertEqual(hit["title"], "마켓세트2")
        self.assertEqual(hit["team"], "콘텐츠팀")
        self.assertEqual(hit["status"]["ok_pattern"], "OK:")
        self.assertEqual(hit["status"]["fail_pattern"], "FAIL:")
        self.assertEqual(hit["status"]["stale_minutes"], 120)
        self.assertEqual(hit["lines"], 8)
        self.assertIn("widget", res)
        self.assertEqual(res["widget"]["id"], "marketset")

    def test_update_source_changes_path(self):
        self.write_widgets(True)
        res = brain.widget_action({"action": "update", "id": "marketset",
                                   "patch": {"source": "~/.local/k-skill-cron/other.log"}}, [])
        self.assertTrue(res["ok"])
        self.assertEqual(self._cfg_widget()["source"], "~/.local/k-skill-cron/other.log")

    def test_update_kind_changes(self):
        self.write_widgets(True)
        res = brain.widget_action({"action": "update", "id": "marketset", "patch": {"kind": "markdown"}}, [])
        self.assertTrue(res["ok"])
        self.assertEqual(self._cfg_widget()["kind"], "markdown")

    def test_update_csv_fields_x_y_last(self):
        self.write_widgets(True, extra_widgets=[
            {"id": "growth", "title": "성장", "kind": "csv", "source": "~/.local/k-skill-cron/other.log"}])
        res = brain.widget_action({"action": "update", "id": "growth",
                                   "patch": {"x": "date", "y": "members", "last": 10}}, [])
        self.assertTrue(res["ok"])
        hit = self._cfg_widget("growth")
        self.assertEqual(hit["x"], "date")
        self.assertEqual(hit["y"], "members")
        self.assertEqual(hit["last"], 10)

    def test_update_json_fields_list(self):
        self.write_widgets(True, extra_widgets=[
            {"id": "flight", "title": "항공권", "kind": "json", "source": "~/.local/k-skill-cron/other.log"}])
        res = brain.widget_action({"action": "update", "id": "flight",
                                   "patch": {"fields": ["best_price", "route"]}}, [])
        self.assertTrue(res["ok"])
        self.assertEqual(self._cfg_widget("flight")["fields"], ["best_price", "route"])

    def test_update_fields_rejects_non_list(self):
        self.write_widgets(True, extra_widgets=[
            {"id": "flight", "title": "항공권", "kind": "json", "source": "~/.local/k-skill-cron/other.log"}])
        with self.assertRaises(brain.BrainError):
            brain.widget_action({"action": "update", "id": "flight", "patch": {"fields": "best_price"}}, [])

    # -- 검증 실패 ------------------------------------------------------------

    def test_update_source_outside_home_rejected(self):
        self.write_widgets(True)
        with self.assertRaises(brain.BrainError):
            brain.widget_action({"action": "update", "id": "marketset", "patch": {"source": "/etc/passwd"}}, [])

    def test_update_kind_command_rejected(self):
        self.write_widgets(True)
        with self.assertRaises(brain.BrainError):
            brain.widget_action({"action": "update", "id": "marketset", "patch": {"kind": "command"}}, [])

    def test_update_bad_regex_rejected(self):
        self.write_widgets(True)
        with self.assertRaises(brain.BrainError):
            brain.widget_action({"action": "update", "id": "marketset", "patch": {"ok_pattern": "("}}, [])

    def test_update_negative_stale_rejected(self):
        self.write_widgets(True)
        with self.assertRaises(brain.BrainError):
            brain.widget_action({"action": "update", "id": "marketset", "patch": {"stale_minutes": -5}}, [])

    def test_update_negative_lines_rejected(self):
        self.write_widgets(True)
        with self.assertRaises(brain.BrainError):
            brain.widget_action({"action": "update", "id": "marketset", "patch": {"lines": -1}}, [])

    # -- brain- 접두 에이전트 ---------------------------------------------

    def test_update_brain_prefixed_source_refused(self):
        self.write_widgets(True)
        with self.assertRaises(brain.BrainError) as cm:
            brain.widget_action({"action": "update", "id": "brain-brief",
                                 "patch": {"source": "~/.local/k-skill-cron/other.log"}}, [])
        self.assertIn("brain-brief", "brain-brief")  # sanity
        self.assertIn("에이전트", str(cm.exception))

    def test_update_brain_prefixed_kind_refused(self):
        self.write_widgets(True)
        with self.assertRaises(brain.BrainError):
            brain.widget_action({"action": "update", "id": "brain-brief", "patch": {"kind": "markdown"}}, [])

    def test_update_brain_prefixed_title_allowed(self):
        self.write_widgets(True)
        res = brain.widget_action({"action": "update", "id": "brain-brief", "patch": {"title": "비서2"}}, [])
        self.assertTrue(res["ok"])
        self.assertEqual(self._cfg_widget("brain-brief")["title"], "비서2")

    def test_update_missing_widget_raises(self):
        self.write_widgets(True)
        with self.assertRaises(brain.BrainError):
            brain.widget_action({"action": "update", "id": "nope", "patch": {"title": "x"}}, [])

    # -- CLI `widget set` ------------------------------------------------------

    def test_cli_widget_set_roundtrip(self):
        self.write_widgets(True)
        parser = brain.build_parser()
        args = parser.parse_args(["widget", "set", "marketset", "title=마켓세트3", "stale=90", "ok=OK:"])
        rc = brain.cmd_widget(args)
        self.assertEqual(rc, brain.EXIT_OK)
        hit = self._cfg_widget()
        self.assertEqual(hit["title"], "마켓세트3")
        self.assertEqual(hit["status"]["stale_minutes"], 90)
        self.assertEqual(hit["status"]["ok_pattern"], "OK:")

    def test_cli_widget_set_bypasses_allow_hire_gate(self):
        self.write_widgets(False)
        parser = brain.build_parser()
        args = parser.parse_args(["widget", "set", "marketset", "team=운영팀"])
        rc = brain.cmd_widget(args)
        self.assertEqual(rc, brain.EXIT_OK)
        self.assertEqual(self._cfg_widget()["team"], "운영팀")

    def test_cli_widget_set_unknown_key_rejected(self):
        self.write_widgets(True)
        parser = brain.build_parser()
        args = parser.parse_args(["widget", "set", "marketset", "nope=1"])
        with self.assertRaises(brain.BrainError):
            brain.cmd_widget(args)

    def test_cli_widget_set_requires_pair(self):
        self.write_widgets(True)
        parser = brain.build_parser()
        args = parser.parse_args(["widget", "set", "marketset"])
        with self.assertRaises(brain.BrainError):
            brain.cmd_widget(args)

    def test_cli_widget_show_still_works(self):
        self.write_widgets(True)
        parser = brain.build_parser()
        args = parser.parse_args(["widget", "show", "marketset"])
        rc = brain.cmd_widget(args)
        self.assertEqual(rc, brain.EXIT_OK)

    # -- HTTP / /api/office 회귀 ------------------------------------------

    def test_http_update_widget_and_office_still_fine(self):
        self.write_widgets(True)
        srv = brain.make_server(self.vault, 0, quiet=True)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/session", timeout=5) as r:
                tok = json.loads(r.read().decode("utf-8"))["token"]

            def post(body):
                req = urllib.request.Request(base + "/api/widget", data=json.dumps(body).encode("utf-8"), method="POST",
                                             headers={"Content-Type": "application/json", "X-Brain-Token": tok})
                with urllib.request.urlopen(req, timeout=10) as r:
                    return json.loads(r.read().decode("utf-8"))

            res = post({"action": "update", "id": "marketset", "patch": {"team": "HTTP팀", "lines": 3}})
            self.assertTrue(res["ok"])
            self.assertEqual(self._cfg_widget()["team"], "HTTP팀")

            with urllib.request.urlopen(base + "/api/office", timeout=5) as r:
                d = json.loads(r.read().decode("utf-8"))
            self.assertTrue(d["hireable"])
            self.assertIn("widgets", d)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
