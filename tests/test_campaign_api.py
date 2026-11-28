import datetime as dt

from doubao_checkin import campaign, config
from doubao_checkin.api import ApiResponse, AuthError
from doubao_checkin.credentials import Credential


def _api(monkeypatch, responder):
    instance = campaign.CampaignApi(Credential([{"name": "sessionid", "value": "x"}], mode="env"))
    monkeypatch.setattr(instance.client, "post", responder)
    return instance


def _ok(data):
    return ApiResponse(code=0, msg="", data=data, raw={"code": 0, "data": data})


def test_detail_reads_total_and_tasks(monkeypatch):
    payload = {
        "progress": {"total_amount": 1800},
        "tasks": [{"campaign_code": "a", "title": "A", "task_status": 2, "task_id": "9"}],
        "milestones": [{"title": "500"}],
    }
    instance = _api(monkeypatch, lambda path, body=None, referer=None: _ok(payload))
    result = instance.detail()
    assert result.total_amount == 1800
    assert result.tasks[0].claimable is True
    assert result.milestones == [{"title": "500"}]


def test_detail_surfaces_auth_error(monkeypatch):
    instance = _api(
        monkeypatch,
        lambda path, body=None, referer=None: ApiResponse(10001, "登录失效", None, {}),
    )
    try:
        instance.detail()
    except AuthError as exc:
        assert "登录态失效" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应该抛 AuthError")


def _today_record():
    now = dt.datetime.now(config.CN_TZ)
    return {
        "task_code": "daily_signin",
        "create_time_ms": int(now.timestamp() * 1000),
        "amount": 200,
    }


def test_signin_is_already_when_record_exists(monkeypatch):
    instance = _api(
        monkeypatch, lambda path, body=None, referer=None: _ok({"records": [_today_record()]})
    )
    outcome = instance.signin()
    assert outcome.result == "already"
    assert outcome.amount == 200


def test_signin_success_when_api_confirms(monkeypatch):
    calls = []

    def responder(path, body=None, referer=None):
        calls.append(path)
        if path == config.RECORDS_PATH:
            return _ok({"records": []})
        if path == config.CHECKIN_PATH:
            return _ok({"checked_in": True, "reward_amount": 200})
        return _ok({})

    instance = _api(monkeypatch, responder)
    outcome = instance.signin()
    assert outcome.result == "success"
    assert outcome.amount == 200
    assert config.REPORT_PATH in calls
    assert config.CHECKIN_PATH in calls


def test_signin_closed_maps_710010702(monkeypatch):
    def responder(path, body=None, referer=None):
        if path == config.RECORDS_PATH:
            return _ok({"records": []})
        return ApiResponse(710010702, "系统错误", None, {})

    instance = _api(monkeypatch, responder)
    outcome = instance.signin()
    assert outcome.result == "SIGNIN_CLOSED"


def test_signin_failed_on_other_codes(monkeypatch):
    def responder(path, body=None, referer=None):
        if path == config.RECORDS_PATH:
            return _ok({"records": []})
        return ApiResponse(710010202, "invalid param", None, {})

    instance = _api(monkeypatch, responder)
    assert instance.signin().result == "FAILED"


def test_claim_requires_task_id():
    instance = campaign.CampaignApi(Credential([{"name": "sessionid", "value": "x"}], mode="env"))
    task = campaign.Task(code="a", title="A", status=2)
    assert isinstance(instance.claim(task), str)


def test_claim_sends_string_task_id(monkeypatch):
    seen = {}

    def responder(path, body=None, referer=None):
        seen["path"] = path
        seen["body"] = body
        return _ok({})

    instance = _api(monkeypatch, responder)
    task = campaign.Task(code="a", title="A", status=2, task_id="123")
    instance.claim(task)
    assert seen["path"] == config.CLAIM_PATH
    assert seen["body"] == {"task_id": "123"}


def test_claim_all_counts(monkeypatch):
    calls = []

    def responder(path, body=None, referer=None):
        if path == config.DETAIL_PATH:
            return _ok(
                {
                    "tasks": [
                        {"campaign_code": "a", "title": "A", "task_status": 2, "task_id": "1"},
                        {"campaign_code": "b", "title": "B", "task_status": 2, "task_id": "2"},
                        {"campaign_code": "c", "title": "C", "task_status": 1},
                    ]
                }
            )
        calls.append(body["task_id"])
        if body["task_id"] == "1":
            return _ok({})
        return ApiResponse(0, "", {"biz_error": {"code": 2, "message": "不符合条件"}}, {})

    instance = _api(monkeypatch, responder)
    success, failed, reasons = instance.claim_all()
    assert success == 1
    assert failed == 1
    assert calls == ["1", "2"]
    assert "不符合条件" in reasons[0]


def test_claim_all_reports_auth_failure(monkeypatch):
    def responder(path, body=None, referer=None):
        if path == config.DETAIL_PATH:
            return _ok(
                {"tasks": [{"campaign_code": "a", "title": "A", "task_status": 2, "task_id": "1"}]}
            )
        raise AuthError("登录态失效")

    instance = _api(monkeypatch, responder)
    success, failed, reasons = instance.claim_all()
    assert success == 0
    assert failed == 1
    assert "登录" in reasons[0]
