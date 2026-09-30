"""export --html: 오프라인 공유·인쇄용 단일 HTML 내보내기.

결정(상황·결정·이유·되돌아볼 날)·프로젝트 허브·일지·노트·사람·일정 섹션,
위키링크 → 페이지 내 앵커(존재/부재 모두), --since·--type·--project 필터,
--no-body, HTML 정합성(h2 개수=섹션 개수), 외부 자산 없음(사용자 링크 제외)을 검증한다."""
import json
import os
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


class _StrictHTMLParser(HTMLParser):
    """html.parser로 문서를 훑기만 하며, 파싱 오류가 나면 예외를 던진다."""

    def error(self, message):
        raise AssertionError(f"HTML 파싱 오류: {message}")


class ExportHtmlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        (self.vault / "decisions").mkdir(parents=True)
        (self.vault / "projects").mkdir(parents=True)
        (self.vault / "notes" / "2026" / "09").mkdir(parents=True)
        (self.vault / "journal" / "2026").mkdir(parents=True)
        (self.vault / "people").mkdir(parents=True)
        (self.vault / "events" / "2026").mkdir(parents=True)
        (self.vault / "BRAIN.md").write_text("# BRAIN\n", encoding="utf-8")
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault)}), encoding="utf-8")

        (self.vault / "decisions" / "dashboard-server.md").write_text(
            "---\n"
            'title: "대시보드를 로컬 서버로 띄울지"\n'
            "type: decision\n"
            "created: 2026-09-10\n"
            "status: open\n"
            "revisit: 2026-10-01\n"
            'project: "세컨드브레인"\n'
            "tags: [infra]\n"
            "---\n"
            "# 대시보드를 로컬 서버로 띄울지\n\n"
            "## 상황\n\n"
            "정적 HTML과 로컬 서버 중 고민. [[project-secondbrain]] 참고. "
            "없는 노트 [[nope-nonexistent]]도 참고.\n\n"
            "## 결정\n\n"
            "로컬 서버로 간다.\n\n"
            "## 이유\n\n"
            "실시간 갱신이 **필요**해서.\n\n"
            "## 되돌아볼 날짜\n",
            encoding="utf-8",
        )
        (self.vault / "projects" / "project-secondbrain.md").write_text(
            "---\n"
            'title: "세컨드브레인"\n'
            "type: project\n"
            "created: 2026-09-01\n"
            "---\n"
            "# 세컨드브레인\n\n## 목표\n\n개인 지식 볼트를 CLI로 관리한다.\n\n## 메모\n",
            encoding="utf-8",
        )
        (self.vault / "notes" / "2026" / "09" / "note-cli-tips.md").write_text(
            "---\n"
            'title: "CLI 팁 모음"\n'
            "type: note\n"
            "created: 2026-09-15\n"
            'project: "세컨드브레인"\n'
            "tags: [cli, tips]\n"
            'summary: "자주 쓰는 brain.py 명령 정리"\n'
            "---\n"
            "# CLI 팁 모음\n\n## 목록\n\n"
            "- `doctor`로 점검\n"
            "- [ ] 체크박스 항목\n"
            "- [x] 완료된 항목\n\n"
            "일반 문단입니다. [링크](https://example.com) 도 있어요.\n",
            encoding="utf-8",
        )
        (self.vault / "journal" / "2026" / "2026-09-20.md").write_text(
            "---\n"
            'title: "2026-09-20"\n'
            "type: journal\n"
            "created: 2026-09-20\n"
            "---\n"
            "# 2026-09-20\n\n## 오늘\n\n- 내보내기 기능 설계함\n\n## 잘한 것\n\n## 내일 첫 일\n",
            encoding="utf-8",
        )
        (self.vault / "people" / "kim-choel-su.md").write_text(
            "---\n"
            'title: "김철수"\n'
            "type: person\n"
            "created: 2026-09-05\n"
            "---\n"
            "# 김철수\n\n## 맥락\n\n동료. [[dashboard-server]] 결정에 참여.\n",
            encoding="utf-8",
        )
        (self.vault / "events" / "2026" / "2026-09-25-team-sync.md").write_text(
            "---\n"
            'title: "팀 싱크"\n'
            "type: event\n"
            "created: 2026-09-25\n"
            "event_date: 2026-09-25\n"
            'location: "회의실 A"\n'
            "---\n"
            "# 팀 싱크\n\n## 준비\n\n## 동선\n\n## 메모\n\n간단한 메모.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _out(self, name="out.html"):
        return self.home / name

    def test_export_contains_all_sections_and_titles(self):
        out = self._out()
        res = brain.export_html(self.vault, out)
        self.assertTrue(out.is_file())
        self.assertEqual(res["notes"], 6)
        self.assertEqual(res["path"], str(out))
        self.assertEqual(res["bytes"], out.stat().st_size)

        text = out.read_text(encoding="utf-8")
        for title in ("대시보드를 로컬 서버로 띄울지", "세컨드브레인", "CLI 팁 모음",
                     "2026-09-20", "김철수", "팀 싱크"):
            self.assertIn(title, text)
        for section in ("목차", "결정", "프로젝트 허브", "일지·회고", "노트", "사람", "일정 노트"):
            self.assertIn(section, text)
        # 결정의 상황·결정·이유 섹션 라벨이 렌더링됐는지
        self.assertIn("상황", text)
        self.assertIn("이유", text)
        self.assertIn("되돌아볼 날 2026-10-01", text)

    def test_wikilinks_resolve_and_broken_stay_plain(self):
        out = self._out()
        brain.export_html(self.vault, out)
        text = out.read_text(encoding="utf-8")
        # 존재하는 노트로의 위키링크는 인페이지 앵커
        self.assertIn('href="#n-project-secondbrain"', text)
        self.assertIn('href="#n-dashboard-server"', text)
        # 존재하지 않는 노트는 앵커 없이 흐린 텍스트 클래스만
        self.assertNotIn('href="#n-nope-nonexistent"', text)
        self.assertIn("wikilink-broken", text)
        self.assertIn("nope-nonexistent", text)

    def test_since_filter_reduces_notes(self):
        out_all = self._out("all.html")
        out_since = self._out("since.html")
        res_all = brain.export_html(self.vault, out_all)
        res_since = brain.export_html(self.vault, out_since, since="2026-09-16")
        self.assertLess(res_since["notes"], res_all["notes"])
        self.assertEqual(res_since["notes"], 2)  # 2026-09-16 이후: 일지(09-20) + 팀 싱크(09-25)

    def test_type_filter_reduces_notes(self):
        out = self._out()
        res = brain.export_html(self.vault, out, types=["decision"])
        self.assertEqual(res["notes"], 1)
        text = out.read_text(encoding="utf-8")
        self.assertIn("대시보드를 로컬 서버로 띄울지", text)
        self.assertNotIn("김철수", text)

    def test_project_filter(self):
        out = self._out()
        res = brain.export_html(self.vault, out, projects=["세컨드브레인"])
        self.assertEqual(res["notes"], 2)  # project: 필드로 소속된 결정 + CLI 팁 모음(프로젝트 노트 자체는 project 필드가 없음)

    def test_no_body_omits_body_text(self):
        out = self._out()
        brain.export_html(self.vault, out, with_body=False)
        text = out.read_text(encoding="utf-8")
        self.assertNotIn("실시간 갱신", text)
        self.assertNotIn("개인 지식 볼트를 CLI로 관리한다", text)
        # 제목·메타는 그대로 남아 있어야 함
        self.assertIn("대시보드를 로컬 서버로 띄울지", text)

    def test_html_is_well_formed_and_section_count_matches_h2(self):
        out = self._out()
        brain.export_html(self.vault, out)
        text = out.read_text(encoding="utf-8")
        parser = _StrictHTMLParser()
        parser.feed(text)  # 예외 없이 끝나면 통과
        # 목차 + 결정/프로젝트허브/일지/노트/사람/일정 = 7
        self.assertEqual(text.count("<h2"), 7)

    def test_no_external_assets_except_user_links(self):
        out = self._out()
        brain.export_html(self.vault, out)
        text = out.read_text(encoding="utf-8")
        self.assertNotIn("<link ", text)
        self.assertNotIn("fonts.googleapis", text)
        import re
        urls = re.findall(r'(?:href|src)="(https?://[^"]+)"', text)
        self.assertEqual(urls, ["https://example.com"])

    def test_cmd_export_cli_and_json(self):
        out = self._out("cli.html")
        rc = brain.main(["export", "--html", str(out), "--json"])
        self.assertEqual(rc, brain.EXIT_OK)
        self.assertTrue(out.is_file())

    def test_cmd_export_unknown_type_errors(self):
        out = self._out("bad.html")
        rc = brain.main(["export", "--html", str(out), "--type", "no-such-type"])
        self.assertNotEqual(rc, brain.EXIT_OK)
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
