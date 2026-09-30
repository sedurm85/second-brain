"""lint: 프론트매터·타입·링크·중복·BOM/CRLF 등 볼트 데이터 품질 점검 + --fix, doctor 연동."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_brain import BrainTestCase, TODAY  # noqa: E402

import brain  # noqa: E402  (test_brain이 scripts 경로를 추가함)


class LintTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.init()
        # init 샘플 가이드 노트 본문에 예시용 "[[파일]]" 텍스트가 있어 그 자체로 link-broken을
        # 하나 만든다. lint 테스트의 "깨끗한 볼트" 기준을 흔들지 않도록 지우고 시작한다.
        for n in brain.load_notes(self.vault):
            if n.title == "세컨드브레인 시작하기":
                n.path.unlink()
        brain.invalidate_notes_cache()
        brain.build_index(self.vault)

    def by_code(self, report):
        out = {}
        for i in report["issues"]:
            out.setdefault(i["code"], []).append(i)
        return out

    def write_raw(self, rel, text, encoding="utf-8", bom=False, crlf=False):
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if crlf:
            text = text.replace("\n", "\r\n")
        data = text.encode(encoding)
        if bom:
            data = b"\xef\xbb\xbf" + data
        p.write_bytes(data)
        return p

    def craft_issues(self):
        """각 코드가 최소 1개씩 나오도록 노트를 직접 만든다."""
        # frontmatter-missing
        self.write_raw("notes/2020/01/no-frontmatter.md", "# 제목만 있음\n본문\n")
        # bom-or-crlf (정상 프론트매터 + BOM + CRLF)
        self.write_raw(
            "notes/2026/09/bom-crlf.md",
            "---\ntitle: BOM 테스트\ntype: note\ncreated: 2026-09-01\ntags: []\n---\n# BOM 테스트\n본문\n",
            bom=True, crlf=True,
        )
        # tags-not-list (fixable)
        self.write_raw(
            "notes/2026/09/tags-str.md",
            "---\ntitle: 태그 문자열\ntype: note\ncreated: 2026-09-01\ntags: a, b\n---\n# 태그 문자열\n",
        )
        # type-invalid
        self.write_raw(
            "notes/2026/09/bad-type.md",
            "---\ntitle: 이상한 타입\ntype: banana\ncreated: 2026-09-01\ntags: []\n---\n# 이상한 타입\n",
        )
        # created-invalid
        self.write_raw(
            "notes/2026/09/bad-created.md",
            "---\ntitle: 날짜 이상\ntype: note\ncreated: 어제\ntags: []\n---\n# 날짜 이상\n",
        )
        # title-missing
        self.write_raw(
            "notes/2026/09/no-title.md",
            "---\ntype: note\ncreated: 2026-09-01\ntags: []\n---\n# 제목 없음\n",
        )
        # title-duplicate (두 노트가 같은 title)
        self.write_raw(
            "notes/2026/09/dup-a.md",
            "---\ntitle: 중복 제목\ntype: note\ncreated: 2026-09-01\ntags: []\n---\n# A\n",
        )
        self.write_raw(
            "notes/2026/09/dup-b.md",
            "---\ntitle: 중복 제목\ntype: note\ncreated: 2026-09-02\ntags: []\n---\n# B\n",
        )
        # stem-collision (다른 폴더, 같은 stem)
        self.write_raw(
            "notes/2026/09/same-stem.md",
            "---\ntitle: 같은 stem 1\ntype: note\ncreated: 2026-09-01\ntags: []\n---\n# 1\n",
        )
        self.write_raw(
            "projects/same-stem.md",
            "---\ntitle: 같은 stem 2\ntype: project\ncreated: 2026-09-01\ntags: []\n---\n# 2\n",
        )
        # type-folder-mismatch (project인데 notes/ 아래)
        self.write_raw(
            "notes/2026/09/misplaced-project.md",
            "---\ntitle: 잘못된 위치\ntype: project\ncreated: 2026-09-01\ntags: []\n---\n# 잘못된 위치\n",
        )
        # link-self (fixable)
        self.write_raw(
            "notes/2026/09/self-link.md",
            "---\ntitle: 셀프링크\ntype: note\ncreated: 2026-09-01\ntags: []\n---\n"
            "# 셀프링크\n본문에서 [[self-link|나 자신]]을 가리킴\n",
        )
        # link-broken (fixable — relink 후보 있음: 밑줄 → 하이픈)
        self.write_raw(
            "notes/2026/09/target-note.md",
            "---\ntitle: 타깃 노트\ntype: note\ncreated: 2026-09-01\ntags: []\n---\n# 타깃 노트\n",
        )
        self.write_raw(
            "notes/2026/09/linker.md",
            "---\ntitle: 링커\ntype: note\ncreated: 2026-09-01\ntags: []\n---\n"
            "# 링커\n[[target_note]] 참조. [[아무데도-없는-노트]]도 참조.\n",
        )
        # summary-too-long
        self.write_raw(
            "notes/2026/09/long-summary.md",
            "---\ntitle: 긴 요약\ntype: note\ncreated: 2026-09-01\ntags: []\nsummary: \""
            + ("가" * 401) + "\"\n---\n# 긴 요약\n",
        )
        # imported-unenriched
        self.write_raw(
            "notes/2026/09/imported.md",
            "---\ntitle: 가져온 노트\ntype: note\ncreated: 2026-09-01\ntags: []\n"
            "imported_from: memory.md\n---\n# 가져온 노트\n",
        )
        # orphan-old (링크 없음 + 90일 전 생성)
        self.write_raw(
            "notes/2020/01/orphan.md",
            "---\ntitle: 오래된 고아\ntype: note\ncreated: 2020-01-01\ntags: []\n---\n# 오래된 고아\n",
        )
        # journal-date-mismatch (fixable)
        self.write_raw(
            "journal/2026/2026-09-15.md",
            "---\ntitle: 일지\ntype: journal\ncreated: 2026-09-15\ntags: []\n"
            "journal_date: 2026-09-14\n---\n# 일지\n",
        )
        # event-key-mismatch + event-past-unchecked
        self.write_raw(
            "events/2026/2026-08-01-워크숍.md",
            "---\ntitle: 워크숍\ntype: event\ncreated: 2026-08-01\ntags: []\n"
            "event_key: \"2026-08-02|워크숍\"\nevent_date: 2026-08-01\n---\n"
            "# 워크숍\n\n## 준비\n\n- [ ] 자리 예약\n- [x] 초대장 발송\n",
        )
        brain.invalidate_notes_cache()

    def test_all_codes_detected_with_correct_counts(self):
        self.craft_issues()
        report = brain.lint_vault(self.vault, today=TODAY)
        by = self.by_code(report)
        expected_codes = {
            "frontmatter-missing", "type-invalid", "type-folder-mismatch", "created-invalid",
            "title-missing", "title-duplicate", "tags-not-list", "link-broken", "link-self",
            "orphan-old", "event-key-mismatch", "event-past-unchecked", "journal-date-mismatch",
            "summary-too-long", "stem-collision", "bom-or-crlf", "imported-unenriched",
        }
        missing = expected_codes - set(by)
        self.assertFalse(missing, f"빠진 코드: {missing} / 발견: {sorted(by)}")
        # 개수 확인(핵심 케이스)
        self.assertEqual(report["counts"]["frontmatter-missing"], 1)
        self.assertEqual(report["counts"]["title-duplicate"], 2)
        self.assertEqual(report["counts"]["stem-collision"], 2)
        self.assertEqual(report["counts"]["tags-not-list"], 1)
        self.assertEqual(report["counts"]["bom-or-crlf"], 1)
        self.assertEqual(report["counts"]["journal-date-mismatch"], 1)
        self.assertEqual(report["counts"]["event-key-mismatch"], 1)
        self.assertEqual(report["counts"]["event-past-unchecked"], 1)
        # link-broken: target_note(고침 가능) + 아무데도-없는-노트(고침 불가) = 2건
        self.assertEqual(report["counts"]["link-broken"], 2)
        broken = by["link-broken"]
        fixables = [i for i in broken if i["fixable"]]
        unfixables = [i for i in broken if not i["fixable"]]
        self.assertEqual(len(fixables), 1)
        self.assertEqual(len(unfixables), 1)
        self.assertEqual(fixables[0]["detail"]["to"], "target-note")
        # trailing-whitespace-heavy는 구현 대상이 아님(skip)
        self.assertNotIn("trailing-whitespace-heavy", by)

    def test_not_fixable_codes_are_report_only(self):
        self.craft_issues()
        report = brain.lint_vault(self.vault, today=TODAY)
        report_only = {"frontmatter-missing", "type-invalid", "type-folder-mismatch",
                       "created-invalid", "title-missing", "title-duplicate", "orphan-old",
                       "event-key-mismatch", "event-past-unchecked", "summary-too-long",
                       "stem-collision", "imported-unenriched"}
        for code in report_only:
            for i in [x for x in report["issues"] if x["code"] == code]:
                self.assertFalse(i["fixable"], f"{code}는 report-only여야 함: {i}")

    def test_fix_applies_only_fixable_and_second_run_has_fewer_issues(self):
        self.craft_issues()
        before = brain.lint_vault(self.vault, today=TODAY)
        before_count = len(before["issues"])
        fixed = brain.fix_lint_issues(self.vault, before["issues"])
        fixed_codes = {c for f in fixed for c in f["codes"]}
        self.assertEqual(fixed_codes, {"tags-not-list", "link-self", "link-broken",
                                       "journal-date-mismatch", "bom-or-crlf"})

        after = brain.lint_vault(self.vault, today=TODAY)
        after_count = len(after["issues"])
        self.assertLess(after_count, before_count)

        after_by = self.by_code(after)
        # 고쳐진 코드는 (relink 불가 링크 제외) 더 이상 나오지 않아야 함
        self.assertNotIn("tags-not-list", after_by)
        self.assertNotIn("link-self", after_by)
        self.assertNotIn("journal-date-mismatch", after_by)
        self.assertNotIn("bom-or-crlf", after_by)
        # link-broken 중 고칠 수 없던 건(아무데도-없는-노트)은 남아 있어야 함
        self.assertEqual(len(after_by.get("link-broken", [])), 1)
        self.assertFalse(after_by["link-broken"][0]["fixable"])
        # 고칠 수 없는 코드들은 그대로 남아 있어야 함
        for code in ("frontmatter-missing", "type-invalid", "created-invalid",
                     "title-missing", "title-duplicate", "stem-collision"):
            self.assertIn(code, after_by, f"{code}는 안 고쳐져야 함")

        # 실제로 파일 내용이 고쳐졌는지 확인
        meta, body = brain.parse_frontmatter((self.vault / "notes/2026/09/tags-str.md").read_text(encoding="utf-8"))
        self.assertEqual(meta["tags"], ["a", "b"])
        _, body2 = brain.parse_frontmatter((self.vault / "notes/2026/09/self-link.md").read_text(encoding="utf-8"))
        self.assertNotIn("[[self-link", body2)
        meta3, _ = brain.parse_frontmatter((self.vault / "notes/2026/09/linker.md").read_text(encoding="utf-8"))
        j_meta, _ = brain.parse_frontmatter((self.vault / "journal/2026/2026-09-15.md").read_text(encoding="utf-8"))
        self.assertEqual(j_meta["journal_date"], "2026-09-15")
        raw = (self.vault / "notes/2026/09/bom-crlf.md").read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r\n", raw)

    def test_no_files_deleted_by_fix(self):
        self.craft_issues()
        before_files = sorted(p.relative_to(self.vault).as_posix() for p in brain.iter_note_files(self.vault))
        report = brain.lint_vault(self.vault, today=TODAY)
        brain.fix_lint_issues(self.vault, report["issues"])
        after_files = sorted(p.relative_to(self.vault).as_posix() for p in brain.iter_note_files(self.vault))
        self.assertEqual(before_files, after_files)

    def test_cli_exit_codes_and_json(self):
        self.craft_issues()
        code, out, err = self.run_cli("lint", "--json")
        self.assertEqual(code, brain.EXIT_INPUT, err)
        data = brain.json.loads(out) if hasattr(brain, "json") else None
        import json as _json
        data = _json.loads(out)
        self.assertGreater(len(data["issues"]), 0)

        code2, out2, _ = self.run_cli("lint", "--fix")
        self.assertIn("고침", out2)

        code3, _, err3 = self.run_cli("lint")
        self.assertEqual(code3, brain.EXIT_INPUT, "고칠 수 없는 이슈가 남아 있어 2여야 함")

    def test_clean_vault_has_no_issues_and_exit_zero(self):
        self.new("note", "깨끗한 노트", body="# 깨끗한 노트\n본문\n")
        code, out, err = self.run_cli("lint")
        self.assertEqual(code, brain.EXIT_OK, err)
        self.assertIn("이슈 없음", out)

    def test_doctor_has_vault_check_item(self):
        self.new("note", "깨끗한 노트", body="# 깨끗한 노트\n본문\n")
        r = brain.doctor_report()
        names = {i["name"]: i for i in r["items"]}
        self.assertIn("볼트 점검", names)
        self.assertTrue(names["볼트 점검"]["ok"])

    def test_doctor_vault_check_reflects_issues(self):
        self.craft_issues()
        r = brain.doctor_report()
        names = {i["name"]: i for i in r["items"]}
        self.assertIn("볼트 점검", names)
        self.assertFalse(names["볼트 점검"]["ok"])
        self.assertIn("lint --fix", names["볼트 점검"]["fix"])


if __name__ == "__main__":
    import unittest
    unittest.main()
