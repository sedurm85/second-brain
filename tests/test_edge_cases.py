"""파서 경계 테스트(v0.28) — frontmatter round-trip, slugify, tokenize/dash_search,
parse_tasks/TAG_RE, agenda.parse_ics, _tail_lines, event_key/find_or_create_event_note.

각 케이스는 실제 버그를 찾으면 scripts/에서 최소한으로 고치고, 의도된 동작이면
주석으로 그 이유를 남긴다(표준 unittest, 임시 HOME 패턴은 tests/test_journal.py 참고).
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import agenda  # noqa: E402
import brain  # noqa: E402

KST = ZoneInfo("Asia/Seoul")


class EdgeCaseBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = Path(os.path.realpath(self.tmp))
        self._old_home = os.environ.get("HOME")
        self._old_vault = os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ["HOME"] = str(self.home)
        self.vault = self.home / "brain"

    def tearDown(self):
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        if self._old_vault is not None:
            os.environ["SECOND_BRAIN_VAULT"] = self._old_vault
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = brain.main(list(argv))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def init(self):
        code, _, err = self.run_cli("init")
        self.assertEqual(code, 0, err)


def roundtrip(meta, body=""):
    """dump_frontmatter(meta)+body -> parse_frontmatter로 되돌려 (meta2, body2) 반환."""
    text = brain.dump_frontmatter(meta, body)
    return brain.parse_frontmatter(text)


# ---------------------------------------------------------------------------
# 1. frontmatter 라운드트립
# ---------------------------------------------------------------------------

class FrontmatterEdgeTest(EdgeCaseBase):
    def test_title_with_colon_quote_hash(self):
        meta = {"title": '결정: "빠른" 배포 -- #1', "created": "2026-09-30"}
        meta2, body2 = roundtrip(meta)
        self.assertEqual(meta2, meta)
        self.assertEqual(body2, "")

    def test_title_leading_trailing_spaces_preserved_in_value(self):
        # dump_frontmatter는 원시 값을 그대로 직렬화(트리밍은 create_note 쪽 책임).
        # 앞뒤 공백이 있는 문자열도 quoting 규칙 때문에 그대로 왕복되어야 한다.
        meta = {"title": "  spaced  "}
        meta2, _ = roundtrip(meta)
        self.assertEqual(meta2, meta)

    def test_unicode_title_roundtrip(self):
        meta = {"title": "日本語タイトル 😀 mixed 한글", "tags": ["混合", "한글태그"]}
        meta2, _ = roundtrip(meta)
        self.assertEqual(meta2, meta)

    def test_inline_vs_block_tag_list_equivalent(self):
        inline_meta, _ = brain.parse_frontmatter("---\ntags: [a, b]\n---\n")
        block_meta, _ = brain.parse_frontmatter("---\ntags:\n  - a\n  - b\n---\n")
        self.assertEqual(inline_meta["tags"], block_meta["tags"])
        self.assertEqual(inline_meta["tags"], ["a", "b"])

    def test_empty_tags_roundtrip(self):
        meta = {"title": "t", "tags": []}
        meta2, _ = roundtrip(meta)
        self.assertEqual(meta2["tags"], [])

    def test_wikilink_values_roundtrip(self):
        meta = {"links": ["[[Some Note]]", "[[다른-노트]]"], "title": "[[wiki]] in title"}
        meta2, _ = roundtrip(meta)
        self.assertEqual(meta2, meta)

    def test_body_with_dash_lines_not_confused_for_frontmatter_end(self):
        body = "본문 시작\n---\n표 구분선처럼 보이는 줄\n---\n끝\n"
        meta = {"title": "t"}
        meta2, body2 = roundtrip(meta, body)
        self.assertEqual(meta2, meta)
        self.assertEqual(body2, body)

    def test_crlf_line_endings(self):
        text = "---\r\ntitle: t\r\ntags:\r\n  - a\r\n  - b\r\n---\r\nbody line1\r\nbody line2\r\n"
        meta, body = brain.parse_frontmatter(text)
        self.assertEqual(meta, {"title": "t", "tags": ["a", "b"]})
        self.assertEqual(body, "body line1\nbody line2\n")

    def test_bom_at_file_start(self):
        text = "﻿---\ntitle: t\n---\nbody\n"
        meta, body = brain.parse_frontmatter(text)
        self.assertEqual(meta, {"title": "t"})
        self.assertEqual(body, "body\n")

    def test_missing_closing_dashes_treated_as_no_frontmatter(self):
        text = "---\ntitle: t\n본문이 이어지는데 닫는 --- 가 없음\n"
        meta, body = brain.parse_frontmatter(text)
        # 닫는 구분선이 없으면 전체를 프론트매터 없는 본문으로 취급(관대한 파서 정책, 버그 아님)
        self.assertEqual(meta, {})
        self.assertEqual(body, text)

    def test_unknown_keys_preserved(self):
        text = "---\ntitle: t\nunknown_key: 어떤값\n---\nbody\n"
        meta, _ = brain.parse_frontmatter(text)
        self.assertEqual(meta["unknown_key"], "어떤값")

    def test_created_like_date_stays_string(self):
        meta = {"created": "2026-09-30"}
        meta2, _ = roundtrip(meta)
        self.assertEqual(meta2["created"], "2026-09-30")
        self.assertIsInstance(meta2["created"], str)

    def test_numeric_and_bool_python_values_are_coerced_to_quoted_strings(self):
        # dump_frontmatter는 이 볼트의 전체 계약(모든 meta 값은 문자열/리스트/딕트)을 지키기 위해
        # int/bool처럼 얼핏 보면 야믈 스칼라로 오인될 값은 항상 따옴표로 감싸 문자열로 고정한다.
        # 그래서 Python int/bool을 그대로 넣으면 문자열로 "바뀌는" 것은 의도된 동작이며,
        # 완전한 라운드트립(타입 보존)은 애초에 이 함수의 계약이 아니다 — 버그로 보지 않는다.
        meta2, _ = roundtrip({"count": 5})
        self.assertEqual(meta2, {"count": "5"})
        meta3, _ = roundtrip({"active": True})
        self.assertEqual(meta3, {"active": "True"})


# ---------------------------------------------------------------------------
# 2. slugify / target_path / unique_stem
# ---------------------------------------------------------------------------

class SlugifyEdgeTest(EdgeCaseBase):
    def test_emoji_title_not_empty(self):
        s = brain.slugify("😀🎉🔥")
        self.assertTrue(s)  # 빈 문자열이면 안 됨

    def test_only_punctuation_falls_back_to_untitled(self):
        self.assertEqual(brain.slugify("!!!???..."), "untitled")

    def test_mixed_script_lowercased(self):
        s = brain.slugify("한글Mixed混合123")
        self.assertTrue(s)
        self.assertNotIn("Mixed", s)  # 영문은 소문자화됨

    def test_very_long_title_capped_and_nonempty(self):
        s = brain.slugify("가" * 200)
        self.assertTrue(s)
        self.assertLessEqual(len(s), 60)

    def test_leading_digits_allowed(self):
        self.assertEqual(brain.slugify("123 leading digits"), "123-leading-digits")

    def test_dotdot_in_title_cannot_traverse_path(self):
        # '..'는 금지문자(.)가 제거되어 사라지므로 경로 이동 시도가 무력화된다
        s = brain.slugify("../../etc/passwd")
        self.assertNotIn("..", s)
        self.assertNotIn("/", s)

    def test_target_path_stays_inside_vault_even_for_punctuation_title(self):
        self.init()
        p = brain.create_note(self.vault, "note", "!!!???", created="2026-09-30")
        real = Path(os.path.realpath(str(p)))
        self.assertEqual(brain.ensure_in_vault(self.vault, real), real)
        self.assertEqual(p.stem, "untitled")

    def test_unique_stem_collision_series_for_punctuation_titles(self):
        self.init()
        p1 = brain.create_note(self.vault, "note", "???", created="2026-09-01")
        p2 = brain.create_note(self.vault, "note", "***", created="2026-09-02")
        self.assertEqual(p1.stem, "untitled")
        self.assertEqual(p2.stem, "untitled-2")


# ---------------------------------------------------------------------------
# 3. tokenize / dash_search
# ---------------------------------------------------------------------------

class SearchEdgeTest(EdgeCaseBase):
    def setUp(self):
        super().setUp()
        self.init()
        notes_dir = self.vault / "notes" / "2026" / "09"
        notes_dir.mkdir(parents=True, exist_ok=True)
        meta = {"title": "제목없음", "type": "note", "created": "2026-09-30", "tags": [],
                "summary": "오직요약에만있는단어 uniquesummaryword"}
        (notes_dir / "a.md").write_text(brain.dump_frontmatter(meta, "본문에는 다른 내용만 있다"), encoding="utf-8")
        brain.invalidate_notes_cache()

    def test_punctuation_only_query_raises_brainerror_not_crash(self):
        with self.assertRaises(brain.BrainError):
            brain.dash_search(self.vault, "!!!???...")

    def test_extremely_long_query_does_not_crash(self):
        long_q = "테스트word " * 3000
        res = brain.dash_search(self.vault, long_q)
        self.assertIsInstance(res, list)

    def test_hangul_jamo_query_does_not_crash(self):
        res = brain.dash_search(self.vault, "ㄱㄴㄷㄹ")
        self.assertIsInstance(res, list)

    def test_mixed_case_english_query_matches_lowercased_index(self):
        res = brain.dash_search(self.vault, "UNIQUESUMMARYWORD")
        self.assertEqual(len(res), 1)

    def test_query_matching_only_in_summary_still_found(self):
        res = brain.dash_search(self.vault, "uniquesummaryword")
        self.assertEqual(len(res), 1)
        self.assertIn("uniquesummaryword", res[0]["summary"])


# ---------------------------------------------------------------------------
# 4. parse_tasks / TAG_RE
# ---------------------------------------------------------------------------

class TaskParseEdgeTest(EdgeCaseBase):
    def write_inbox(self, text_bytes):
        self.init()
        (self.vault / "inbox.md").write_bytes(text_bytes)

    def test_bad_due_date_dropped_not_crash(self):
        self.write_inbox("- [ ] 잘못된 날짜 @due(bad-date)\n".encode("utf-8"))
        tasks = brain.parse_tasks(self.vault, date(2026, 9, 30))
        self.assertEqual(len(tasks), 1)
        self.assertIsNone(tasks[0]["due"])  # DATE_RE에 안 맞으면 None으로 정리됨

    def test_empty_due_is_falsy(self):
        self.write_inbox("- [ ] 빈 마감 @due()\n".encode("utf-8"))
        tasks = brain.parse_tasks(self.vault, date(2026, 9, 30))
        # @due()는 빈 문자열로 남지만("" vs None) 이후 로직(bucket_tasks 등)은 모두
        # truthy 검사만 하므로 실질적으로 None과 동일하게 처리된다 — 버그 아님.
        self.assertFalse(tasks[0]["due"])

    def test_tag_inside_middle_of_text_still_stripped(self):
        self.write_inbox("- [ ] 문장 중간에 @waiting(누군가) 태그가 있다\n".encode("utf-8"))
        tasks = brain.parse_tasks(self.vault, date(2026, 9, 30))
        self.assertEqual(tasks[0]["waiting"], "누군가")
        self.assertNotIn("@waiting", tasks[0]["text"])

    def test_nested_brackets_in_tag_value(self):
        self.write_inbox("- [ ] 중첩 [괄호] 테스트 @project(A[B])\n".encode("utf-8"))
        tasks = brain.parse_tasks(self.vault, date(2026, 9, 30))
        self.assertEqual(tasks[0]["project"], "A[B]")
        self.assertIn("[괄호]", tasks[0]["text"])  # 본문 속 일반 괄호는 그대로 남는다

    def test_uppercase_x_checkbox_marks_done(self):
        self.write_inbox("- [X] 대문자 완료\n".encode("utf-8"))
        tasks = brain.parse_tasks(self.vault, date(2026, 9, 30))
        self.assertTrue(tasks[0]["done"])

    def test_tab_indent_still_matched(self):
        self.write_inbox("\t- [ ] 탭 들여쓰기 항목\n".encode("utf-8"))
        tasks = brain.parse_tasks(self.vault, date(2026, 9, 30))
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["text"], "탭 들여쓰기 항목")

    def test_crlf_inbox_no_stray_carriage_return(self):
        content = "- [ ] crlf 작업 @due(2026-10-01)\r\n- [x] crlf 완료\r\n"
        self.write_inbox(content.encode("utf-8"))
        tasks = brain.parse_tasks(self.vault, date(2026, 9, 30))
        self.assertEqual(len(tasks), 2)
        for t in tasks:
            self.assertNotIn("\r", t["text"])
            self.assertNotIn("\r", t["raw"])


# ---------------------------------------------------------------------------
# 5. agenda.parse_ics
# ---------------------------------------------------------------------------

def ics(*veventss, calname=None):
    head = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
    if calname:
        head += f"X-WR-CALNAME:{calname}\r\n"
    body = "".join(veventss)
    return (head + body + "END:VCALENDAR\r\n")


class IcsEdgeTest(EdgeCaseBase):
    def setUp(self):
        super().setUp()
        self.ws = datetime(2026, 9, 28, tzinfo=KST)
        self.we = datetime(2026, 10, 20, tzinfo=KST)

    def parse(self, vevent_body):
        text = ics(vevent_body)
        return agenda.parse_ics(text, self.ws, self.we, tz=KST)

    def test_folded_line_with_tab_continuation(self):
        vevent = ("BEGIN:VEVENT\r\nUID:1\r\nDTSTART:20260930T100000\r\nDTEND:20260930T110000\r\n"
                   "SUMMARY:Long summary that is fol\r\n ded across lines with a tab\r\n\tcontinuation\r\n"
                   "END:VEVENT\r\n")
        evs = self.parse(vevent)
        self.assertEqual(evs[0]["title"], "Long summary that is folded across lines with a tabcontinuation")

    def test_escaped_comma_semicolon_newline_in_description(self):
        vevent = ("BEGIN:VEVENT\r\nUID:1\r\nDTSTART:20260930T100000\r\nDTEND:20260930T110000\r\n"
                   "SUMMARY:s\r\nDESCRIPTION:line1\\nline2\\, comma\\; semi\r\nEND:VEVENT\r\n")
        evs = self.parse(vevent)
        self.assertEqual(evs[0]["description"], "line1\nline2, comma; semi")

    def test_dtstart_value_date_is_all_day(self):
        vevent = "BEGIN:VEVENT\r\nUID:1\r\nDTSTART;VALUE=DATE:20260930\r\nSUMMARY:allday\r\nEND:VEVENT\r\n"
        evs = self.parse(vevent)
        self.assertTrue(evs[0]["all_day"])
        self.assertEqual(evs[0]["start"], "2026-09-30")

    def test_dtstart_utc_z_converted_to_local(self):
        vevent = ("BEGIN:VEVENT\r\nUID:1\r\nDTSTART:20260930T010000Z\r\nDTEND:20260930T020000Z\r\n"
                   "SUMMARY:utc\r\nEND:VEVENT\r\n")
        evs = self.parse(vevent)
        self.assertEqual(evs[0]["start"], "2026-09-30T10:00+09:00")  # UTC+9

    def test_unknown_tzid_falls_back_to_local_tz(self):
        vevent = ("BEGIN:VEVENT\r\nUID:1\r\nDTSTART;TZID=Mars/Colony:20260930T100000\r\n"
                   "DTEND;TZID=Mars/Colony:20260930T110000\r\nSUMMARY:unknown tz\r\nEND:VEVENT\r\n")
        evs = self.parse(vevent)  # 존재하지 않는 TZID라도 예외 없이 로컬 시간대로 처리돼야 함
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["start"], "2026-09-30T10:00+09:00")

    def test_rrule_count_limits_instances(self):
        vevent = ("BEGIN:VEVENT\r\nUID:1\r\nDTSTART:20260928T090000\r\nDTEND:20260928T100000\r\n"
                   "RRULE:FREQ=DAILY;COUNT=3\r\nSUMMARY:daily3\r\nEND:VEVENT\r\n")
        evs = self.parse(vevent)
        self.assertEqual(len(evs), 3)

    def test_rrule_until_bounds_instances(self):
        vevent = ("BEGIN:VEVENT\r\nUID:1\r\nDTSTART:20260928T090000\r\nDTEND:20260928T100000\r\n"
                   "RRULE:FREQ=DAILY;UNTIL=20261001T000000Z\r\nSUMMARY:u\r\nEND:VEVENT\r\n")
        evs = self.parse(vevent)
        self.assertEqual([e["start"][:10] for e in evs], ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01"])

    def test_exdate_list_excludes_multiple_dates(self):
        vevent = ("BEGIN:VEVENT\r\nUID:1\r\nDTSTART:20260928T090000\r\nDTEND:20260928T100000\r\n"
                   "RRULE:FREQ=DAILY;COUNT=5\r\nEXDATE:20260929T090000,20260930T090000\r\n"
                   "SUMMARY:exlist\r\nEND:VEVENT\r\n")
        evs = self.parse(vevent)
        self.assertEqual([e["start"][:10] for e in evs], ["2026-09-28", "2026-10-01", "2026-10-02"])

    def test_recurrence_id_overrides_one_instance(self):
        vevent = ("BEGIN:VEVENT\r\nUID:13\r\nDTSTART:20260928T090000\r\nDTEND:20260928T100000\r\n"
                   "RRULE:FREQ=DAILY;COUNT=3\r\nSUMMARY:master\r\nEND:VEVENT\r\n"
                   "BEGIN:VEVENT\r\nUID:13\r\nRECURRENCE-ID:20260929T090000\r\n"
                   "DTSTART:20260929T150000\r\nDTEND:20260929T160000\r\nSUMMARY:master (moved)\r\nEND:VEVENT\r\n")
        evs = self.parse(vevent)
        titles_starts = sorted((e["title"], e["start"]) for e in evs)
        self.assertEqual(titles_starts, sorted([
            ("master", "2026-09-28T09:00+09:00"),
            ("master", "2026-09-30T09:00+09:00"),
            ("master (moved)", "2026-09-29T15:00+09:00"),
        ]))
        # 원본 반복의 09-29 09:00 회차는 대체되어 없어야 한다
        self.assertNotIn(("master", "2026-09-29T09:00+09:00"), titles_starts)

    def test_status_cancelled_excluded(self):
        vevent = ("BEGIN:VEVENT\r\nUID:1\r\nDTSTART:20260930T090000\r\nDTEND:20260930T100000\r\n"
                   "STATUS:CANCELLED\r\nSUMMARY:cancelled\r\nEND:VEVENT\r\n")
        evs = self.parse(vevent)
        self.assertEqual(evs, [])

    def test_dtend_missing_defaults_to_one_hour(self):
        vevent = "BEGIN:VEVENT\r\nUID:1\r\nDTSTART:20260930T090000\r\nSUMMARY:noend\r\nEND:VEVENT\r\n"
        evs = self.parse(vevent)
        self.assertEqual(evs[0]["start"], "2026-09-30T09:00+09:00")
        self.assertEqual(evs[0]["end"], "2026-09-30T10:00+09:00")

    def test_dtend_missing_allday_defaults_to_same_day(self):
        vevent = "BEGIN:VEVENT\r\nUID:1\r\nDTSTART;VALUE=DATE:20260930\r\nSUMMARY:noend\r\nEND:VEVENT\r\n"
        evs = self.parse(vevent)
        self.assertEqual(evs[0]["start"], "2026-09-30")
        self.assertEqual(evs[0]["end"], "2026-10-01")  # exclusive end, 당일치

    def test_allday_multiday_range(self):
        vevent = ("BEGIN:VEVENT\r\nUID:1\r\nDTSTART;VALUE=DATE:20260930\r\nDTEND;VALUE=DATE:20261003\r\n"
                   "SUMMARY:multiday\r\nEND:VEVENT\r\n")
        evs = self.parse(vevent)
        self.assertEqual(evs[0]["start"], "2026-09-30")
        self.assertEqual(evs[0]["end"], "2026-10-03")

    def test_dtstart_after_dtend_does_not_crash(self):
        # 잘못된 입력(시작이 끝보다 나중)이라도 예외 없이 결과가 나와야 한다는 게 요구사항.
        # end < start가 되는 건 입력 오류를 그대로 반영한 것일 뿐이라 여기서 보정하지 않는다(버그로 보지 않음).
        vevent = ("BEGIN:VEVENT\r\nUID:1\r\nDTSTART:20260930T120000\r\nDTEND:20260930T100000\r\n"
                   "SUMMARY:inverted\r\nEND:VEVENT\r\n")
        evs = self.parse(vevent)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["title"], "inverted")

    def test_byday_ordinal_prefix_ignored_gracefully_for_monthly(self):
        # BYDAY=2MO(매월 둘째 월요일)의 "2" 서열은 지원 대상이 아니다. WEEKLY가 아니면
        # bydays가 무시되고 DTSTART의 날짜(일)만 매달 반복하는 기존 동작으로 자연히 대체된다.
        # 크래시 없이 그레이스풀하게 무시되는지만 확인한다.
        vevent = ("BEGIN:VEVENT\r\nUID:1\r\nDTSTART:20260907T090000\r\nDTEND:20260907T100000\r\n"
                   "RRULE:FREQ=MONTHLY;BYDAY=2MO;COUNT=3\r\nSUMMARY:2ndmonday\r\nEND:VEVENT\r\n")
        evs = self.parse(vevent)
        self.assertGreaterEqual(len(evs), 1)


# ---------------------------------------------------------------------------
# 6. _tail_lines
# ---------------------------------------------------------------------------

class TailLinesEdgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_huge_single_line_over_max_bytes_still_returns_content(self):
        # 버그였던 부분: 창(window) 전체가 개행 없는(또는 단 하나의 개행만 있는) 초대형
        # 한 줄로 채워지면, "잘린 첫 줄 버림" 로직이 그 유일한 줄까지 버려 빈 리스트를 반환했다.
        # 로그가 통째로 사라지는 대신, 잘렸더라도 내용을 보여주도록 scripts/brain.py를 고쳤다.
        p = Path(self.tmp) / "huge.log"
        p.write_text("A" * 300000 + "\n", encoding="utf-8")  # 300001 bytes, max_bytes=262144
        out = brain._tail_lines(p, 5)
        self.assertEqual(len(out), 1)
        self.assertGreater(len(out[0]), 0)

    def test_huge_single_line_without_trailing_newline(self):
        p = Path(self.tmp) / "huge_no_nl.log"
        p.write_text("B" * 300000, encoding="utf-8")
        out = brain._tail_lines(p, 5)
        self.assertEqual(len(out), 1)

    def test_normal_multiline_truncation_unaffected(self):
        p = Path(self.tmp) / "normal.log"
        with p.open("wb") as f:
            for i in range(20000):
                f.write(f"line-{i}\r\n".encode("utf-8"))
        out = brain._tail_lines(p, 3)
        self.assertEqual(out, ["line-19997", "line-19998", "line-19999"])

    def test_crlf_file_lines_have_no_stray_cr(self):
        p = Path(self.tmp) / "crlf.log"
        p.write_bytes(b"a\r\nb\r\nc\r\n")
        out = brain._tail_lines(p, 3)
        self.assertEqual(out, ["a", "b", "c"])
        for ln in out:
            self.assertNotIn("\r", ln)


# ---------------------------------------------------------------------------
# 7. event_key / find_or_create_event_note (제목에 '|' 포함)
# ---------------------------------------------------------------------------

class EventKeyPipeTest(EdgeCaseBase):
    def test_event_key_with_pipe_in_title_roundtrips(self):
        key = agenda.event_key("2026-09-30", "회의 A|B 파트")
        self.assertEqual(key, "2026-09-30|회의 A|B 파트")

    def test_find_or_create_event_note_title_with_pipe(self):
        self.init()
        key = agenda.event_key("2026-09-30", "회의 A|B 파트")
        n, created = brain.find_or_create_event_note(self.vault, key)
        self.assertTrue(created)
        self.assertEqual(n.title, "회의 A|B 파트")
        exact, _ = brain.event_notes_index(self.vault)
        self.assertIn(key, exact)
        # 두 번째 호출은 같은 노트를 찾아야 하고 새로 만들면 안 된다
        n2, created2 = brain.find_or_create_event_note(self.vault, key)
        self.assertFalse(created2)
        self.assertEqual(n2.path, n.path)


if __name__ == "__main__":
    unittest.main()
