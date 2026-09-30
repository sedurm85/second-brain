"""검색 연산자·패싯(v0.29) — type:/tag:/project:/since:/until:/has:/status:/is:orphan/
-부정어/"정확한 문구" 조합, facets, cmd_search 패싯 라인, /api/search 응답 모양."""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402

TODAY = date(2026, 9, 30)


def ago(k):
    return (TODAY - timedelta(days=k)).isoformat()


class SearchOpsTestCase(unittest.TestCase):
    """볼트: 타입·태그·날짜가 섞인 노트들. 결정 3(open/decided/superseded), 고아 노트 1."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = Path(os.path.realpath(self.tmp))
        self._old_home = os.environ.get("HOME")
        self._old_vault = os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ["HOME"] = str(self.home)
        self.vault = self.home / "brain"
        code, _, err = self.run_cli("init")
        self.assertEqual(code, 0, err)
        mk = lambda *a, **kw: brain.create_note(self.vault, *a, **kw)  # noqa: E731
        self.n_decision_infra = mk("decision", "쿠버네티스 이전 결정", tags=["인프라", "AI"],
                                   project="플랫폼", created=ago(10), status="open",
                                   extra={"summary": "쿠버네티스로 옮기기로 했다"})
        self.n_decision_old = mk("decision", "레거시 유지 결정", tags=["인프라"],
                                 created=ago(60), status="superseded")
        self.n_decision_decided = mk("decision", "보안 정책 결정", tags=["보안"],
                                     created=ago(5), status="decided",
                                     extra={"revisit": ago(-30)})
        self.n_note_ai = mk("note", "AI 에이전트 메모", tags=["AI", "인프라"], project="플랫폼",
                            created=ago(3), body="# AI 에이전트 메모\n\n[[쿠버네티스 이전 결정]] 참고\n")
        self.n_note_orphan = mk("note", "아무 링크 없는 노트", tags=["잡담"], created=ago(2),
                                body="# 아무 링크 없는 노트\n\n혼자 있다\n")
        self.n_idea = mk("idea", "보안 자동화 아이디어", tags=["보안", "AI"], created=ago(1),
                         body="# 보안 자동화 아이디어\n\n제로트러스트 이야기\n")
        self.n_source = mk("source", "쿠버네티스 참고 자료", tags=["인프라"], created=ago(45),
                           body="# 쿠버네티스 참고 자료\n\n원문 링크\n")

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

    def search(self, q, **kw):
        return brain.dash_search(self.vault, q, today=TODAY, **kw)


# --- parse_query ------------------------------------------------------------

class ParseQueryTest(unittest.TestCase):
    def test_plain_query_has_no_filters(self):
        terms, filters = brain.parse_query("쿠버네티스 이전")
        self.assertEqual(terms, ["쿠버네티스", "이전"])
        self.assertEqual(filters, {})

    def test_type_repeatable_or(self):
        _, filters = brain.parse_query("type:decision type:note")
        self.assertEqual(filters["type"], ["decision", "note"])

    def test_tag_repeatable_case_insensitive(self):
        _, filters = brain.parse_query("tag:AI tag:인프라")
        self.assertEqual(filters["tag"], ["ai", "인프라"])

    def test_project_since_until(self):
        _, filters = brain.parse_query("project:플랫폼 since:2026-01-01 until:2026-09-01")
        self.assertEqual(filters["project"], "플랫폼")
        self.assertEqual(filters["since"], "2026-01-01")
        self.assertEqual(filters["until"], "2026-09-01")

    def test_relative_since(self):
        for tok in ("7d", "2w", "3m"):
            _, filters = brain.parse_query(f"since:{tok}")
            self.assertEqual(filters["since"], tok)

    def test_has_and_status_and_orphan(self):
        _, filters = brain.parse_query("has:summary has:revisit status:decided is:orphan")
        self.assertEqual(filters["has"], ["summary", "revisit"])
        self.assertEqual(filters["status"], ["decided"])
        self.assertTrue(filters["orphan"])

    def test_negative_term(self):
        terms, filters = brain.parse_query("보안 -잡담")
        self.assertEqual(terms, ["보안"])
        self.assertEqual(filters["neg"], ["잡담"])

    def test_exact_phrase(self):
        terms, filters = brain.parse_query('"제로트러스트 이야기" 아이디어')
        self.assertIn("제로트러스트 이야기", filters["phrase"])
        self.assertIn("제로트러스트 이야기", terms)
        self.assertIn("아이디어", terms)

    def test_invalid_date_dropped_not_raised(self):
        _, filters = brain.parse_query("since:내일")
        self.assertNotIn("since", filters)

    def test_invalid_status_dropped(self):
        _, filters = brain.parse_query("status:모름")
        self.assertNotIn("status", filters)


# --- dash_search 연산자 ------------------------------------------------------

class DashSearchOperatorsTest(SearchOpsTestCase):
    def test_type_filter_or(self):
        res = self.search("type:decision")
        self.assertTrue(res)
        self.assertTrue(all(r["type"] == "decision" for r in res))

    def test_type_filter_or_multiple(self):
        res = self.search("type:decision type:idea")
        self.assertEqual({r["type"] for r in res}, {"decision", "idea"})

    def test_tag_filter_and(self):
        res = self.search("tag:AI tag:인프라")
        paths = {r["path"] for r in res}
        self.assertIn(self.n_note_ai.relative_to(self.vault).as_posix(), paths)
        self.assertNotIn(self.n_idea.relative_to(self.vault).as_posix(), paths)  # AI만 있고 인프라는 없음

    def test_project_filter(self):
        res = self.search("project:플랫폼")
        paths = {r["path"] for r in res}
        self.assertEqual(paths, {self.n_decision_infra.relative_to(self.vault).as_posix(),
                                 self.n_note_ai.relative_to(self.vault).as_posix()})

    def test_since_absolute(self):
        res = self.search(f"since:{ago(6)}")
        for r in res:
            self.assertGreaterEqual(r["created"], ago(6))

    def test_since_relative_days(self):
        res = self.search("since:7d")
        for r in res:
            self.assertGreaterEqual(r["created"], ago(7))
        self.assertNotIn(self.n_decision_old.relative_to(self.vault).as_posix(), {r["path"] for r in res})

    def test_since_relative_weeks_months(self):
        w = self.search("since:2w")
        m = self.search("since:3m")
        self.assertTrue(len(m) >= len(w))

    def test_until_filter(self):
        res = self.search(f"until:{ago(50)}")
        for r in res:
            self.assertLessEqual(r["created"], ago(50))
        self.assertIn(self.n_decision_old.relative_to(self.vault).as_posix(), {r["path"] for r in res})

    def test_has_summary(self):
        res = self.search("has:summary")
        paths = {r["path"] for r in res}
        self.assertIn(self.n_decision_infra.relative_to(self.vault).as_posix(), paths)
        self.assertNotIn(self.n_note_orphan.relative_to(self.vault).as_posix(), paths)

    def test_has_revisit(self):
        res = self.search("has:revisit")
        paths = {r["path"] for r in res}
        self.assertEqual(paths, {self.n_decision_decided.relative_to(self.vault).as_posix()})

    def test_status_filter(self):
        res = self.search("status:superseded")
        self.assertEqual([r["path"] for r in res], [self.n_decision_old.relative_to(self.vault).as_posix()])

    def test_is_orphan(self):
        res = self.search("is:orphan")
        paths = {r["path"] for r in res}
        self.assertIn(self.n_note_orphan.relative_to(self.vault).as_posix(), paths)
        self.assertNotIn(self.n_note_ai.relative_to(self.vault).as_posix(), paths)

    def test_negative_term_excludes(self):
        res = self.search("쿠버네티스 -참고")
        titles = [r["title"] for r in res]
        self.assertIn("쿠버네티스 이전 결정", titles)
        self.assertNotIn("쿠버네티스 참고 자료", titles)

    def test_exact_phrase_must_appear(self):
        res = self.search('"제로트러스트 이야기"')
        self.assertEqual([r["title"] for r in res], ["보안 자동화 아이디어"])

    def test_combination_type_tag_since(self):
        res = self.search("type:decision tag:인프라 since:30d")
        self.assertEqual([r["path"] for r in res],
                         [self.n_decision_infra.relative_to(self.vault).as_posix()])

    def test_filters_only_sorted_by_created_desc(self):
        res = self.search("type:decision")
        created = [r["created"] for r in res]
        self.assertEqual(created, sorted(created, reverse=True))
        for r in res:
            self.assertIn("score", r)
            self.assertIn("snippet", r)

    def test_plain_query_backward_compatible(self):
        res = self.search("쿠버네티스")
        self.assertIsInstance(res, list)
        for r in res:
            for k in ("path", "title", "type", "snippets", "summary", "score"):
                self.assertIn(k, r)

    def test_empty_query_still_raises(self):
        with self.assertRaises(brain.BrainError):
            self.search("")


# --- facets ------------------------------------------------------------------

class FacetsTest(SearchOpsTestCase):
    def test_facets_type_counts(self):
        res = self.search("쿠버네티스")
        self.assertIn("decision", res.facets["types"])
        self.assertIn("source", res.facets["types"])

    def test_facets_top_tags_capped_at_8(self):
        res = self.search("type:decision type:note type:idea type:source")
        self.assertLessEqual(len(res.facets["tags"]), 8)

    def test_facets_are_pre_limit(self):
        full = self.search("type:decision", limit=100)
        limited = self.search("type:decision", limit=1)
        self.assertEqual(len(limited), 1)
        self.assertEqual(full.facets["types"], limited.facets["types"])

    def test_applied_reflects_parsed_filters(self):
        res = self.search("type:decision tag:인프라")
        self.assertEqual(res.applied["type"], ["decision"])
        self.assertEqual(res.applied["tag"], ["인프라"])


# --- cmd_search CLI -----------------------------------------------------------

class CmdSearchOperatorsTest(SearchOpsTestCase):
    def test_operator_query_via_cli(self):
        code, out, err = self.run_cli("search", "type:decision")
        self.assertEqual(code, 0, err)
        self.assertIn("decision", out)
        self.assertIn("타입:", out)

    def test_facets_line_present(self):
        code, out, _ = self.run_cli("search", "type:decision")
        self.assertEqual(code, 0)
        lines = out.strip().split("\n")
        self.assertTrue(any(l.startswith("타입:") for l in lines))

    def test_json_includes_facets_and_applied(self):
        code, out, _ = self.run_cli("search", "type:decision", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIn("facets", data)
        self.assertIn("applied", data)
        self.assertEqual(data["applied"]["type"], ["decision"])

    def test_legacy_flags_still_work(self):
        code, out, err = self.run_cli("search", "쿠버네티스", "--type", "decision")
        self.assertEqual(code, 0, err)
        self.assertIn("쿠버네티스 이전 결정", out)
        self.assertNotIn("쿠버네티스 참고 자료", out)

    def test_no_results_message_has_no_facets_line(self):
        code, out, _ = self.run_cli("search", "존재하지않는단어xyz123")
        self.assertEqual(code, 0)
        self.assertIn("기록 없음", out)
        self.assertNotIn("타입:", out)


# --- /api/search --------------------------------------------------------------

class ApiSearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = Path(os.path.realpath(self.tmp))
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.vault = self.home / "brain"
        for d in ("notes", "decisions", "projects", "people"):
            (self.vault / d).mkdir(parents=True)
        (self.vault / "BRAIN.md").write_text("# BRAIN\n", encoding="utf-8")
        brain.create_note(self.vault, "decision", "결정 노트", created=ago(1))
        brain.create_note(self.vault, "note", "일반 노트", created=ago(2))
        web = self.home / "web"
        web.mkdir()
        (web / "index.html").write_text("<!doctype html><title>대시보드</title>", encoding="utf-8")
        self.srv = brain.make_server(self.vault, 0, web_dir=web, today=TODAY, quiet=True)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.th = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.th.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def api(self, route, **params):
        url = self.base + route + ("?" + urllib.parse.urlencode(params) if params else "")
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_type_operator_via_api(self):
        code, d = self.api("/api/search", q="type:note")
        self.assertEqual(code, 200)
        self.assertTrue(all(h["type"] == "note" for h in d["hits"]))
        self.assertIn("facets", d)
        self.assertIn("applied", d)
        self.assertEqual(d["applied"]["type"], ["note"])


if __name__ == "__main__":
    unittest.main()
