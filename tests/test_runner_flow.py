"""真实浏览器集成测试，覆盖状态机的几条关键路径。"""

import http.server
import threading

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from doubao_checkin import config, runner
from doubao_checkin.credentials import Credential

pytestmark = pytest.mark.browser

PAGES = {
    "/ok": """<html><body>
      <div>每日签到</div><div>累计 200 点</div>
      <button onclick="this.outerHTML='<div>签到成功</div>'">立即签到</button>
    </body></html>""",
    "/already": """<html><body>
      <div>每日签到</div><div>已签到</div><div>累计 200 点</div>
    </body></html>""",
    "/login": """<html><body>
      <div>扫码登录</div><div>登录后同步你的对话</div>
    </body></html>""",
    "/client-only": """<html><body>
      <div>仅支持在豆包电脑版使用本活动</div>
      <div>请前往豆包电脑版完成任务</div>
    </body></html>""",
    "/alt": """<html><body>
      <div>每日签到</div><div>每日完成任务领额度</div>
      <button onclick="this.outerHTML='<div>领取成功</div>'">立即领取</button>
    </body></html>""",
    "/slow": """<html><body>
      <div>每日签到</div><div id="slot">加载中</div>
      <script>
        setTimeout(() => {
          document.getElementById('slot').innerHTML =
            '<button onclick="this.outerHTML=\\'<div>签到成功</div>\\'">立即签到</button>';
        }, 6000);
      </script></body></html>""",
    "/soldout": """<html><body>
      <div>每日签到</div><div>今日名额已抢完</div>
    </body></html>""",
}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/gateway":
            self.send_response(502)
            self.end_headers()
            return
        if self.path == "/missing":
            self.send_response(404)
            self.end_headers()
            return
        body = PAGES.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def base_url():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()


@pytest.fixture(autouse=True)
def fast_timing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "SHOTS_DIR", tmp_path / "shots")
    monkeypatch.setattr(config, "LOG_FILE", tmp_path / "checkin.log")
    monkeypatch.setattr(config, "RESULT_FILE", tmp_path / "last-result.json")
    monkeypatch.setattr(config, "COOKIE_FILE", tmp_path / "cookies.json")
    monkeypatch.setattr(config.Timing, "PAGE_SETTLE", 200)
    monkeypatch.setattr(config.Timing, "READY_TIMEOUT", 10.0)
    monkeypatch.setattr(config.Timing, "READY_POLL", 100)
    monkeypatch.setattr(config.Timing, "CLICK_SETTLE", 200)
    monkeypatch.setattr(config.Timing, "ENTRY_SETTLE", 200)
    monkeypatch.setattr(config.Timing, "BUTTON_RETRY_WAIT", 300)
    monkeypatch.setattr(config.Timing, "BODY_TIMEOUT", 2000)


def _run(base, path):
    return runner.run_checkin(
        credential=Credential(cookies=[], mode="env"),
        url=f"{base}{path}",
        budget_seconds=60,
        timeout=10,
        allow_fallback=False,
    )


def test_normal_checkin(base_url):
    result = _run(base_url, "/ok")
    assert result.result == "success"
    assert "立即签到" in (result.detail or "")
    assert result.duration_ms is not None


def test_already_signed(base_url):
    result = _run(base_url, "/already")
    assert result.result == "already"


def test_login_wall(base_url):
    result = _run(base_url, "/login")
    assert result.result == "AUTH_ERROR"
    assert result.screenshot


def test_client_only(base_url):
    result = _run(base_url, "/client-only")
    assert result.result == "CLIENT_ONLY"
    assert result.screenshot


def test_alternative_button_text_is_clicked(base_url):
    """P0 回归：按钮文案是「立即领取」时必须真的点，不能判成已签。"""
    result = _run(base_url, "/alt")
    assert result.result == "success"
    assert "立即领取" in (result.detail or "")


def test_slow_render_is_not_treated_as_signed(base_url):
    """P0 回归：标题先渲染、按钮延迟出现时，要等，不能判成已签。"""
    result = _run(base_url, "/slow")
    assert result.result == "success"


def test_sold_out_is_failed(base_url):
    result = _run(base_url, "/soldout")
    assert result.result == "FAILED"
    assert result.screenshot


def test_success_does_not_keep_screenshot(base_url, tmp_path):
    result = _run(base_url, "/ok")
    assert result.result == "success"
    assert result.screenshot is None
    assert not list((tmp_path / "shots").glob("*.png"))


def test_http_404_is_network_error(base_url, monkeypatch):
    monkeypatch.setattr(runner, "RETRY_DELAYS", ())
    result = _run(base_url, "/missing")
    assert result.result == "NETWORK"
    assert "404" in (result.detail or "")


def test_http_502_is_network_error(base_url, monkeypatch):
    monkeypatch.setattr(runner, "RETRY_DELAYS", ())
    result = _run(base_url, "/gateway")
    assert result.result == "NETWORK"


def test_profile_mode_uses_persistent_context(base_url, tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    result = runner.run_checkin(
        credential=Credential(mode="profile", profile_dir=profile),
        url=f"{base_url}/ok",
        budget_seconds=60,
        timeout=10,
        allow_fallback=False,
    )
    assert result.result == "success"
    # 成功不截图
    assert result.screenshot is None
