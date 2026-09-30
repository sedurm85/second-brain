"""load_notes() 캐시: 생성/수정(외부 에디터 포함)/삭제 즉시 반영, 캐시 적중 시 내용 동일 + 재사용,
SECOND_BRAIN_NO_NOTE_CACHE=1로 완전히 끌 수 있는지 확인한다."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402


class NotesCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in
                     ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_JOBS_DIR", "SECOND_BRAIN_NO_NOTE_CACHE")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ["SECOND_BRAIN_JOBS_DIR"] = str(self.home / "nojobs")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ.pop("SECOND_BRAIN_NO_NOTE_CACHE", None)
        cfgdir = self.home / ".config" / "second-brain"
        cfgdir.mkdir(parents=True)
        self.vault = self.home / "brain"
        (cfgdir / "config.json").write_text(
            json.dumps({"vault": str(self.vault), "calendar": {"sources": []}}), encoding="utf-8")
        brain.main(["init", "--vault", str(self.vault)])
        brain.invalidate_notes_cache()  # 이전 테스트가 남긴 캐시가 혹시 있어도 깨끗하게 시작

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        brain.invalidate_notes_cache()
        self.tmp.cleanup()

    def test_create_visible_immediately(self):
        stems_before = {n.stem for n in brain.load_notes(self.vault)}
        path = brain.create_note(self.vault, "note", "새로 만든 노트")
        notes = brain.load_notes(self.vault)
        self.assertIn(path.stem, {n.stem for n in notes})
        self.assertNotIn(path.stem, stems_before)

    def test_external_edit_visible_without_restart(self):
        path = brain.create_note(self.vault, "note", "원래 제목")
        notes = brain.load_notes(self.vault)  # 캐시 채움
        before = next(n for n in notes if n.path == path)
        self.assertEqual(before.title, "원래 제목")
        # write_note()를 거치지 않고 Obsidian이 직접 파일을 덮어썼다고 가정(캐시 무효화 호출 없음).
        # 제목 길이를 바꿔 mtime 해상도에 의존하지 않고 지문(size)이 반드시 달라지게 한다.
        text = path.read_text(encoding="utf-8")
        text = text.replace("원래 제목", "외부 에디터가 바꾼 훨씬 더 긴 제목")
        path.write_text(text, encoding="utf-8")
        notes2 = brain.load_notes(self.vault)
        after = next(n for n in notes2 if n.path == path)
        self.assertEqual(after.title, "외부 에디터가 바꾼 훨씬 더 긴 제목")

    def test_delete_gone_without_restart(self):
        path = brain.create_note(self.vault, "note", "지울 노트")
        self.assertIn(path.stem, {n.stem for n in brain.load_notes(self.vault)})
        path.unlink()
        notes = brain.load_notes(self.vault)
        self.assertNotIn(path.stem, {n.stem for n in notes})

    def test_cache_hit_returns_equal_content_and_reuses_objects(self):
        brain.create_note(self.vault, "note", "캐시 확인용 노트1")
        brain.create_note(self.vault, "note", "캐시 확인용 노트2")
        notes1 = brain.load_notes(self.vault)
        notes2 = brain.load_notes(self.vault)
        self.assertIsNot(notes1, notes2)  # 리스트 자체는 매번 새 객체(호출자가 변형해도 캐시 오염 안 됨)
        self.assertEqual([n.to_dict() for n in notes1], [n.to_dict() for n in notes2])
        by_stem1 = {n.stem: n for n in notes1}
        by_stem2 = {n.stem: n for n in notes2}
        for stem, n1 in by_stem1.items():
            self.assertIs(n1, by_stem2[stem])  # 안 바뀌었으면 Note 객체까지 재사용(재파싱 안 함)

    def test_disabled_via_env_var(self):
        brain.create_note(self.vault, "note", "환경변수 테스트 노트")
        os.environ["SECOND_BRAIN_NO_NOTE_CACHE"] = "1"
        parsed = {"n": 0}
        real_init = brain.Note.__init__

        def counting_init(self, vault, path):
            parsed["n"] += 1
            return real_init(self, vault, path)

        brain.Note.__init__ = counting_init
        try:
            brain.load_notes(self.vault)
            first = parsed["n"]
            brain.load_notes(self.vault)
            second = parsed["n"] - first
            self.assertGreater(first, 0)
            self.assertEqual(first, second)  # 캐시 꺼짐: 매 호출마다 전부 재파싱
        finally:
            brain.Note.__init__ = real_init


if __name__ == "__main__":
    unittest.main()
