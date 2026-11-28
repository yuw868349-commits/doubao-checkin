"""结果对象、日志和落盘。"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
from dataclasses import asdict, dataclass
from enum import Enum

from . import config

MAX_LOG_BYTES = 512 * 1024
KEEP_LOG_LINES = 2000


class Result(str, Enum):
    """结果码。字符串值同时用于 JSON、日志和退出码映射。"""

    SUCCESS = "success"
    ALREADY = "already"
    AUTH_ERROR = "AUTH_ERROR"
    CLIENT_ONLY = "CLIENT_ONLY"
    NETWORK = "NETWORK"
    TIMEOUT = "TIMEOUT"
    BROWSER = "BROWSER"
    SIGNIN_CLOSED = "SIGNIN_CLOSED"
    NO_CREDENTIAL = "NO_CREDENTIAL"
    UNKNOWN = "unknown"
    FAILED = "FAILED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ResultMeta:
    report: str
    exit_code: int
    needs_attention: bool


# 唯一数据源：新增结果码只改这里，REPORTS / EXIT_CODES / NEEDS_ATTENTION 自动派生。
META: dict[str, ResultMeta] = {
    Result.SUCCESS.value: ResultMeta("签到成功，额度已到账", 0, False),
    Result.ALREADY.value: ResultMeta("今天已经签过，没有重复领取", 0, False),
    Result.UNKNOWN.value: ResultMeta("接口返回了没见过的状态", 8, True),
    Result.AUTH_ERROR.value: ResultMeta("登录态失效，需要重新登录", 2, True),
    Result.CLIENT_ONLY.value: ResultMeta("活动页把当前会话判定成网页端，拒绝签到", 3, True),
    Result.SIGNIN_CLOSED.value: ResultMeta(
        "平台已下线网页端签到入口，接口仍在但本账号/本时段不可用", 11, True
    ),
    Result.NETWORK.value: ResultMeta("接口打不开，重试后仍然失败", 4, True),
    Result.TIMEOUT.value: ResultMeta("时间预算用完或请求超时", 5, True),
    Result.BROWSER.value: ResultMeta("历史结果码：浏览器模式已移除，保留兼容", 6, True),
    Result.NO_CREDENTIAL.value: ResultMeta("没有找到登录凭据", 7, True),
    Result.FAILED.value: ResultMeta("页面明确提示签到失败，额度没有到账", 10, True),
    Result.ERROR.value: ResultMeta("运行时出现未预期异常", 9, True),
}

REPORTS = {name: meta.report for name, meta in META.items()}
EXIT_CODES = {name: meta.exit_code for name, meta in META.items()}
NEEDS_ATTENTION = {name for name, meta in META.items() if meta.needs_attention}

# 只有这些命令产生的成功记录才算「今天已经签过」。
CHECKIN_COMMANDS = ("run", "silent", "silent-poll")


def now_local() -> dt.datetime:
    return dt.datetime.now(config.CN_TZ)


def now_str() -> str:
    return now_local().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class CheckinResult:
    result: str
    command: str = "run"
    mode: str = ""
    screenshot: str | None = None
    detail: str | None = None
    session_days_left: float | None = None
    duration_ms: int | None = None

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
        tmp.replace(path)
    finally:
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()


def _ensure_private(path) -> None:
    # Windows 上 chmod 不生效，靠用户目录本身的 ACL
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def append_log(message: str) -> None:
    if os.environ.get("DOUBAO_LOG_FORMAT") == "json":
        record = {"ts": now_local().isoformat(timespec="seconds"), "message": message}
        line = json.dumps(record, ensure_ascii=False) + "\n"
    else:
        line = f"[{now_str()}] {message}\n"
    try:
        config.ensure_dirs()
        if config.LOG_FILE.exists() and config.LOG_FILE.stat().st_size > MAX_LOG_BYTES:
            lines = config.LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines(
                keepends=True
            )[-KEEP_LOG_LINES:]
            _atomic_write(config.LOG_FILE, "".join(lines))
        with config.LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line)
        _ensure_private(config.LOG_FILE)
    except OSError:
        pass


def write_result(result: CheckinResult) -> None:
    payload = result.to_dict()
    payload["saved_at"] = now_str()
    payload["saved_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    try:
        config.ensure_dirs()
        _atomic_write(config.RESULT_FILE, json.dumps(payload, ensure_ascii=False, indent=2))
        _ensure_private(config.RESULT_FILE)
    except OSError:
        pass


def _record_date(payload: dict) -> dt.date | None:
    raw = payload.get("saved_at_utc")
    if raw:
        try:
            stamp = dt.datetime.fromisoformat(str(raw))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=dt.timezone.utc)
            return stamp.astimezone(config.CN_TZ).date()
        except ValueError:
            pass
    raw = payload.get("saved_at")
    if raw:
        try:
            return dt.date.fromisoformat(str(raw)[:10])
        except ValueError:
            return None
    return None


def already_signed_from_last_result() -> bool:
    try:
        payload = json.loads(config.RESULT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if payload.get("command") not in CHECKIN_COMMANDS:
        return False
    if payload.get("result") not in (Result.SUCCESS.value, Result.ALREADY.value):
        return False
    return _record_date(payload) == now_local().date()
