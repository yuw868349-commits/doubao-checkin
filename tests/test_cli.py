import json

import pytest

from doubao_checkin import cli, config, report
from doubao_checkin.report import EXIT_CODES, REPORTS, CheckinResult


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "SHOTS_DIR", tmp_path / "shots")
    monkeypatch.setattr(config, "LOG_FILE", tmp_path / "checkin.log")
    monkeypatch.setattr(config, "RESULT_FILE", tmp_path / "last-result.json")
    monkeypatch.setattr(config, "COOKIE_FILE", tmp_path / "cookies.json")
    monkeypatch.setattr(config, "PROFILE_DIR", tmp_path / "profile")


def test_run_defaults():
    args = cli.build_parser().parse_args(["run"])
    assert args.url == config.ACTIVITY_URL
    assert args.headed is False
    assert args.budget == config.BUDGET_SECONDS
    assert args.block_resources is False
    assert args.notify_webhook is None


def test_url_and_flags_are_accepted():
    args = cli.build_parser().parse_args(
        ["run", "--url", "https://example.com", "--headed", "--json", "--block-resources"]
    )
    assert args.url == "https://example.com"
    assert args.headed is True
    assert args.json is True
    assert args.block_resources is True


def test_silent_poll_defaults():
    args = cli.build_parser().parse_args(["silent-poll"])
    assert args.poll_window == 1800
    assert args.poll_interval == 120


def test_every_result_has_an_exit_code():
    assert set(REPORTS) <= set(EXIT_CODES)
    assert set(REPORTS) == set(EXIT_CODES)


def test_login_result_does_not_block_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli, "login_and_export", lambda: CheckinResult("success", detail="已保存 Cookie")
    )
    assert cli.main(["login"]) == 0
    assert not report.already_signed_from_last_result()

    payload = json.loads((tmp_path / "last-result.json").read_text(encoding="utf-8"))
    assert payload["command"] == "login"
    assert payload["result"] == "success"


def test_silent_skips_after_checkin_success(tmp_path, monkeypatch):
    report.write_result(CheckinResult("success", command="run"))

    def boom(**kwargs):
        raise AssertionError("不该再跑浏览器")

    monkeypatch.setattr(cli, "run_checkin", boom)
    assert cli.main(["silent"]) == 0
    payload = json.loads((tmp_path / "last-result.json").read_text(encoding="utf-8"))
    assert payload["result"] == "already"
    assert payload["detail"] == "今日已有成功记录，跳过"


def test_silent_poll_skips_after_checkin_success(tmp_path, monkeypatch):
    report.write_result(CheckinResult("success", command="run"))

    def boom(**kwargs):
        raise AssertionError("不该再跑浏览器")

    monkeypatch.setattr(cli, "run_checkin", boom)
    monkeypatch.setattr(
        cli, "load_credential", lambda: (_ for _ in ()).throw(AssertionError("不该加载凭据"))
    )
    assert cli.main(["silent-poll"]) == 0


def test_failed_result_has_its_own_exit_code():
    assert EXIT_CODES["FAILED"] == 10
    assert CheckinResult("FAILED").needs_attention


def test_doctor_reports_unwritable_dir(monkeypatch, capsys):
    monkeypatch.setattr(config, "writable", lambda path: (False, "permission denied"))
    code = cli._doctor(as_json=False)
    out = capsys.readouterr().out
    assert code == 1
    assert "不可写" in out
    assert "permission denied" in out


def test_insecure_prints_warning(monkeypatch, capsys):
    monkeypatch.setattr(config, "writable", lambda path: (True, None))
    monkeypatch.setattr(cli, "run_checkin", lambda **kwargs: CheckinResult("success"))
    assert cli.main(["run", "--insecure"]) == 0
    err = capsys.readouterr().err
    assert "证书校验" in err


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


# ---------------------------------------------------------------- 轮询与输出


def test_poll_retries_then_succeeds(monkeypatch):
    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs)
        return CheckinResult("NETWORK" if len(calls) == 1 else "success")

    monkeypatch.setattr(cli, "run_checkin", fake_run)
    args = cli.build_parser().parse_args(
        ["silent-poll", "--poll-interval", "0", "--poll-window", "5"]
    )
    result = cli._poll(args, object())
    assert result.result == "success"
    assert result.command == "silent-poll"
    assert len(calls) == 2


def test_poll_stops_on_auth_error(monkeypatch):
    monkeypatch.setattr(cli, "run_checkin", lambda **kwargs: CheckinResult("AUTH_ERROR"))
    args = cli.build_parser().parse_args(["silent-poll"])
    result = cli._poll(args, object())
    assert result.result == "AUTH_ERROR"


def test_poll_respects_deadline(monkeypatch):
    calls = []

    def fake_run(**kwargs):
        calls.append(1)
        return CheckinResult("NETWORK")

    monkeypatch.setattr(cli, "run_checkin", fake_run)
    args = cli.build_parser().parse_args(
        ["silent-poll", "--poll-window", "0", "--poll-interval", "0"]
    )
    result = cli._poll(args, object())
    assert result.result == "NETWORK"
    assert len(calls) == 1


def test_emit_json(capsys):
    code = cli._emit(CheckinResult("success"), json_output=True)
    out = capsys.readouterr().out
    assert code == 0
    assert '"result": "success"' in out


def test_emit_human(capsys):
    result = CheckinResult("TIMEOUT", detail="超时", session_days_left=3.5, duration_ms=1200)
    code = cli._emit(result, json_output=False)
    out = capsys.readouterr().out
    assert code == 5
    assert "超时" in out
    assert "3.5 天" in out
    assert "1200 ms" in out


def test_doctor_json(monkeypatch, capsys):
    monkeypatch.setattr(config, "writable", lambda path: (True, None))
    code = cli._doctor(as_json=True)
    payload = json.loads(capsys.readouterr().out)
    assert code in (0, 1)
    assert payload["data_writable"] is True
    assert payload["shots_writable"] is True
