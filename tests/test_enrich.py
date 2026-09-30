"""enrich: 프롬프트 구성, JSON 파싱(펜스·잡담), 적용(제목·요약·태그·링크 검증), CLI(가짜 Claude), 요약 검색 반영."""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


class EnrichTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_ASK_CMD")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        (cfgdir / "config.json").write_text(json.dumps({"vault": str(self.vault), "calendar": {"sources": []}}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            brain.main(["init", "--vault", str(self.vault)])
            brain.main(["new", "--type", "note", "--title", "미디엄 변환도 한글로 작성", "--tags", "claude-memory,feedback", "--body", "미디엄 변환 시 영어로 작성하면 안 된다. 한글로. 태그만 영문.", "--created", "2026-04-01"])
            brain.main(["new", "--type", "note", "--title", "사용자가 JD를 공유하면 JD 분석과 기업 분석을 함께", "--tags", "claude-memory", "--body", "JD를 받으면 핏 분석과 재무·연봉·복지·평판 기업 분석을 항상 함께 한다.", "--created", "2026-05-01"])
            brain.main(["new", "--type", "note", "--title", "이미 정제된 노트", "--body", "본문", "--created", "2026-06-01"])
        # imported_from 표시 + 하나는 summary 있음
        for n in brain.load_notes(self.vault):
            if n.type != "note" or "시작하기" in n.title:  # init이 만든 안내 노트는 제외
                continue
            n.meta["imported_from"] = "memory"
            if n.title == "이미 정제된 노트":
                n.meta["summary"] = "이미 있음"
            brain.write_note(n.path, n.meta, n.body)
        # 가짜 Claude: 프롬프트에서 stem 목록을 읽어 그럴듯한 JSON을 낸다(펜스+잡담 포함)
        fake = self.home / "fake_claude.py"
        fake.write_text('''#!/opt/miniconda3/bin/python3
import sys, json, re
p = sys.stdin.read()
items = json.loads(p.rsplit("[노트들]",1)[1])
out = []
for it in items:
    stem = it["stem"]
    out.append({"stem": stem, "title": ("미디엄 변환은 한글로" if "미디엄" in it["title"] else "JD 공유 시 기업 분석 세트"),
                "summary": "요약: " + it["body"][:40], "tags": ["글쓰기", "규칙"], "links": [o["stem"] for o in items if o["stem"] != stem] + ["없는-노트"]})
print("네, 결과예요.\\n```json\\n" + json.dumps(out, ensure_ascii=False) + "\\n```\\n끝.")
''', encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        os.environ["SECOND_BRAIN_ASK_CMD"] = sys.executable + " " + str(fake)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_json_parse_variants(self):
        fake = self.home / "fake2.py"
        fake.write_text("import sys; sys.stdin.read(); print('설명 먼저 [{\"a\": 1}] 뒤 잡담')", encoding="utf-8")
        os.environ["SECOND_BRAIN_ASK_CMD"] = sys.executable + " " + str(fake)
        self.assertEqual(brain._run_claude_json("x"), [{"a": 1}])

    def test_enrich_cli_and_search(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = brain.main(["enrich", "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertIn("(dry-run)", buf.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = brain.main(["enrich"])
        self.assertEqual(rc, 0, buf.getvalue())
        out = buf.getvalue()
        self.assertIn("정제 2개", out)  # 요약 있는 노트는 제외
        notes = {n.stem: n for n in brain.load_notes(self.vault)}
        m = next(n for n in notes.values() if n.meta.get("original_title") == "미디엄 변환도 한글로 작성")
        self.assertEqual(m.title, "미디엄 변환은 한글로")
        self.assertTrue(m.meta["summary"].startswith("요약:"))
        self.assertIn("글쓰기", m.tags)
        self.assertNotIn("claude-memory", m.tags)
        self.assertIn("feedback", m.tags)  # 기존 태그 유지
        links = brain.as_list(m.meta.get("links"))
        self.assertTrue(any("jd" in l for l in links))
        self.assertFalse(any("없는-노트" in l for l in links))  # 존재하지 않는 stem은 버림
        # 요약이 검색에 잡힘
        hits = brain.dash_search(self.vault, "요약", 5)
        self.assertTrue(any(h["title"] == "미디엄 변환은 한글로" for h in hits))
        # 두 번째 실행은 대상 없음
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            brain.main(["enrich"])
        self.assertIn("정제할 노트가 없어요", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
