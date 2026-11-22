"""页面状态判断。

豆包的签到卡片改过几版文案，判断逻辑集中在这里，改版时只改这几行。
"""

from __future__ import annotations

CLICK_TEXTS = ("立即签到", "立即领取", "签到领额度", "今日签到", "签到")
SUCCESS_TEXTS = ("签到成功", "领取成功")
ALREADY_TEXTS = ("已签到", "明日再来", "今日已领")

ENTRY_TEXTS = ("免费领额度", "领额度", "积分")


def is_signed(body: str) -> bool:
    """卡片在、按钮不在，就算已签。

    这条比找「已签到」文案可靠：任务卡片是常驻的，签到后按钮会变成对勾，
    页面正文里不一定留「已签到」三个字。
    """
    if "每日签到" in body and "立即签到" not in body:
        return True
    return any(text in body for text in ALREADY_TEXTS)


def classify(body: str, clicked: bool) -> str:
    """返回 success / already / unknown。

    点过按钮后按钮消失，只能靠 clicked 区分「刚刚签上」和「本来就签过」。
    """
    if any(text in body for text in SUCCESS_TEXTS):
        return "success"
    if is_signed(body):
        return "success" if clicked else "already"
    return "unknown"


def is_client_only(body: str) -> bool:
    return "仅支持在豆包电脑版" in body or "仅支持在豆包工作电脑版" in body


def needs_login(body: str) -> bool:
    """页面出现扫码登录，或者登录字样但没有任何签到相关按钮。"""
    if "扫码登录" in body:
        return True
    if "登录" not in body:
        return False
    return not any(text in body for text in ("立即签到", "立即领取", "签到"))
