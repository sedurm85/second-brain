"""직원 채용·부서 이동·퇴사: widget_action add/move/rename/remove, allow_hire 게이트, /api/office·/api/widget."""
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


class HireTest(unittest.TestCase):
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
            {"id": "marketset", "title": "마켓세트", "kind": "log", "source": "~/.local/k-skill-cron/marketset.log", "status": {"ok_pattern": "len:"}},
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

    # -- allow_hire 게이트 --------------------------------------------------

    def test_add_without_allow_hire_blocked(self):
        with self.assertRaises(brain.BrainError) as cm:
            brain.widget_action({"action": "add", "title": "새 위젯", "source": "~/.local/k-skill-cron/marketset.log"}, [])
        self.assertIn("allow_hire", str(cm.exception))

    def test_move_remove_rename_also_gated(self):
        for body in ({"action": "move", "id": "marketset", "team": "생활팀"},
                     {"action": "remove", "id": "marketset"},
                     {"action": "rename", "id": "marketset", "title": "새이름"}):
            with self.assertRaises(brain.BrainError) as cm:
                brain.widget_action(body, [])
            self.assertIn("allow_hire", str(cm.exception))

    # -- add ------------------------------------------------------------

    def test_add_widget_basic(self):
        self.write_widgets(True)
        res = brain.widget_action({"action": "add", "title": "새 자동화", "source": "~/.local/k-skill-cron/marketset.log",
                                   "kind": "log", "team": "생활팀", "ok_pattern": "len:"}, [])
        self.assertTrue(res["ok"])
        self.assertEqual(res["id"], "새-자동화")
        cfg = json.loads(self.wpath.read_text(encoding="utf-8"))
        hit = next(w for w in cfg["widgets"] if w["id"] == "새-자동화")
        self.assertEqual(hit["title"], "새 자동화")
        self.assertEqual(hit["team"], "생활팀")
        self.assertEqual(hit["status"]["ok_pattern"], "len:")

    def test_add_widget_duplicate_title_gets_unique_id(self):
        self.write_widgets(True)
        brain.widget_action({"action": "add", "title": "중복", "source": "~/.local/k-skill-cron/marketset.log"}, [])
        res2 = brain.widget_action({"action": "add", "title": "중복", "source": "~/.local/k-skill-cron/marketset.log"}, [])
        self.assertEqual(res2["id"], "중복-2")

    def test_add_widget_title_length(self):
        self.write_widgets(True)
        with self.assertRaises(brain.BrainError):
            brain.widget_action({"action": "add", "title": "", "source": "~/.local/k-skill-cron/marketset.log"}, [])
        with self.assertRaises(brain.BrainError):
            brain.widget_action({"action": "add", "title": "가" * 41, "source": "~/.local/k-skill-cron/marketset.log"}, [])

    def test_add_widget_rejects_outside_home(self):
        self.write_widgets(True)
        with self.assertRaises(brain.BrainError):
            brain.widget_action({"action": "add", "title": "밖", "source": "/etc/passwd"}, [])
        with self.assertRaises(brain.BrainError):
            brain.widget_action({"action": "add", "title": "부모", "source": "~/../etc/passwd"}, [])

    def test_add_widget_rejects_command_kind(self):
        self.write_widgets(True)
        with self.assertRaises(brain.BrainError):
            brain.widget_action({"action": "add", "title": "명령", "kind": "command", "source": "echo hi"}, [])

    def test_add_widget_rejects_bad_regex(self):
        self.write_widgets(True)
        with self.assertRaises(brain.BrainError):
            brain.widget_action({"action": "add", "title": "정규식", "source": "~/.local/k-skill-cron/marketset.log",
                                 "ok_pattern": "("}, [])

    # -- move / rename / remove ------------------------------------------

    def test_move_widget(self):
        self.write_widgets(True)
        res = brain.widget_action({"action": "move", "id": "marketset", "team": "콘텐츠팀"}, [])
        self.assertEqual(res["team"], "콘텐츠팀")
        cfg = json.loads(self.wpath.read_text(encoding="utf-8"))
        self.assertEqual(next(w for w in cfg["widgets"] if w["id"] == "marketset")["team"], "콘텐츠팀")

    def test_rename_widget(self):
        self.write_widgets(True)
        res = brain.widget_action({"action": "rename", "id": "marketset", "title": "마켓세트2"}, [])
        self.assertEqual(res["title"], "마켓세트2")
        cfg = json.loads(self.wpath.read_text(encoding="utf-8"))
        self.assertEqual(next(w for w in cfg["widgets"] if w["id"] == "marketset")["title"], "마켓세트2")

    def test_remove_widget(self):
        self.write_widgets(True)
        res = brain.widget_action({"action": "remove", "id": "marketset"}, [])
        self.assertTrue(res["ok"])
        cfg = json.loads(self.wpath.read_text(encoding="utf-8"))
        self.assertNotIn("marketset", [w["id"] for w in cfg["widgets"]])

    def test_remove_brain_prefixed_refused(self):
        self.write_widgets(True)
        with self.assertRaises(brain.BrainError) as cm:
            brain.widget_action({"action": "remove", "id": "brain-brief"}, [])
        self.assertIn("agents remove", str(cm.exception))

    def test_unknown_action_message_lists_new_actions(self):
        with self.assertRaises(brain.BrainError) as cm:
            brain.widget_action({"action": "nope"}, [])
        msg = str(cm.exception)
        for kw in ("add", "move", "rename", "remove"):
            self.assertIn(kw, msg)

    # -- HTTP / API --------------------------------------------------------

    def test_office_has_hireable_flag(self):
        srv = brain.make_server(self.vault, 0, quiet=True)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/office", timeout=5) as r:
                d = json.loads(r.read().decode("utf-8"))
            self.assertFalse(d["hireable"])
            self.write_widgets(True)
            srv.widget_cache.clear()
            with urllib.request.urlopen(base + "/api/office", timeout=5) as r:
                d = json.loads(r.read().decode("utf-8"))
            self.assertTrue(d["hireable"])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_http_add_widget_appears_in_widgets(self):
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

            res = post({"action": "add", "title": "HTTP 채용", "source": "~/.local/k-skill-cron/marketset.log", "team": "운영팀"})
            self.assertTrue(res["ok"])
            wid = res["id"]
            with urllib.request.urlopen(base + "/api/widgets", timeout=5) as r:
                ws = json.loads(r.read().decode("utf-8"))
            self.assertIn(wid, [w["id"] for w in ws])
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
