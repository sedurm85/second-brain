"""E2E 브라우저 회귀 스위트 공통 베이스.

`brain.py serve --demo`를 서브프로세스로 띄우고 playwright(chromium)로 실제 페이지를 열어
검증한다. CI에는 playwright가 없으므로, import에 실패하면 이 모듈을 쓰는 모든 TestCase를
`@unittest.skipUnless`로 통째로 건너뛴다 — `python3 -m unittest discover -s tests -q`가
CI에서도 항상 초록불이어야 하기 때문이다.

로컬에서 강제로 스킵을 확인하려면: SECOND_BRAIN_E2E_DISABLE=1 python3 -m unittest discover -s tests -q
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
    _IMPORT_ERR = None
except Exception as e:  # ImportError뿐 아니라 playwright 내부 초기화 오류도 방어적으로 스킵
    HAS_PLAYWRIGHT = False
    _IMPORT_ERR = e

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BRAIN_PY = REPO_ROOT / "scripts" / "brain.py"

# 로컬에서 스킵 경로를 검증하고 싶을 때만 쓰는 탈출구(실제 CI 스킵은 playwright 부재로 자연히 일어남).
_DISABLED = os.environ.get("SECOND_BRAIN_E2E_DISABLE") == "1"

SKIP_REASON = (
    "SECOND_BRAIN_E2E_DISABLE=1"
    if _DISABLED
    else f"playwright(+chromium) 없음 — e2e 스위트 스킵 ({_IMPORT_ERR})"
    if not HAS_PLAYWRIGHT
    else ""
)


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_up(port, timeout=25):
    url = f"http://127.0.0.1:{port}/api/session"
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status < 500:
                    return
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            last_err = e
        time.sleep(0.2)
    raise RuntimeError(f"데모 서버가 {timeout}s 안에 /api/session에 응답하지 않았어요: {last_err}")


@unittest.skipUnless(HAS_PLAYWRIGHT and not _DISABLED, SKIP_REASON)
class E2ETestCase(unittest.TestCase):
    """서브클래스마다(파일당) 데모 서버 1개 + 브라우저 1개를 띄워 공유한다(속도).

    각 test 메서드는 새 탭(page)에서 시작하므로 테스트 간 DOM 상태는 격리되지만,
    서버 쪽 데이터(예: 제안 채택)는 클래스 내에서 공유된다 — 그런 부수효과가 있는
    테스트는 파일 안에서 한 번만 실행되도록 주의해서 작성한다.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sb-e2e-")
        cls.home = Path(cls.tmp) / "home"
        cls.jobs_dir = Path(cls.tmp) / "jobs"
        cls.home.mkdir(parents=True)
        cls.jobs_dir.mkdir(parents=True)
        cls.port = _free_port()
        env = dict(os.environ)
        env.update({
            "HOME": str(cls.home),
            "XDG_CACHE_HOME": str(cls.home / ".cache"),
            "SECOND_BRAIN_OFFLINE": "1",
            "SECOND_BRAIN_NO_LAUNCHCTL": "1",
            "SECOND_BRAIN_JOBS_DIR": str(cls.jobs_dir),
        })
        cls.proc = subprocess.Popen(
            [sys.executable, str(BRAIN_PY), "serve", "--demo", "--port", str(cls.port)],
            cwd=str(REPO_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        cls.browser = None
        cls._pw = None
        try:
            _wait_up(cls.port)
        except Exception:
            cls._dump_server_log()
            cls._kill_server()
            raise
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch()

    @classmethod
    def _dump_server_log(cls):
        try:
            if cls.proc.stdout:
                out = cls.proc.stdout.read().decode("utf-8", "replace")
                sys.stderr.write(f"[e2e] 데모 서버 로그:\n{out}\n")
        except Exception:
            pass

    @classmethod
    def _kill_server(cls):
        proc = getattr(cls, "proc", None)
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

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

    # ------------------------------------------------------------------
    def setUp(self):
        self.console_errors = []
        self.pg = self.browser.new_page(viewport={"width": 1280, "height": 900})
        self.pg.on("pageerror", lambda exc: self.console_errors.append(f"pageerror: {exc}"))
        self.pg.on(
            "console",
            lambda msg: self.console_errors.append(f"console.{msg.type}: {msg.text}")
            if msg.type == "error" else None,
        )

    def tearDown(self):
        try:
            self.pg.close()
        except Exception:
            pass

    def page(self, path="/", width=1280, height=900):
        """지정 경로로 이동한 뒤(필요하면 뷰포트도 바꾸고) playwright page를 반환한다."""
        vs = self.pg.viewport_size
        if not vs or vs.get("width") != width or vs.get("height") != height:
            self.pg.set_viewport_size({"width": width, "height": height})
        self.pg.goto(self.base_url + path, wait_until="load")
        return self.pg

    def assert_no_js_errors(self):
        self.assertEqual([], self.console_errors, f"콘솔/페이지 JS 에러 발생: {self.console_errors}")

    def api_json(self, route, params=None):
        url = f"{self.base_url}/api/{route}"
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
