"""agents 명령: plist 생성 내용, dry-run, 임시 LaunchAgents 폴더에 설치(launchctl은 실패해도 파일·위젯은 남음), status, remove."""
import contextlib
import io
import json
import os
import plistlib
import re
import socket
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


class AgentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_LAUNCH_AGENTS", "CLAUDE_CONFIG_DIR", "SECOND_BRAIN_NO_LAUNCHCTL")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.home / ".claude-x")
        os.environ["SECOND_BRAIN_NO_LAUNCHCTL"] = "1"  # 실제 launchd를 건드리지 않음
        self.agents = self.home / "agents"
        self.agents.mkdir()
        os.environ["SECOND_BRAIN_LAUNCH_AGENTS"] = str(self.agents)
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.home / "brain")}), encoding="utf-8")

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_plist_content(self):
        data, log = brain.agent_plist("brief", brain.AGENT_SPECS["brief"], brain_path="/x/brain.py", python="/usr/bin/python3")
        d = plistlib.loads(data)
        self.assertEqual(d["Label"], "com.secondbrain.brief")
        self.assertEqual(d["ProgramArguments"], ["/usr/bin/python3", "/x/brain.py", "brief", "--kakao"])
        self.assertEqual(d["StartCalendarInterval"], {"Hour": 7, "Minute": 0})
        self.assertEqual(d["EnvironmentVariables"]["CLAUDE_CONFIG_DIR"], str(self.home / ".claude-x"))
        self.assertTrue(log.endswith("agents/brief.log"))
        d2 = plistlib.loads(brain.agent_plist("remind", brain.AGENT_SPECS["remind"])[0])
        self.assertEqual(d2["StartInterval"], 600)

    def test_dry_run_and_install_files(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(brain.main(["agents", "install", "brief", "--dry-run"]), 0)
        self.assertIn("dry", buf.getvalue())
        self.assertEqual(list(self.agents.glob("*.plist")), [])
        res = brain.agents_install(["brief", "evening"])
        self.assertEqual({r["name"] for r in res}, {"brief", "evening"})
        self.assertTrue((self.agents / "com.secondbrain.brief.plist").exists())
        self.assertTrue((self.agents / "com.secondbrain.evening.plist").exists())
        # 위젯 자동 추가
        cfg = json.loads(brain.widgets_config_path().read_text(encoding="utf-8"))
        ids = [w["id"] for w in cfg["widgets"]]
        self.assertIn("brain-brief", ids)
        self.assertIn("brain-evening", ids)
        # 두 번째 설치는 건너뜀, --force면 덮어씀
        res2 = brain.agents_install(["brief"])
        self.assertTrue(res2[0]["action"].startswith("skip"))
        res3 = brain.agents_install(["brief"], force=True)
        self.assertEqual(res3[0]["action"], "install")
        st = {r["name"]: r for r in brain.agents_status()}
        self.assertTrue(st["brief"]["installed"])
        self.assertFalse(st["remind"]["installed"])
        brain.agents_remove(["brief"])
        self.assertFalse((self.agents / "com.secondbrain.brief.plist").exists())

    def test_bad_name(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(brain.main(["agents", "install", "nothing"]), brain.EXIT_INPUT)  # main이 BrainError를 종료 코드 2로

    def test_serve_plist_keepalive(self):
        data, log = brain.agent_plist("serve", brain.AGENT_SPECS["serve"], brain_path="/x/brain.py", python="/usr/bin/python3")
        d = plistlib.loads(data)
        self.assertEqual(d["Label"], "com.secondbrain.serve")
        self.assertEqual(d["ProgramArguments"], ["/usr/bin/python3", "/x/brain.py", "serve", "--port", "7777"])
        self.assertTrue(d["RunAtLoad"])
        self.assertEqual(d["KeepAlive"], {"SuccessfulExit": False})
        self.assertNotIn("StartCalendarInterval", d)
        self.assertNotIn("StartInterval", d)
        self.assertTrue(log.endswith("agents/serve.log"))

    def test_serve_install_dry_run(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(brain.main(["agents", "install", "serve", "--dry-run"]), 0)
        self.assertIn("dry", buf.getvalue())
        self.assertEqual(list(self.agents.glob("*.plist")), [])

    def test_serve_widget_ok_pattern_matches_startup_line(self):
        # cmd_serve가 실제로 찍는 시작 줄: `url = f"http://127.0.0.1:{srv.server_address[1]}"` 그대로
        startup_line = "http://127.0.0.1:52341"
        self.assertIsNotNone(re.search(brain._SERVE_STATUS_OK, startup_line))
        self.assertIsNone(re.search(brain._SERVE_STATUS_FAIL, startup_line))
        res = brain.agents_install(["serve"])
        self.assertEqual(res[0]["name"], "serve")
        cfg = json.loads(brain.widgets_config_path().read_text(encoding="utf-8"))
        widgets = {w["id"]: w for w in cfg["widgets"]}
        self.assertIn("brain-serve", widgets)
        w = widgets["brain-serve"]
        self.assertEqual(w["title"], "대시보드 서버")
        self.assertEqual(w["team"], "운영팀")
        self.assertIsNotNone(re.search(w["status"]["ok_pattern"], startup_line))
        self.assertNotIn("stale_minutes", w["status"])  # 상주 서버는 stale 판정 없음

    def test_default_install_excludes_serve(self):
        res = brain.agents_install(list(brain.DEFAULT_AGENT_NAMES))
        self.assertNotIn("serve", {r["name"] for r in res})
        self.assertFalse((self.agents / "com.secondbrain.serve.plist").exists())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(brain.main(["agents", "install", "--dry-run", "--json"]), 0)
        out = json.loads(buf.getvalue())
        self.assertNotIn("serve", {r["name"] for r in out})

    def test_agents_status_reachable_false_on_free_port(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
        s.close()  # 바인딩 해제 직후라 이 포트엔 아무것도 안 뜬다
        st = {r["name"]: r for r in brain.agents_status(serve_port=free_port)}
        self.assertFalse(st["serve"]["reachable"])
        self.assertEqual(st["serve"]["port"], free_port)


if __name__ == "__main__":
    unittest.main()


class BackupTest(AgentsTest):
    def test_backup_zip_and_rotation(self):
        import zipfile
        from datetime import datetime
        vault = self.home / "brain"
        for d in ("notes", "decisions", "events"):
            (vault / d).mkdir(parents=True)
        (vault / "BRAIN.md").write_text("# B\n", encoding="utf-8")
        (vault / "notes" / "a.md").write_text("---\ntitle: a\n---\n# a\n", encoding="utf-8")
        (vault / ".git").mkdir()
        (vault / ".git" / "junk").write_text("x", encoding="utf-8")
        dest = self.home / "bk"
        for i in range(3):
            brain.backup_vault(vault, dest, keep=2, now=datetime(2026, 9, 28 + i, 23, 0))
        names = sorted(p.name for p in dest.glob("brain-*.zip"))
        self.assertEqual(names, ["brain-20260929.zip", "brain-20260930.zip"])  # keep=2로 가장 오래된 것 정리
        with zipfile.ZipFile(dest / "brain-20260930.zip") as z:
            self.assertEqual(sorted(z.namelist()), ["BRAIN.md", "notes/a.md"])  # .git 제외
        self.assertIn("backup", brain.AGENT_SPECS)
