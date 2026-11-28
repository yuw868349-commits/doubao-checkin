"""活动任务、签到与领奖。

接口行为都是实测过的：

- `v3/detail/` 是任务清单，`task_status` 0 未开始、1 待完成、2 已完成/待确认；
  任务完成后才带 `task_id`，领奖只认这个 id（字符串形式，传数字会 400）。
- 每日签到在 v3 任务里可能根本不存在；接口 `/campaign/landing/checkin` 要求
  先上报一次 click，再执行签到。部分账号/时段会被服务端以 710010702 拒绝，
  这属于平台侧下线，不是参数错误。
"""

from __future__ import annotations

import contextlib
import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from . import config, report
from .api import ApiError, ApiResponse, AuthError, Client
from .credentials import Credential

# 服务端对签到操作的拒绝码：接口还在，但这个账号/这个时段不给签
CODE_SIGNIN_CLOSED = 710010702


@dataclass
class Task:
    code: str
    title: str
    status: int
    task_id: str | None = None
    item_code: str | None = None
    subtitle: str = ""
    reward: int | None = None

    @property
    def claimable(self) -> bool:
        """只有服务端发了 task_id 的任务才可能领奖。"""
        return bool(self.task_id) and self.status in (2, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_code": self.code,
            "title": self.title,
            "status": self.status,
            "task_id": self.task_id,
            "item_code": self.item_code,
            "subtitle": self.subtitle,
            "reward": self.reward,
        }


@dataclass
class Campaign:
    tasks: list[Task] = field(default_factory=list)
    total_amount: int | None = None
    milestones: list[dict[str, Any]] = field(default_factory=list)

    def claimable(self) -> list[Task]:
        return [t for t in self.tasks if t.claimable]


@dataclass
class SigninOutcome:
    result: str
    detail: str = ""
    amount: int | None = None
    record_time: dt.datetime | None = None


def _number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def _text(record: dict[str, Any], names: Iterable[str]) -> str:
    for name in names:
        value = record.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def tasks_from(payload: dict[str, Any]) -> list[Task]:
    """从 detail 响应里取任务，字段缺失时继续向下走，不假设固定层级。"""
    found: list[Task] = []
    seen: set[str] = set()

    def walk(node: Any, depth: int) -> None:
        if depth > 8 or node is None:
            return
        if isinstance(node, list):
            for entry in node:
                walk(entry, depth + 1)
            return
        if not isinstance(node, dict):
            return
        code = _text(node, ("campaign_code", "campaign_item_code", "item_code"))
        title = _text(node, ("title", "name", "task_name"))
        if code and title and code not in seen:
            seen.add(code)
            rewards = node.get("reward_summaries")
            reward = None
            if isinstance(rewards, list) and rewards:
                amount = rewards[0].get("reward_amount") if isinstance(rewards[0], dict) else None
                reward = _number(amount)
            found.append(
                Task(
                    code=code,
                    title=title,
                    status=_number(node.get("task_status", node.get("status"))) or 0,
                    task_id=(
                        str(node.get("task_id")) if node.get("task_id") not in (None, "") else None
                    ),
                    item_code=_text(node, ("campaign_item_code",)),
                    subtitle=_text(node, ("subtitle", "description")),
                    reward=reward,
                )
            )
        for value in node.values():
            if isinstance(value, (dict, list)):
                walk(value, depth + 1)

    walk(payload, 0)
    return found


def records_from(payload: dict[str, Any]) -> list[dict[str, Any]]:
    records = payload.get("records")
    if isinstance(records, list):
        return [r for r in records if isinstance(r, dict)]
    return []


def record_time(record: dict[str, Any]) -> dt.datetime | None:
    stamp = _number(record.get("create_time_ms"))
    if stamp is None:
        return None
    try:
        return dt.datetime.fromtimestamp(stamp / 1000, config.CN_TZ)
    except (OverflowError, OSError, ValueError):
        return None


class CampaignApi:
    def __init__(self, credential: Credential) -> None:
        self.credential = credential
        self.client = Client(credential.cookies)

    # ---- 读取 -------------------------------------------------------------

    def detail(self) -> Campaign:
        response = self.client.post(
            config.DETAIL_PATH, {"campaign_group_code": config.CAMPAIGN_GROUP}
        )
        self._ensure_ok(response, "活动详情")
        data = response.data if isinstance(response.data, dict) else {}
        total = None
        progress = data.get("progress")
        if isinstance(progress, dict):
            total = _number(progress.get("total_amount"))
        return Campaign(
            tasks=tasks_from(data),
            total_amount=total,
            milestones=[m for m in data.get("milestones") or [] if isinstance(m, dict)],
        )

    def records(self, page_size: int = 100) -> list[dict[str, Any]]:
        response = self.client.post(config.RECORDS_PATH, {"page_size": page_size})
        self._ensure_ok(response, "额度流水")
        return records_from(response.data if isinstance(response.data, dict) else {})

    # ---- 签到 -------------------------------------------------------------

    def today_signin(self) -> dict[str, Any] | None:
        """额度流水里今天的 daily_signin 记录就是已签的铁证。"""
        today = dt.datetime.now(config.CN_TZ).date()
        for record in self.records():
            if str(record.get("task_code")) != "daily_signin":
                continue
            stamp = record_time(record)
            if stamp and stamp.date() == today:
                return record
        return None

    def signin(self) -> SigninOutcome:
        record = self.today_signin()
        if record is not None:
            stamp = record_time(record)
            return SigninOutcome(
                result="already",
                detail="额度流水里已有今天的每日签到记录",
                amount=_number(record.get("amount")),
                record_time=stamp,
            )

        self._report_click()
        response = self.client.post(
            config.CHECKIN_PATH,
            {
                "scene": config.CAMPAIGN_GROUP,
                "biz_params": {
                    "campaign_code": "daily_signin",
                    "campaign_group_code": config.CAMPAIGN_GROUP,
                },
            },
        )
        code = response.code
        if code == 0:
            data = response.data if isinstance(response.data, dict) else {}
            checked = bool(data.get("checked_in") or data.get("checked_in_today"))
            if checked:
                return SigninOutcome(
                    result="success",
                    detail="签到接口返回成功",
                    amount=_number(data.get("reward_amount")),
                )
            # code 0 但不能确认：用流水兜底
            fallback = self.today_signin()
            if fallback is not None:
                return SigninOutcome(
                    result="success",
                    detail="接口未回确认，但额度流水已出现今日签到",
                    amount=_number(fallback.get("amount")),
                    record_time=record_time(fallback),
                )
            return SigninOutcome(result="unknown", detail="签到接口返回 code 0，但流水里没有记录")
        if code == CODE_SIGNIN_CLOSED:
            return SigninOutcome(
                result="SIGNIN_CLOSED",
                detail="平台以 710010702 拒绝签到：网页端签到入口已下线，接口仍在但当前账号/时段不可用",
            )
        return SigninOutcome(
            result="FAILED", detail=f"签到被拒：code={code} {response.message()}".strip()
        )

    def _report_click(self) -> None:
        """签到的前置动作：上报一次卡片点击，缺了它 checkin 会直接被拒。"""
        with contextlib.suppress(ApiError, AuthError):
            # 上报失败不阻断，checkin 自己会再报一次拒绝原因
            self.client.post(
                config.REPORT_PATH,
                {
                    "event_type": "click",
                    "biz_event_name": "daily_signin",
                    "biz_params": {
                        "campaign_code": "daily_signin",
                        "campaign_group_code": config.CAMPAIGN_GROUP,
                    },
                    "card_event": {"card_group_id": "", "campaign_code": "daily_signin"},
                },
            )

    # ---- 领奖 -------------------------------------------------------------

    def claim(self, task: Task) -> ApiResponse | str:
        if not task.task_id:
            return "这个任务还没有可领取的 task_id（未完成或已领取）"
        return self.client.post(config.CLAIM_PATH, {"task_id": str(task.task_id)})

    def claim_all(self) -> tuple[int, int, list[str]]:
        """领掉所有带 task_id 的任务，返回（成功数，失败数，原因）。"""
        campaign = self.detail()
        success = failed = 0
        reasons: list[str] = []
        for task in campaign.claimable():
            try:
                outcome = self.claim(task)
            except (ApiError, AuthError) as exc:
                failed += 1
                reasons.append(f"{task.title}: {exc}")
                continue
            if isinstance(outcome, str):
                failed += 1
                reasons.append(f"{task.title}: {outcome}")
                continue
            if outcome.ok():
                success += 1
                report.append_log(f"领奖成功：{task.title}")
            else:
                failed += 1
                reasons.append(f"{task.title}: {outcome.message() or outcome.code}")
        return success, failed, reasons

    # ---- 工具 -------------------------------------------------------------

    @staticmethod
    def _ensure_ok(response: ApiResponse, what: str) -> None:
        if response.code == 0:
            return
        if response.code in (10001, 10002, 10003):
            raise AuthError(f"{what}：登录态失效（code={response.code}）")
        raise ApiError(f"{what}失败：code={response.code} {response.message()}".strip())
