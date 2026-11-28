"""豆包活动接口的 HTTP 客户端。

只依赖标准库：
- 活动接口按客户端信封校验，缺 query 参数会回 code=1 invalid param；
- 写操作返回 code=0 但 biz_error 里可能带业务失败，必须一并处理。
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

from . import config, report


class ApiError(RuntimeError):
    """网络层失败，已经重试到上限。"""


class AuthError(RuntimeError):
    """登录态失效。"""


@dataclass
class ApiResponse:
    code: int
    msg: str
    data: Any
    raw: dict[str, Any]

    @property
    def biz_error(self) -> dict[str, Any] | None:
        data = self.data
        if isinstance(data, dict):
            err = data.get("biz_error")
            if isinstance(err, dict):
                return err
        return None

    def ok(self) -> bool:
        return self.code == 0 and self.biz_error is None

    def message(self) -> str:
        parts = [self.msg or self.raw.get("message") or ""]
        err = self.biz_error
        if err:
            text = str(err.get("message") or "").strip()
            if text:
                parts.append(text)
        return "；".join(p for p in parts if p)


class Client:
    def __init__(self, cookies: list[dict[str, Any]], *, user_agent: str | None = None) -> None:
        self.cookies = cookies
        self.user_agent = user_agent or config.CLIENT_UA
        self.cookie_header = "; ".join(
            f"{c['name']}={c['value']}" for c in cookies if c.get("name") and c.get("value")
        )

    def _url(self, path: str) -> str:
        params = dict(config.CLIENT_PARAMS)
        params["web_tab_id"] = str(uuid.uuid4())
        return f"{config.SITE}{path}?{urllib.parse.urlencode(params)}"

    def _headers(self, referer: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN",
            "Origin": config.SITE,
            "User-Agent": self.user_agent,
            "Referer": referer,
            "Agw-Js-Conv": "str",
            "Cookie": self.cookie_header,
        }

    def post(
        self, path: str, payload: dict[str, Any] | None = None, *, referer: str | None = None
    ) -> ApiResponse:
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - URL 固定是 https://www.doubao.com
            self._url(path),
            data=body,
            headers=self._headers(referer or config.REFERER),
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(config.Timing.RETRIES + 1):
            try:
                with urllib.request.urlopen(  # noqa: S310 - URL 固定是 https://www.doubao.com
                    request, timeout=config.Timing.TIMEOUT
                ) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                    return self._parse(raw)
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise AuthError(f"HTTP {exc.code}，登录态可能失效") from exc
                last_error = exc
                if exc.code == 400:
                    raise ApiError("HTTP 400：请求参数被拒绝") from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt < config.Timing.RETRIES:
                delay = config.Timing.BACKOFF[min(attempt, len(config.Timing.BACKOFF) - 1)]
                report.append_log(f"请求 {path} 失败（{last_error}），{delay:.0f} 秒后重试")
                time.sleep(delay)
        raise ApiError(f"请求 {path} 失败：{last_error}")

    @staticmethod
    def _parse(raw: dict[str, Any]) -> ApiResponse:
        try:
            code = int(raw.get("code", raw.get("status_code", -1)))
        except (TypeError, ValueError):
            code = -1
        return ApiResponse(code=code, msg=str(raw.get("msg") or ""), data=raw.get("data"), raw=raw)


def jitter_sleep(base: float) -> None:
    """带抖动，避免多实例同时撞接口。"""
    time.sleep(base + random.uniform(0, base / 2))  # noqa: S311 - 只是退避抖动，不需要密码学随机
