"""코어(web/core.html, "/core") 브라우저 회귀."""
from ._base import E2ETestCase


class CoreTest(E2ETestCase):
    def test_01_ask_today_caption_mentions_schedule(self):
        pg = self.page("/core")
        pg.wait_for_selector("#askForm", timeout=8000)
        pg.locator("#ask").fill("오늘 뭐 있어")
        pg.locator("#askForm button[type=submit]").click()
        pg.wait_for_function(
            "() => (document.getElementById('caption').textContent || '').indexOf('일정') !== -1",
            timeout=6000,
        )
        self.assert_no_js_errors()

    def test_02_ask_company_status_caption_mentions_rate_or_first_record(self):
        pg = self.page("/core")
        pg.wait_for_selector("#askForm", timeout=8000)
        pg.locator("#ask").fill("회사 현황")
        pg.locator("#askForm button[type=submit]").click()
        pg.wait_for_function(
            """() => {
                var t = document.getElementById('caption').textContent || '';
                return t.indexOf('성공률') !== -1 || t.indexOf('첫 기록') !== -1;
            }""",
            timeout=6000,
        )
        self.assert_no_js_errors()

    def test_03_gear_opens_settings_dialog(self):
        pg = self.page("/core")
        pg.wait_for_selector("#settingsBtn", timeout=8000)
        pg.locator("#settingsBtn").click()
        pg.wait_for_selector("#cfgSheet.open", timeout=4000)
        self.assertEqual("false", pg.get_attribute("#cfgSheet", "aria-hidden"))
        self.assert_no_js_errors()

    def test_04_accept_meeting_suggestion_via_voice_phrase(self):
        before = self.api_json("today")
        before_count = (before.get("suggestions") or {}).get("count") or 0
        self.assertGreaterEqual(before_count, 1)

        pg = self.page("/core")
        pg.wait_for_selector("#askForm", timeout=8000)
        pg.locator("#ask").fill("팀 주간회의 제안 채택해")
        pg.locator("#askForm button[type=submit]").click()
        pg.wait_for_function(
            "() => (document.getElementById('caption').textContent || '').indexOf('적었어요') !== -1",
            timeout=6000,
        )
        after = self.api_json("today")
        after_count = (after.get("suggestions") or {}).get("count") or 0
        self.assertEqual(before_count - 1, after_count)
        self.assert_no_js_errors()
