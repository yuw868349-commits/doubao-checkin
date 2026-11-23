"""页面状态判断。

豆包的签到卡片改过几版文案，判断逻辑集中在这里，改版时只改这几块表。
"""

from __future__ import annotations

# 不收裸「签到」：它是「已签到」「签到成功」的子串，会把已签页面判成可点
CLICK_TEXTS = ("立即签到", "立即领取", "签到领额度", "今日签到")
SUCCESS_TEXTS = ("签到成功", "领取成功")
ALREADY_TEXTS = ("已签到", "明日再来", "今日已领")
FAILURE_TEXTS = (
    "已抢完",
    "抢光了",
    "已领完",
    "活动结束",
    "活动已结束",
    "签到失败",
    "领取失败",
    "系统繁忙",
    "操作频繁",
    "稍后再试",
)

CARD_TITLES = ("每日签到",)

ENTRY_TEXTS = ("免费领额度", "领额度")

# 页面上一出现这些字样就说明签到卡片已经渲染出来，不需要再判登录。
# 故意不收裸「签到」「积分」：这两个词在登录页的推广文案里也会出现。
CARD_TEXTS = (
    "每日签到",
    "立即签到",
    "立即领取",
    "签到领额度",
    "今日签到",
    "免费领额度",
    "领额度",
)

CLIENT_ONLY_TEXTS = (
    "仅支持在豆包电脑版",
    "仅支持在豆包工作电脑版",
    "请使用豆包电脑版",
    "请前往豆包电脑版",
)


def card_state(body: str) -> str:
    """返回 clickable / signed / failed / unknown / absent。

    判断顺序按确定性从高到低：
    1. 失败文案最具体，先判；
    2. 只要还有可点的签到文案，就视为未签，按钮优先于「已签到」文案；
    3. 明确的已签文案；
    4. 卡片标题在但状态不明，返回 unknown，不能猜成已签；
    5. 什么都没有，absent。
    """
    if any(text in body for text in FAILURE_TEXTS):
        return "failed"
    if any(text in body for text in CLICK_TEXTS):
        return "clickable"
    if any(text in body for text in ALREADY_TEXTS):
        return "signed"
    if any(text in body for text in CARD_TITLES):
        return "unknown"
    return "absent"


def is_signed(body: str) -> bool:
    """页面明确显示已签。标题在、按钮不在但也没有已签文案时返回 False。"""
    return card_state(body) == "signed"


def classify(body: str, clicked: bool) -> str:
    """返回 success / already / FAILED / unknown。"""
    state = card_state(body)
    if state == "failed":
        return "FAILED"
    if any(text in body for text in SUCCESS_TEXTS):
        return "success"
    if state == "signed":
        return "success" if clicked else "already"
    return "unknown"


def is_client_only(body: str) -> bool:
    return any(text in body for text in CLIENT_ONLY_TEXTS)


def needs_login(body: str) -> bool:
    """页面出现扫码登录，或者只有登录入口、看不到任何签到相关字样。

    卡片已经渲染出来时一律不算登录墙：页面里「登录」二字经常出现在无关文案里。
    """
    if "扫码登录" in body:
        return True
    if any(text in body for text in CARD_TEXTS):
        return False
    return "登录" in body
