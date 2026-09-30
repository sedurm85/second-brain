"""사무실(web/office.html, "/office") 브라우저 회귀."""
from ._base import E2ETestCase


class OfficeTest(E2ETestCase):
    def test_01_loads_rooms_kpi_shift_strip(self):
        pg = self.page("/office")
        pg.wait_for_selector(".room", timeout=8000)
        self.assertGreaterEqual(pg.locator(".room").count(), 2)
        self.assertGreaterEqual(pg.locator(".kpi").count(), 1)
        self.assertEqual(1, pg.locator("#shiftStrip").count())
        self.assert_no_js_errors()

    def test_02_click_desk_opens_detail_with_weekly_brief_button(self):
        pg = self.page("/office")
        pg.wait_for_selector(".desk", timeout=8000)
        pg.locator(".desk").first.click()
        pg.wait_for_selector(".detail.open", timeout=4000)
        detail_text = pg.locator(".detail").inner_text()
        self.assertIn("이번 주 한 줄", detail_text)
        self.assert_no_js_errors()

    def test_03_mobile_width_no_scroll_and_detail_becomes_bottom_sheet(self):
        pg = self.page("/office", width=390, height=844)
        pg.wait_for_selector(".desk", timeout=8000)
        scroll_width = pg.evaluate("document.documentElement.scrollWidth")
        self.assertLessEqual(scroll_width, 390)
        pg.locator(".desk").first.click()
        pg.wait_for_selector(".detail.open", timeout=4000)
        box = pg.eval_on_selector(
            ".detail",
            "el => { var s = getComputedStyle(el); return {position: s.position, bottom: s.bottom, left: s.left}; }",
        )
        self.assertEqual("fixed", box["position"])
        self.assertEqual("0px", box["bottom"])
        self.assertEqual("0px", box["left"])
        self.assert_no_js_errors()
