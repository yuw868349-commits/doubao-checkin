from doubao_checkin import state


def test_clickable_when_button_present():
    assert state.card_state("每日签到\n立即签到") == "clickable"
    assert not state.is_signed("每日签到\n立即签到")


def test_signed_when_explicit_text():
    assert state.card_state("每日签到\n已签到\n明日再来") == "signed"
    assert state.is_signed("每日签到\n已签到")


def test_title_without_button_is_unknown_not_signed():
    """标题已渲染、按钮还没出来时不能猜成已签。"""
    assert state.card_state("每日签到\n累计 200 点") == "unknown"
    assert not state.is_signed("每日签到\n累计 200 点")


def test_alternative_button_texts_are_clickable():
    for body in ("每日签到\n立即领取", "每日签到\n今日签到", "每日签到\n签到领额度"):
        assert state.card_state(body) == "clickable", body


def test_signed_text_with_button_present_is_clickable():
    """多任务卡片里可能同时出现「已签到」和「立即签到」，按钮优先。"""
    assert state.card_state("已签到\n每日签到\n立即签到") == "clickable"


def test_sold_out_is_failed():
    assert state.card_state("每日签到\n今日名额已抢完") == "failed"
    assert state.classify("每日签到\n今日名额已抢完", clicked=True) == "FAILED"


def test_activity_ended_is_failed():
    assert state.classify("每日签到\n活动已结束", clicked=True) == "FAILED"


def test_clicked_and_signed_is_success():
    assert state.classify("每日签到\n签到成功", clicked=True) == "success"


def test_signed_without_click_is_already():
    assert state.classify("已签到\n每日签到", clicked=False) == "already"


def test_clicked_but_still_clickable_is_unknown():
    """点完按钮还在，说明没点上或者页面没反应。"""
    assert state.classify("每日签到\n立即签到", clicked=True) == "unknown"


def test_unknown_state():
    assert state.classify("活动页升级中", clicked=False) == "unknown"


def test_client_only_banner():
    assert state.is_client_only("仅支持在豆包电脑版签到")
    assert state.is_client_only("请前往豆包电脑版完成任务")
    assert not state.is_client_only("每日签到\n立即签到")


def test_login_wall():
    assert state.needs_login("扫码登录\n登录后同步你的对话")


def test_login_entry_only():
    assert state.needs_login("登录\n注册\n帮助中心")


def test_card_page_is_not_a_login_wall():
    """卡片已经渲染出来时，正文里出现「登录」不算登录墙。"""
    assert not state.needs_login("登录后每日签到领额度\n每日签到\n立即签到")
    assert not state.needs_login("每日签到\n签到领额度")


def test_plain_page_needs_no_login():
    assert not state.needs_login("每日签到\n立即签到\n累计 0 点")
