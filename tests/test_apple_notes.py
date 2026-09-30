"""애플 메모 어댑터(apple_notes.py)와 brain.py `import --apple-notes` 연결 테스트.
실제 osascript/Notes.app은 부르지 않는다 — 고정 JSON을 출력하는 가짜 스크립트로 대체."""
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_brain import BrainTestCase  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import apple_notes  # noqa: E402
import brain  # noqa: E402  (test_brain이 scripts 경로를 추가함)


# ---------------------------------------------------------------------------
# html_to_markdown 단위 테스트
# ---------------------------------------------------------------------------

class HtmlToMarkdownTest(unittest.TestCase):
    def test_headings(self):
        self.assertEqual(apple_notes.html_to_markdown("<h1>제목</h1><h2>부제</h2>"), "# 제목\n\n## 부제")

    def test_bold_italic(self):
        md = apple_notes.html_to_markdown("<p>본문 <b>굵게</b>와 <i>기울임</i></p>")
        self.assertEqual(md, "본문 **굵게**와 *기울임*")

    def test_unordered_list(self):
        md = apple_notes.html_to_markdown("<ul><li>사과</li><li>우유</li></ul>")
        self.assertEqual(md, "- 사과\n- 우유")

    def test_ordered_list(self):
        md = apple_notes.html_to_markdown("<ol><li>가</li><li>나</li></ol>")
        self.assertEqual(md, "1. 가\n2. 나")

    def test_links(self):
        md = apple_notes.html_to_markdown('<p>링크: <a href="https://example.com">예제</a></p>')
        self.assertEqual(md, "링크: [예제](https://example.com)")

    def test_br_and_div_become_newlines(self):
        md = apple_notes.html_to_markdown("line1<br>line2<div>div줄</div>")
        self.assertEqual(md, "line1\nline2\ndiv줄")

    def test_entities_unescaped(self):
        md = apple_notes.html_to_markdown("&amp; &lt;tag&gt; &nbsp;entity")
        self.assertNotIn("&amp;", md)
        self.assertIn("& <tag>", md)

    def test_nested_formatting(self):
        md = apple_notes.html_to_markdown("<b><i>중첩</i></b>")
        self.assertEqual(md, "***중첩***")

    def test_images_leave_attachment_marker(self):
        md = apple_notes.html_to_markdown("<p>사진 <img src='x.png'> 참고</p>")
        self.assertIn("(첨부 1개)", md)

    def test_multiple_attachments_counted(self):
        md = apple_notes.html_to_markdown("<p><img src='a.png'><object data='b.pdf'></object></p>")
        self.assertIn("(첨부 2개)", md)

    def test_collapses_more_than_two_blank_lines(self):
        md = apple_notes.html_to_markdown("<p>a</p>\n\n\n\n<p>b</p>")
        self.assertEqual(md, "a\n\nb")

    def test_empty_html_returns_empty_string(self):
        self.assertEqual(apple_notes.html_to_markdown(""), "")
        self.assertEqual(apple_notes.html_to_markdown(None), "")


# ---------------------------------------------------------------------------
# fetch_notes / import 통합 테스트
# ---------------------------------------------------------------------------

FIXTURE_NOTES = [
    {"id": "n1", "title": "회의 메모", "folder": "업무",
     "created": "2026-01-05T09:00:00.000Z", "modified": "2026-01-05T09:30:00.000Z",
     "body_html": "<p>참석자 <b>김철수</b></p><ul><li>안건1</li><li>안건2</li></ul>",
     "plaintext": "참석자 김철수\n안건1\n안건2"},
    {"id": "n2", "title": "장보기", "folder": "할일",
     "created": "2026-03-10T08:00:00.000Z", "modified": "2026-03-10T08:00:00.000Z",
     "body_html": "<ul><li>사과</li><li>우유</li></ul>", "plaintext": "사과\n우유"},
    {"id": "n3", "title": "아이디어", "folder": "업무",
     "created": "2026-06-20T21:00:00.000Z", "modified": "2026-06-20T21:00:00.000Z",
     "body_html": "<p>새 기능 아이디어</p>", "plaintext": "새 기능 아이디어"},
]


def _fixture_script(notes):
    """지정한 notes를 --folder 인자(argv[1], JSON 배열)로 필터링해 JSON을 출력하는 가짜 osascript."""
    payload = json.dumps(notes, ensure_ascii=False)
    return (
        "import json, sys\n"
        f"notes = json.loads({payload!r})\n"
        "wanted = json.loads(sys.argv[1]) if len(sys.argv) > 1 else []\n"
        "if wanted:\n"
        "    notes = [n for n in notes if n.get('folder') in wanted]\n"
        "sys.stdout.write(json.dumps({'notes': notes}, ensure_ascii=False))\n"
    )


PERMISSION_SCRIPT = (
    "import sys\n"
    "sys.stderr.write('execution error: 메모에 접근할 수 없습니다. (-1743)\\n')\n"
    "sys.exit(1)\n"
)


class AppleNotesTestCase(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.init()
        apple_notes.LAST_ERROR = None

    def write_script(self, name, content):
        p = self.home / name
        p.write_text(content, encoding="utf-8")
        return p

    def use_fixture(self, notes=None):
        p = self.write_script("fake_apple_notes.py", _fixture_script(notes if notes is not None else FIXTURE_NOTES))
        self._env = self.env_override("SECOND_BRAIN_APPLE_NOTES_CMD", f"{sys.executable} {p}")
        self._env.__enter__()

    def use_permission_error(self):
        p = self.write_script("fake_apple_notes_perm.py", PERMISSION_SCRIPT)
        self._env = self.env_override("SECOND_BRAIN_APPLE_NOTES_CMD", f"{sys.executable} {p}")
        self._env.__enter__()

    def tearDown(self):
        if getattr(self, "_env", None):
            self._env.__exit__(None, None, None)
        super().tearDown()

    def env_override(self, key, value):
        import os

        class _Ctx:
            def __enter__(ctx):
                ctx.old = os.environ.get(key)
                os.environ[key] = value
                return ctx

            def __exit__(ctx, *a):
                if ctx.old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = ctx.old

        return _Ctx()

    def imported_notes(self):
        return [n for n in brain.load_notes(self.vault) if n.meta.get("imported_from", "").startswith("apple-notes:")]


class ImportCreatesNotesTest(AppleNotesTestCase):
    def test_creates_three_notes_with_frontmatter(self):
        self.use_fixture()
        code, out, err = self.run_cli("import", "--apple-notes")
        self.assertEqual(code, 0, err)
        self.assertIn("생성 3", out)
        notes = self.imported_notes()
        self.assertEqual(len(notes), 3)
        by_id = {n.meta["imported_from"]: n for n in notes}
        n1 = by_id["apple-notes:n1"]
        self.assertEqual(n1.title, "회의 메모")
        self.assertEqual(n1.type, "note")
        self.assertEqual(n1.created, "2026-01-05")
        self.assertIn("apple-notes", n1.tags)
        self.assertIn("업무", n1.tags)
        self.assertEqual(n1.meta["source_modified"], "2026-01-05T09:30:00.000Z")
        self.assertIn("**김철수**", n1.body)
        self.assertIn("- 안건1", n1.body)
        self.assertIn("맥락: Apple Notes 「업무」에서 가져옴", n1.body)
        self.assertTrue(n1.body.startswith("# 회의 메모"))

    def test_rerun_is_idempotent_and_skips(self):
        self.use_fixture()
        self.run_cli("import", "--apple-notes")
        code, out, err = self.run_cli("import", "--apple-notes")
        self.assertEqual(code, 0, err)
        self.assertIn("생성 0", out)
        self.assertIn("갱신 0", out)
        self.assertIn("건너뜀 3", out)
        self.assertEqual(len(self.imported_notes()), 3)

    def test_bumped_modified_triggers_update(self):
        self.use_fixture()
        self.run_cli("import", "--apple-notes")
        bumped = [dict(n) for n in FIXTURE_NOTES]
        bumped[0] = dict(bumped[0], title="회의 메모(수정)", body_html="<p>바뀐 내용</p>",
                          modified="2026-09-01T00:00:00.000Z")
        self.use_fixture(bumped)
        code, out, err = self.run_cli("import", "--apple-notes")
        self.assertEqual(code, 0, err)
        self.assertIn("갱신 1", out)
        self.assertIn("건너뜀 2", out)
        notes = self.imported_notes()
        self.assertEqual(len(notes), 3)  # 새 파일이 아니라 기존 파일을 갱신
        n1 = next(n for n in notes if n.meta["imported_from"] == "apple-notes:n1")
        self.assertIn("바뀐 내용", n1.body)
        self.assertEqual(n1.meta["source_modified"], "2026-09-01T00:00:00.000Z")

    def test_folder_filter(self):
        self.use_fixture()
        code, out, err = self.run_cli("import", "--apple-notes", "--folder", "할일")
        self.assertEqual(code, 0, err)
        self.assertIn("생성 1", out)
        notes = self.imported_notes()
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].title, "장보기")

    def test_since_filter(self):
        self.use_fixture()
        code, out, err = self.run_cli("import", "--apple-notes", "--since", "2026-03-01")
        self.assertEqual(code, 0, err)
        self.assertIn("생성 2", out)
        titles = sorted(n.title for n in self.imported_notes())
        self.assertEqual(titles, ["아이디어", "장보기"])

    def test_dry_run_writes_nothing(self):
        self.use_fixture()
        code, out, err = self.run_cli("import", "--apple-notes", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("미리보기", out)
        self.assertIn("생성 3", out)
        self.assertEqual(self.imported_notes(), [])

    def test_permission_error_exits_with_code_2(self):
        self.use_permission_error()
        code, out, err = self.run_cli("import", "--apple-notes")
        self.assertEqual(code, brain.EXIT_INPUT)
        self.assertIn("-1743", err)
        self.assertEqual(self.imported_notes(), [])


if __name__ == "__main__":
    unittest.main()
