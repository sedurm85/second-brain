"""v0.3 비서 모드 테스트 — 위젯(widgets.json)·/api/widgets·/api/today·today CLI (표준 unittest)."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402

TODAY = date(2026, 9, 30)


class HomeCase(unittest.TestCase):
    """임시 HOME. 실제 ~/brain·~/.config는 건드리지 않는다."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = Path(os.path.realpath(self.tmp))
        self._old = {k: os.environ.get(k) for k in ("HOME", "SECOND_BRAIN_VAULT")}
        os.environ["HOME"] = str(self.home)
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        self.data = self.home / ".local" / "state"
        self.data.mkdir(parents=True)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, name, text, age_minutes=0):
        p = self.data / name
        p.write_text(text, encoding="utf-8")
        if age_minutes:
            t = time.time() - age_minutes * 60
            os.utime(p, (t, t))
        return p

    def src(self, name):
        return "~/.local/state/" + name

    def write_config(self, widgets, allow=False):
        p = brain.widgets_config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"allow_commands": allow, "widgets": widgets}, ensure_ascii=False),
                     encoding="utf-8")

    def ev(self, w, allow=False):
        return brain.evaluate_widget(w, 0, allow)


class ConfigTest(HomeCase):
    def test_no_config_empty_list(self):
        self.assertEqual(brain.load_widgets_config()["widgets"], [])
        self.assertEqual(brain.collect_widgets(), [])

    def test_init_widgets_no_overwrite(self):
        p, created = brain.init_widgets_config()
        self.assertTrue(created)
        self.assertEqual(p, self.home / ".config" / "second-brain" / "widgets.json")
        cfg = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(len(cfg["widgets"]), 5)
        self.assertFalse(cfg["allow_commands"])
        p.write_text('{"widgets": []}', encoding="utf-8")
        _, created2 = brain.init_widgets_config()
        self.assertFalse(created2)
        self.assertEqual(p.read_text(encoding="utf-8"), '{"widgets": []}')

    def test_broken_config_is_empty(self):
        p = brain.widgets_config_path()
        p.parent.mkdir(parents=True)
        p.write_text("{깨짐", encoding="utf-8")
        cfg = brain.load_widgets_config()
        self.assertEqual(cfg["widgets"], [])
        self.assertTrue(cfg["error"])


class PathTest(HomeCase):
    def test_outside_home_rejected(self):
        r = self.ev({"id": "x", "kind": "log", "source": "/etc/hosts"})
        self.assertEqual(r["status"], "missing")
        self.assertIn("error", r)

    def test_dotdot_rejected(self):
        r = self.ev({"id": "x", "kind": "log", "source": "~/.local/../.local/state/a.log"})
        self.assertEqual(r["status"], "missing")
        self.assertIn("..", r["error"])

    def test_symlink_outside_rejected(self):
        outside = tempfile.mkdtemp()
        try:
            (Path(outside) / "s.log").write_text("ok\n", encoding="utf-8")
            os.symlink(Path(outside) / "s.log", self.data / "link.log")
            r = self.ev({"id": "x", "kind": "log", "source": self.src("link.log")})
            self.assertEqual(r["status"], "missing")
            self.assertIn("error", r)
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_missing_file(self):
        r = self.ev({"id": "x", "kind": "json", "source": self.src("nope.json")})
        self.assertEqual(r["status"], "missing")
        self.assertNotIn("error", r)


class LogTest(HomeCase):
    ST = {"ok_pattern": "len:", "fail_pattern": "Traceback|Error", "stale_minutes": 60}

    def w(self, **kw):
        return dict({"id": "m", "title": "마켓", "kind": "log", "source": self.src("m.log"),
                     "status": self.ST, "lines": 3}, **kw)

    def test_ok(self):
        self.put("m.log", "a\nb\nsent len: 120\n")
        r = self.ev(self.w())
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["data"]["lines"], ["a", "b", "sent len: 120"])
        self.assertEqual(r["summary"], "sent len: 120")
        self.assertIsNotNone(r["updated_at"])
        self.assertEqual(set(r), {"id", "title", "kind", "state", "team", "source", "status_cfg", "status", "updated_at", "age_minutes",
                                  "summary", "data"})

    def test_fail_beats_ok(self):
        self.put("m.log", "len: 1\nTraceback (most recent call last)\nValueError: x\n")
        self.assertEqual(self.ev(self.w())["status"], "fail")

    def test_only_last_n_lines(self):
        self.put("m.log", "Traceback old\n1\n2\nlen: 3\n")
        self.assertEqual(self.ev(self.w())["status"], "ok")

    def test_unknown_when_no_pattern(self):
        self.put("m.log", "hello\n")
        self.assertEqual(self.ev(self.w())["status"], "unknown")

    def test_stale_priority(self):
        self.put("m.log", "Traceback\n", age_minutes=120)
        r = self.ev(self.w())
        self.assertEqual(r["status"], "stale")
        self.assertGreaterEqual(r["age_minutes"], 119)


class JsonCsvMarkdownTest(HomeCase):
    def test_json_dotted_fields(self):
        self.put("f.json", json.dumps({"best": {"price": 312000, "route": ["ICN", "BKK"]},
                                       "checked_at": "2026-09-30"}))
        r = self.ev({"id": "f", "kind": "json", "source": self.src("f.json"),
                     "fields": ["best.price", "best.route.1", "checked_at"]})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["data"]["fields"], {"best.price": 312000, "best.route.1": "BKK",
                                               "checked_at": "2026-09-30"})
        self.assertIn("best.price: 312000", r["summary"])

    def test_json_missing_field_warn_and_broken(self):
        self.put("f.json", '{"a": 1}')
        r = self.ev({"id": "f", "kind": "json", "source": self.src("f.json"), "fields": ["a", "b.c"]})
        self.assertEqual(r["status"], "warn")
        self.assertIsNone(r["data"]["fields"]["b.c"])
        self.put("g.json", "{not json")
        self.assertEqual(self.ev({"id": "g", "kind": "json", "source": self.src("g.json")})["status"],
                         "warn")

    def test_csv_numbers_and_spark(self):
        rows = ["date,members,memo"]
        start = date(2026, 8, 1)
        for i in range(40):
            rows.append(f"{(start + timedelta(days=i)).isoformat()},{100 + i},x")
        rows.insert(5, "2026-08-04,N/A,깨진행")
        rows.append("2026-09-10,\"1,234\",쉼표")
        self.put("g.csv", "\n".join(rows) + "\n")
        r = self.ev({"id": "g", "kind": "csv", "source": self.src("g.csv"), "x": "date",
                     "y": "members", "last": 10})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["data"]["columns"], ["date", "members"])
        self.assertEqual(len(r["data"]["rows"]), 10)
        self.assertEqual(r["data"]["rows"][-1], ["2026-09-10", 1234])
        self.assertTrue(all(isinstance(y, (int, float)) for _, y in r["data"]["rows"]))
        self.assertTrue(r["summary"].startswith("최근 1,234 / 30일 변화"))

    def test_csv_30day_change(self):
        self.put("g.csv", "date,m\n2026-08-01,10\n2026-09-01,50\n2026-09-30,80\n")
        r = self.ev({"id": "g", "kind": "csv", "source": self.src("g.csv"), "x": "date", "y": "m"})
        self.assertEqual(r["summary"], "최근 80 / 30일 변화 +30")

    def test_csv_bad_column(self):
        self.put("g.csv", "a,b\n1,2\n")
        r = self.ev({"id": "g", "kind": "csv", "source": self.src("g.csv"), "x": "date", "y": "m"})
        self.assertEqual(r["status"], "warn")

    def test_markdown_first_lines(self):
        self.put("j.md", "# 채용 스카우트 결과\n\n- A사 보안팀장\n- B사 CISO\n- C사\n")
        r = self.ev({"id": "j", "kind": "markdown", "source": self.src("j.md"), "lines": 3})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["data"]["text"], "# 채용 스카우트 결과\n\n- A사 보안팀장")
        self.assertEqual(r["summary"], "채용 스카우트 결과")


class CommandTest(HomeCase):
    def test_disabled_unknown(self):
        r = self.ev({"id": "d", "kind": "command", "source": "echo hi"}, allow=False)
        self.assertEqual(r["status"], "unknown")
        self.assertEqual(r["summary"], "명령 실행 비활성(allow_commands)")
        self.assertEqual(r["data"], {"stdout": "", "exit_code": None})

    def test_ok_fail_truncate(self):
        r = self.ev({"id": "d", "kind": "command", "source": "echo 안녕"}, allow=True)
        self.assertEqual((r["status"], r["data"]["exit_code"], r["summary"]), ("ok", 0, "안녕"))
        r = self.ev({"id": "d", "kind": "command", "source": "echo x; exit 3"}, allow=True)
        self.assertEqual((r["status"], r["data"]["exit_code"]), ("fail", 3))
        r = self.ev({"id": "d", "kind": "command",
                     "source": "python3 -c \"print('a'*10000)\""}, allow=True)
        self.assertLessEqual(len(r["data"]["stdout"].encode("utf-8")), 4096)
        self.assertLessEqual(len(r["summary"]), 200)

    def test_timeout_fail(self):
        r = self.ev({"id": "d", "kind": "command", "source": "sleep 5", "timeout_sec": 1}, allow=True)
        self.assertEqual(r["status"], "fail")
        self.assertIn("시간 초과", r["summary"])


class CacheTest(HomeCase):
    def test_cache_hit_until_mtime_or_ttl(self):
        p = self.put("m.log", "len: 1\n", age_minutes=5)
        w = {"id": "m", "kind": "log", "source": self.src("m.log"), "status": {"ok_pattern": "len:"}}
        c = brain.WidgetCache()
        now = [1000.0]
        clock = lambda: now[0]  # noqa: E731
        c.get(w, 0, False, clock)
        c.get(w, 0, False, clock)
        self.assertEqual(c.parses, 1)  # mtime 불변 → 재파싱 없음
        p.write_text("Traceback\n", encoding="utf-8")
        t = time.time()
        os.utime(p, (t, t))
        self.assertEqual(c.get(w, 0, False, clock)["status"], "unknown")
        self.assertEqual(c.parses, 2)  # mtime 변경 → 재파싱
        now[0] += 61
        c.get(w, 0, False, clock)
        self.assertEqual(c.parses, 3)  # 60초 경과 → 재파싱


class TodayTest(HomeCase):
    def setUp(self):
        super().setUp()
        self.vault = self.home / "brain"
        for d in ("notes", "decisions", "projects", "people"):
            (self.vault / d).mkdir(parents=True)
        (self.vault / "BRAIN.md").write_text("# BRAIN\n", encoding="utf-8")
        (self.vault / "inbox.md").write_text(
            "# inbox\n\n- [ ] 항공권 확인\n- [x] 끝난 일\n  - [ ] 세금 신고\n일반 줄\n", encoding="utf-8")
        mk = lambda *a, **kw: brain.create_note(self.vault, *a, **kw)  # noqa: E731
        real = date.today()
        self.real = real
        mk("decision", "곧 볼 결정", created=real.isoformat(), status="open",
           revisit=(real + timedelta(days=3)).isoformat())
        mk("decision", "지난 결정", created=(real - timedelta(days=20)).isoformat(), status="open",
           revisit=(real - timedelta(days=1)).isoformat())
        mk("decision", "먼 결정", created=(real - timedelta(days=20)).isoformat(), status="open",
           revisit=(real + timedelta(days=30)).isoformat())
        mk("decision", "끝난 결정", created=(real - timedelta(days=20)).isoformat(), status="decided",
           revisit=(real + timedelta(days=1)).isoformat())
        mk("note", "이번 주 노트", created=real.isoformat())
        self.put("ok.log", "len: 1\n")
        self.put("bad.log", "Error: boom\n")
        self.write_config([
            {"id": "ok", "title": "정상", "kind": "log", "source": self.src("ok.log"),
             "status": {"ok_pattern": "len:", "fail_pattern": "Error"}},
            {"id": "bad", "title": "고장", "kind": "log", "source": self.src("bad.log"),
             "status": {"ok_pattern": "len:", "fail_pattern": "Error"}},
            {"id": "gone", "title": "없음", "kind": "json", "source": self.src("gone.json")},
            {"id": "disk", "title": "디스크", "kind": "command", "source": "df -h /"},
        ])

    def test_greeting_bands(self):
        self.assertEqual([brain.greeting_for(h) for h in (5, 10, 11, 16, 17, 21, 22, 3)],
                         ["좋은 아침이에요", "좋은 아침이에요", "오후예요", "오후예요",
                          "저녁이에요", "저녁이에요", "늦은 시간이에요", "늦은 시간이에요"])

    def test_dash_today(self):
        now = datetime(2026, 9, 30, 8, 0).astimezone()
        t = brain.dash_today(self.vault, today=self.real, now=now)
        self.assertEqual(t["greeting"], "좋은 아침이에요")
        self.assertEqual(t["date"], self.real.isoformat())
        self.assertEqual([d["title"] for d in t["revisit"]], ["지난 결정", "곧 볼 결정"])
        self.assertEqual([d["days_left"] for d in t["revisit"]], [-1, 3])
        self.assertEqual(t["inbox"], ["항공권 확인", "세금 신고"])
        self.assertEqual(t["this_week"], {"new_notes": 1, "new_decisions": 1})
        self.assertEqual(t["widgets_summary"], {"ok": 1, "warn": 0, "fail": 1, "stale": 0, "missing": 1, "paused": 0})
        self.assertEqual(t["top_widgets"][0]["id"], "bad")
        self.assertLessEqual(len(t["kakao"]), 200)

    def test_today_cli_human_and_json(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(brain.main(["today"]), 0)
        out = buf.getvalue().rstrip("\n")
        head, _, kakao = out.partition("\n\n카톡용(200자)\n")
        self.assertTrue(kakao)
        self.assertLessEqual(len(kakao), 200)
        self.assertLessEqual(len(head.split("\n")), 6)
        self.assertIn("고장", head)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            brain.main(["today", "--json"])
        d = json.loads(buf.getvalue())
        self.assertEqual(len(d["inbox"]), 2)

    def test_kakao_long_truncated(self):
        (self.vault / "inbox.md").write_text(
            "\n".join(f"- [ ] {'아주 긴 할 일 ' * 10}{i}" for i in range(20)), encoding="utf-8")
        t = brain.dash_today(self.vault, today=self.real)
        self.assertLessEqual(len(t["kakao"]), 200)
        self.assertIn("언젠가 20", t["kakao"])  # 마감 없는 항목 20개는 언젠가 묶음 수로만, 200자 안

    def test_widgets_cli_empty_hint(self):
        brain.widgets_config_path().unlink()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            brain.main(["widgets"])
        self.assertIn("config init-widgets", buf.getvalue())

    def test_api_widgets_and_today(self):
        srv = brain.make_server(self.vault, 0, today=self.real, quiet=True)
        th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        th.start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/widgets", timeout=5) as r:
                self.assertEqual(r.headers["Content-Type"], "application/json; charset=utf-8")
                ws = json.loads(r.read().decode("utf-8"))
            self.assertEqual([w["status"] for w in ws], ["ok", "fail", "missing", "unknown"])
            with urllib.request.urlopen(base + "/api/today", timeout=5) as r:
                t = json.loads(r.read().decode("utf-8"))
            for k in ("date", "greeting", "weekday", "revisit", "inbox", "this_week",
                      "widgets_summary", "top_widgets"):
                self.assertIn(k, t)
            self.assertEqual(t["revisit"][1]["days_left"], 3)
            self.assertEqual(srv.widget_cache.parses, 4)  # 두 번째 요청은 캐시
        finally:
            srv.shutdown()
            srv.server_close()


class PausedTest(HomeCase):
    """state: paused — 멈춘 자동화는 내용은 읽되 status=paused, 경고·top에서 제외, 목록 뒤로."""

    def test_paused_widget(self):
        self.put("dead.log", "Traceback: boom\n")
        self.put("live.log", "len: 3\n")
        self.write_config([
            {"id": "dead", "kind": "log", "source": self.src("dead.log"), "state": "paused",
             "status": {"fail_pattern": "Traceback"}},
            {"id": "live", "kind": "log", "source": self.src("live.log"),
             "status": {"ok_pattern": "len:"}},
        ])
        ws = brain.collect_widgets()
        self.assertEqual([w["id"] for w in ws], ["live", "dead"])  # active 먼저
        dead = ws[1]
        self.assertEqual((dead["state"], dead["status"]), ("paused", "paused"))
        self.assertTrue(dead["summary"].startswith("멈춤 · "))
        self.assertEqual(dead["data"]["lines"], ["Traceback: boom"])  # 기록은 보존
        t = brain.dash_today(None, widgets=ws)
        self.assertEqual(t["widgets_summary"]["paused"], 1)
        self.assertEqual(t["widgets_summary"]["fail"], 0)
        self.assertNotIn("dead", [w["id"] for w in t["top_widgets"]])
        self.assertNotIn("fail", t["kakao"])
        self.assertIn("멈춤 1", brain.today_human(t))

    def test_unknown_state_is_active(self):
        self.put("a.log", "len: 1\n")
        r = self.ev({"id": "a", "kind": "log", "source": self.src("a.log"), "state": "weird",
                     "status": {"ok_pattern": "len:"}})
        self.assertEqual((r["state"], r["status"]), ("active", "ok"))


if __name__ == "__main__":
    unittest.main()
