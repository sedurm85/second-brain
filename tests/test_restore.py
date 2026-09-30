"""restore: 백업 zip에서 볼트 복구 — backup_vault의 역연산.
dry-run(변경 없음) · 기본은 보존 · --replace로 zip에 없는 파일 제거 ·
복구 전 안전 백업 자동 생성 · zip-slip/비볼트 zip 거부 · --list."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


class RestoreTest(unittest.TestCase):
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
        (self.vault / "notes").mkdir(parents=True)
        (self.vault / "BRAIN.md").write_text("# BRAIN\n", encoding="utf-8")
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault)}), encoding="utf-8")

        self.note_a = self.vault / "notes" / "a.md"
        self.note_b = self.vault / "notes" / "b.md"
        self.note_c = self.vault / "notes" / "c.md"
        self.a_text = "---\ntitle: a\ncreated: 2026-09-01\n---\n# a\n원본 내용\n"
        self.b_text = "---\ntitle: b\ncreated: 2026-09-01\n---\n# b\n원본 내용\n"
        self.c_text = "---\ntitle: c\ncreated: 2026-09-01\n---\n# c\n원본 내용\n"
        self.note_a.write_text(self.a_text, encoding="utf-8")
        self.note_b.write_text(self.b_text, encoding="utf-8")
        self.note_c.write_text(self.c_text, encoding="utf-8")
        self.dest = self.home / ".cache" / "second-brain" / "backups"

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_dry_run_restore_and_replace(self):
        b = brain.backup_vault(self.vault, now=datetime(2026, 9, 29, 23, 0))
        zpath = Path(b["path"])

        # 변조: b 수정, c 삭제, d 새로 추가
        self.note_b.write_text("---\ntitle: b\ncreated: 2026-09-01\n---\n# b\n수정된 내용\n", encoding="utf-8")
        self.note_c.unlink()
        note_d = self.vault / "notes" / "d.md"
        note_d.write_text("---\ntitle: d\ncreated: 2026-09-30\n---\n# d\n새 노트\n", encoding="utf-8")

        # dry-run: 계획만 보여주고 아무것도 바뀌지 않아야 함
        res = brain.restore_vault(self.vault, zpath, dry=True)
        self.assertTrue(res["dry_run"])
        self.assertIsNone(res["safety_backup"])
        self.assertIn("notes/b.md", res["overwritten"])
        self.assertIn("BRAIN.md", res["overwritten"])
        self.assertIn("notes/c.md", res["created"])
        self.assertIn("notes/d.md", res["preserved"])
        self.assertEqual(note_d.read_text(encoding="utf-8"), "---\ntitle: d\ncreated: 2026-09-30\n---\n# d\n새 노트\n")
        self.assertFalse(self.note_c.exists())
        self.assertEqual(sorted(p.name for p in self.dest.glob("brain-*.zip")), [zpath.name])  # 안전백업 안 생김

        # 실제 복구(기본: 보존)
        res2 = brain.restore_vault(self.vault, zpath, now=datetime(2026, 9, 30, 10, 15, 0))
        self.assertFalse(res2["dry_run"])
        self.assertEqual(self.note_b.read_text(encoding="utf-8"), self.b_text)  # 수정 되돌림
        self.assertTrue(self.note_c.exists())  # 삭제됐던 노트 복구
        self.assertEqual(self.note_c.read_text(encoding="utf-8"), self.c_text)
        self.assertTrue(note_d.exists())  # zip에 없던 노트는 보존
        self.assertTrue(res2["safety_backup"])
        safety = Path(res2["safety_backup"])
        self.assertTrue(safety.is_file())
        self.assertEqual(safety.name, "brain-20260930-before-restore-101500.zip")
        with zipfile.ZipFile(safety) as z:
            self.assertIn("notes/d.md", z.namelist())  # 복구 직전 상태(d 포함)를 담아야 함

        # --replace: zip에 없는 파일(notes/d.md) 제거
        res3 = brain.restore_vault(self.vault, zpath, keep_current=False,
                                   now=datetime(2026, 9, 30, 10, 20, 0))
        self.assertIn("notes/d.md", res3["removed"])
        self.assertFalse(note_d.exists())

    def test_zip_slip_rejected(self):
        bad = self.home / "evil.zip"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("BRAIN.md", "# BRAIN\n")
            z.writestr("../evil.md", "gotcha")
        with self.assertRaises(brain.BrainError):
            brain.restore_vault(self.vault, bad)
        self.assertFalse((self.vault.parent / "evil.md").exists())

    def test_non_vault_zip_rejected(self):
        bad = self.home / "notavault.zip"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("readme.txt", "hello")
        with self.assertRaises(brain.BrainError):
            brain.restore_vault(self.vault, bad)

    def test_cli_list_and_no_arg_uses_newest(self):
        b1 = brain.backup_vault(self.vault, now=datetime(2026, 9, 28, 23, 0))
        b2 = brain.backup_vault(self.vault, now=datetime(2026, 9, 29, 23, 0))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = brain.main(["restore", "--list"])
        self.assertEqual(rc, brain.EXIT_OK)
        text = out.getvalue()
        self.assertIn(Path(b1["path"]).name, text)
        self.assertIn(Path(b2["path"]).name, text)

        out2 = io.StringIO()
        with contextlib.redirect_stdout(out2):
            rc2 = brain.main(["restore", "--dry-run", "--json"])
        self.assertEqual(rc2, brain.EXIT_OK)
        data = json.loads(out2.getvalue())
        self.assertEqual(data["zip"], b2["path"])  # 인자 없으면 최신 백업 사용

    def test_cli_rejects_bad_zip(self):
        bad = self.home / "notavault.zip"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("readme.txt", "hello")
        out = io.StringIO()
        with contextlib.redirect_stderr(out):
            rc = brain.main(["restore", str(bad)])
        self.assertEqual(rc, brain.EXIT_INPUT)


if __name__ == "__main__":
    unittest.main()
