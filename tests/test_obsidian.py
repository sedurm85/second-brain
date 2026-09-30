"""Obsidian 볼트 초기 설정(v0.30): `brain.py obsidian init/status` — 그래프 색·데일리노트·템플릿."""
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


class ObsidianTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault)}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _run(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = brain.main(argv)
        return code, buf.getvalue()


class ObsidianInitTest(ObsidianTestBase):
    def test_init_writes_expected_files(self):
        code, out = self._run(["obsidian", "init"])
        self.assertEqual(code, 0)
        for rel in (".obsidian/app.json", ".obsidian/core-plugins.json", ".obsidian/daily-notes.json",
                    ".obsidian/templates.json", ".obsidian/graph.json",
                    "_templates/일지.md", "_templates/결정.md", "_templates/일정.md", "_templates/사람.md"):
            self.assertTrue((self.vault / rel).is_file(), f"missing {rel}")
            self.assertIn(rel, out)

    def test_json_files_parse(self):
        self._run(["obsidian", "init"])
        for rel in (".obsidian/app.json", ".obsidian/core-plugins.json", ".obsidian/daily-notes.json",
                    ".obsidian/templates.json", ".obsidian/graph.json"):
            data = json.loads((self.vault / rel).read_text(encoding="utf-8"))
            self.assertTrue(data)

    def test_app_json_keys(self):
        self._run(["obsidian", "init"])
        app = json.loads((self.vault / ".obsidian" / "app.json").read_text(encoding="utf-8"))
        self.assertEqual(app["newFileFolderPath"], "notes")
        self.assertEqual(app["attachmentFolderPath"], "_attachments")
        self.assertFalse(app["useMarkdownLinks"])

    def test_core_plugins_is_array_and_has_key_plugins(self):
        self._run(["obsidian", "init"])
        plugins = json.loads((self.vault / ".obsidian" / "core-plugins.json").read_text(encoding="utf-8"))
        self.assertIsInstance(plugins, list)
        for must in ("daily-notes", "templates", "graph", "backlink", "file-explorer"):
            self.assertIn(must, plugins)

    def test_daily_notes_points_at_template(self):
        self._run(["obsidian", "init"])
        dn = json.loads((self.vault / ".obsidian" / "daily-notes.json").read_text(encoding="utf-8"))
        self.assertEqual(dn["template"], "_templates/일지")
        self.assertEqual(dn["format"], "YYYY-MM-DD")

    def test_graph_colorgroups_at_least_five(self):
        self._run(["obsidian", "init"])
        graph = json.loads((self.vault / ".obsidian" / "graph.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(graph["colorGroups"]), 5)
        self.assertFalse(graph["showTags"])
        self.assertTrue(graph["showOrphans"])
        for cg in graph["colorGroups"]:
            self.assertIn("query", cg)
            self.assertIn("rgb", cg["color"])

    def test_templates_have_type_frontmatter(self):
        self._run(["obsidian", "init"])
        text = (self.vault / "_templates" / "일지.md").read_text(encoding="utf-8")
        self.assertIn("type: journal", text)
        self.assertIn("created:", text)
        self.assertIn("tags: []", text)
        text = (self.vault / "_templates" / "결정.md").read_text(encoding="utf-8")
        self.assertIn("type: decision", text)

    def test_templates_excluded_from_load_notes(self):
        before = len(brain.load_notes(self.vault))
        self._run(["obsidian", "init"])
        after = brain.load_notes(self.vault)
        self.assertEqual(len(after), before)
        for n in after:
            self.assertNotIn("_templates", n.rel)

    def test_second_init_writes_nothing_new(self):
        self._run(["obsidian", "init"])
        code, out = self._run(["obsidian", "init"])
        self.assertEqual(code, 0)
        self.assertIn("건너뜀", out)
        for rel in (".obsidian/app.json", "_templates/일지.md"):
            self.assertNotRegex(out, r"작성.*" + rel.replace(".", r"\."))

    def test_force_overwrites(self):
        self._run(["obsidian", "init"])
        app_path = self.vault / ".obsidian" / "app.json"
        app_path.write_text("{}", encoding="utf-8")
        code, out = self._run(["obsidian", "init", "--force"])
        self.assertEqual(code, 0)
        app = json.loads(app_path.read_text(encoding="utf-8"))
        self.assertEqual(app["newFileFolderPath"], "notes")


class ObsidianStatusTest(ObsidianTestBase):
    def test_status_before_init(self):
        code, out = self._run(["obsidian", "status"])
        self.assertEqual(code, 0)
        self.assertIn("!! ", out)

    def test_status_after_init(self):
        self._run(["obsidian", "init"])
        code, out = self._run(["obsidian", "status"])
        self.assertEqual(code, 0)
        self.assertNotIn("!! ", out)
        self.assertIn("OK ", out)

    def test_status_json(self):
        self._run(["obsidian", "init"])
        code, out = self._run(["obsidian", "status", "--json"])
        data = json.loads(out)
        self.assertTrue(all(data["files"].values()))
        self.assertIn("is_git", data)
        self.assertIn("gitignored", data)


class ObsidianGitignoreTest(ObsidianTestBase):
    def test_gitignore_gets_workspace_line_when_git_repo_and_gitignore_exists(self):
        # init_vault(--git)이 없으므로 .git·gitignore를 직접 만든 뒤 obsidian init을 돌려본다.
        (self.vault / ".git").mkdir()
        (self.vault / ".gitignore").write_text(".trash/\n", encoding="utf-8")
        self._run(["obsidian", "init"])
        content = (self.vault / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".obsidian/workspace*.json", content)

    def test_gitignore_untouched_without_git_repo(self):
        self._run(["obsidian", "init"])
        self.assertFalse((self.vault / ".gitignore").exists())


if __name__ == "__main__":
    unittest.main()
