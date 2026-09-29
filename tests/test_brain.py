"""brain.py 단위 테스트 — 임시 HOME/볼트에서 실행 (표준 unittest)."""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402

TODAY = date(2026, 9, 30)


class BrainTestCase(unittest.TestCase):
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

    def new(self, ntype, title, **kw):
        return brain.create_note(self.vault, ntype, title, **kw)


# --- 프론트매터 -------------------------------------------------------------

class FrontmatterTest(BrainTestCase):
    def test_roundtrip_lists_korean_colon(self):
        meta = {"title": "결정: DB는 SQLite로", "type": "decision", "created": "2026-09-30",
                "tags": ["데이터베이스", "arch", "a, b"], "source": "https://x.com/a?b=1#frag",
                "links": ["[[001-foo]]", "[[노트]]"], "empty": [], "note": 'say "hi" # not comment'}
        text = brain.dump_frontmatter(meta, "# 본문\n내용\n")
        meta2, body2 = brain.parse_frontmatter(text)
        self.assertEqual(meta2, meta)
        self.assertEqual(body2, "# 본문\n내용\n")

    def test_inline_list_parse(self):
        meta, _ = brain.parse_frontmatter("---\ntags: [a, \"b c\", 한글]\n---\nbody")
        self.assertEqual(meta["tags"], ["a", "b c", "한글"])

    def test_multiline_list_parse(self):
        meta, body = brain.parse_frontmatter("---\ntags:\n  - x\n  - '요: 소'\ntitle: t\n---\nB")
        self.assertEqual(meta["tags"], ["x", "요: 소"])
        self.assertEqual(meta["title"], "t")
        self.assertEqual(body, "B")

    def test_no_frontmatter(self):
        meta, body = brain.parse_frontmatter("# 제목\n본문")
        self.assertEqual(meta, {})
        self.assertEqual(body, "# 제목\n본문")

    def test_nested_metadata_map(self):
        text = "---\nname: x\ndescription: \"d\"\nmetadata: \n  node_type: memory\n  type: project\n---\nb"
        meta, _ = brain.parse_frontmatter(text)
        self.assertEqual(meta["metadata"], {"node_type": "memory", "type": "project"})

    def test_reserved_and_numeric_values_quoted(self):
        meta = {"title": "true", "n": "2026", "d": "2026-09-30", "dash": "- x"}
        meta2, _ = brain.parse_frontmatter(brain.dump_frontmatter(meta))
        self.assertEqual(meta2, meta)
        self.assertIn('title: "true"', brain.dump_frontmatter(meta))

    def test_multiline_value_roundtrip(self):
        meta = {"title": "a\nb"}
        self.assertEqual(brain.parse_frontmatter(brain.dump_frontmatter(meta))[0], meta)


# --- slug · 경로 --------------------------------------------------------------

class SlugPathTest(BrainTestCase):
    def test_slugify_korean(self):
        self.assertEqual(brain.slugify("세컨드 브레인: 설계/초안?"), "세컨드-브레인-설계초안")
        self.assertEqual(brain.slugify("Hello World"), "hello-world")
        self.assertEqual(brain.slugify("???"), "untitled")

    def test_slug_collision(self):
        self.init()
        p1 = self.new("note", "같은 제목", created="2026-09-01")
        p2 = self.new("note", "같은 제목", created="2026-10-01")  # 다른 월이어도 stem 유일
        p3 = self.new("idea", "같은 제목")
        self.assertEqual(p1.stem, "같은-제목")
        self.assertEqual(p2.stem, "같은-제목-2")
        self.assertEqual(p3.stem, "같은-제목-3")

    def test_paths_by_type(self):
        self.init()
        self.assertEqual(self.new("meeting", "회의", created="2026-08-15").relative_to(self.vault).as_posix(),
                         "notes/2026/08/회의.md")
        self.assertTrue(self.new("project", "프로젝트 X").match("projects/프로젝트-x.md"))
        self.assertTrue(self.new("person", "홍길동").match("people/홍길동.md"))

    def test_decision_sequence(self):
        self.init()
        a = self.new("decision", "첫 결정")
        b = self.new("decision", "두번째 결정")
        c = self.new("decision", "첫 결정")
        self.assertEqual(a.name, "001-첫-결정.md")
        self.assertEqual(b.name, "002-두번째-결정.md")
        self.assertEqual(c.name, "003-첫-결정.md")
        meta, _ = brain.parse_frontmatter(a.read_text(encoding="utf-8"))
        self.assertEqual(meta["status"], "open")

    def test_new_cli_required_fields(self):
        self.init()
        code, out, err = self.run_cli("new", "--type", "source", "--title", "좋은 글", "--tags",
                                      "ai, 읽기", "--source", "https://e.com", "--people", "홍 길동",
                                      "--body", "요약")
        self.assertEqual(code, 0, err)
        path = Path(out.strip().split("\n")[0])
        meta, body = brain.parse_frontmatter(path.read_text(encoding="utf-8"))
        for k in ("title", "type", "created", "tags"):
            self.assertIn(k, meta)
        self.assertEqual(meta["tags"], ["ai", "읽기"])
        self.assertEqual(meta["people"], ["[[홍-길동]]"])
        self.assertEqual(body.strip(), "요약")

    def test_new_invalid_date_exit2(self):
        self.init()
        code, _, err = self.run_cli("new", "--type", "decision", "--title", "x", "--revisit", "2026/1/1")
        self.assertEqual(code, 2)
        self.assertIn("revisit", err)

    def test_new_body_file(self):
        self.init()
        f = self.home / "body.md"
        f.write_text("파일 본문", encoding="utf-8")
        code, out, _ = self.run_cli("new", "--type", "note", "--title", "t", "--body-file", str(f))
        self.assertEqual(code, 0)
        self.assertIn("파일 본문", Path(out.strip()).read_text(encoding="utf-8"))


# --- 안전 · 볼트 없음 ---------------------------------------------------------

class SafetyTest(BrainTestCase):
    def test_reject_vault_outside_home(self):
        code, _, err = self.run_cli("init", "--vault", "/tmp/../etc/brain-test")
        self.assertEqual(code, 2)
        self.assertIn("홈 디렉토리 밖", err)

    def test_reject_config_vault_outside_home(self):
        code, _, _ = self.run_cli("config", "set", "vault", "/usr/local/brain")
        self.assertEqual(code, 2)

    def test_reject_import_outside_home(self):
        self.init()
        with self.assertRaises(brain.BrainError):
            brain.import_path(self.vault, "/etc")

    def test_show_outside_vault_rejected(self):
        self.init()
        outside = self.home / "secret.md"
        outside.write_text("x", encoding="utf-8")
        code, _, _ = self.run_cli("show", str(outside))
        self.assertEqual(code, 2)

    def test_index_silent_without_vault(self):
        code, out, _ = self.run_cli("index", "--head", "40")
        self.assertEqual((code, out), (0, ""))
        code, out, _ = self.run_cli("index")
        self.assertEqual((code, out), (0, ""))

    def test_other_commands_exit3_without_vault(self):
        self.assertEqual(self.run_cli("search", "foo")[0], 3)
        self.assertEqual(self.run_cli("config", "get")[0], 3)
        self.assertEqual(self.run_cli("new", "--type", "note", "--title", "x")[0], 3)

    def test_config_set_get(self):
        self.assertEqual(self.run_cli("config", "set", "index_head", "25")[0], 0)
        self.init()
        code, out, _ = self.run_cli("config", "get", "index_head", "--json")
        self.assertEqual(json.loads(out), {"index_head": 25})
        self.assertEqual(self.run_cli("config", "set", "index_head", "abc")[0], 2)


# --- 검색 ---------------------------------------------------------------------

class SearchTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.init()

    def test_tokenize(self):
        self.assertEqual(brain.tokenize("검색엔진 BM25, Hello-World"),
                         ["검색", "색엔", "엔진", "bm25", "hello", "world"])
        self.assertEqual(brain.tokenize("책"), ["책"])

    def test_title_beats_body(self):
        d = TODAY.isoformat()
        self.new("note", "쿠버네티스 운영", created=d, body="클러스터 이야기")
        self.new("note", "주간 회고", created=d, body="이번 주에 쿠버네티스 운영을 조금 했다")
        res = brain.search(self.vault, "쿠버네티스", today=TODAY)
        self.assertEqual(res[0]["title"], "쿠버네티스 운영")
        self.assertGreater(res[0]["score"], res[1]["score"])

    def test_recency_ranks_newer_first(self):
        self.new("note", "테라폼 메모", created=(TODAY - timedelta(days=400)).isoformat(), body="테라폼")
        self.new("note", "테라폼 메모 새것", created=TODAY.isoformat(), body="테라폼")
        res = brain.search(self.vault, "테라폼", today=TODAY)
        self.assertEqual(res[0]["title"], "테라폼 메모 새것")

    def test_recency_factor_half_life(self):
        f0 = brain.recency_factor(TODAY.isoformat(), TODAY)
        f90 = brain.recency_factor((TODAY - timedelta(days=90)).isoformat(), TODAY)
        self.assertAlmostEqual(f0, 1.0)
        self.assertAlmostEqual(f90, 0.75)

    def test_filters(self):
        self.new("decision", "캐시 전략", tags=["perf"], project="alpha", created="2026-09-01")
        self.new("note", "캐시 메모", tags=["perf"], project="beta", created="2026-06-01")
        self.assertEqual([r["type"] for r in brain.search(self.vault, "캐시", ntype="decision")], ["decision"])
        self.assertEqual(len(brain.search(self.vault, "캐시", project="beta")), 1)
        self.assertEqual(len(brain.search(self.vault, "캐시", tag="PERF")), 2)
        self.assertEqual(len(brain.search(self.vault, "캐시", since="2026-08-01")), 1)
        self.assertEqual(len(brain.search(self.vault, "캐시", limit=1)), 1)

    def test_search_json_snippet(self):
        self.new("note", "아무거나", body="첫 줄\n두번째 줄에 레디스 이야기\n셋째\n넷째 레디스")
        code, out, _ = self.run_cli("search", "레디스", "--json")
        data = json.loads(out)
        self.assertEqual(code, 0)
        r = data["results"][0]
        for k in ("path", "title", "type", "score", "snippet"):
            self.assertIn(k, r)
        self.assertEqual(len(r["snippet"]), 2)
        self.assertTrue(all("레디스" in s for s in r["snippet"]))

    def test_search_no_results(self):
        code, out, _ = self.run_cli("search", "존재하지않는단어xyz")
        self.assertEqual(code, 0)
        self.assertIn("기록 없음", out)


# --- 인덱스 · 리뷰 · 링크 · 결정 ----------------------------------------------

class IndexReviewTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.init()

    def test_index_four_sections(self):
        self.new("project", "알파")
        self.new("note", "알파 노트", project="알파")
        self.new("decision", "알파 결정", project="알파", revisit="2026-01-01")
        content, stats = brain.build_index(self.vault, TODAY)
        for h in ("## 최근 기록", "## 프로젝트", "## 미해결 결정", "## 고아 노트"):
            self.assertIn(h, content)
        self.assertIn("[[알파]] · 2개", content)
        self.assertIn("**도래**", content)
        self.assertEqual(stats["open_decisions"], 1)

    def test_open_decisions_sorted_by_revisit(self):
        self.new("decision", "나중", revisit="2026-12-01")
        self.new("decision", "먼저", revisit="2026-10-01")
        self.new("decision", "끝난 것", status="decided")
        content, _ = brain.build_index(self.vault, TODAY)
        sec = content.split("## 미해결 결정")[1].split("##")[0]
        self.assertLess(sec.index("먼저"), sec.index("나중"))
        self.assertNotIn("끝난 것", sec)

    def test_project_hub_autoblock_idempotent(self):
        hub = self.new("project", "베타")
        n = self.new("note", "베타 메모", project="베타")
        brain.build_index(self.vault, TODAY)
        text1 = hub.read_text(encoding="utf-8")
        self.assertIn(f"[[{n.stem}]]", text1)
        brain.build_index(self.vault, TODAY)
        self.assertEqual(hub.read_text(encoding="utf-8"), text1)
        self.assertEqual(text1.count(brain.AUTO_BEGIN), 1)

    def test_orphans_and_head(self):
        a = self.new("note", "고아 하나")
        code, out, _ = self.run_cli("index", "--head", "3")
        self.assertEqual(code, 0)
        self.assertEqual(len(out.rstrip("\n").split("\n")), 3)
        content = (self.vault / "BRAIN.md").read_text(encoding="utf-8")
        self.assertIn(f"[[{a.stem}]]", content.split("## 고아 노트")[1])

    def test_link_both_sides_no_dup(self):
        a = self.new("note", "에이")
        b = self.new("note", "비")
        self.assertEqual(self.run_cli("link", str(a), "비")[0], 0)
        self.run_cli("link", str(a), str(b))
        ma = brain.parse_frontmatter(a.read_text(encoding="utf-8"))[0]
        mb = brain.parse_frontmatter(b.read_text(encoding="utf-8"))[0]
        self.assertEqual(ma["links"], ["[[비]]"])
        self.assertEqual(mb["links"], ["[[에이]]"])

    def test_supersede(self):
        old = self.new("decision", "옛 결정")
        new = self.new("decision", "새 결정", status="decided")
        code, _, err = self.run_cli("decide", "--supersede", str(old), str(new))
        self.assertEqual(code, 0, err)
        mo = brain.parse_frontmatter(old.read_text(encoding="utf-8"))[0]
        mn = brain.parse_frontmatter(new.read_text(encoding="utf-8"))[0]
        self.assertEqual(mo["status"], "superseded")
        self.assertEqual(mn["supersedes"], [f"[[{old.stem}]]"])
        content = (self.vault / "BRAIN.md").read_text(encoding="utf-8")
        self.assertNotIn("옛 결정", content.split("## 미해결 결정")[1].split("##")[0])

    def test_supersede_requires_decision(self):
        n = self.new("note", "노트")
        d = self.new("decision", "결정")
        self.assertEqual(self.run_cli("decide", "--supersede", str(n), str(d))[0], 2)

    def test_review_suggestions_and_due(self):
        a = self.new("note", "첫째", tags=["k8s", "ops"], created=TODAY.isoformat())
        b = self.new("note", "둘째", tags=["k8s", "ops"], created=TODAY.isoformat())
        self.new("note", "셋째", tags=["k8s"], created=TODAY.isoformat())
        self.new("note", "옛날", created="2025-01-01")
        self.new("decision", "재검토", revisit="2026-09-01")
        r = brain.review(self.vault, days=7, today=TODAY)
        top = r["link_suggestions"][0]
        self.assertEqual({top["a"], top["b"]}, {a.relative_to(self.vault).as_posix(),
                                                 b.relative_to(self.vault).as_posix()})
        self.assertEqual(len(r["link_suggestions"]), 3)
        self.assertNotIn("옛날", [n["title"] for n in r["new_notes"]])
        self.assertEqual([d["title"] for d in r["due_decisions"]], ["재검토"])
        brain.add_link(self.vault, str(a), str(b))
        r2 = brain.review(self.vault, days=7, today=TODAY)
        self.assertEqual(len(r2["link_suggestions"]), 2)

    def test_review_cli_json(self):
        code, out, _ = self.run_cli("review", "--days", "7", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(set(json.loads(out)), {"days", "since", "new_notes", "due_decisions",
                                                "orphans", "link_suggestions"})

    def test_show_json(self):
        p = self.new("note", "보기", tags=["t"], body="본문입니다")
        code, out, _ = self.run_cli("show", p.stem, "--json")
        d = json.loads(out)
        self.assertEqual((code, d["meta"]["title"], d["body"].strip()), (0, "보기", "본문입니다"))


# --- 가져오기 -----------------------------------------------------------------

class ImportTest(BrainTestCase):
    def setUp(self):
        super().setUp()
        self.init()
        self.src = self.home / "src"
        self.src.mkdir()

    def test_markdown_folder_import_and_dup_skip(self):
        (self.src / "a.md").write_text("# 헤딩 제목\n본문", encoding="utf-8")
        (self.src / "b.md").write_text("---\ntitle: 프론트\ntags: [x]\ncreated: 2026-01-02\n---\n내용",
                                       encoding="utf-8")
        (self.src / ".obsidian").mkdir()
        (self.src / ".obsidian" / "c.md").write_text("무시", encoding="utf-8")
        before = {p: p.read_text(encoding="utf-8") for p in self.src.rglob("*.md")}
        r = brain.import_path(self.vault, str(self.src))
        titles = sorted(x["title"] for x in r["imported"])
        self.assertEqual(titles, ["프론트", "헤딩 제목"])
        self.assertTrue((self.vault / "notes/2026/01/프론트.md").exists())
        r2 = brain.import_path(self.vault, str(self.src))
        self.assertEqual((len(r2["imported"]), len(r2["skipped"])), (0, 2))
        self.assertEqual(before, {p: p.read_text(encoding="utf-8") for p in self.src.rglob("*.md")})

    def test_dry_run_writes_nothing(self):
        (self.src / "a.md").write_text("# 드라이\n", encoding="utf-8")
        before = sorted(p.as_posix() for p in self.vault.rglob("*"))
        code, out, _ = self.run_cli("import", str(self.src), "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("미리보기", out)
        self.assertEqual(before, sorted(p.as_posix() for p in self.vault.rglob("*")))

    def test_memory_format_mapping(self):
        mem = self.src / "proj" / "memory"
        mem.mkdir(parents=True)
        (mem / "MEMORY.md").write_text("- [a](a.md) — 인덱스", encoding="utf-8")
        (mem / "feedback_x.md").write_text(
            "---\nname: 날짜 확인 필수\ndescription: 설명 한 줄\ntype: feedback\n"
            "modified: 2026-08-14T05:10:52.298Z\n---\n본문", encoding="utf-8")
        (mem / "project_y.md").write_text(
            "---\nname: project_swhub\ndescription: \"허브: 설명\"\nmetadata: \n  type: project\n"
            "  modified: 2026-08-10T12:05:27.302Z\n---\n프로젝트 본문", encoding="utf-8")
        (mem / "reference_z.md").write_text(
            "---\nname: ref\ndescription: d\nmetadata:\n  type: reference\n---\nr", encoding="utf-8")
        (mem / "user_w.md").write_text(
            "---\nname: 사용자\ndescription: d\ntype: user\n---\nu", encoding="utf-8")
        r = brain.import_path(self.vault, str(mem))
        by_title = {x["title"]: x for x in r["imported"]}
        self.assertEqual(by_title["날짜 확인 필수"]["type"], "note")
        self.assertEqual(by_title["swhub"]["type"], "project")
        self.assertEqual(by_title["ref"]["type"], "source")
        self.assertEqual(by_title["사용자"]["type"], "person")
        self.assertEqual(len(r["skipped"]), 1)  # MEMORY.md
        dest = self.vault / by_title["날짜 확인 필수"]["dest"]
        meta, body = brain.parse_frontmatter(dest.read_text(encoding="utf-8"))
        self.assertEqual(meta["created"], "2026-08-14")
        self.assertIn("feedback", meta["tags"])
        self.assertTrue(body.startswith("> 설명 한 줄"))
        self.assertTrue(by_title["swhub"]["dest"].startswith("projects/"))

    def test_import_vault_itself_rejected(self):
        with self.assertRaises(brain.BrainError):
            brain.import_path(self.vault, str(self.vault))


class InitTest(BrainTestCase):
    def test_init_structure_idempotent(self):
        self.init()
        for d in ("notes", "decisions", "projects", "people", "BRAIN.md", "inbox.md"):
            self.assertTrue((self.vault / d).exists(), d)
        self.assertEqual(len(list((self.vault / "notes").rglob("*.md"))), 1)
        self.init()
        self.assertEqual(len(list((self.vault / "notes").rglob("*.md"))), 1)

    def test_init_custom_vault_saved_to_config(self):
        code, _, _ = self.run_cli("init", "--vault", str(self.home / "my-vault"))
        self.assertEqual(code, 0)
        cfg = json.loads((self.home / ".config/second-brain/config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["vault"], str(self.home / "my-vault"))

    def test_init_git(self):
        if not shutil.which("git"):
            self.skipTest("git 없음")
        self.assertEqual(self.run_cli("init", "--git")[0], 0)
        self.assertTrue((self.vault / ".git").is_dir())


if __name__ == "__main__":
    unittest.main()
