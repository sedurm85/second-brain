"""보드(web/index.html, "/") 브라우저 회귀."""
import uuid

from ._base import E2ETestCase


class BoardTest(E2ETestCase):
    def test_01_loads_hero_report_and_nav(self):
        pg = self.page("/")
        pg.wait_for_selector("#report p", timeout=8000)
        paras = pg.locator("#report p")
        self.assertGreaterEqual(paras.count(), 3, "히어로 보고가 3문장 이상이어야 해요")
        nav_text = pg.locator(".nav").inner_text()
        for label in ("일지", "사람", "리포트"):
            self.assertIn(label, nav_text)
        self.assert_no_js_errors()

    def test_02_slash_focuses_search_and_palette_shows_task_command(self):
        pg = self.page("/")
        pg.wait_for_selector("#q")
        pg.locator("body").click()  # 검색창 밖에 포커스를 둔 상태에서 시작
        pg.keyboard.press("/")
        focused_id = pg.evaluate("document.activeElement && document.activeElement.id")
        self.assertEqual("q", focused_id)
        pg.keyboard.type(">할 일")
        pg.wait_for_selector("#results:not([hidden])", timeout=4000)
        results_text = pg.locator("#results").inner_text()
        self.assertIn("할 일로", results_text)
        self.assert_no_js_errors()

    def test_03_help_dialog_opens_with_question_mark_and_escape_closes(self):
        pg = self.page("/")
        pg.wait_for_selector("#q")
        pg.locator("body").click()
        pg.keyboard.press("?")
        pg.wait_for_selector("#scModal.open", timeout=4000)
        self.assertEqual("false", pg.get_attribute("#scModal", "aria-hidden"))
        pg.keyboard.press("Escape")
        pg.wait_for_function(
            "() => !document.getElementById('scModal').classList.contains('open')",
            timeout=4000,
        )
        self.assertEqual("true", pg.get_attribute("#scModal", "aria-hidden"))
        self.assert_no_js_errors()

    def test_04_upcoming_event_suggestion_accept_reduces_today_count(self):
        before = self.api_json("today")
        before_count = (before.get("suggestions") or {}).get("count") or 0
        self.assertGreaterEqual(before_count, 1, "데모 데이터에 대기 중 제안이 있어야 해요")

        pg = self.page("/")
        pg.wait_for_selector(".up-list button.ev", timeout=8000)
        # 「제주 출장」은 다가오는 일정 목록 항목이면서 브레인 제안이 달려 있는 데모 데이터
        pg.locator(".up-list button.ev", has_text="제주 출장").first.click()
        pg.wait_for_selector("#panel.open .ev-sug", timeout=4000)
        header = pg.locator("#panel .ev-sug h3").inner_text()
        self.assertIn("의 제안", header)
        pg.locator('#panel .ev-sug [data-sug="accept"]').click()
        pg.wait_for_function(
            "() => !document.querySelector('#panel .ev-sug')",
            timeout=4000,
        )
        after = self.api_json("today")
        after_count = (after.get("suggestions") or {}).get("count") or 0
        self.assertEqual(before_count - 1, after_count)
        self.assert_no_js_errors()

    def test_05_task_add_appends_to_board(self):
        pg = self.page("/")
        marker = "e2e 할 일 " + uuid.uuid4().hex[:8]
        pg.wait_for_selector("#taskAdd [name=text]", timeout=8000)
        pg.locator("#taskAdd [name=text]").fill(marker)
        pg.locator("#taskAdd button[type=submit]").click()
        pg.wait_for_selector(f"#tasksGrid >> text={marker}", timeout=6000)
        self.assert_no_js_errors()

    def test_06_note_memo_posts_and_rerenders_panel(self):
        pg = self.page("/")
        pg.wait_for_selector("#jr .j-title", timeout=8000)
        pg.locator("#jr .j-title").first.click()
        pg.wait_for_selector('#panel.open form[data-act="note-memo"] [name=text]', timeout=4000)
        marker = "e2e 메모 " + uuid.uuid4().hex[:8]
        pg.locator('form[data-act="note-memo"] [name=text]').fill(marker)
        pg.locator('form[data-act="note-memo"] button[type=submit]').click()
        pg.wait_for_selector(f"#panelBody >> text={marker}", timeout=6000)
        self.assert_no_js_errors()

    def test_07_overview_has_five_stat_tiles_and_people_render(self):
        pg = self.page("/")
        pg.wait_for_selector(".tiles .stat-tile", timeout=8000)
        self.assertEqual(5, pg.locator(".tiles .stat-tile").count())
        pg.wait_for_selector("#pplGrid .pplcard", timeout=8000)
        self.assertGreaterEqual(pg.locator("#pplGrid .pplcard").count(), 1)
        self.assert_no_js_errors()

    def test_09_decision_keep_toasts_and_moves_revisit(self):
        # 데모 데이터의 d5(「대시보드를 로컬 서버로 띄울지」)는 revisit이 오늘+2일이라 되돌아볼 결정 카드에 뜬다.
        before = self.api_json("today")
        rv_before = [d for d in (before.get("revisit") or []) if "대시보드" in d["title"]]
        self.assertEqual(1, len(rv_before), "데모 데이터에 되돌아볼 결정(대시보드)이 있어야 해요")

        pg = self.page("/")
        pg.wait_for_selector(".rv-row .rv-item", timeout=8000)
        row = pg.locator(".rv-row", has_text="대시보드").first
        row.locator('.rv-act[data-ract="keep"]').click()
        pg.wait_for_selector(".rv-inline form", timeout=4000)
        pg.locator(".rv-inline select[name=days]").select_option("30")
        pg.locator(".rv-inline form button[type=submit]").click()
        pg.wait_for_selector("#scToast.show", timeout=6000)
        self.assertIn("유지", pg.locator("#scToast").inner_text())

        # +30일로 밀리면 7일 창(t.revisit)에서는 빠지지만, 결정 보드(open)의 revisit 값 자체는 바뀐다.
        def revisit_moved():
            j = self.api_json("decisions")
            hit = [d for d in (j.get("open") or []) if "대시보드" in d["title"]]
            return bool(hit) and hit[0]["revisit"] != rv_before[0]["revisit"]

        for _ in range(20):
            if revisit_moved():
                break
            pg.wait_for_timeout(300)
        self.assertTrue(revisit_moved(), "유지 처리 후 revisit이 앞으로 밀려야 해요")
        self.assert_no_js_errors()

    def test_08_mobile_width_no_horizontal_scroll(self):
        pg = self.page("/", width=390, height=844)
        pg.wait_for_selector("#report p", timeout=8000)
        scroll_width = pg.evaluate("document.documentElement.scrollWidth")
        self.assertLessEqual(scroll_width, 390)
        self.assert_no_js_errors()
