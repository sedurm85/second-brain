"""카톡 200자 예산 배정(assemble_kakao) + kakao_brief/evening_brief 우선순위 재구성 테스트."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


class AssembleKakaoTest(unittest.TestCase):
    def test_drops_lowest_priority_first(self):
        parts = [
            (1, "HEADER", None),
            (1, "MUST", None),
            (2, "X" * 50, None),
            (5, "Y" * 50, None),
        ]
        r = brain.assemble_kakao(parts, max_len=20)
        self.assertEqual(r, "HEADER / MUST")
        self.assertNotIn("X", r)
        self.assertNotIn("Y", r)
        self.assertLessEqual(len(r), 20)

    def test_shrink_used_before_dropping(self):
        parts = [
            (1, "H", None),
            (1, "M", None),
            (3, "LONGTEXTLONGTEXT", lambda _s: "SHORT"),
        ]
        r = brain.assemble_kakao(parts, max_len=15)
        self.assertIn("SHORT", r)
        self.assertNotIn("LONGTEXT", r)
        self.assertLessEqual(len(r), 15)

    def test_dropped_part_never_appears_partially(self):
        long_text = "이것은절대중간에서잘리면안되는긴문장입니다"
        parts = [
            (1, "H", None),
            (1, "M", None),
            (4, long_text, None),
        ]
        r = brain.assemble_kakao(parts, max_len=10)
        # 예산이 부족하면 통째로 빠진다 — 부분 문자열로 잘려 나오면 안 된다.
        self.assertEqual(r, "H / M")
        self.assertNotIn(long_text[:5], r)

    def test_header_always_present(self):
        parts = [(1, "[헤더]", None), (5, "덜중요함" * 30, None)]
        r = brain.assemble_kakao(parts, max_len=30)
        self.assertTrue(r.startswith("[헤더]"))

    def test_hard_clip_when_header_alone_exceeds_budget(self):
        r = brain.assemble_kakao([(1, "매우매우긴헤더텍스트", None)], max_len=5)
        self.assertEqual(len(r), 5)
        self.assertTrue(r.endswith("…"))

    def test_hard_clip_when_header_plus_first_must_part_exceeds_budget(self):
        r = brain.assemble_kakao(
            [(1, "[헤더]", None), (1, "매우긴필수파트텍스트입니다무조건있어야함", None)],
            max_len=10,
        )
        self.assertLessEqual(len(r), 10)
        self.assertTrue(r.startswith("[헤더]"))
        self.assertTrue(r.endswith("…"))

    def test_empty_parts_returns_empty_string(self):
        self.assertEqual(brain.assemble_kakao([]), "")

    def test_final_order_is_original_sequence_not_priority(self):
        # 우선순위는 "누구를 뺄지"만 정하고, 남는 파트의 순서는 원래 순서를 따른다.
        parts = [
            (1, "A", None),
            (3, "B", None),
            (2, "C", None),
        ]
        r = brain.assemble_kakao(parts, max_len=20)
        self.assertEqual(r, "A / B / C")


class KakaoBriefBudgetTest(unittest.TestCase):
    def _busy_day_t(self):
        e1 = {"title": "팀 회의", "all_day": False, "start": "2026-09-30T09:00",
              "weather": {"place": "서울", "code": 61, "umbrella": True, "rain_prob": 80, "cold": False, "hot": False}}
        e2 = {"title": "고객 미팅", "all_day": False, "start": "2026-09-30T09:10"}
        e3 = {"title": "보안 점검", "all_day": False, "start": "2026-09-30T14:00"}
        gap = {"from": "팀 회의", "to": "고객 미팅", "gap_min": 5}
        return {
            "date": "2026-09-30",
            "weekday": "수요일",
            "greeting": "좋은 아침이에요",
            "agenda": {"today": [e1, e2, e3], "gaps": [gap], "upcoming": [], "steps_today": []},
            "mail": {},
            "top_widgets": [{"title": "위젯1", "status": "fail"}, {"title": "위젯2", "status": "fail"}],
            "widgets_summary": {"ok": 0, "warn": 0, "fail": 2, "stale": 0, "missing": 0, "paused": 0},
            "suggestions": {"count": 6},
            "tasks": {"today": [{"text": f"할일{i}", "kind": "task"} for i in range(5)], "counts": {}},
            "revisit": [{"title": "실행 이렇게", "days_left": 0}, {"title": "두번째 결정", "days_left": -1}],
            "this_week": {"new_notes": 12, "new_decisions": 2},
        }

    def test_busy_day_fits_budget_and_keeps_priority_1_2(self):
        t = self._busy_day_t()
        msg = brain.kakao_brief(t)
        self.assertLessEqual(len(msg), brain.KAKAO_MAX)
        self.assertIn("일정", msg)
        self.assertIn("빠듯", msg)
        self.assertIn("실패", msg)
        # 낮은 우선순위(결정/이번 주)는 빠질 수 있다 — 반드시 빠져야 하는 건 아니다.

    def test_busy_day_never_cuts_mid_word(self):
        t = self._busy_day_t()
        msg = brain.kakao_brief(t)
        if msg.endswith("…"):
            return  # 하드클립 케이스는 별도로 허용됨
        segments = msg.split(" / ")
        self.assertTrue(all(seg == seg.strip() for seg in segments))

    def test_tighter_budget_drops_low_priority_notes_and_decisions_first(self):
        t = self._busy_day_t()
        # KAKAO_MAX를 임시로 줄여 우선순위 5(이번 주)가 먼저 빠지는지 확인한다.
        orig = brain.KAKAO_MAX
        try:
            brain.KAKAO_MAX = 90
            msg = brain.kakao_brief(t)
        finally:
            brain.KAKAO_MAX = orig
        self.assertLessEqual(len(msg), 90)
        self.assertIn("일정", msg)
        self.assertNotIn("이번 주", msg)


class EveningBriefBudgetTest(unittest.TestCase):
    def _t(self):
        return {"date": "2026-09-30", "tasks": {"today": []}}

    def test_evening_with_journal_clause_fits_budget(self):
        t = self._t()
        msg = brain.evening_brief(t, [], extra_parts=[(2, "일지: 오늘 하루를 차분히 정리했어요", None)])
        self.assertLessEqual(len(msg), brain.KAKAO_MAX)
        self.assertIn("일지", msg)
        self.assertIn("오늘 할 일은 다 끝났어요", msg)

    def test_evening_without_extra_parts_matches_previous_contract(self):
        t = self._t()
        msg = brain.evening_brief(t, [])
        self.assertIn("오늘 할 일은 다 끝났어요", msg)
        self.assertIn("내일 일정 없음", msg)
        self.assertLessEqual(len(msg), brain.KAKAO_MAX)


if __name__ == "__main__":
    unittest.main()
