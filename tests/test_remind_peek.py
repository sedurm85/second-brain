"""화면 내 알림(v0.30): /api/remind-peek — 보드·코어가 카톡·macOS 알림 없이도 직접 물어보는 읽기 전용 조회.
cmd_remind가 쓰는 sent-log(reminded.json)는 이 엔드포인트가 절대 건드리지 않는다(마킹은 클라이언트 localStorage 몫)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import agenda  # noqa: E402
import brain  # noqa: E402


class RemindPeekTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_OFFLINE")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ["SECOND_BRAIN_OFFLINE"] = "1"  # 날씨 등 네트워크 금지

        # 서버는 agenda_cache에 now를 주입받을 방법이 없으므로(실제 datetime.now() 사용),
        # 실제 지금 시각을 기준으로 20분 뒤 시작하는 일정을 ICS에 심는다(events_before=30분 안).
        start = datetime.now(timezone.utc) + timedelta(minutes=20)
        end = start + timedelta(minutes=30)
        ics = ("BEGIN:VCALENDAR\n"
               "BEGIN:VEVENT\n"
               "UID:peek1\n"
               f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}\n"
               f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}\n"
               "SUMMARY:치과 예약\n"
               "LOCATION:2층\n"
               "END:VEVENT\n"
               "END:VCALENDAR\n")
        (self.home / "cal.ics").write_text(ics, encoding="utf-8")
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        (cfgdir / "config.json").write_text(json.dumps({
            "vault": str(self.vault),
            "calendar": {"sources": [{"kind": "ics", "name": "t", "path": str(self.home / "cal.ics")}]},
        }), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _serve(self):
        srv = brain.make_server(self.vault, 0, quiet=True)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        return srv, f"http://127.0.0.1:{srv.server_address[1]}"

    def test_peek_returns_upcoming_event_twice_without_marking_sent(self):
        srv, base = self._serve()
        try:
            with urllib.request.urlopen(base + "/api/remind-peek", timeout=5) as r:
                d = json.loads(r.read().decode("utf-8"))
            self.assertEqual(set(d.keys()), {"due", "tasks_due", "now"})
            hit = next((x for x in d["due"] if "치과 예약" in x["text"]), None)
            self.assertIsNotNone(hit)
            self.assertTrue(hit["id"].startswith("event|"))

            # 두 번째 호출에서도 여전히 같은 항목이 온다: 서버는 아무것도 sent로 표시하지 않는다(읽기 전용 peek).
            with urllib.request.urlopen(base + "/api/remind-peek", timeout=5) as r:
                d2 = json.loads(r.read().decode("utf-8"))
            hit2 = next((x for x in d2["due"] if "치과 예약" in x["text"]), None)
            self.assertIsNotNone(hit2)
            self.assertEqual(hit["id"], hit2["id"])

            # launchd remind가 쓰는 sent-log(reminded.json)는 이 엔드포인트가 절대 만들거나 건드리지 않는다.
            sent_log = agenda.cache_dir() / "reminded.json"
            self.assertFalse(sent_log.exists())
        finally:
            srv.shutdown()
            srv.server_close()

    def test_task_due_today_included_once(self):
        today_iso = datetime.now().date().isoformat()
        (self.vault / "inbox.md").write_text(
            "# inbox\n\n- [ ] 세금 신고 @due(" + today_iso + ")\n- [ ] 아직 안 된 일 @due(2099-01-01)\n",
            encoding="utf-8")
        srv, base = self._serve()
        try:
            with urllib.request.urlopen(base + "/api/remind-peek", timeout=5) as r:
                d = json.loads(r.read().decode("utf-8"))
            matches = [t for t in d["tasks_due"] if t["text"] == "세금 신고"]
            self.assertEqual(len(matches), 1)
            self.assertTrue(matches[0]["id"].startswith("task:"))
            self.assertEqual(matches[0]["due"], today_iso)
            # 마감이 먼 할 일은 포함되지 않는다
            self.assertFalse(any(t["text"] == "아직 안 된 일" for t in d["tasks_due"]))
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
