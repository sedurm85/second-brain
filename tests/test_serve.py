"""대시보드 서버(serve) 테스트 — 임시 HOME/볼트 + 스레드 서버 + urllib (표준 unittest)."""
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
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402

TODAY = date(2026, 9, 30)


def ago(k):
    return (TODAY - timedelta(days=k)).isoformat()


class ServeTestCase(unittest.TestCase):
    """볼트: 프로젝트 1, 사람 1, 노트 3, 결정 3(open 1·decided 1·superseded 1)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = Path(os.path.realpath(self.tmp))
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.vault = self.home / "brain"
        for d in ("notes", "decisions", "projects", "people"):
            (self.vault / d).mkdir(parents=True)
        (self.vault / "BRAIN.md").write_text("# BRAIN\n", encoding="utf-8")
        mk = lambda *a, **kw: brain.create_note(self.vault, *a, **kw)  # noqa: E731
        self.proj = mk("project", "알파", created=ago(20), body="# 알파\n")
        self.person = mk("person", "홍길동", created=ago(19))
        self.n1 = mk("note", "검색 메모", created=ago(2), project="알파", tags=["검색"],
                     body="# 검색 메모\n\n[[알파]] 와 [[없는노트]] 참조. [[알파]] 다시.\n",
                     extra={"links": ["[[알파]]", "[[홍길동]]", "[[유령]]"]})
        self.n2 = mk("idea", "그래프 아이디어", created=ago(5), project="알파",
                     body="# 그래프 아이디어\n\n[[검색-메모]] 참고\n")
        self.n3 = mk("note", "오래된 노트", created=ago(40), body="# 오래된 노트\n\n본문\n")
        self.d_old = mk("decision", "옛 결정", created=ago(30), status="decided", project="알파")
        self.d_new = mk("decision", "새 결정", created=ago(10), status="decided", project="알파")
        brain.supersede(self.vault, self.d_old, self.d_new)
        self.d_open = mk("decision", "열린 결정", created=ago(1), status="open",
                         revisit=(TODAY + timedelta(days=3)).isoformat())
        self.web = self.home / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("<!doctype html><title>대시보드</title>", encoding="utf-8")
        (self.web / "app.js").write_text("console.log(1)", encoding="utf-8")
        self.start(self.web)

    def start(self, web_dir):
        self.srv = brain.make_server(self.vault, 0, web_dir=web_dir, today=TODAY,
                                     quiet=True)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.th = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.th.start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()

    def tearDown(self):
        self.stop()
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def get(self, route, **params):
        url = self.base + route + ("?" + urllib.parse.urlencode(params) if params else "")
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def api(self, route, **params):
        code, headers, body = self.get(route, **params)
        self.assertIn("application/json", headers["Content-Type"])
        return code, json.loads(body.decode("utf-8"))


class SummaryTest(ServeTestCase):
    def test_counts_and_this_week(self):
        code, d = self.api("/api/summary")
        self.assertEqual(code, 200)
        self.assertEqual(d["counts"], {"notes": 3, "decisions": 3, "projects": 1, "people": 1,
                                       "total": 8})
        self.assertEqual(d["this_week"], {"new_notes": 2, "new_decisions": 1})
        self.assertEqual(d["vault"], str(self.vault))
        self.assertIn("generated_at", d)

    def test_open_decisions_and_recent(self):
        _, d = self.api("/api/summary")
        self.assertEqual([x["title"] for x in d["open_decisions"]], ["열린 결정"])
        self.assertEqual(d["open_decisions"][0]["days_left"], 3)
        created = [r["created"] for r in d["recent"]]
        self.assertEqual(created, sorted(created, reverse=True))
        self.assertEqual(set(d["recent"][0]), {"path", "title", "type", "created", "project", "tags"})

    def test_headers_no_cache_and_korean_raw(self):
        code, headers, body = self.get("/api/summary")
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertIn("열린 결정".encode("utf-8"), body)  # ensure_ascii=False
        self.assertNotIn(b"\\u", body)


class GraphTest(ServeTestCase):
    def test_edges_dedup_and_missing_targets_excluded(self):
        _, g = self.api("/api/graph")
        ids = {n["id"] for n in g["nodes"]}
        self.assertEqual(len(g["nodes"]), 8)
        pairs = [tuple(sorted((e["source"], e["target"]))) for e in g["edges"]]
        self.assertEqual(len(pairs), len(set(pairs)), "무방향 중복 에지")
        for e in g["edges"]:
            self.assertIn(e["source"], ids)
            self.assertIn(e["target"], ids)
        self.assertNotIn("없는노트", ids | {e["target"] for e in g["edges"]})
        self.assertNotIn("유령", {e["target"] for e in g["edges"]})
        # 위키링크 2회 + 프론트매터 1회로 걸린 검색메모-알파는 1개
        self.assertEqual(pairs.count(tuple(sorted(("검색-메모", "알파")))), 1)
        self.assertIn(tuple(sorted(("검색-메모", "홍길동"))), pairs)  # 프론트매터 links
        self.assertIn(tuple(sorted(("검색-메모", "그래프-아이디어"))), pairs)  # 본문 위키링크

    def test_degree_matches_edges(self):
        _, g = self.api("/api/graph")
        from collections import Counter
        deg = Counter()
        for e in g["edges"]:
            deg[e["source"]] += 1
            deg[e["target"]] += 1
        for n in g["nodes"]:
            self.assertEqual(n["degree"], deg[n["id"]], n["id"])
        node = next(n for n in g["nodes"] if n["id"] == "검색-메모")
        self.assertEqual(node["degree"], 3)  # 알파·홍길동·그래프-아이디어
        self.assertEqual(node["path"], self.n1.relative_to(self.vault).as_posix())


class SearchTest(ServeTestCase):
    def test_search_matches_cli_shape(self):
        code, res = self.api("/api/search", q="검색 메모", limit=5)
        self.assertEqual(code, 200)
        self.assertEqual(res[0]["title"], "검색 메모")
        for k in ("path", "title", "type", "score", "snippets"):
            self.assertIn(k, res[0])
        self.assertIsInstance(res[0]["snippets"], list)
        cli = brain.search(self.vault, "검색 메모", limit=5, today=TODAY)
        self.assertEqual([r["path"] for r in res], [r["path"] for r in cli])

    def test_search_empty_query_400(self):
        code, d = self.api("/api/search", q="")
        self.assertEqual(code, 400)
        self.assertIn("error", d)


class NoteTest(ServeTestCase):
    def test_note_links_in_out(self):
        rel = self.n1.relative_to(self.vault).as_posix()
        code, d = self.api("/api/note", path=rel)
        self.assertEqual(code, 200)
        self.assertEqual(d["title"], "검색 메모")
        self.assertEqual(d["frontmatter"]["project"], "알파")
        self.assertIn("[[없는노트]]", d["body"])
        self.assertEqual(d["links_out"], sorted(["알파", "홍길동"]))
        self.assertIn("그래프-아이디어", d["links_in"])

    def test_path_escape_rejected_400(self):
        for bad in ("../etc/passwd.md", "notes/../../x.md", "/etc/hosts.md", "~/x.md",
                    str(self.n1)):
            code, d = self.api("/api/note", path=bad)
            self.assertEqual(code, 400, bad)
            self.assertIn("error", d)

    def test_symlink_outside_vault_rejected(self):
        outside = self.home / "secret.md"
        outside.write_text("# 비밀\n", encoding="utf-8")
        (self.vault / "notes" / "link.md").symlink_to(outside)
        code, _ = self.api("/api/note", path="notes/link.md")
        self.assertEqual(code, 400)

    def test_missing_note_404(self):
        code, d = self.api("/api/note", path="notes/nope.md")
        self.assertEqual(code, 404)
        self.assertIn("error", d)


class TimelineDecisionsProjectsTest(ServeTestCase):
    def test_timeline_sorted_desc_and_window(self):
        code, t = self.api("/api/timeline", days=30)
        self.assertEqual(code, 200)
        dates = [x["date"] for x in t]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertTrue(all(x >= ago(29) for x in dates))
        self.assertNotIn(ago(30), dates)
        self.assertNotIn(ago(40), dates)
        first = t[0]["items"][0]
        self.assertEqual(set(first), {"path", "title", "type", "project"})
        _, t60 = self.api("/api/timeline", days=60)
        self.assertIn(ago(40), [x["date"] for x in t60])

    def test_timeline_bad_days_400(self):
        code, _ = self.api("/api/timeline", days="abc")
        self.assertEqual(code, 400)

    def test_decisions_classified_with_days_left(self):
        _, d = self.api("/api/decisions")
        self.assertEqual([x["title"] for x in d["open"]], ["열린 결정"])
        self.assertEqual([x["title"] for x in d["decided"]], ["새 결정"])
        self.assertEqual([x["title"] for x in d["superseded"]], ["옛 결정"])
        self.assertEqual(d["open"][0]["days_left"], 3)
        self.assertIsNone(d["decided"][0]["days_left"])
        self.assertEqual(d["decided"][0]["supersedes"], [self.d_old.stem])
        self.assertEqual(set(d["open"][0]), {"path", "title", "created", "revisit", "days_left",
                                             "project", "supersedes"})

    def test_projects_aggregate(self):
        _, ps = self.api("/api/projects")
        self.assertEqual(len(ps), 1)
        p = ps[0]
        self.assertEqual(p["name"], "알파")
        self.assertEqual(p["path"], "projects/알파.md")
        self.assertEqual(p["note_count"], 2)
        self.assertEqual(p["decision_count"], 2)
        self.assertEqual(p["last_activity"], ago(2))
        self.assertEqual(p["recent"][0]["title"], "검색 메모")


class StaticTest(ServeTestCase):
    def test_root_serves_index_html(self):
        code, headers, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertIn("대시보드".encode("utf-8"), body)

    def test_web_file_content_type_and_escape(self):
        code, headers, _ = self.get("/web/app.js")
        self.assertEqual(code, 200)
        self.assertIn("javascript", headers["Content-Type"])
        code, _, _ = self.get("/web/%2e%2e/brain/BRAIN.md")
        self.assertEqual(code, 404)

    def test_root_404_when_index_missing(self):
        self.stop()
        empty = self.home / "empty-web"
        empty.mkdir()
        self.start(empty)
        code, d = self.api("/")
        self.assertEqual(code, 404)
        self.assertIn("error", d)

    def test_unknown_api_404_and_server_survives_500(self):
        code, _ = self.api("/api/nope")
        self.assertEqual(code, 404)
        orig = brain.dash_graph
        brain.dash_graph = lambda v: 1 / 0
        try:
            import io
            from contextlib import redirect_stderr
            with redirect_stderr(io.StringIO()):
                code, d = self.api("/api/graph")
        finally:
            brain.dash_graph = orig
        self.assertEqual(code, 500)
        self.assertIn("ZeroDivisionError", d["error"])
        code, _ = self.api("/api/summary")
        self.assertEqual(code, 200)


class DemoVaultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = os.path.realpath(self.tmp)

    def tearDown(self):
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_demo_vault_counts(self):
        v = brain.build_demo_vault(today=TODAY)
        self.assertEqual(v, Path(os.path.realpath(self.tmp)) / ".cache" / "second-brain" / "demo")
        self.assertTrue(brain.vault_exists(v))
        s = brain.dash_summary(v, TODAY)
        self.assertEqual(s["counts"], {"notes": 22, "decisions": 6, "projects": 3, "people": 2,
                                       "total": 33})
        d = brain.dash_decisions(v, TODAY)
        self.assertEqual({k: len(x) for k, x in d.items()},
                         {"open": 2, "decided": 3, "superseded": 1})
        self.assertTrue(any(0 <= (x["days_left"] or -1) <= 7 for x in d["open"]))
        g = brain.dash_graph(v)
        self.assertGreaterEqual(len(g["edges"]), 25)
        dates = {n["created"] for n in g["nodes"]}
        self.assertTrue(all(ago(60) <= x <= TODAY.isoformat() for x in dates))
        self.assertEqual(len(brain.dash_projects(v)), 3)

    def test_demo_rebuild_is_idempotent(self):
        brain.build_demo_vault(today=TODAY)
        v = brain.build_demo_vault(today=TODAY)
        self.assertEqual(brain.dash_summary(v, TODAY)["counts"]["total"], 33)


if __name__ == "__main__":
    unittest.main()
