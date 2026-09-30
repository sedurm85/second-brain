"""사무실(web/office.html, "/office") 브라우저 회귀."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ._base import BRAIN_PY, HAS_PLAYWRIGHT, REPO_ROOT, SKIP_REASON, E2ETestCase, _DISABLED, _free_port, _wait_up


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


@unittest.skipUnless(HAS_PLAYWRIGHT and not _DISABLED, SKIP_REASON)
class OfficeHireModalFitTest(unittest.TestCase):
    """demo 서버는 allow_hire=false라 채용 모달을 못 띄운다. 임시 HOME + widgets.json(allow_hire:true)으로
    데모가 아닌 서버를 직접 띄워, 모바일 뷰포트에서 채용 모달이 화면 높이 안에 들어오는지 확인한다."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sb-e2e-hire-")
        home = Path(cls.tmp) / "home"
        home.mkdir(parents=True)
        cfg_dir = home / ".config" / "second-brain"
        cfg_dir.mkdir(parents=True)
        cls.env = dict(os.environ)
        cls.env.update({
            "HOME": str(home), "XDG_CACHE_HOME": str(home / ".cache"),
            "SECOND_BRAIN_OFFLINE": "1", "SECOND_BRAIN_NO_LAUNCHCTL": "1",
        })
        subprocess.run([sys.executable, str(BRAIN_PY), "init"], cwd=str(REPO_ROOT), env=cls.env,
                        check=True, capture_output=True)
        (cfg_dir / "widgets.json").write_text(
            json.dumps({"allow_commands": False, "allow_run": False, "allow_hire": True, "widgets": []}),
            encoding="utf-8",
        )
        cls.port = _free_port()
        cls.proc = subprocess.Popen(
            [sys.executable, str(BRAIN_PY), "serve", "--port", str(cls.port)],
            cwd=str(REPO_ROOT), env=cls.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        cls.browser = None
        cls._pw = None
        try:
            _wait_up(cls.port)
        except Exception:
            cls._kill_server()
            raise
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch()

    @classmethod
    def _kill_server(cls):
        if cls.proc.poll() is None:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
                cls.proc.wait(timeout=5)

    @classmethod
    def tearDownClass(cls):
        try:
            if cls.browser:
                cls.browser.close()
        finally:
            try:
                if cls._pw:
                    cls._pw.stop()
            finally:
                cls._kill_server()
                shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_hire_modal_fits_mobile_viewport_height(self):
        page = self.browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(f"http://127.0.0.1:{self.port}/office", wait_until="load")
        page.wait_for_selector("#hireBtn:not([hidden])", timeout=8000)
        page.locator("#hireBtn").click()
        page.wait_for_selector("#hireModal:not([hidden])", timeout=4000)
        box = page.eval_on_selector("#hireModal", "el => el.getBoundingClientRect()")
        inner_height = page.evaluate("window.innerHeight")
        self.assertLessEqual(box["height"], inner_height)
        page.close()
