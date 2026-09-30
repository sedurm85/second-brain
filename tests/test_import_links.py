"""import 위키링크 재작성 · 메모리 description 제목 · relink 테스트."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_brain import BrainTestCase  # noqa: E402

import brain  # noqa: E402  (test_brain이 scripts 경로를 추가함)


def mem(dirpath, fname, name, desc, mtype, body):
    (dirpath / fname).write_text(
        f"---\nname: {name}\ndescription: {desc}\nmetadata:\n  type: {mtype}\n---\n{body}",
        encoding="utf-8")


class ImportLinksTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.init()
        self.mem = self.home / "src" / "proj" / "memory"
        self.mem.mkdir(parents=True)

    def read(self, rel):
        return brain.parse_frontmatter((self.vault / rel).read_text(encoding="utf-8"))

    def by_file(self, r):
        return {Path(x["file"]).name: x for x in r["imported"]}

    def test_memory_links_rewritten_in_body(self):
        mem(self.mem, "project_biz_discovery.md", "project_biz_discovery", "사업 발굴 — 설명",
            "project", "관련: [[feedback_date_check]], [[feedback_date_check|별칭]]")
        mem(self.mem, "feedback_date_check.md", "feedback_date_check", "날짜 확인 필수 — 설명",
            "feedback", "역링크 [[project_biz_discovery#요약]]")
        r = brain.import_path(self.vault, str(self.mem))
        files = self.by_file(r)
        self.assertEqual(files["project_biz_discovery.md"]["dest"], "projects/biz-discovery.md")
        _, body = self.read("projects/biz-discovery.md")
        self.assertIn("[[date-check]]", body)
        self.assertIn("[[date-check|별칭]]", body)
        self.assertNotIn("feedback_date_check", body)
        _, body2 = self.read(files["feedback_date_check.md"]["dest"])
        self.assertIn("[[biz-discovery#요약]]", body2)
        self.assertGreaterEqual(len(brain.dash_graph(self.vault)["edges"]), 1)

    def test_markdown_frontmatter_links_rewritten(self):
        src = self.home / "md"
        src.mkdir()
        (src / "Alpha Note.md").write_text(
            "---\ntitle: 알파\ncreated: 2026-05-01\nlinks: [\"[[Beta_File]]\", Beta_File]\n---\n본문 [[Beta_File]]",
            encoding="utf-8")
        (src / "Beta_File.md").write_text("---\ntitle: 베타\ncreated: 2026-05-02\n---\nb",
                                          encoding="utf-8")
        r = brain.import_path(self.vault, str(src))
        files = self.by_file(r)
        beta_stem = Path(files["Beta_File.md"]["dest"]).stem
        self.assertEqual(beta_stem, "베타")
        meta, body = self.read(files["Alpha Note.md"]["dest"])
        self.assertEqual(meta["links"], ["[[베타]]", "[[베타]]"])
        self.assertIn("[[베타]]", body)
        self.assertEqual(r["relinked"], 1)

    def test_unmapped_links_preserved(self):
        mem(self.mem, "project_a.md", "project_a", "에이 — x", "project",
            "밖 [[somewhere_else]] 없음 [[project_missing]]")
        r = brain.import_path(self.vault, str(self.mem))
        _, body = self.read(r["imported"][0]["dest"])
        self.assertIn("[[somewhere_else]]", body)
        self.assertIn("[[project_missing]]", body)

    def test_title_em_dash_and_hyphen(self):
        self.assertEqual(
            brain._description_title("「세컨드브레인」 Claude Code 플러그인 — 대화 중 기억해둬로 쌓기"),
            "「세컨드브레인」 Claude Code 플러그인")
        self.assertEqual(brain._description_title("카카오 알림 헬퍼 - cron에서 푸시"), "카카오 알림 헬퍼")

    def test_title_period_comma(self):
        self.assertEqual(brain._description_title("v0.2.0 공개 완료, 스킬 7개. 테스트 66개"),
                         "v0.2.0 공개 완료")
        self.assertEqual(brain._description_title("사용량 아껴 쓰기. 서브에이전트 최소화"),
                         "사용량 아껴 쓰기")

    def test_title_truncate_and_fallback(self):
        long = "가" * 50
        self.assertEqual(brain._description_title(long), "가" * 40)
        self.assertIsNone(brain._description_title("짧은 설명"))
        self.assertIsNone(brain._description_title(" — 뒤만 있음"))
        mem(self.mem, "project_swhub.md", "project_swhub", "SW Hub 사내 SW 관리 — 월별 확인",
            "project", "b")
        mem(self.mem, "reference_ref.md", "reference_ref", "d", "reference", "r")
        r = brain.import_path(self.vault, str(self.mem))
        files = self.by_file(r)
        self.assertEqual(files["project_swhub.md"]["title"], "SW Hub 사내 SW 관리")
        self.assertEqual(files["project_swhub.md"]["dest"], "projects/swhub.md")  # stem 불변
        self.assertEqual(files["reference_ref.md"]["title"], "ref")  # 폴백

    def test_relink_dry_run_and_apply(self):
        brain.create_note(self.vault, "project", "biz discovery", body="프로젝트")
        n = brain.create_note(self.vault, "note", "메모", body="참고 [[project_biz_discovery]] [[nope_x]]")
        path = Path(n)
        before = path.read_text(encoding="utf-8")
        code, out, _ = self.run_cli("relink", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("변경 파일 1개", out)
        self.assertEqual(before, path.read_text(encoding="utf-8"))
        code, out, _ = self.run_cli("relink")
        self.assertEqual(code, 0)
        after = path.read_text(encoding="utf-8")
        self.assertIn("[[biz-discovery]]", after)
        self.assertIn("[[nope_x]]", after)
        code, out, _ = self.run_cli("relink")
        self.assertIn("변경 파일 0개", out)


if __name__ == "__main__":
    import unittest
    unittest.main()
