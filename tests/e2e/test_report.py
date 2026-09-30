"""리포트(web/report.html, "/report") 브라우저 회귀."""
from ._base import E2ETestCase


class ReportTest(E2ETestCase):
    def test_01_renders_with_headings_and_print_stylesheet(self):
        pg = self.page("/report")
        pg.wait_for_selector("h2", timeout=8000)
        self.assertGreaterEqual(pg.locator("h2").count(), 5)
        has_print_style = pg.evaluate(
            """() => Array.from(document.querySelectorAll('style'))
                .some(s => s.textContent.indexOf('@media print') !== -1)"""
        )
        self.assertTrue(has_print_style, "인쇄용 @media print 스타일이 있어야 해요")
        self.assert_no_js_errors()
