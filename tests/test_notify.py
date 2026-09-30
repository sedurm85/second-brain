"""notify(): 카톡 헬퍼 → 없으면 macOS 알림 센터 폴백. DRY/LOG 환경변수, osascript 이스케이프, --kakao/--notify CLI 통합."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


class NotifyUnitTest(unittest.TestCase):
    """notify() 자체 동작(파일시스템 헬퍼 유무·환경변수)만 본다. 실제 osascript/카톡 호출 없음."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in
                     ("HOME", "SECOND_BRAIN_NOTIFY_DRY", "SECOND_BRAIN_NOTIFY_LOG")}
        os.environ["HOME"] = str(self.home)
        os.environ["SECOND_BRAIN_NOTIFY_DRY"] = "1"
        os.environ.pop("SECOND_BRAIN_NOTIFY_LOG", None)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _cfg(self, **extra):
        return dict(extra)

    def test_dry_no_helper_darwin_falls_back_to_center(self):
        with mock.patch.object(brain.sys, "platform", "darwin"):
            res = brain.notify("안녕", cfg=self._cfg())
        self.assertTrue(res["sent"])
        self.assertEqual([c["name"] for c in res["channels"]], ["center"])
        self.assertTrue(res["channels"][0]["ok"])

    def test_dry_kakao_helper_present_goes_first(self):
        helper = self.home / "notify_kakao.py"
        helper.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        with mock.patch.object(brain.sys, "platform", "darwin"):
            res = brain.notify("안녕", cfg=self._cfg(kakao_cmd=str(helper)))
        # kakao가 먼저(그리고 이 경우 유일하게) 시도된다 — center로 중복 발송하지 않음
        self.assertEqual([c["name"] for c in res["channels"]], ["kakao"])
        self.assertTrue(res["sent"])

    def test_notify_channels_center_only_skips_kakao(self):
        helper = self.home / "notify_kakao.py"
        helper.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        with mock.patch.object(brain.sys, "platform", "darwin"):
            res = brain.notify("안녕", cfg=self._cfg(kakao_cmd=str(helper), notify_channels=["center"]))
        self.assertEqual([c["name"] for c in res["channels"]], ["center"])

    def test_notify_channels_kakao_only_skips_center(self):
        # 헬퍼가 없어도 notify_channels가 kakao뿐이면 center로 넘어가지 않는다
        with mock.patch.object(brain.sys, "platform", "darwin"):
            res = brain.notify("안녕", cfg=self._cfg(notify_channels=["kakao"]))
        self.assertEqual(res["channels"], [])
        self.assertFalse(res["sent"])

    def test_non_darwin_skips_center(self):
        with mock.patch.object(brain.sys, "platform", "linux"):
            res = brain.notify("안녕", cfg=self._cfg())
        self.assertEqual(res["channels"], [])
        self.assertFalse(res["sent"])

    def test_notify_center_false_disables_center(self):
        with mock.patch.object(brain.sys, "platform", "darwin"):
            res = brain.notify("안녕", cfg=self._cfg(notify_center=False))
        self.assertEqual(res["channels"], [])
        self.assertFalse(res["sent"])

    def test_notify_log_receives_one_line_per_attempt(self):
        logp = self.home / "notify.log"
        os.environ["SECOND_BRAIN_NOTIFY_LOG"] = str(logp)
        with mock.patch.object(brain.sys, "platform", "darwin"):
            brain.notify("이것은 로그로 남는 문장입니다", cfg=self._cfg())
        lines = logp.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        channel, ok, text = lines[0].split("\t", 2)
        self.assertEqual(channel, "center")
        self.assertEqual(ok, "True")
        self.assertEqual(text, "이것은 로그로 남는 문장입니다"[:80])

    def test_osa_quote_escapes_quotes_and_backslashes(self):
        self.assertEqual(brain._osa_quote('그가 "안녕"이라 했다'), '그가 \\"안녕\\"이라 했다')
        self.assertEqual(brain._osa_quote("경로 C:\\temp"), "경로 C:\\\\temp")
        self.assertEqual(brain._osa_quote('mix "a\\b"'), 'mix \\"a\\\\b\\"')


class NotifyCliTest(unittest.TestCase):
    """cmd_brief --kakao 경로에서 notify()가 실제로 물려 들어가는지(가짜 헬퍼로, 실제 카톡/알림 센터 호출 없이)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in
                     ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_NOTIFY_DRY", "SECOND_BRAIN_NOTIFY_LOG")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ.pop("SECOND_BRAIN_NOTIFY_DRY", None)
        os.environ.pop("SECOND_BRAIN_NOTIFY_LOG", None)
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        self.cfg_path = cfgdir / "config.json"
        self.cfg_path.write_text(json.dumps({"vault": str(self.vault), "calendar": {"sources": []}}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _set_kakao_cmd(self, helper_path):
        cfg = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        cfg["kakao_cmd"] = str(helper_path)
        self.cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    def test_brief_kakao_with_working_helper_succeeds(self):
        helper = self.home / "notify_kakao.py"
        helper.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        self._set_kakao_cmd(helper)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = brain.main(["brief", "--kakao"])
        self.assertEqual(rc, 0)
        self.assertIn("알림 발송 완료", buf.getvalue())

    def test_brief_notify_alias_behaves_like_kakao(self):
        helper = self.home / "notify_kakao.py"
        helper.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        self._set_kakao_cmd(helper)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = brain.main(["brief", "--notify"])
        self.assertEqual(rc, 0)
        self.assertIn("알림 발송 완료", buf.getvalue())

    def test_brief_kakao_no_helper_dry_falls_back_to_center_on_darwin(self):
        os.environ["SECOND_BRAIN_NOTIFY_DRY"] = "1"
        buf = io.StringIO()
        with mock.patch.object(brain.sys, "platform", "darwin"), \
             contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = brain.main(["brief", "--kakao"])
        self.assertEqual(rc, 0)
        self.assertIn("알림 발송 완료", buf.getvalue())

    def test_brief_kakao_no_channel_available_reports_and_fails(self):
        # 헬퍼도 없고 darwin도 아니면 알릴 채널이 하나도 없다
        buf = io.StringIO()
        with mock.patch.object(brain.sys, "platform", "linux"), \
             contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()) as err:
            rc = brain.main(["brief", "--kakao"])
        self.assertEqual(rc, 2)
        self.assertIn("알림 채널이 없어요", err.getvalue())

    def test_notify_test_command_dry(self):
        os.environ["SECOND_BRAIN_NOTIFY_DRY"] = "1"
        buf = io.StringIO()
        with mock.patch.object(brain.sys, "platform", "darwin"), \
             contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = brain.main(["notify", "test", "안녕하세요"])
        self.assertEqual(rc, 0)
        self.assertIn("center", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
