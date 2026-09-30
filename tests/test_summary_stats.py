"""개요 통계(dash_summary "stats") 테스트 — 임시 HOME/볼트, 표준 unittest."""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402

TODAY = date(2026, 9, 30)


def ago(k):
    return (TODAY - timedelta(days=k)).isoformat()


class StatsTestCase(unittest.TestCase):
    """볼트 구성(총 19개 노트):
    - journal daily 8개: 연속 3일(오늘~2일전) + 끊긴 뒤 연속 5일(6~10일전) + 주간회고 1개
    - decision 4개: superseded 1(옛 결정→새 결정으로 대체 시 링크 1개 생성) · decided 1 · open 2(그중 1개 도래)
    - note 6개: 태그 노트 3개(핵심 태그 공유, 그중 2개 summary 있음) · 링크 노트 2개(상호 링크 1개) · 고아 노트 1개
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = Path(os.path.realpath(self.tmp))
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.vault = self.home / "brain"
        self.vault.mkdir(parents=True)
        mk = lambda *a, **kw: brain.create_note(self.vault, *a, **kw)  # noqa: E731

        # 일지: 오늘부터 연속 3일(현재 스트릭) + 6~10일 전 연속 5일(최장 스트릭) + 주간 회고
        for k in (0, 1, 2):
            mk("journal", f"일지 {k}", created=ago(k))
        for k in (6, 7, 8, 9, 10):
            mk("journal", f"일지 {k}", created=ago(k))
        mk("journal", "주간-회고", created=ago(0))

        # 결정: 옛 결정 → 새 결정으로 대체(링크 1개 생성), 열린 결정 2개(1개는 도래)
        self.d_old = mk("decision", "옛 결정", created=ago(30), status="decided")
        self.d_new = mk("decision", "새 결정", created=ago(10), status="decided")
        brain.supersede(self.vault, self.d_old, self.d_new)
        mk("decision", "진행 중 결정", created=ago(1), status="open",
           revisit=(TODAY + timedelta(days=3)).isoformat())
        mk("decision", "마감 결정", created=ago(1), status="open", revisit=TODAY.isoformat())

        # 노트: 태그 공유 3개(2개 summary 있음), 상호 링크 2개, 고아 1개
        mk("note", "핵심 노트 1", created=ago(3), tags=["핵심"], extra={"summary": "요약1"})
        mk("note", "핵심 노트 2", created=ago(4), tags=["핵심"], extra={"summary": "요약2"})
        mk("note", "핵심 노트 3", created=ago(5), tags=["핵심"])
        mk("note", "링크 노트 A", created=ago(2), tags=["링크"],
           body="# 링크 노트 A\n\n[[링크-노트-b]] 참조\n")
        mk("note", "링크 노트 B", created=ago(2))
        mk("note", "고아 노트", created=ago(6), tags=["고아"])

        self.web = self.home / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("<!doctype html><title>대시보드</title>", encoding="utf-8")

    def tearDown(self):
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def summary(self):
        return brain.dash_summary(self.vault, TODAY)


class JournalStreakTest(StatsTestCase):
    def test_current_and_best_streak(self):
        streak = self.summary()["stats"]["journal_streak"]
        self.assertEqual(streak, {"current": 3, "best": 5})

    def test_weekly_journal_excluded_from_streak(self):
        # 주간-회고는 일간 스트릭에 섞이지 않아야 한다(위 test가 3/5로 고정된 것으로 간접 확인).
        notes = brain.load_notes(self.vault)
        weekly = [n for n in notes if n.type == "journal" and n.stem.endswith("-weekly")]
        self.assertEqual(len(weekly), 1)


class DecisionsStatsTest(StatsTestCase):
    def test_open_decided_superseded_due(self):
        d = self.summary()["stats"]["decisions"]
        self.assertEqual(d, {"open": 2, "decided": 1, "superseded": 1, "due": 1})


class TagsAndTypesTest(StatsTestCase):
    def test_top_tag_is_most_shared(self):
        top = self.summary()["stats"]["top_tags"]
        self.assertEqual(top[0], ["핵심", 3])

    def test_by_type_counts(self):
        by_type = self.summary()["stats"]["by_type"]
        self.assertEqual(by_type.get("journal"), 9)
        self.assertEqual(by_type.get("decision"), 4)
        self.assertEqual(by_type.get("note"), 6)
        self.assertEqual(sum(by_type.values()), 19)


class LinksAndCoverageTest(StatsTestCase):
    def test_links_total_orphans_avg(self):
        links = self.summary()["stats"]["links"]
        self.assertEqual(links["total"], 2)  # 옛결정-새결정, 링크노트A-B
        self.assertEqual(links["orphans"], 15)  # 19 - 4개(연결된 노트)
        self.assertEqual(links["avg_per_note"], round(4 / 19, 2))

    def test_summary_coverage(self):
        cov = self.summary()["stats"]["summary_coverage"]
        self.assertEqual(cov, {"with": 2, "without": 17})


class WeeksAndRangeTest(StatsTestCase):
    def test_twelve_weeks_oldest_first(self):
        weeks = self.summary()["stats"]["weeks"]
        self.assertEqual(len(weeks), 12)
        starts = [w["start"] for w in weeks]
        self.assertEqual(starts, sorted(starts))
        week_start = (TODAY - timedelta(days=TODAY.weekday())).isoformat()
        self.assertEqual(weeks[-1]["start"], week_start)
        for w in weeks:
            self.assertRegex(w["week"], r"^\d{4}-W\d{2}$")

    def test_week_buckets_sum_to_totals(self):
        weeks = self.summary()["stats"]["weeks"]
        self.assertEqual(sum(w["notes"] for w in weeks), 6)
        self.assertEqual(sum(w["decisions"] for w in weeks), 4)
        self.assertEqual(sum(w["journals"] for w in weeks), 9)

    def test_oldest_newest_size(self):
        stats = self.summary()["stats"]
        self.assertEqual(stats["oldest"], ago(30))
        self.assertEqual(stats["newest"], TODAY.isoformat())
        self.assertIsInstance(stats["size_kb"], int)
        self.assertGreaterEqual(stats["size_kb"], 0)


class ApiSummaryStatsTest(StatsTestCase):
    def test_api_summary_returns_stats(self):
        srv = brain.make_server(self.vault, 0, web_dir=self.web, today=TODAY, quiet=True)
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        th.start()
        try:
            with urllib.request.urlopen(base + "/api/summary", timeout=5) as r:
                d = json.loads(r.read().decode("utf-8"))
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertIn("stats", d)
        self.assertEqual(len(d["stats"]["weeks"]), 12)
        self.assertEqual(d["stats"]["journal_streak"], {"current": 3, "best": 5})


if __name__ == "__main__":
    unittest.main()
