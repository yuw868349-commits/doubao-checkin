"""结果对象、日志和落盘。"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import asdict, dataclass, field

from . import config

REPORTS = {
    "success": "签到成功，额度已到账",
    "already": "今天已经签过，没有重复领取",
    "unknown": "页面状态没认出来，建议看截图",
    "AUTH_ERROR": "登录态失效，需要重新登录",
    "CLIENT_ONLY": "活动页把当前会话判定成网页端，拒绝签到",
    "NETWORK": "页面打不开，重试后仍然失败",
    "TIMEOUT": "时间预算用完",
    "BROWSER": "浏览器或 Playwright 环境有问题",
    "NO_CREDENTIAL": "没有找到登录凭据",
    "ERROR": "运行时出现未预期异常",
}

NEEDS_ATTENTION = {
    "unknown",
    "AUTH_ERROR",
    "CLIENT_ONLY",
    "NETWORK",
    "TIMEOUT",
    "BROWSER",
    "NO_CREDENTIAL",
    "ERROR",
}

MAX_LOG_BYTES = 512 * 1024
KEEP_LOG_LINES = 2000


def now_str() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class CheckinResult:
    result: str
    mode: str = ""
    screenshot: str | None = None
    detail: str | None = None
    session_days_left: float | None = field(default=None)

    @property
    def report(self) -> str:
        return REPORTS.get(self.result, self.result)

    @property
    def needs_attention(self) -> bool:
        return self.result in NEEDS_ATTENTION

    def to_dict(self) -> dict:
        data = asdict(self)
        data["report"] = self.report
        data["needs_attention"] = self.needs_attention
        return data


def _atomic_write(path, text: str) -> None:
    """先写临时文件再替换，外部定时任务不会读到半截 JSON。"""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def append_log(message: str) -> None:
    line = f"[{now_str()}] {message}\n"
    try:
        config.ensure_dirs()
        if config.LOG_FILE.exists() and config.LOG_FILE.stat().st_size > MAX_LOG_BYTES:
            lines = config.LOG_FILE.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines(keepends=True)[-KEEP_LOG_LINES:]
            _atomic_write(config.LOG_FILE, "".join(lines))
        with config.LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


def write_result(result: CheckinResult) -> None:
    payload = result.to_dict()
    payload["saved_at"] = now_str()
    try:
        config.ensure_dirs()
        _atomic_write(config.RESULT_FILE, json.dumps(payload, ensure_ascii=False, indent=2))
    except OSError:
        pass


def already_signed_from_last_result() -> bool:
    try:
        payload = json.loads(config.RESULT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    saved = str(payload.get("saved_at", ""))
    today = dt.date.today().isoformat()
    return saved.startswith(today) and payload.get("result") in ("success", "already")
