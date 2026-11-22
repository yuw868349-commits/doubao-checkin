import json
import os
import stat
import time

import pytest

from doubao_checkin import credentials, state  # noqa: F401


def test_normalize_accepts_val_alias():
    cookie = credentials.normalize_cookie({"name": "sessionid", "val": "abc", "host_key": ".doubao.com"})
    assert cookie["value"] == "abc"
    assert cookie["domain"] == ".doubao.com"


def test_normalize_rejects_empty_value():
    assert credentials.normalize_cookie({"name": "sessionid", "value": ""}) is None
    assert credentials.normalize_cookie({"name": "", "value": "abc"}) is None


def test_parse_array_and_object():
    raw = json.dumps([{"name": "sessionid", "value": "a"}])
    assert len(credentials.cookies_from_json(raw)) == 1

    raw = json.dumps({"cookies": [{"name": "sid_tt", "value": "b"}]})
    assert len(credentials.cookies_from_json(raw)) == 1


def test_parse_cookie_header():
    raw = json.dumps({"cookie": "sessionid=x; sid_guard=y"})
    cookies = credentials.cookies_from_json(raw)
    assert {c["name"] for c in cookies} == {"sessionid", "sid_guard"}


def test_empty_json_is_an_error():
    with pytest.raises(ValueError):
        credentials.cookies_from_json("[]")


def test_session_days_left_uses_latest_expiry():
    soon = {"name": "sessionid", "value": "x", "expires": time.time() + 86400}
    later = {"name": "sid_guard", "value": "x", "expires": time.time() + 86400 * 30}
    days = credentials.session_days_left([soon, later])
    assert days and 29.0 <= days <= 30.0


def test_save_cookies_permissions(tmp_path, monkeypatch):
    target = tmp_path / "cookies.json"
    monkeypatch.setattr(credentials.config, "COOKIE_FILE", target)
    monkeypatch.setattr(credentials.config, "ensure_dirs", lambda: None)

    credentials.save_cookies([{"name": "sessionid", "value": "x"}])
    assert json.loads(target.read_text()) == [{"name": "sessionid", "value": "x"}]
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
