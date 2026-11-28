import json
import urllib.error
import urllib.parse

import pytest

from doubao_checkin import api, config


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _cookies():
    return [
        {"name": "sessionid", "value": "secret", "domain": ".doubao.com"},
        {"name": "other", "value": "x", "domain": ".doubao.com"},
    ]


def test_request_carries_client_envelope(monkeypatch):
    seen = []

    def fake_urlopen(request, timeout):
        seen.append(request)
        return FakeResponse({"code": 0, "data": {"ok": True}})

    monkeypatch.setattr(api.urllib.request, "urlopen", fake_urlopen)
    response = api.Client(_cookies()).post("/x", {"a": 1})
    assert response.ok()

    request = seen[0]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
    for key, value in config.CLIENT_PARAMS.items():
        assert query[key] == [value]
    assert query["web_tab_id"]
    assert request.get_header("Agw-js-conv") == "str"
    assert request.get_header("Cookie").startswith("sessionid=secret")


def test_biz_error_marks_response_not_ok(monkeypatch):
    monkeypatch.setattr(
        api.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse(
            {"code": 0, "data": {"biz_error": {"code": 2, "message": "不符合条件"}}}
        ),
    )
    response = api.Client(_cookies()).post("/x")
    assert not response.ok()
    assert "不符合条件" in response.message()


def test_http_401_becomes_auth_error(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, None)

    monkeypatch.setattr(api.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(api.AuthError):
        api.Client(_cookies()).post("/x")


def test_http_400_is_not_retried(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(request.full_url, 400, "bad", {}, None)

    monkeypatch.setattr(api.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(api.ApiError):
        api.Client(_cookies()).post("/x")
    assert len(calls) == 1


def test_network_errors_retry_then_raise(monkeypatch):
    calls = []
    monkeypatch.setattr(config.Timing, "RETRIES", 2)
    monkeypatch.setattr(config.Timing, "BACKOFF", (0.0, 0.0))

    def fake_urlopen(request, timeout):
        calls.append(1)
        raise urllib.error.URLError("down")

    monkeypatch.setattr(api.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(api.ApiError):
        api.Client(_cookies()).post("/x")
    assert len(calls) == 3


def test_recovers_after_one_network_error(monkeypatch):
    calls = []
    monkeypatch.setattr(config.Timing, "RETRIES", 1)
    monkeypatch.setattr(config.Timing, "BACKOFF", (0.0,))

    def fake_urlopen(request, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.URLError("blip")
        return FakeResponse({"code": 0})

    monkeypatch.setattr(api.urllib.request, "urlopen", fake_urlopen)
    assert api.Client(_cookies()).post("/x").ok()


def test_parse_tolerates_non_int_code(monkeypatch):
    monkeypatch.setattr(
        api.urllib.request,
        "urlopen",
        lambda request, timeout: FakeResponse({"code": "oops", "msg": "bad"}),
    )
    response = api.Client(_cookies()).post("/x")
    assert response.code == -1
    assert not response.ok()
