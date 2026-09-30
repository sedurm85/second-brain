"""메일 어댑터: 헤더 파싱, fetch 응답 파싱, 세 묶음 분류, 미설정 상태, today 연동, CLI."""
import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402
import mailer  # noqa: E402


def hdr(**kw):
    lines = []
    for k, v in kw.items():
        lines.append(f"{k.replace('_', '-')}: {v}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")


NOW = datetime(2026, 9, 30, 6, 0, tzinfo=timezone.utc)


class MailTest(unittest.TestCase):
    def test_parse_and_triage(self):
        inbox = [
            (b'1 (FLAGS () BODY[HEADER.FIELDS (...)] {x}', hdr(From="=?utf-8?b?7ISg66y07IKs?= <tax@example.com>", To="me@gmail.com", Subject="=?utf-8?b?7J6Q66OMIO2ZleyduA==?=", Date="Tue, 30 Sep 2026 12:00:00 +0900", Message_ID="<a1@example.com>")),
            (b'2 (FLAGS (\\Seen \\Flagged) ...', hdr(From="boss@corp.com", To="me@gmail.com", Subject="회의 자료 확인 부탁", Date="Mon, 29 Sep 2026 09:00:00 +0900", Message_ID="<b2@corp.com>")),
            (b'3 (FLAGS ()', hdr(From="noreply@newsletter.io", To="me@gmail.com", Subject="Weekly digest", Date="Tue, 30 Sep 2026 01:00:00 +0000", Message_ID="<n3@newsletter.io>", List_Unsubscribe="<mailto:x>")),
            (b'4 (FLAGS (\\Seen)', hdr(From="friend@example.com", To="me@gmail.com", Subject="Re: 주말 약속", Date="Sun, 28 Sep 2026 20:00:00 +0900", Message_ID="<f4@example.com>", In_Reply_To="<mine-1@gmail.com>")),
            (b'5 (FLAGS ()', hdr(From="hotel@booking.com", To="me@gmail.com", Subject="Reservation confirmed: Jeju", Date="Tue, 30 Sep 2026 02:00:00 +0000", Message_ID="<h5@booking.com>", Precedence="bulk")),
            b")",
        ]
        sent = [
            (b'1 (FLAGS (\\Seen)', hdr(From="me@gmail.com", To="friend@example.com", Subject="주말 약속", Date="Sat, 27 Sep 2026 10:00:00 +0900", Message_ID="<mine-1@gmail.com>")),
            (b'2 (FLAGS (\\Seen)', hdr(From="me@gmail.com", To="accountant@firm.com", Subject="세무 자료 문의", Date="Mon, 29 Sep 2026 10:00:00 +0900", Message_ID="<mine-2@gmail.com>")),
        ]
        ib = mailer.parse_fetch_response(inbox, "INBOX")
        self.assertEqual(len(ib), 5)
        self.assertEqual(ib[0]["from_name"], "세무사")
        self.assertEqual(ib[0]["subject"], "자료 확인")
        self.assertTrue(ib[0]["unread"])
        self.assertTrue(ib[1]["flagged"] and not ib[1]["unread"])
        st = mailer.parse_fetch_response(sent, "Sent")
        t = mailer.triage(ib, st, ["me@gmail.com"], NOW)
        self.assertEqual([m["subject"] for m in t["reply"]], ["회의 자료 확인 부탁", "자료 확인"])  # 깃발 먼저
        self.assertEqual([m["subject"] for m in t["waiting"]], ["세무 자료 문의"])  # 주말 약속은 답이 왔음
        self.assertEqual({m["subject"] for m in t["info"]}, {"Weekly digest", "Reservation confirmed: Jeju"})
        self.assertEqual(t["counts"], {"reply": 2, "waiting": 1, "info": 2})
        self.assertEqual(t["bookings"][0]["subject"], "Reservation confirmed: Jeju")
        self.assertTrue(all("_dt" not in m for m in t["reply"]))
        s = mailer.mail_sentence(dict(t, configured=True, status="ok"))
        self.assertIn("답장할 메일 2통", s)
        self.assertIn("기다리는 답 1통", s)
        self.assertEqual(mailer.mail_kakao(dict(t, configured=True, status="ok")), "메일 답장 2·대기 1")


class MailConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.home / "brain"), "calendar": {"sources": []}}), encoding="utf-8")

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_unconfigured_and_today(self):
        m = mailer.collect_mail(brain.load_config())
        self.assertFalse(m["configured"])
        self.assertIsNone(mailer.mail_sentence(m))
        self.assertEqual(mailer.mail_kakao(m), "")
        t = brain.dash_today(None, today=NOW.date(), widgets=[], agenda={"today": [], "upcoming": []})
        self.assertFalse(t["mail"]["configured"])
        self.assertNotIn("메일", t["kakao"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(brain.main(["mail", "list"]), 0)
        self.assertIn("메일 미연결", buf.getvalue())

    def test_add_requires_password_file_and_saves(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(brain.main(["mail", "add", "me@gmail.com"]), brain.EXIT_INPUT)  # 파일 없음
        pf = self.home / ".config" / "second-brain" / "mail.pass"
        pf.write_text("app-password-here\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(brain.main(["mail", "add", "me@gmail.com"]), 0)
        cfg = brain.load_config()
        self.assertEqual(cfg["mail"]["host"], "imap.gmail.com")
        self.assertEqual(cfg["mail"]["sent_folder"], "[Gmail]/Sent Mail")
        self.assertEqual(oct(pf.stat().st_mode & 0o777), "0o600")
        # 실제 접속은 실패(네트워크 없음/가짜 비밀번호) → status fail, 예외 없이
        m = mailer.collect_mail(cfg, force=True)
        self.assertTrue(m["configured"])
        self.assertEqual(m["status"], "fail")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(brain.main(["mail", "remove"]), 0)
        self.assertNotIn("mail", brain.load_config())


if __name__ == "__main__":
    unittest.main()
