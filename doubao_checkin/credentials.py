"""找登录凭据。

优先级：环境变量 → data/cookies.json → 客户端凭据 → data/profile。
每个平台的客户端存储格式不一样，这里只做读取，不写回。
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field

from . import config

_COOKIE_COLUMNS = "host_key, name, encrypted_value, path, expires_utc, is_httponly, is_secure"
_COOKIE_SELECT = f"SELECT {_COOKIE_COLUMNS}, samesite FROM cookies"  # noqa: S608
# 老版本库没有 samesite 列，退化查询保持列数一致
_COOKIE_SELECT_LEGACY = f"SELECT {_COOKIE_COLUMNS}, -1 AS samesite FROM cookies"  # noqa: S608

# Chromium 的 SameSite 枚举：-1 未设置，0 不限制，1 Lax，2 Strict
_SAME_SITE_DB = {-1: None, 0: "None", 1: "Lax", 2: "Strict"}
# 浏览器扩展导出的写法：no_restriction / unspecified 之类
_SAME_SITE_TEXT = {
    "strict": "Strict",
    "lax": "Lax",
    "none": "None",
    "no_restriction": "None",
    "unspecified": None,
    "": None,
}


@dataclass
class Credential:
    cookies: list[dict] = field(default_factory=list)
    mode: str = ""
    profile_dir: pathlib.Path | None = None


def _has_session(cookies: list[dict]) -> bool:
    return any(cookie.get("name") in config.SESSION_COOKIE_NAMES for cookie in cookies)


# ---------------------------------------------------------------- 通用


def _as_expires(raw: object) -> float:
    """Cookie 过期时间统一成 Unix 秒；认不出来、0、负数都当会话 Cookie。"""
    if raw is None or isinstance(raw, bool):
        return -1.0
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        text = str(raw).strip()
        if not text:
            return -1.0
        try:
            value = float(text)
        except ValueError:
            return -1.0
    if value <= 0:
        return -1.0
    # 有些导出工具给的是毫秒时间戳，Playwright 只认秒，直接传会抛错
    if value > 1e11:
        value /= 1000.0
    return value


def _normalize_same_site(raw: object) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return _SAME_SITE_DB.get(raw)
    return _SAME_SITE_TEXT.get(str(raw).strip().lower())


def normalize_cookie(raw: dict) -> dict | None:
    name = raw.get("name")
    value = raw.get("value", raw.get("val"))
    if not name or value in (None, ""):
        return None
    if isinstance(value, str):
        value = value.strip()
    if not value:
        return None

    secure = bool(raw.get("secure", raw.get("is_secure", True)))
    same_site = _normalize_same_site(raw.get("sameSite", raw.get("same_site")))
    # SameSite=None 必须带 Secure，否则浏览器会当成 Lax 处理
    if same_site == "None" and not secure:
        same_site = "Lax"

    cookie = {
        "name": str(name),
        "value": str(value),
        "domain": str(raw.get("domain") or raw.get("host_key") or ".doubao.com"),
        "path": str(raw.get("path") or "/"),
        "expires": _as_expires(
            raw.get("expires", raw.get("expires_utc", raw.get("expirationDate")))
        ),
        "httpOnly": bool(raw.get("httpOnly", raw.get("is_httponly", False))),
        "secure": secure,
    }
    if same_site:
        cookie["sameSite"] = same_site
    return cookie


def _parse_cookie_header(header: str) -> list[dict]:
    cookies = []
    if header.lower().startswith("cookie:"):
        header = header[len("cookie:") :]
    for chunk in header.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        name, value = chunk.split("=", 1)
        cookies.append({"name": name.strip(), "value": value.strip()})
    return cookies


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

    # 数组里混着 "a=b; c=d" 这种字符串也认
    expanded: list[dict] = []
    for item in data:
        if isinstance(item, str):
            expanded.extend(_parse_cookie_header(item))
        elif isinstance(item, dict):
            expanded.append(item)

    cookies = []
    for item in expanded:
        cookie = normalize_cookie(item)
        if cookie:
            cookies.append(cookie)
    if not cookies:
        raise ValueError("Cookie JSON 里没有可用的 name/value 对")
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
    primary, fallback = [], []
    now = time.time()
    for cookie in cookies:
        name = cookie.get("name")
        if name not in config.SESSION_COOKIE_NAMES:
            continue
        expires = _as_expires(cookie.get("expires"))
        if expires <= 0:
            continue
        days = (expires - now) / 86400
        if days <= 0:
            continue
        if name in ("sessionid", "sessionid_ss"):
            primary.append(days)
        else:
            fallback.append(days)
    pool = primary or fallback
    return round(min(pool), 1) if pool else None


def save_cookies(cookies: list[dict]) -> pathlib.Path:
    config.ensure_dirs()
    tmp = config.COOKIE_FILE.with_name(config.COOKIE_FILE.name + ".tmp")
    tmp.write_text(
        json.dumps(cookies, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Windows 上 chmod 不生效，靠用户目录本身的 ACL
    with contextlib.suppress(OSError):
        tmp.chmod(0o600)
    tmp.replace(config.COOKIE_FILE)
    return config.COOKIE_FILE


# ---------------------------------------------------------------- 客户端 Cookie 库


def _copy_db(src: pathlib.Path, dst: pathlib.Path) -> None:
    """复制 Cookie 库，连 -wal / -shm 一起带走，否则可能读到旧快照。"""
    shutil.copy2(src, dst)
    for suffix in ("-wal", "-shm"):
        side = src.with_name(src.name + suffix)
        if not side.exists():
            continue
        try:
            shutil.copy2(side, dst.with_name(dst.name + suffix))
        except OSError:
            # 客户端正在写的时候 -wal 可能拿不到，主体库仍然可读
            continue


def _query_rows(db: pathlib.Path) -> list[tuple]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        try:
            return conn.execute(_COOKIE_SELECT).fetchall()
        except sqlite3.OperationalError:
            return conn.execute(_COOKIE_SELECT_LEGACY).fetchall()
    finally:
        conn.close()


def _rows_from_copy(db: pathlib.Path) -> list[tuple]:
    with tempfile.TemporaryDirectory() as tmp:
        copy = pathlib.Path(tmp) / "Cookies"
        _copy_db(db, copy)
        return _query_rows(copy)


def _chrome_time(value: object) -> float:
    try:
        number = float(str(value or 0))
    except (TypeError, ValueError):
        return -1
    if number <= 0:
        return -1
    # Chromium 的 expires_utc 是 1601-01-01 起的微秒
    return number / 1_000_000 - 11644473600


def _build_cookies(rows: list[tuple], decrypt) -> tuple[list[dict], list[str]]:
    """把库里的行解成 Playwright 能吃的 Cookie，单条失败不影响其它条。"""
    cookies: list[dict] = []
    errors: list[str] = []
    for host, name, enc, cookie_path, expires, http_only, secure, same_site in rows:
        try:
            value = decrypt(bytes(enc or b""))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
            continue
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"{name}: 解密结果不是 UTF-8")
            continue
        if not text:
            continue

        cookie = {
            "name": name,
            "value": text,
            "domain": host,
            "path": cookie_path or "/",
            "expires": _chrome_time(expires),
            "httpOnly": bool(http_only),
            "secure": bool(secure),
        }
        try:
            site = _SAME_SITE_DB.get(int(same_site))
        except (TypeError, ValueError):
            site = None
        if site == "None" and not cookie["secure"]:
            site = "Lax"
        if site:
            cookie["sameSite"] = site
        cookies.append(cookie)
    return cookies, errors


# ---------------------------------------------------------------- macOS


def _mac_app_dirs() -> list[pathlib.Path]:
    roots = [
        pathlib.Path.home() / "Library/Application Support/Doubao",
        pathlib.Path.home()
        / "Library/Containers/com.doubao.desktop/Data/Library/Application Support/Doubao",
    ]
    return [path for path in roots if path.exists()]


def _mac_cookie_paths(root: pathlib.Path) -> list[pathlib.Path]:
    patterns = ("Cookies", "*/Cookies", "*/Network/Cookies", "*/*/Network/Cookies")
    found: list[pathlib.Path] = []
    seen: set[pathlib.Path] = set()
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if not path.is_file() or path in seen:
                continue
            seen.add(path)
            found.append(path)
    return found


def _mac_key() -> bytes | None:
    password = None
    for service in ("Doubao Safe Storage", "doubao Safe Storage", "豆包 Safe Storage"):
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s", service, "-w"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if out.returncode == 0 and out.stdout.strip():
                password = out.stdout.strip()
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


def _unpad(plain: bytes) -> bytes:
    """Chromium 用 PKCS#7 填充；更老的版本用空格填充，两种都认。"""
    if not plain:
        return plain
    pad = plain[-1]
    if 1 <= pad <= 16 and plain.endswith(bytes([pad]) * pad):
        return plain[:-pad]
    return plain.rstrip(b" ")


def _mac_decrypt(value: bytes, key: bytes) -> bytes:
    if value[:3] == b"v10":
        from Crypto.Cipher import AES

        iv = b" " * 16
        cipher = AES.new(key, AES.MODE_CBC, iv)
        return _unpad(cipher.decrypt(value[3:]))
    if value[:3] == b"v20":
        raise RuntimeError("Cookie 是 v20/App-Bound 加密，当前不支持")
    return value


def _read_mac_cookies(path: pathlib.Path, key: bytes) -> list[dict]:
    rows = _rows_from_copy(path)
    cookies, errors = _build_cookies(rows, lambda enc: _mac_decrypt(enc, key))
    if rows and not cookies and errors:
        raise RuntimeError(f"{len(errors)} 条 Cookie 解密失败：{errors[0]}")
    return cookies


def _load_mac() -> Credential | None:
    dirs = _mac_app_dirs()
    if not dirs:
        return None
    key = _mac_key()
    if not key:
        raise RuntimeError(
            "找不到钥匙串里的 Doubao Safe Storage 密钥，"
            "确认豆包电脑版已登录，且允许当前用户读取钥匙串"
        )

    problems: list[str] = []
    for root in dirs:
        for path in _mac_cookie_paths(root):
            try:
                cookies = _read_mac_cookies(path, key)
            except (OSError, sqlite3.Error, RuntimeError) as exc:
                problems.append(f"{path.name}：{exc}")
                continue
            if _has_session(cookies):
                return Credential(cookies, mode="mac-client")
            problems.append(f"{path}：没有 sessionid")
    if problems:
        raise RuntimeError("macOS 客户端 Cookie 不可用：" + "；".join(problems[:2]))
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


def _win_cookie_dbs(root: pathlib.Path) -> list[pathlib.Path]:
    patterns = ("Network/Cookies", "*/Network/Cookies", "*/*/Network/Cookies")
    found: list[pathlib.Path] = []
    seen: set[pathlib.Path] = set()
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if not path.is_file() or path in seen:
                continue
            seen.add(path)
            found.append(path)
    return found


def _dpapi_unprotect(raw: bytes) -> bytes | None:
    """调 Windows DPAPI 解开一段密文。非 Windows 或调用失败返回 None。"""
    if not raw:
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt
    except ImportError:
        return None

    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(raw, len(raw))
    blob_in = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        return None
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        windll.kernel32.LocalFree(blob_out.pbData)


def _win_master_key(local_state: pathlib.Path) -> bytes | None:
    try:
        payload = json.loads(local_state.read_text(encoding="utf-8"))
        encrypted = payload.get("os_crypt", {}).get("encrypted_key")
        if not encrypted:
            return None
        raw = base64.b64decode(encrypted)
        if raw[:5] != b"DPAPI":
            return None
        return _dpapi_unprotect(raw[5:])
    except (OSError, ValueError, AttributeError):
        return None


def _win_decrypt(value: bytes, key: bytes) -> bytes:
    if value[:3] == b"v10":
        if len(value) < 31:
            raise RuntimeError("v10 密文长度不足，无法校验 GCM tag")
        from Crypto.Cipher import AES

        nonce, body, tag = value[3:15], value[15:-16], value[-16:]
        try:
            return AES.new(key, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(body, tag)
        except ValueError as exc:
            raise RuntimeError("GCM 校验失败，主密钥不匹配或数据损坏") from exc
    if value[:3] == b"v20":
        raise RuntimeError("Cookie 是 v20/App-Bound 加密，当前不支持")
    return value


def _copy_locked_db(path: pathlib.Path, target: pathlib.Path) -> bool:
    try:
        _copy_db(path, target)
        return True
    except OSError:
        pass
    try:
        subprocess.run(
            ["esentutl", "/y", str(path), "/vss", "/d", str(target)],
            capture_output=True,
            timeout=30,
            check=False,
        )
        return target.exists()
    except (OSError, subprocess.SubprocessError):
        return False


def _read_win_cookies(db: pathlib.Path, key: bytes) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        copy = pathlib.Path(tmp) / "Cookies"
        if not _copy_locked_db(db, copy):
            raise RuntimeError(f"Cookie 库被占用，复制失败：{db}")
        rows = _query_rows(copy)

    cookies, errors = _build_cookies(rows, lambda enc: _win_decrypt(enc, key))
    if rows and not cookies and errors:
        raise RuntimeError(f"{len(errors)} 条 Cookie 解密失败：{errors[0]}")
    return cookies


def _load_windows() -> Credential | None:
    # 不靠 os.name 判断：目录存在就尝试读，DPAPI 在非 Windows 上会返回 None。
    problems: list[str] = []
    for root in _win_app_dirs():
        dbs = _win_cookie_dbs(root)
        if not dbs:
            continue
        local_state = root / "Local State"
        if not local_state.exists():
            local_state = root / "User Data" / "Local State"
        key = _win_master_key(local_state) if local_state.exists() else None
        if not key:
            problems.append(f"{local_state}：读不到 DPAPI 主密钥")
            continue
        for db in dbs:
            try:
                cookies = _read_win_cookies(db, key)
            except (OSError, sqlite3.Error, RuntimeError) as exc:
                problems.append(f"{db.parent.name}：{exc}")
                continue
            if _has_session(cookies):
                return Credential(cookies, mode="windows-client")
            problems.append(f"{db}：没有 sessionid")
    if problems:
        raise RuntimeError("Windows 客户端 Cookie 不可用：" + "；".join(problems[:2]))
    return None


# ---------------------------------------------------------------- 对外


def load_credential(exclude: set[str] | None = None) -> Credential:
    """按优先级找凭据。exclude 里的来源跳过，用于登录态失效后的回落。"""
    exclude = exclude or set()
    problems: list[str] = []
    candidates: list[Credential] = []

    try:
        injected = _load_injected()
    except RuntimeError as exc:
        problems.append(str(exc))
        injected = None
    if injected:
        candidates.append(injected)

    if sys.platform == "darwin" and "mac-client" not in exclude:
        try:
            mac = _load_mac()
        except RuntimeError as exc:
            problems.append(str(exc))
            mac = None
        if mac:
            candidates.append(mac)

    if "windows-client" not in exclude:
        try:
            win = _load_windows()
        except RuntimeError as exc:
            problems.append(str(exc))
            win = None
        if win:
            candidates.append(win)

    # 客户端凭据不可用时还能退回持久化 profile
    if config.PROFILE_DIR.exists() and "profile" not in exclude:
        candidates.append(Credential(mode="profile", profile_dir=config.PROFILE_DIR))

    for candidate in candidates:
        if candidate.mode not in exclude:
            return candidate

    message = (
        "没有找到登录凭据。macOS/Windows 请先登录豆包电脑版；"
        "Linux 请设置 DOUBAO_COOKIES_JSON 或 data/cookies.json；"
        "也可以运行 python checkin.py login"
    )
    if problems:
        message = "；".join(problems) + "。" + message
    raise RuntimeError(message)
