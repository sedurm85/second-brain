"""agents 명령: plist 생성 내용, dry-run, 임시 LaunchAgents 폴더에 설치(launchctl은 실패해도 파일·위젯은 남음), status, remove."""
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


class AgentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_LAUNCH_AGENTS", "CLAUDE_CONFIG_DIR")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.home / ".claude-x")
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


if __name__ == "__main__":
    unittest.main()
