"""找登录凭据。

优先级：环境变量 → data/cookies.json → 客户端凭据 → data/profile。
每个平台的客户端存储格式不一样，这里只做读取，不写回。
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass, field

from . import config

COOKIE_KEYS = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")


@dataclass
class Credential:
    cookies: list[dict] = field(default_factory=list)
    mode: str = ""
    profile_dir: pathlib.Path | None = None


# ---------------------------------------------------------------- 通用


def normalize_cookie(raw: dict) -> dict | None:
    name = raw.get("name")
    value = raw.get("value", raw.get("val"))
    if not name or value in (None, ""):
        return None
    if isinstance(value, str):
        value = value.strip()
    if not value:
        return None

    return {
        "name": str(name),
        "value": str(value),
        "domain": str(raw.get("domain") or raw.get("host_key") or ".doubao.com"),
        "path": str(raw.get("path") or "/"),
        "expires": float(raw.get("expires", raw.get("expires_utc", -1)) or -1),
        "httpOnly": bool(raw.get("httpOnly", raw.get("is_httponly", False))),
        "secure": bool(raw.get("secure", raw.get("is_secure", True))),
        "sameSite": raw.get("sameSite") or raw.get("same_site") or "Lax",
    }


def cookies_from_json(raw: str) -> list[dict]:
    data = json.loads(raw)
    if isinstance(data, dict):
        if "cookies" in data:
            data = data["cookies"]
        elif "cookie" in data and isinstance(data["cookie"], str):
            data = _parse_cookie_header(data["cookie"])
        else:
            data = [data]
    if not isinstance(data, list):
        raise ValueError("Cookie JSON 必须是数组，或带 cookies 字段的对象")

    cookies = []
    for item in data:
        if isinstance(item, str):
            item = _parse_cookie_header(item)[0] if _parse_cookie_header(item) else {}
        if not isinstance(item, dict):
            continue
        cookie = normalize_cookie(item)
        if cookie:
            cookies.append(cookie)
    if not cookies:
        raise ValueError("Cookie JSON 里没有可用的 name/value 对")
    return cookies


def _parse_cookie_header(header: str) -> list[dict]:
    cookies = []
    for chunk in header.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        name, value = chunk.split("=", 1)
        cookies.append({"name": name.strip(), "value": value.strip()})
    return cookies


def _load_injected() -> Credential | None:
    raw = os.environ.get("DOUBAO_COOKIES_JSON", "").strip()
    if raw:
        try:
            return Credential(cookies_from_json(raw), mode="env")
        except ValueError as exc:
            raise RuntimeError(f"DOUBAO_COOKIES_JSON 解析失败：{exc}") from exc

    if config.COOKIE_FILE.exists():
        try:
            return Credential(
                cookies_from_json(config.COOKIE_FILE.read_text(encoding="utf-8")),
                mode="injected",
            )
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"data/cookies.json 读取失败：{exc}") from exc
    return None


def session_days_left(cookies: list[dict]) -> float | None:
    best = None
    for cookie in cookies:
        if cookie.get("name") not in config.SESSION_COOKIE_NAMES:
            continue
        expires = float(cookie.get("expires", -1) or -1)
        if expires <= 0:
            continue
        days = (expires - __import__("time").time()) / 86400
        if days > 0 and (best is None or days > best):
            best = days
    return round(best, 1) if best else None


def save_cookies(cookies: list[dict]) -> pathlib.Path:
    config.ensure_dirs()
    config.COOKIE_FILE.write_text(
        json.dumps(cookies, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        os.chmod(config.COOKIE_FILE, 0o600)
    except OSError:
        pass
    return config.COOKIE_FILE


# ---------------------------------------------------------------- macOS


def _mac_app_dirs() -> list[pathlib.Path]:
    roots = [
        pathlib.Path.home() / "Library/Application Support/Doubao",
        pathlib.Path.home() / "Library/Containers/com.doubao.desktop/Data/Library/Application Support/Doubao",
    ]
    return [path for path in roots if path.exists()]


def _mac_key() -> bytes | None:
    password = None
    for service in ("Doubao Safe Storage", "doubao Safe Storage", "豆包 Safe Storage"):
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s", service, "-w"],
                capture_output=True, text=True, timeout=10,
            )
            if out.returncode == 0 and out.stdout.strip():
                password = out.stdout.strip().encode()
                break
        except (OSError, subprocess.SubprocessError):
            continue
    if not password:
        return None
    try:
        from Crypto.Hash import SHA1
        from Crypto.Protocol.KDF import PBKDF2

        return PBKDF2(password, b"saltysalt", 16, count=1003, hmac_hash_module=SHA1)
    except Exception:
        return None


def _mac_decrypt(value: bytes, key: bytes) -> bytes:
    from Crypto.Cipher import AES

    if value[:3] == b"v10":
        iv = b" " * 16
        cipher = AES.new(key, AES.MODE_CBC, iv)
        plain = cipher.decrypt(value[3:])
        if plain.endswith(b"\x20\x20"):
            plain = plain[:-2]
        return plain
    if value[:3] == b"v20":
        raise RuntimeError("Cookie 是 v20/App-Bound 加密，当前不支持")
    return value


def _read_mac_cookies(path: pathlib.Path, key: bytes) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        copy = pathlib.Path(tmp) / "Cookies"
        shutil.copy2(path, copy)
        conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT host_key, name, encrypted_value, path, expires_utc, is_httponly, is_secure FROM cookies").fetchall()
        finally:
            conn.close()

    cookies = []
    for host, name, enc, cookie_path, expires, http_only, secure in rows:
        try:
            value = _mac_decrypt(bytes(enc or b""), key).decode("utf-8", errors="replace")
        except Exception:
            continue
        if not value:
            continue
        cookies.append({
            "name": name,
            "value": value,
            "domain": host,
            "path": cookie_path or "/",
            "expires": _chrome_time(expires),
            "httpOnly": bool(http_only),
            "secure": bool(secure),
            "sameSite": "Lax",
        })
    return cookies


def _chrome_time(value) -> float:
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        return -1
    if value <= 0:
        return -1
    # Chromium 的 expires_utc 是 1601-01-01 起的微秒
    return value / 1_000_000 - 11644473600


def _load_mac() -> Credential | None:
    dirs = _mac_app_dirs()
    if not dirs:
        return None
    key = _mac_key()
    if not key:
        return None
    for root in dirs:
        candidates = sorted(root.glob("*/Cookies")) + sorted(root.glob("Cookies"))
        for path in candidates:
            try:
                cookies = _read_mac_cookies(path, key)
            except (OSError, sqlite3.Error):
                continue
            if any(cookie["name"] == "sessionid" for cookie in cookies):
                return Credential(cookies, mode="mac-client")
    return None


# ---------------------------------------------------------------- Windows


def _win_app_dirs() -> list[pathlib.Path]:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    bases = [
        pathlib.Path(local) / "Doubao",
        pathlib.Path(local) / "DoubaoUserData",
        pathlib.Path(local) / "Programs" / "Doubao",
    ]
    found = [path for path in bases if path.exists()]
    if found:
        return found

    # 有些安装器把数据放在 %LOCALAPPDATA%\<随机目录>\User Data 下
    try:
        for path in pathlib.Path(local).iterdir():
            if not path.is_dir():
                continue
            if (path / "User Data" / "Local State").exists():
                found.append(path / "User Data")
    except OSError:
        pass
    return found


def _win_master_key(local_state: pathlib.Path) -> bytes | None:
    try:
        payload = json.loads(local_state.read_text(encoding="utf-8"))
        encrypted = payload.get("os_crypt", {}).get("encrypted_key")
        if not encrypted:
            return None
        raw = base64.b64decode(encrypted)
        if raw[:5] != b"DPAPI":
            return None
        raw = raw[5:]
        import ctypes
        import ctypes.wintypes as wt

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        blob_in = DATA_BLOB(len(raw), ctypes.cast(ctypes.create_string_buffer(raw), ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    except (OSError, ValueError, AttributeError):
        return None


def _win_decrypt(value: bytes, key: bytes) -> bytes:
    if value[:3] == b"v10":
        from Crypto.Cipher import AES

        return AES.new(key, AES.MODE_GCM, nonce=value[3:15]).decrypt(value[15:])
    if value[:3] == b"v20":
        raise RuntimeError("Cookie 是 v20/App-Bound 加密，当前不支持")
    return value


def _copy_locked_db(path: pathlib.Path, target: pathlib.Path) -> bool:
    try:
        shutil.copy2(path, target)
        return True
    except OSError:
        pass
    try:
        subprocess.run(
            ["esentutl", "/y", str(path), "/vss", "/d", str(target)],
            capture_output=True, timeout=30, check=False,
        )
        return target.exists()
    except (OSError, subprocess.SubprocessError):
        return False


def _query_cookies(db: pathlib.Path) -> list[tuple]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute(
            "SELECT host_key, name, encrypted_value, path, expires_utc, is_httponly, is_secure FROM cookies"
        ).fetchall()
    finally:
        conn.close()


def _read_win_cookies(db: pathlib.Path, key: bytes) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        copy = pathlib.Path(tmp) / "Cookies"
        if not _copy_locked_db(db, copy):
            raise RuntimeError(f"Cookie 库被占用，复制失败：{db}")
        rows = _query_cookies(copy)

    cookies = []
    for host, name, enc, cookie_path, expires, http_only, secure in rows:
        try:
            value = _win_decrypt(bytes(enc or b""), key).decode("utf-8", errors="replace")
        except Exception:
            continue
        if not value:
            continue
        cookies.append({
            "name": name,
            "value": value,
            "domain": host,
            "path": cookie_path or "/",
            "expires": _chrome_time(expires),
            "httpOnly": bool(http_only),
            "secure": bool(secure),
            "sameSite": "Lax",
        })
    return cookies


def _load_windows() -> Credential | None:
    if os.name != "nt":
        return None
    for root in _win_app_dirs():
        local_state = root / "Local State"
        if not local_state.exists():
            local_state = root / "User Data" / "Local State"
        db_candidates = sorted(root.glob("*/Network/Cookies")) + sorted(root.glob("Network/Cookies"))
        if not db_candidates:
            continue
        key = _win_master_key(local_state) if local_state.exists() else None
        if not key:
            continue
        for db in db_candidates:
            try:
                cookies = _read_win_cookies(db, key)
            except (OSError, sqlite3.Error, RuntimeError):
                continue
            if any(cookie["name"] == "sessionid" for cookie in cookies):
                return Credential(cookies, mode="windows-client")
    return None


# ---------------------------------------------------------------- 对外


def load_credential(prefer_profile: bool = False) -> Credential:
    if prefer_profile and config.PROFILE_DIR.exists():
        return Credential(mode="profile", profile_dir=config.PROFILE_DIR)

    try:
        injected = _load_injected()
    except RuntimeError:
        raise
    if injected:
        return injected

    if os.name == "posix" and __import__("sys").platform == "darwin":
        mac = _load_mac()
        if mac:
            return mac

    if os.name == "nt":
        win = _load_windows()
        if win:
            return win

    if config.PROFILE_DIR.exists():
        return Credential(mode="profile", profile_dir=config.PROFILE_DIR)

    raise RuntimeError(
        "没有找到登录凭据。macOS/Windows 请先登录豆包电脑版；"
        "Linux 请设置 DOUBAO_COOKIES_JSON 或 data/cookies.json；"
        "也可以运行 python checkin.py login"
    )
