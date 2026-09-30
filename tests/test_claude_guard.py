"""Claude 호출 가드: 사용량 JSONL 로그(종류별), 시간당 상한(+SECOND_BRAIN_CLAUDE_UNLIMITED 우회), 동시성 락,
/api/claude-usage, doctor의 「Claude 사용량」 항목."""
import contextlib
import io
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


class ClaudeGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in
                     ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_ASK_CMD",
                      "SECOND_BRAIN_JOBS_DIR", "SECOND_BRAIN_CLAUDE_UNLIMITED")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ["SECOND_BRAIN_JOBS_DIR"] = str(self.home / "nojobs")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ.pop("SECOND_BRAIN_CLAUDE_UNLIMITED", None)
        self.cfgdir = self.home / ".config" / "second-brain"
        self.cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        self.cfg_path = self.cfgdir / "config.json"
        self.cfg_path.write_text(json.dumps({"vault": str(self.vault), "calendar": {"sources": []}}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])
        # 가짜 Claude: 호출마다 유효한 빈 JSON 객체를 답한다(성공 카운트용)
        fake = self.home / "fake_claude.py"
        fake.write_text('import sys\nsys.stdin.read()\nprint("{}")\n', encoding="utf-8")
        os.environ["SECOND_BRAIN_ASK_CMD"] = sys.executable + " " + str(fake)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _set_limit(self, n):
        cfg = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        cfg["claude_hourly_limit"] = n
        self.cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    def _usage_lines(self):
        p = brain.claude_usage_log_path()
        if not p.exists():
            return []
        return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]

    # ── 사용량 로그 ──────────────────────────────────────────────
    def test_usage_log_records_kind_per_call(self):
        brain.ask_assistant("오늘 일정 뭐 있어?", {"date": "2026-09-30"}, self.vault)
        brain._run_claude_json("[프롬프트]", kind="enrich")
        lines = self._usage_lines()
        kinds = [l["kind"] for l in lines]
        self.assertEqual(kinds, ["ask", "enrich"])
        for l in lines:
            self.assertTrue(l["ok"])
            self.assertIsInstance(l["ms"], int)
            self.assertGreaterEqual(l["ms"], 0)
            self.assertGreater(l["chars_in"], 0)
            self.assertGreaterEqual(l["chars_out"], 0)
            self.assertIn("ts", l)

    def test_claude_usage_summary_shape(self):
        brain._run_claude_json("[프롬프트]", kind="journal")
        brain._run_claude_json("[프롬프트]", kind="retro")
        u = brain.claude_usage(24)
        self.assertEqual(u["calls"], 2)
        self.assertEqual(u["by_kind"], {"journal": 1, "retro": 1})
        self.assertEqual(u["last"]["kind"], "retro")
        self.assertEqual(u["hour_calls"], 2)

    # ── 시간당 상한 ──────────────────────────────────────────────
    def test_hourly_cap_blocks_third_call(self):
        self._set_limit(2)
        brain._run_claude_json("[프롬프트]", kind="prepare")
        brain._run_claude_json("[프롬프트]", kind="prepare")
        with self.assertRaises(brain.BrainError) as ctx:
            brain._run_claude_json("[프롬프트]", kind="prepare")
        self.assertIn("상한", str(ctx.exception))
        # 세 번째 호출은 로그도 남기지 않는다(상한 체크가 subprocess 실행보다 먼저)
        self.assertEqual(len(self._usage_lines()), 2)

    def test_hourly_cap_unlimited_env_bypasses(self):
        self._set_limit(1)
        brain._run_claude_json("[프롬프트]", kind="links")
        with self.assertRaises(brain.BrainError):
            brain._run_claude_json("[프롬프트]", kind="links")
        os.environ["SECOND_BRAIN_CLAUDE_UNLIMITED"] = "1"
        raw = brain._run_claude_json("[프롬프트]", kind="links")  # 우회 → 성공
        self.assertEqual(raw, {})
        self.assertEqual(len(self._usage_lines()), 2)

    # ── 동시성 락 ────────────────────────────────────────────────
    def test_concurrency_lock_blocks_parallel_call(self):
        self.assertTrue(brain._CLAUDE_LOCK.acquire(timeout=0))
        try:
            with self.assertRaises(brain.BrainError) as ctx:
                brain._run_claude_json("[프롬프트]", kind="brief")
            self.assertIn("다른 질문", str(ctx.exception))
            with self.assertRaises(brain.BrainError) as ctx2:
                brain.ask_assistant("질문", {"date": "2026-09-30"}, self.vault)
            self.assertIn("다른 질문", str(ctx2.exception))
        finally:
            brain._CLAUDE_LOCK.release()
        self.assertEqual(self._usage_lines(), [])  # 락에 막히면 사용량 기록도 안 남는다

    # ── /api/claude-usage ───────────────────────────────────────
    def test_api_claude_usage(self):
        brain._run_claude_json("[프롬프트]", kind="enrich")
        srv = brain.make_server(self.vault, 0, web_dir=self.home / "web", today=None, quiet=True)
        (self.home / "web").mkdir(exist_ok=True)
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        th.start()
        try:
            with urllib.request.urlopen(base + "/api/claude-usage", timeout=5) as r:
                self.assertEqual(r.status, 200)
                d = json.loads(r.read().decode("utf-8"))
        finally:
            srv.shutdown()
            srv.server_close()
        for k in ("calls", "by_kind", "last", "hour_calls", "limit", "remaining_hour"):
            self.assertIn(k, d)
        self.assertEqual(d["calls"], 1)
        self.assertEqual(d["limit"], 40)
        self.assertEqual(d["remaining_hour"], 39)

    # ── doctor ───────────────────────────────────────────────────
    def test_doctor_has_claude_usage_item(self):
        brain._run_claude_json("[프롬프트]", kind="enrich")
        r = brain.doctor_report()
        items = {i["name"]: i for i in r["items"]}
        self.assertIn("Claude 사용량", items)
        self.assertTrue(items["Claude 사용량"]["ok"])
        self.assertIn("24h", items["Claude 사용량"]["detail"])
        self.assertIn("1회", items["Claude 사용량"]["detail"])


if __name__ == "__main__":
    unittest.main()
