from doubao_checkin import state


def test_signed_when_button_missing():
    assert state.is_signed("每日签到\n累计 200 点")


def test_not_signed_when_button_present():
    assert not state.is_signed("每日签到\n立即签到")


def test_clicked_and_signed_is_success():
    assert state.classify("每日签到\n签到成功", clicked=True) == "success"


def test_signed_without_click_is_already():
    assert state.classify("每日签到\n累计 200 点", clicked=False) == "already"


def test_unknown_state():
    assert state.classify("活动页升级中", clicked=False) == "unknown"


def test_client_only_banner():
    assert state.is_client_only("仅支持在豆包电脑版签到")
    assert state.needs_login("扫码登录\n登录后同步你的对话")


def test_plain_page_needs_no_login():
    assert not state.needs_login("每日签到\n立即签到\n累计 0 点")
