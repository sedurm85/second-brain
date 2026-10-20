"""글 공방(studio) 테스트 — 임시 HOME/볼트/콘텐츠 폴더 + 가짜 Claude 명령 + 스레드 서버 (표준 unittest)."""
import json
import os
import shutil
import sys
import tempfile
import textwrap
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402
import studio  # noqa: E402

FAKE_CLAUDE = textwrap.dedent('''
    import json, re, sys, time
    from pathlib import Path
    prompt = sys.stdin.read()
    out = lambda o: print(json.dumps(o, ensure_ascii=False), flush=True)
    if "FAIL_PLEASE" in prompt:
        out({"type": "result", "is_error": True, "result": "일부러 실패"})
        sys.exit(1)
    if prompt.startswith("/blog-write"):
        out({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "WebSearch", "input": {"query": "에이전트 보안"}}]}})
        body = "# 가짜 원본\\n\\n" + ("본문 " * 40) + "\\n\\n## Sources\\n1. a\\n2. b\\n\\n## Thumbnail Prompt\\n```\\n푸른 16:9 썸네일\\n```\\n"
        Path("writer/2026-10-20-fake-topic.md").write_text('---\\ntitle: "가짜 원본"\\ndate: 2026-10-20\\n---\\n\\n' + body, encoding="utf-8")
        out({"type": "assistant", "message": {"content": [{"type": "text", "text": "원본을 썼어요"}]}})
        out({"type": "result", "is_error": False, "result": "끝\\nSTUDIO_FILE: writer/2026-10-20-fake-topic.md"})
    else:
        files = re.findall(r"^- (writer/\\S+\\.md)$", prompt, re.M)
        if "NOFILES" in prompt:
            files = []
        for f in files:
            Path(f).write_text('---\\ntitle: "짧은 제목"\\n---\\n\\n변환 본문이에요.\\n', encoding="utf-8")
        out({"type": "result", "is_error": False, "result": "STUDIO_FILES: " + ", ".join(files)})
''')


def doc(title, body, subtitle=None):
    sub = f'subtitle: "{subtitle}"\n' if subtitle is not None else ""
    return f'---\ntitle: "{title}"\n{sub}date: 2026-10-20\n---\n\n{body}\n'


class StudioUnitTest(unittest.TestCase):
    """검수 규칙(순수 함수)."""

    def test_cafe_hard_limit_and_markdown(self):
        text = doc("카페 제목", "## 소제목\n" + "가" * 1600 + "\n**굵게**")
        checks = {c["key"]: c for c in studio.check_doc(brain, text, "naver-cafe")}
        self.assertEqual(checks["chars"]["level"], "fail")
        self.assertEqual(checks["bold"]["level"], "fail")
        self.assertEqual(checks["markdown"]["level"], "fail")
        self.assertIn("제목 기호 #", checks["markdown"]["label"])
        self.assertEqual(checks["title"]["level"], "ok")

    def test_remember_checks(self):
        text = doc("보안팀장 16년차의 첫 허용", "보안팀장 16년차, 올해 처음으로 'AI 금지'를 풀었습니다.\n\n" + "가" * 850)
        checks = {c["key"]: c for c in studio.check_doc(brain, text, "remember")}
        self.assertNotIn("title", checks)  # 리멤버엔 제목 칸이 없다
        self.assertEqual(checks["chars"]["level"], "ok")
        self.assertEqual(checks["markdown"]["level"], "ok")
        self.assertEqual(checks["hashtags"]["level"], "ok")
        long_tag = {c["key"]: c for c in studio.check_doc(brain, doc("t", "가" * 1200 + "\n\n#DevOps #커리어"), "remember")}
        self.assertEqual(long_tag["chars"]["level"], "warn")  # 1,100자 넘으면 줄이기
        self.assertEqual(long_tag["hashtags"]["label"], "해시태그 2개")
        bad = {c["key"]: c for c in studio.check_doc(brain, doc("t", "## 소제목\n" + "가" * 1600), "remember")}
        self.assertEqual(bad["chars"]["level"], "fail")
        self.assertEqual(bad["markdown"]["level"], "fail")
        self.assertEqual(studio.FILE_RE.match("2026-10-20-ai-ban-remember.md").group(3), "remember")

    def test_title_limit_counts_every_char(self):
        text = doc("가" * 31, "본문")
        checks = {c["key"]: c for c in studio.check_doc(brain, text, "naver-blog")}
        self.assertEqual(checks["title"]["level"], "fail")
        self.assertEqual(checks["title"]["label"], "제목 31/30자")

    def test_brunch_subtitle_required(self):
        checks = {c["key"]: c for c in studio.check_doc(brain, doc("제목", "본문"), "brunch")}
        self.assertEqual(checks["subtitle"]["level"], "fail")
        ok = {c["key"]: c for c in studio.check_doc(brain, doc("제목", "본문", "부제"), "brunch")}
        self.assertEqual(ok["subtitle"]["level"], "ok")

    def test_medium_korean_and_ratio(self):
        en = doc("Title", "This is an English body text only " * 20)
        checks = {c["key"]: c for c in studio.check_doc(brain, en, "medium", original_chars=700)}
        self.assertEqual(checks["korean"]["level"], "fail")
        self.assertIn("원본 대비", checks["chars"]["detail"])

    def test_original_checks(self):
        body = "가" * 10001 + "\n\n## Sources\n" + "\n".join(f"{i}. s" for i in range(1, 16))
        checks = {c["key"]: c for c in studio.check_doc(brain, doc("원본", body))}
        self.assertEqual(checks["chars"]["level"], "ok")
        self.assertEqual(checks["sources"]["label"], "출처 15개")
        foot = "## Sources\n" + "\n".join(f"[^{i}]: 출처 {i}" for i in range(1, 18)) + "\n\n## Thumbnail Prompt\nx"
        checks = {c["key"]: c for c in studio.check_doc(brain, doc("원본", foot))}
        self.assertEqual(checks["sources"]["label"], "출처 17개")

    def test_copy_text_drops_frontmatter_and_h1(self):
        t = studio.copy_text(brain, doc("t", "# 큰 제목\n\n첫 문단"), "naver-blog")
        self.assertEqual(t, "첫 문단\n")

    def test_thumbnail_prompt(self):
        self.assertEqual(studio.thumbnail_prompt("## Thumbnail Prompt\n```\n푸른 화면\n```\n"), "푸른 화면")

    def test_thumbnail_options_split_and_strip(self):
        fenced = ("## Thumbnail Prompt\n\n### Option 1 (Recommended) — 다섯 문\n\n```\n돌 관문 16:9\n```\n\n"
                  "### Option 2 (Alternative) — 다섯 강\n\n```text\n강 삼각주 16:9\n```\n\n## 다음\n무시")
        self.assertEqual(studio.thumbnail_options(fenced), [
            {"label": "Option 1 (Recommended) — 다섯 문", "prompt": "돌 관문 16:9"},
            {"label": "Option 2 (Alternative) — 다섯 강", "prompt": "강 삼각주 16:9"}])
        bare = "## Thumbnail Prompt\n### A안\n첫 장면\n### B안\n둘째 장면\n"
        self.assertEqual([o["prompt"] for o in studio.thumbnail_options(bare)], ["첫 장면", "둘째 장면"])
        self.assertEqual(studio.thumbnail_options("## Thumbnail Prompt\n그냥 한 덩어리\n"), [{"label": "", "prompt": "그냥 한 덩어리"}])
        self.assertEqual(studio.thumbnail_options("## Sources\n1. a"), [])
        ai = "## Thumbnail Prompt (AI Image Generation)\n\nStyle: 네온\n\nPrompt: 로봇 가재\n\n## 끝"
        self.assertEqual(studio.thumbnail_options(ai), [{"label": "", "prompt": "Style: 네온\n\nPrompt: 로봇 가재"}])
        dalle = '### DALL-E 3 썸네일 프롬프트\n\n> **Prompt**: "A dramatic illustration"\n\n---\n\n*기준일*'
        self.assertEqual(studio.thumbnail_options(dalle), [{"label": "", "prompt": "A dramatic illustration"}])
        bold = "## Thumbnail Prompt\n**Style:** 영화풍\n**Prompt:** 대장간"
        self.assertEqual(studio.thumbnail_options(bold)[0]["prompt"], "Style: 영화풍\nPrompt: 대장간")


class StudioServeTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = Path(os.path.realpath(self.tmp))
        self._env = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_STUDIO_DIR",
                                                    "SECOND_BRAIN_STUDIO_CMD")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        self.vault = self.home / "brain"
        for d in ("notes", "decisions", "projects", "people"):
            (self.vault / d).mkdir(parents=True)
        (self.vault / "BRAIN.md").write_text("# BRAIN\n", encoding="utf-8")
        self.content = self.home / "content"
        w = self.content / "writer"
        (w / "backup").mkdir(parents=True)
        (w / "2026-10-01-old-post.md").write_text(doc("예전 원본", "본문"), encoding="utf-8")
        (w / "2026-10-01-old-post-naver-cafe.md").write_text(doc("예전 카페", "카페 본문"), encoding="utf-8")
        (w / "2026-10-01-old-post-remember.md").write_text(doc("예전 리멤버", "리멤버 본문"), encoding="utf-8")
        (w / "backup" / "2026-03-10-ancient.md").write_text(doc("백업 원본", "옛 본문"), encoding="utf-8")
        (w / "notes.txt").write_text("무시", encoding="utf-8")
        fake = self.home / "fake_claude.py"
        fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        os.environ["SECOND_BRAIN_STUDIO_DIR"] = str(self.content)
        os.environ["SECOND_BRAIN_STUDIO_CMD"] = f"{sys.executable} {fake}"
        self.srv = brain.make_server(self.vault, 0, quiet=True)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        import threading
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.token = self.get("/api/session")["token"]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def get(self, path, status=200):
        try:
            with urllib.request.urlopen(self.base + path) as r:
                self.assertEqual(r.status, status)
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, status, e.read().decode("utf-8"))
            return json.loads(e.read().decode("utf-8") or "{}")

    def post(self, path, body, status=200, token=True):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "X-Brain-Token": self.token if token else ""})
        try:
            with urllib.request.urlopen(req) as r:
                self.assertEqual(r.status, status)
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            self.assertEqual(e.code, status, raw)
            return json.loads(raw or "{}")

    def wait_job(self, pid, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            d = self.get(f"/api/studio/piece?id={pid}")
            jobs = d["jobs"]
            if jobs and jobs[-1]["status"] not in ("queued", "running"):
                return d, jobs[-1]
            time.sleep(0.1)
        self.fail("작업이 끝나지 않았어요")

    def test_overview_scans_active_and_backup(self):
        d = self.get("/api/studio")
        self.assertTrue(d["content_ok"])
        stems = {r["stem"]: r for r in d["recent"]}
        self.assertEqual(stems["2026-10-01-old-post"]["platforms"], ["naver-cafe", "remember"])
        self.assertEqual(stems["2026-10-01-old-post"]["title"], "예전 원본")
        self.assertTrue(stems["2026-03-10-ancient"]["backup"])
        self.assertEqual(d["history"]["per_day"]["2026-10-01"], 3)
        self.assertEqual(d["history"]["per_platform"]["naver-cafe"], 1)
        self.assertEqual([p["key"] for p in d["platforms"]], studio.PLATFORM_ORDER)

    def test_studio_page_served(self):
        with urllib.request.urlopen(self.base + "/studio") as r:
            self.assertIn("text/html", r.headers["Content-Type"])

    def test_keyword_roundtrip_and_hide(self):
        r = self.post("/api/studio/keyword", {"text": "에이전트 권한 사고", "memo": "오늘 회의에서", "link": "https://ex.com/a"})
        kws = self.get("/api/studio")["keywords"]
        self.assertEqual([k["title"] for k in kws], ["에이전트 권한 사고"])
        self.assertEqual(kws[0]["source"], "https://ex.com/a")
        self.assertIn("오늘 회의에서", kws[0]["memo"])
        self.post("/api/studio/keyword", {"text": "링크", "link": "javascript:alert(1)"}, status=400)
        self.post("/api/studio/keyword", {"text": "  "}, status=400)
        self.post("/api/studio/keyword-hide", {"id": r["id"]})
        self.assertEqual(self.get("/api/studio")["keywords"], [])

    def test_write_requires_token(self):
        self.post("/api/studio/keyword", {"text": "x"}, status=403, token=False)

    def test_write_job_attaches_original_then_adapt_selected_only(self):
        k = self.post("/api/studio/keyword", {"text": "에이전트 보안"})["id"]
        pc = self.post("/api/studio/piece", {"title": "에이전트 보안 사고", "keywords": [k], "brief": "실무자 시각"})["piece"]
        d, job = self.wait_job(pc["id"])
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(d["original"], "writer/2026-10-20-fake-topic.md")
        self.assertEqual(d["thumbnail"], "푸른 16:9 썸네일")
        self.assertTrue(any("WebSearch" in s for s in job["steps"]))
        self.assertEqual(self.get("/api/studio")["keywords"][0]["used_by"], [pc["id"]])
        self.post("/api/studio/adapt", {"id": pc["id"], "platforms": []}, status=400)
        self.post("/api/studio/adapt", {"id": pc["id"], "platforms": ["naver-cafe", "brunch", "nope"]})
        d, job = self.wait_job(pc["id"])
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(job["platforms"], ["brunch", "naver-cafe"])
        self.assertEqual(sorted(d["files"]), ["brunch", "naver-cafe"])
        self.assertEqual(d["stage"], "adapted")
        f = self.get("/api/studio/file?" + urllib.parse.urlencode({"path": d["files"]["naver-cafe"], "platform": "naver-cafe"}))
        self.assertEqual(f["copy"], "변환 본문이에요.\n")
        self.assertTrue(f["editable"])
        self.post("/api/studio/posted", {"id": pc["id"], "platform": "naver-cafe", "posted": True,
                                         "url": "https://cafe.naver.com/x/1"})
        ov = self.get("/api/studio")
        self.assertEqual(ov["history"]["posted"]["naver-cafe"], 1)
        self.assertEqual(next(p for p in ov["pieces"] if p["id"] == pc["id"])["stage"], "posted")

    def test_adapt_without_files_is_failure(self):
        a = self.post("/api/studio/import", {"path": "writer/2026-10-01-old-post.md"})["piece"]
        self.post("/api/studio/adapt", {"id": a["id"], "platforms": ["brunch"], "note": "NOFILES"})
        d, job = self.wait_job(a["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("하나도", job["error"])

    def test_failed_job_reports_error(self):
        pc = self.post("/api/studio/piece", {"title": "FAIL_PLEASE"})["piece"]
        d, job = self.wait_job(pc["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("일부러 실패", job["error"])
        self.assertIsNone(d["original"])

    def test_import_existing_original_once(self):
        a = self.post("/api/studio/import", {"path": "writer/2026-10-01-old-post.md"})["piece"]
        b = self.post("/api/studio/import", {"path": "writer/2026-10-01-old-post.md"})["piece"]
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(a["title"], "예전 원본")
        self.post("/api/studio/import", {"path": "writer/2026-10-01-old-post-naver-cafe.md"}, status=400)
        d = self.get(f"/api/studio/piece?id={a['id']}")
        self.assertEqual(d["files"], {"naver-cafe": "writer/2026-10-01-old-post-naver-cafe.md",
                                      "remember": "writer/2026-10-01-old-post-remember.md"})

    def test_path_guards(self):
        self.get("/api/studio/file?path=../brain/BRAIN.md", status=400)
        self.get("/api/studio/file?path=writer/../../brain/BRAIN.md", status=400)
        self.get("/api/studio/file?path=writer/notes.txt", status=400)
        self.get("/api/studio/file?path=writer/backup/2026-03-10-ancient.md")  # 백업은 읽기 가능
        self.post("/api/studio/save", {"path": "writer/backup/2026-03-10-ancient.md", "text": "---\n---\n"}, status=400)
        self.post("/api/studio/save", {"path": "writer/2026-10-01-old-post.md", "text": "프론트매터 없음"}, status=400)
        r = self.post("/api/studio/save", {"path": "writer/2026-10-01-old-post.md", "text": doc("고친 제목", "새 본문")})
        self.assertTrue(r["ok"])
        self.assertIn("고친 제목", (self.content / "writer/2026-10-01-old-post.md").read_text(encoding="utf-8"))

    def test_recover_marks_running_jobs_stopped(self):
        studio.save_state({"pieces": {}, "jobs": [{"id": "j1", "status": "running", "piece_id": "p"}],
                           "hidden_keywords": []})
        studio.JobRunner(brain)
        self.assertEqual(studio.load_state()["jobs"][0]["status"], "stopped")


if __name__ == "__main__":
    unittest.main()
