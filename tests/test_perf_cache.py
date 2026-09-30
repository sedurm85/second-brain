"""v0.31 성능 캐시(cached_link_graph/cached_search_docs/widget_history) 정확성 테스트.

핵심 계약: (1) 노트를 수정하면 재시작 없이 바로 반영된다(캐시 키가 볼트 지문/로그
mtime+size라서), (2) 캐시가 안 바뀐 동안은 재계산 없이 같은 결과를 재사용한다,
(3) SECOND_BRAIN_NO_NOTE_CACHE=1이면 캐시를 완전히 끈다."""
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import brain  # noqa: E402

TODAY = date(2026, 9, 30)


class PerfCacheTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._old = {k: os.environ.get(k) for k in
                     ("HOME", "XDG_CACHE_HOME", "SECOND_BRAIN_VAULT", "SECOND_BRAIN_JOBS_DIR",
                      "SECOND_BRAIN_NO_NOTE_CACHE")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ["SECOND_BRAIN_JOBS_DIR"] = str(self.home / "nojobs")
        os.environ.pop("SECOND_BRAIN_VAULT", None)
        os.environ.pop("SECOND_BRAIN_NO_NOTE_CACHE", None)
        self.vault = self.home / "brain"
        brain.main(["init", "--vault", str(self.vault)])
        brain.invalidate_notes_cache()
        brain._WIDGET_HISTORY_CACHE.clear()  # 다른 테스트 파일이 남긴 위젯 로그 캐시와 섞이지 않게

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        brain.invalidate_notes_cache()
        brain._WIDGET_HISTORY_CACHE.clear()
        self.tmp.cleanup()

    # ---------------------------------------------------------------- link graph

    def test_link_graph_cache_hit_returns_same_object(self):
        """볼트가 안 바뀐 동안 두 번째 호출은 재계산 없이 같은 adj 객체를 돌려준다."""
        brain.create_note(self.vault, "note", "노트 A", created=ago(3))
        brain.create_note(self.vault, "note", "노트 B", created=ago(2))
        _, adj1 = brain.cached_link_graph(self.vault)
        _, adj2 = brain.cached_link_graph(self.vault)
        self.assertIs(adj1, adj2, "볼트가 안 바뀌었으면 캐시된 adj 객체를 그대로 재사용해야 한다")

    def test_link_graph_cache_reflects_edit_immediately(self):
        """노트를 고쳐 링크를 추가하면 재시작 없이 바로 link_graph에 반영돼야 한다."""
        path_a = brain.create_note(self.vault, "note", "노트 A", created=ago(3))
        brain.create_note(self.vault, "note", "노트 B", created=ago(2))
        _, adj_before = brain.cached_link_graph(self.vault)
        self.assertNotIn("노트-b", adj_before.get("노트-a", set()))

        text = path_a.read_text(encoding="utf-8")
        text = text.rstrip("\n") + "\n\n[[노트 B]]\n"
        path_a.write_text(text, encoding="utf-8")
        brain.invalidate_notes_cache()  # write_note()를 거치지 않은 직접 편집이라 캐시 무효화도 직접 호출

        _, adj_after = brain.cached_link_graph(self.vault)
        self.assertIn("노트-b", adj_after.get("노트-a", set()))

    def test_link_graph_cache_disabled_by_env(self):
        os.environ["SECOND_BRAIN_NO_NOTE_CACHE"] = "1"
        brain.create_note(self.vault, "note", "노트 C", created=ago(1))
        brain.cached_link_graph(self.vault)
        brain.cached_link_graph(self.vault)
        self.assertEqual(len(brain._LINK_GRAPH_CACHE), 0,
                          "SECOND_BRAIN_NO_NOTE_CACHE=1이면 _LINK_GRAPH_CACHE를 채우면 안 된다")

    # ---------------------------------------------------------------- search index

    def test_search_cache_reflects_edit_immediately(self):
        """검색 인덱스 캐시가 있어도, 노트를 고쳐 단어를 바꾸면 다음 검색이 바로 반영돼야 한다.
        두 단어는 tokenize()의 2-gram이 서로 겹치지 않도록 골랐다(부분 겹침이면 옛 단어 검색에서도
        새 본문이 약하게 매칭돼 테스트가 흔들린다)."""
        note = brain.create_note(self.vault, "note", "검색테스트 노트", created=ago(1),
                                  body="# 검색테스트 노트\n\n김치찌개 이야기\n")
        hits_before = brain.dash_search(self.vault, "김치찌개", today=TODAY)
        self.assertTrue(any(h["path"] == note.relative_to(self.vault).as_posix() for h in hits_before))

        text = note.read_text(encoding="utf-8").replace("김치찌개", "스키타기")
        note.write_text(text, encoding="utf-8")
        brain.invalidate_notes_cache()

        hits_after_old_word = brain.dash_search(self.vault, "김치찌개", today=TODAY)
        self.assertFalse(any(h["path"] == note.relative_to(self.vault).as_posix() for h in hits_after_old_word),
                          "옛 단어로는 더 이상 찾히면 안 된다(캐시가 낡은 토큰을 들고 있으면 실패)")
        hits_after_new_word = brain.dash_search(self.vault, "스키타기", today=TODAY)
        self.assertTrue(any(h["path"] == note.relative_to(self.vault).as_posix() for h in hits_after_new_word),
                         "새 단어는 바로 찾혀야 한다")

    def test_search_cache_hit_reuses_tokenized_doc(self):
        """볼트가 안 바뀌면 cached_search_docs가 같은 (tf, length) 튜플을 재사용한다."""
        brain.create_note(self.vault, "note", "캐시 재사용 노트", created=ago(1), body="# 캐시 재사용 노트\n\n본문\n")
        docs1 = brain.cached_search_docs(self.vault)
        docs2 = brain.cached_search_docs(self.vault)
        self.assertIsNotNone(docs1)
        self.assertIs(docs1, docs2, "볼트가 안 바뀌었으면 같은 캐시 dict 객체를 돌려줘야 한다")

    def test_search_cache_disabled_by_env(self):
        os.environ["SECOND_BRAIN_NO_NOTE_CACHE"] = "1"
        brain.create_note(self.vault, "note", "노트 D", created=ago(1))
        self.assertIsNone(brain.cached_search_docs(self.vault))
        self.assertEqual(len(brain._SEARCH_INDEX_CACHE), 0,
                          "SECOND_BRAIN_NO_NOTE_CACHE=1이면 _SEARCH_INDEX_CACHE를 채우면 안 된다")
        # 캐시가 꺼져도 검색 자체는 여전히 정확해야 한다.
        hits = brain.dash_search(self.vault, "노트 D", today=TODAY)
        self.assertTrue(len(hits) >= 1)

    # ---------------------------------------------------------------- widget_history

    def _widget(self, log_path, fail_pattern="Traceback|Error"):
        return {"id": "w1", "kind": "log", "source": str(log_path),
                "status_cfg": {"fail_pattern": fail_pattern}}

    def test_widget_history_reflects_log_write_immediately(self):
        """widget_history가 60초 캐시를 쓰지만, 로그 파일이 바뀌면(mtime/size) 바로 반영돼야 한다."""
        log_path = self.home / "w1.log"
        log_path.write_text(f"{TODAY.isoformat()} 09:00:00 작업 완료 len: 3\n", encoding="utf-8")
        w = self._widget(log_path)
        h1 = brain.widget_history(w, days=1, today=TODAY)
        self.assertEqual(h1["total_runs"], 1)
        self.assertEqual(h1["total_fails"], 0)

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{TODAY.isoformat()} 09:05:00 Traceback (most recent call last):\n")
        h2 = brain.widget_history(w, days=1, today=TODAY)
        self.assertEqual(h2["total_runs"], 2, "로그에 줄이 추가되면 캐시가 아니라 새 내용을 반영해야 한다")
        self.assertEqual(h2["total_fails"], 1)

    def test_widget_history_cache_hit_is_identical_object(self):
        log_path = self.home / "w2.log"
        log_path.write_text(f"{TODAY.isoformat()} 09:00:00 작업 완료 len: 3\n", encoding="utf-8")
        w = self._widget(log_path)
        h1 = brain.widget_history(w, days=1, today=TODAY)
        h2 = brain.widget_history(w, days=1, today=TODAY)
        self.assertIs(h1, h2, "로그가 안 바뀌었으면 같은 결과 객체를 재사용해야 한다")

    def test_widget_history_cache_disabled_by_env(self):
        os.environ["SECOND_BRAIN_NO_NOTE_CACHE"] = "1"
        log_path = self.home / "w3.log"
        log_path.write_text(f"{TODAY.isoformat()} 09:00:00 작업 완료 len: 3\n", encoding="utf-8")
        w = self._widget(log_path)
        brain.widget_history(w, days=1, today=TODAY)
        brain.widget_history(w, days=1, today=TODAY)
        self.assertEqual(len(brain._WIDGET_HISTORY_CACHE), 0,
                          "SECOND_BRAIN_NO_NOTE_CACHE=1이면 _WIDGET_HISTORY_CACHE를 채우면 안 된다")


def ago(days):
    from datetime import timedelta
    return (TODAY - timedelta(days=days)).isoformat()


if __name__ == "__main__":
    unittest.main()
