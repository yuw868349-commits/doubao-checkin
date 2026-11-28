import json
import os
import sqlite3
import stat
import time

import pytest
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

from doubao_checkin import credentials

WIN_KEY = b"0123456789abcdef0123456789abcdef"
MAC_KEY = b"0123456789abcdef"


def win_encrypt(plain: bytes, key: bytes = WIN_KEY) -> bytes:
    """按 Chromium v10 的真实布局造数据：v10 | 12B nonce | 密文 | 16B tag。"""
    nonce = get_random_bytes(12)
    body, tag = AES.new(key, AES.MODE_GCM, nonce=nonce).encrypt_and_digest(plain)
    return b"v10" + nonce + body + tag


def mac_encrypt(plain: bytes, key: bytes = MAC_KEY) -> bytes:
    """macOS 走 AES-128-CBC + PKCS#7，IV 是 16 个空格。"""
    pad = 16 - len(plain) % 16
    padded = plain + bytes([pad]) * pad
    return b"v10" + AES.new(key, AES.MODE_CBC, b" " * 16).encrypt(padded)


def mac_encrypt_space_padded(plain: bytes, key: bytes = MAC_KEY) -> bytes:
    """老版本 Chromium 用空格补齐到 16 字节。"""
    padded = plain + b" " * (16 - len(plain) % 16)
    return b"v10" + AES.new(key, AES.MODE_CBC, b" " * 16).encrypt(padded)


def make_db(path, rows, with_samesite=True) -> None:
    conn = sqlite3.connect(path)
    columns = (
        "host_key TEXT, name TEXT, encrypted_value BLOB, path TEXT, "
        "expires_utc INTEGER, is_httponly INTEGER, is_secure INTEGER"
    )
    if with_samesite:
        columns += ", samesite INTEGER"
    conn.execute(f"CREATE TABLE cookies ({columns})")
    placeholders = ",".join("?" * (8 if with_samesite else 7))
    conn.executemany(f"INSERT INTO cookies VALUES ({placeholders})", rows)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- Cookie JSON


def test_normalize_accepts_val_alias():
    cookie = credentials.normalize_cookie(
        {"name": "sessionid", "val": "abc", "host_key": ".doubao.com"}
    )
    assert cookie["value"] == "abc"
    assert cookie["domain"] == ".doubao.com"


def test_normalize_rejects_empty_value():
    assert credentials.normalize_cookie({"name": "sessionid", "value": ""}) is None
    assert credentials.normalize_cookie({"name": "", "value": "abc"}) is None


def test_normalize_accepts_extension_same_site():
    cookie = credentials.normalize_cookie(
        {"name": "sessionid", "value": "a", "sameSite": "no_restriction", "secure": True}
    )
    assert cookie["sameSite"] == "None"


def test_normalize_downgrades_insecure_none():
    cookie = credentials.normalize_cookie(
        {"name": "sessionid", "value": "a", "sameSite": "no_restriction", "secure": False}
    )
    assert cookie["sameSite"] == "Lax"


def test_normalize_accepts_expiration_date():
    cookie = credentials.normalize_cookie(
        {"name": "sessionid", "value": "a", "expirationDate": 1893456000.5}
    )
    assert cookie["expires"] == pytest.approx(1893456000.5)


def test_normalize_tolerates_bad_expires():
    cookie = credentials.normalize_cookie({"name": "sessionid", "value": "a", "expires": "下午"})
    assert cookie["expires"] == -1


def test_parse_array_and_object():
    raw = json.dumps([{"name": "sessionid", "value": "a"}])
    assert len(credentials.cookies_from_json(raw)) == 1

    raw = json.dumps({"cookies": [{"name": "sid_tt", "value": "b"}]})
    assert len(credentials.cookies_from_json(raw)) == 1


def test_parse_cookie_header():
    raw = json.dumps({"cookie": "sessionid=x; sid_guard=y"})
    cookies = credentials.cookies_from_json(raw)
    assert {c["name"] for c in cookies} == {"sessionid", "sid_guard"}


def test_parse_cookie_header_with_prefix():
    cookies = credentials.cookies_from_json(json.dumps({"cookie": "Cookie: sessionid=x"}))
    assert cookies[0]["name"] == "sessionid"


def test_empty_json_is_an_error():
    with pytest.raises(ValueError):
        credentials.cookies_from_json("[]")


def test_session_days_left_prefers_session_cookie():
    soon = {"name": "sessionid", "value": "x", "expires": time.time() + 86400}
    later = {"name": "sid_guard", "value": "x", "expires": time.time() + 86400 * 30}
    days = credentials.session_days_left([soon, later])
    assert days and 0.9 <= days <= 1.1


def test_session_days_left_falls_back():
    only = {"name": "sid_guard", "value": "x", "expires": time.time() + 86400 * 30}
    days = credentials.session_days_left([only])
    assert days and 29.0 <= days <= 30.0


def test_save_cookies_permissions(tmp_path, monkeypatch):
    target = tmp_path / "cookies.json"
    monkeypatch.setattr(credentials.config, "COOKIE_FILE", target)
    monkeypatch.setattr(credentials.config, "ensure_dirs", lambda: None)

    credentials.save_cookies([{"name": "sessionid", "value": "x"}])
    assert json.loads(target.read_text()) == [{"name": "sessionid", "value": "x"}]
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


# ---------------------------------------------------------------- Windows v10


def test_windows_v10_roundtrip():
    plain = b"1a2b3c4d5e6f708192a3b4c5d6e7f809"
    assert credentials._win_decrypt(win_encrypt(plain), WIN_KEY) == plain


def test_windows_v10_keeps_no_trailing_tag_bytes():
    plain = b"sessionid-value"
    got = credentials._win_decrypt(win_encrypt(plain), WIN_KEY)
    assert got == plain
    assert len(got) == len(plain)


def test_windows_v10_rejects_tampered_tag():
    blob = bytearray(win_encrypt(b"sessionid"))
    blob[-1] ^= 0xFF
    with pytest.raises(RuntimeError, match="GCM"):
        credentials._win_decrypt(bytes(blob), WIN_KEY)


def test_windows_v10_rejects_short_ciphertext():
    with pytest.raises(RuntimeError, match="长度"):
        credentials._win_decrypt(b"v10" + b"x" * 10, WIN_KEY)


def test_windows_v20_is_rejected():
    with pytest.raises(RuntimeError, match="v20"):
        credentials._win_decrypt(b"v20" + b"x" * 40, WIN_KEY)


# ---------------------------------------------------------------- macOS v10


@pytest.mark.parametrize("length", [1, 3, 15, 16, 17, 32])
def test_macos_pkcs7_roundtrip(length):
    plain = b"x" * length
    assert credentials._mac_decrypt(mac_encrypt(plain), MAC_KEY) == plain


def test_macos_space_padding_roundtrip():
    plain = b"sessionid"
    assert credentials._mac_decrypt(mac_encrypt_space_padded(plain), MAC_KEY) == plain


def test_macos_v20_is_rejected():
    with pytest.raises(RuntimeError, match="v20"):
        credentials._mac_decrypt(b"v20" + b"x" * 16, MAC_KEY)


# ---------------------------------------------------------------- 读库


def test_reads_mac_cookie_db(tmp_path):
    expires = int((time.time() + 86400 + 11644473600) * 1_000_000)
    db = tmp_path / "Cookies"
    make_db(
        db,
        [
            (".doubao.com", "sessionid", mac_encrypt(b"abc123"), "/", expires, 1, 1, 1),
            (
                ".doubao.com",
                "sid_guard",
                mac_encrypt_space_padded(b"def456"),
                "/",
                expires,
                0,
                1,
                2,
            ),
        ],
    )

    cookies = {c["name"]: c for c in credentials._read_mac_cookies(db, MAC_KEY)}
    assert cookies["sessionid"]["value"] == "abc123"
    assert cookies["sessionid"]["sameSite"] == "Lax"
    assert cookies["sid_guard"]["value"] == "def456"
    assert cookies["sid_guard"]["sameSite"] == "Strict"
    assert cookies["sessionid"]["expires"] == pytest.approx(time.time() + 86400, abs=5)


def test_reads_db_without_samesite_column(tmp_path):
    db = tmp_path / "Cookies"
    make_db(
        db,
        [(".doubao.com", "sessionid", mac_encrypt(b"abc"), "/", 0, 1, 1)],
        with_samesite=False,
    )
    cookies = credentials._read_mac_cookies(db, MAC_KEY)
    assert cookies[0]["value"] == "abc"
    assert "sameSite" not in cookies[0]


def test_all_cookies_failing_raises(tmp_path):
    db = tmp_path / "Cookies"
    make_db(db, [(".doubao.com", "sessionid", b"v20" + b"x" * 32, "/", 0, 1, 1, -1)])
    with pytest.raises(RuntimeError, match="v20"):
        credentials._read_mac_cookies(db, MAC_KEY)


# ---------------------------------------------------------------- 过期时间归一化


def test_expires_zero_becomes_session_cookie():
    cookie = credentials.normalize_cookie({"name": "sessionid", "value": "x", "expires": 0})
    assert cookie["expires"] == -1


def test_expires_negative_becomes_session_cookie():
    cookie = credentials.normalize_cookie({"name": "sessionid", "value": "x", "expires": -5})
    assert cookie["expires"] == -1


def test_expires_milliseconds_are_scaled():
    cookie = credentials.normalize_cookie(
        {"name": "sessionid", "value": "x", "expires": 1893456000000}
    )
    assert cookie["expires"] == pytest.approx(1893456000.0)


def test_expires_seconds_are_kept():
    cookie = credentials.normalize_cookie(
        {"name": "sessionid", "value": "x", "expirationDate": 1893456000.5}
    )
    assert cookie["expires"] == pytest.approx(1893456000.5)


def test_expires_bool_is_rejected():
    cookie = credentials.normalize_cookie({"name": "sessionid", "value": "x", "expires": True})
    assert cookie["expires"] == -1


# ---------------------------------------------------------------- 会话判断


@pytest.mark.parametrize("name", ["sessionid", "sessionid_ss", "sid_tt", "sid_guard"])
def test_has_session_accepts_all_session_names(name):
    assert credentials._has_session([{"name": name, "value": "x"}])


def test_has_session_rejects_unrelated_cookie():
    assert not credentials._has_session([{"name": "theme", "value": "dark"}])


# ---------------------------------------------------------------- DPAPI


def test_dpapi_unprotect_returns_none_off_windows():
    # Linux/macOS 上没有 ctypes.windll
    assert credentials._dpapi_unprotect(b"data") is None


def test_dpapi_unprotect_rejects_empty_input():
    assert credentials._dpapi_unprotect(b"") is None


def test_win_master_key_rejects_non_dpapi_payload(tmp_path):
    import base64

    state = tmp_path / "Local State"
    state.write_text(
        json.dumps({"os_crypt": {"encrypted_key": base64.b64encode(b"XXXXXabc").decode()}}),
        encoding="utf-8",
    )
    assert credentials._win_master_key(state) is None


def test_win_master_key_handles_missing_file(tmp_path):
    assert credentials._win_master_key(tmp_path / "missing") is None


def test_win_master_key_handles_broken_json(tmp_path):
    state = tmp_path / "Local State"
    state.write_text("{oops", encoding="utf-8")
    assert credentials._win_master_key(state) is None


# ---------------------------------------------------------------- 降级链


def test_load_credential_uses_injected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        credentials,
        "_load_injected",
        lambda: credentials.Credential([{"name": "sessionid", "value": "x"}], mode="env"),
    )
    cred = credentials.load_credential()
    assert cred.mode == "env"
    assert cred.cookies[0]["name"] == "sessionid"


def test_load_credential_excludes_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(
        credentials,
        "_load_injected",
        lambda: credentials.Credential([{"name": "sessionid", "value": "x"}], mode="env"),
    )
    monkeypatch.setattr(credentials, "_load_windows", lambda: None)
    with pytest.raises(RuntimeError, match="没有找到登录凭据"):
        credentials.load_credential(exclude={"env"})


def test_load_credential_reports_problems_when_empty(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("DOUBAO_COOKIES_JSON 解析失败")

    monkeypatch.setattr(credentials, "_load_injected", boom)
    monkeypatch.setattr(credentials, "_load_windows", lambda: None)
    with pytest.raises(RuntimeError, match="没有找到登录凭据"):
        credentials.load_credential()


def test_save_cookies_replaces_atomically(tmp_path, monkeypatch):
    target = tmp_path / "cookies.json"
    monkeypatch.setattr(credentials.config, "COOKIE_FILE", target)
    monkeypatch.setattr(credentials.config, "ensure_dirs", lambda: None)

    credentials.save_cookies([{"name": "sessionid", "value": "one"}])
    credentials.save_cookies([{"name": "sessionid", "value": "two"}])
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload == [{"name": "sessionid", "value": "two"}]
    assert not (tmp_path / "cookies.json.tmp").exists()


def test_build_cookies_drops_invalid_utf8():
    rows = [(".doubao.com", "sessionid", b"\xff\xfe", "/", 0, 1, 1, -1)]
    cookies, errors = credentials._build_cookies(rows, lambda enc: enc)
    assert cookies == []
    assert errors and "UTF-8" in errors[0]


# ---------------------------------------------------------------- 客户端整条路径


def test_load_injected_from_file(tmp_path, monkeypatch):
    target = tmp_path / "cookies.json"
    target.write_text(json.dumps([{"name": "sessionid", "value": "x"}]), encoding="utf-8")
    monkeypatch.setattr(credentials.config, "COOKIE_FILE", target)
    monkeypatch.delenv("DOUBAO_COOKIES_JSON", raising=False)
    cred = credentials._load_injected()
    assert cred and cred.mode == "injected"
    assert cred.cookies[0]["value"] == "x"


def test_load_injected_reports_broken_json(tmp_path, monkeypatch):
    target = tmp_path / "cookies.json"
    target.write_text("{oops", encoding="utf-8")
    monkeypatch.setattr(credentials.config, "COOKIE_FILE", target)
    monkeypatch.delenv("DOUBAO_COOKIES_JSON", raising=False)
    with pytest.raises(RuntimeError, match="读取失败"):
        credentials._load_injected()


def test_session_days_left_skips_expired():
    assert (
        credentials.session_days_left(
            [{"name": "sessionid", "value": "x", "expires": time.time() - 100}]
        )
        is None
    )


def test_load_mac_reads_session(tmp_path, monkeypatch):
    root = tmp_path / "Doubao"
    db = root / "Default" / "Network" / "Cookies"
    db.parent.mkdir(parents=True)
    make_db(db, [(".doubao.com", "sessionid", mac_encrypt(b"abc"), "/", 0, 1, 1, 1)])

    monkeypatch.setattr(credentials, "_mac_app_dirs", lambda: [root])
    monkeypatch.setattr(credentials, "_mac_key", lambda: MAC_KEY)
    cred = credentials._load_mac()
    assert cred and cred.mode == "mac-client"
    assert cred.cookies[0]["value"] == "abc"


def test_load_mac_reports_missing_key(tmp_path, monkeypatch):
    root = tmp_path / "Doubao"
    root.mkdir()
    monkeypatch.setattr(credentials, "_mac_app_dirs", lambda: [root])
    monkeypatch.setattr(credentials, "_mac_key", lambda: None)
    with pytest.raises(RuntimeError, match="钥匙串"):
        credentials._load_mac()


def test_load_mac_reports_missing_session(tmp_path, monkeypatch):
    root = tmp_path / "Doubao"
    db = root / "Default" / "Network" / "Cookies"
    db.parent.mkdir(parents=True)
    make_db(db, [(".doubao.com", "theme", mac_encrypt(b"dark"), "/", 0, 1, 1, 1)])

    monkeypatch.setattr(credentials, "_mac_app_dirs", lambda: [root])
    monkeypatch.setattr(credentials, "_mac_key", lambda: MAC_KEY)
    with pytest.raises(RuntimeError, match="不可用"):
        credentials._load_mac()


def test_load_windows_reads_session(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    root = local / "Doubao"
    db = root / "Default" / "Network" / "Cookies"
    db.parent.mkdir(parents=True)
    make_db(db, [(".doubao.com", "sessionid", win_encrypt(b"abc"), "/", 0, 1, 1, 1)])
    (root / "Local State").write_text(
        json.dumps({"os_crypt": {"encrypted_key": "x"}}), encoding="utf-8"
    )

    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(credentials, "_win_master_key", lambda path: WIN_KEY)
    cred = credentials._load_windows()
    assert cred and cred.mode == "windows-client"
    assert cred.cookies[0]["value"] == "abc"


def test_load_windows_without_localappdata(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert credentials._load_windows() is None


def test_win_app_dirs_finds_nested_user_data(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    nested = local / "SomeRandomDir" / "User Data"
    nested.mkdir(parents=True)
    (nested / "Local State").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(local))

    dirs = credentials._win_app_dirs()
    assert nested in dirs
