import json

import pytest

from doubao_checkin import cli, config, report
from doubao_checkin.report import EXIT_CODES, REPORTS, CheckinResult


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "LOG_FILE", tmp_path / "checkin.log")
    monkeypatch.setattr(config, "RESULT_FILE", tmp_path / "last-result.json")
    monkeypatch.setattr(config, "COOKIE_FILE", tmp_path / "cookies.json")


def test_run_defaults():
    args = cli.build_parser().parse_args(["run"])
    assert args.json is False
    assert args.notify_webhook is None


def test_silent_poll_defaults():
    args = cli.build_parser().parse_args(["silent-poll"])
    assert args.poll_window == config.Timing.POLL_WINDOW
    assert args.poll_interval == config.Timing.POLL_INTERVAL


def test_every_result_has_an_exit_code():
    assert set(REPORTS) <= set(EXIT_CODES)
    assert set(REPORTS) == set(EXIT_CODES)


def test_silent_skips_after_checkin_success(tmp_path, monkeypatch):
    report.write_result(CheckinResult("success", command="run"))

    def boom():
        raise AssertionError("不该再请求接口")

    monkeypatch.setattr(cli, "_checkin_once", boom)
    assert cli.main(["silent"]) == 0
    payload = json.loads((tmp_path / "last-result.json").read_text(encoding="utf-8"))
    assert payload["result"] == "already"
    assert payload["detail"] == "今日已有成功记录，跳过"


def test_silent_poll_skips_after_checkin_success(tmp_path, monkeypatch):
    report.write_result(CheckinResult("success", command="run"))

    def boom():
        raise AssertionError("不该再请求接口")

    monkeypatch.setattr(cli, "_checkin_once", boom)
    assert cli.main(["silent-poll"]) == 0


def test_failed_result_has_its_own_exit_code():
    assert EXIT_CODES["FAILED"] == 10
    assert CheckinResult("FAILED").needs_attention


def test_signin_closed_is_a_result():
    assert EXIT_CODES["SIGNIN_CLOSED"] == 11
    assert CheckinResult("SIGNIN_CLOSED").needs_attention


def test_doctor_reports_unwritable_dir(monkeypatch, capsys):
    monkeypatch.setattr(config, "writable", lambda path: (False, "permission denied"))
    code = cli._doctor(as_json=False, online=False)
    out = capsys.readouterr().out
    assert code == 1
    assert "不可写" in out
    assert "permission denied" in out


def test_notify_only_when_attention(monkeypatch):
    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.data))
        return FakeResponse()

    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_urlopen)

    cli._notify(CheckinResult("success"), "https://example.com/hook")
    assert calls == []

    cli._notify(CheckinResult("AUTH_ERROR", detail="过期"), "https://example.com/hook")
    assert len(calls) == 1
    body = json.loads(calls[0][1])
    assert body["result"]["result"] == "AUTH_ERROR"
    assert body["result"]["needs_attention"] is True


def test_notify_failure_is_swallowed(monkeypatch):
    def boom(request, timeout):
        raise OSError("network down")

    monkeypatch.setattr(cli.urllib.request, "urlopen", boom)
    cli._notify(CheckinResult("NETWORK"), "https://example.com/hook")


def test_poll_retries_then_succeeds(monkeypatch):
    calls = []

    def fake_checkin(command):
        calls.append(command)
        return CheckinResult("NETWORK" if len(calls) == 1 else "success", command=command)

    monkeypatch.setattr(cli, "_checkin_once", fake_checkin)
    args = cli.build_parser().parse_args(
        ["silent-poll", "--poll-interval", "0", "--poll-window", "5"]
    )
    result = cli._poll(args)
    assert result.result == "success"
    assert result.command == "silent-poll"
    assert len(calls) == 2


def test_poll_stops_on_auth_error(monkeypatch):
    monkeypatch.setattr(cli, "_checkin_once", lambda command: CheckinResult("AUTH_ERROR"))
    args = cli.build_parser().parse_args(["silent-poll"])
    result = cli._poll(args)
    assert result.result == "AUTH_ERROR"


def test_poll_respects_deadline(monkeypatch):
    calls = []

    def fake_checkin(command):
        calls.append(command)
        return CheckinResult("NETWORK")

    monkeypatch.setattr(cli, "_checkin_once", fake_checkin)
    args = cli.build_parser().parse_args(
        ["silent-poll", "--poll-window", "0", "--poll-interval", "0"]
    )
    result = cli._poll(args)
    assert result.result == "NETWORK"
    assert len(calls) == 1


def test_emit_json(capsys):
    code = cli._emit(CheckinResult("success"), as_json=True)
    out = capsys.readouterr().out
    assert code == 0
    assert '"result": "success"' in out


def test_emit_human(capsys):
    result = CheckinResult("TIMEOUT", detail="超时", session_days_left=3.5, duration_ms=1200)
    code = cli._emit(result, as_json=False)
    out = capsys.readouterr().out
    assert code == 5
    assert "超时" in out
    assert "3.5 天" in out
    assert "1200 ms" in out


def test_doctor_json(monkeypatch, capsys):
    monkeypatch.setattr(config, "writable", lambda path: (True, None))
    code = cli._doctor(as_json=True, online=False)
    payload = json.loads(capsys.readouterr().out)
    assert code in (0, 1)
    assert payload["data_writable"] is True


class _FakeCampaign:
    total_amount = 1800

    def __init__(self):
        self.tasks = [campaign_task()]

    def claim_all(self):
        return 0, 0, []

    def detail(self):
        return self

    def today_signin(self):
        return None

    def records(self):
        return []

    @property
    def milestones(self):
        return []


def campaign_task():
    from doubao_checkin.campaign import Task

    return Task(code="a", title="A", status=1)


def _fake_api():
    class Api:
        credential = type("C", (), {"cookies": []})()

        def detail(self):
            return _FakeCampaign()

        def today_signin(self):
            return None

        def records(self):
            return []

        def claim_all(self):
            return 0, 0, []

        def signin(self):
            raise AssertionError("不该走签到")

    return Api()


def test_main_run_writes_result(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "_checkin_once", lambda command: CheckinResult("success", command=command)
    )
    assert cli.main(["run"]) == 0
    out = capsys.readouterr().out
    assert "签到成功" in out


def test_main_status_prints_tasks(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_open_api", _fake_api)
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "累计额度：1800" in out
    assert "[未完成] A" in out


def test_main_tasks_and_claim(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_open_api", _fake_api)
    assert cli.main(["tasks"]) == 0
    assert cli.main(["claim"]) == 0
    out = capsys.readouterr().out
    assert "领取成功 0 个" in out


def test_main_records(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_open_api", _fake_api)
    assert cli.main(["records"]) == 0
    assert capsys.readouterr().out == ""


def test_main_without_credential(monkeypatch, capsys):
    def boom():
        raise RuntimeError("没有找到登录凭据")

    monkeypatch.setattr(cli, "_open_api", boom)
    assert cli.main(["tasks"]) == 7
    assert "没有可用凭据" in capsys.readouterr().err
