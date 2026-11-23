import datetime as dt
import json
import os
import stat

from doubao_checkin import config, report
from doubao_checkin.report import CheckinResult


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(report.config, "RESULT_FILE", tmp_path / "last-result.json")
    monkeypatch.setattr(report.config, "LOG_FILE", tmp_path / "checkin.log")
    monkeypatch.setattr(report.config, "ensure_dirs", lambda: None)


def test_write_result_is_complete_json(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report.write_result(CheckinResult("success", mode="env"))

    payload = json.loads((tmp_path / "last-result.json").read_text(encoding="utf-8"))
    assert payload["result"] == "success"
    assert payload["command"] == "run"
    assert payload["mode"] == "env"
    assert payload["needs_attention"] is False
    assert payload["saved_at"]
    assert payload["saved_at_utc"].endswith("+00:00")
    assert not list(tmp_path.glob("*.tmp"))


def test_result_file_is_private(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report.write_result(CheckinResult("success"))
    if os.name == "posix":
        mode = stat.S_IMODE((tmp_path / "last-result.json").stat().st_mode)
        assert mode == 0o600


def test_already_signed_today(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report.write_result(CheckinResult("success"))
    assert report.already_signed_from_last_result()


def test_login_record_is_not_a_checkin(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report.write_result(CheckinResult("success", command="login"))
    assert not report.already_signed_from_last_result()


def test_already_signed_ignores_old_record(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    payload = {"result": "success", "command": "run", "saved_at": "2020-01-01 08:00:00"}
    (tmp_path / "last-result.json").write_text(json.dumps(payload), encoding="utf-8")
    assert not report.already_signed_from_last_result()


def test_already_signed_ignores_failure(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    payload = {
        "result": "AUTH_ERROR",
        "command": "run",
        "saved_at": dt.datetime.now(config.CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    }
    (tmp_path / "last-result.json").write_text(json.dumps(payload), encoding="utf-8")
    assert not report.already_signed_from_last_result()


def test_legacy_record_without_command_is_ignored(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    payload = {
        "result": "success",
        "saved_at": dt.datetime.now(config.CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    }
    (tmp_path / "last-result.json").write_text(json.dumps(payload), encoding="utf-8")
    # 旧记录没有 command 字段，宁可多签一次，也不能漏签
    assert not report.already_signed_from_last_result()


def test_utc_stamp_is_judged_in_beijing_time(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    # 北京时间今天 00:30，对应 UTC 昨天 16:30
    now_cn = dt.datetime.now(config.CN_TZ)
    today_early = now_cn.replace(hour=0, minute=30, second=0, microsecond=0)
    payload = {
        "result": "success",
        "command": "run",
        "saved_at": today_early.strftime("%Y-%m-%d %H:%M:%S"),
        "saved_at_utc": today_early.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    (tmp_path / "last-result.json").write_text(json.dumps(payload), encoding="utf-8")
    if now_cn.date() == today_early.date():
        assert report.already_signed_from_last_result()


def test_broken_utc_stamp_falls_back_to_local(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    payload = {
        "result": "success",
        "command": "run",
        "saved_at": dt.datetime.now(config.CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "saved_at_utc": "not-a-date",
    }
    (tmp_path / "last-result.json").write_text(json.dumps(payload), encoding="utf-8")
    assert report.already_signed_from_last_result()


def test_already_signed_tolerates_broken_json(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    (tmp_path / "last-result.json").write_text("{oops", encoding="utf-8")
    assert not report.already_signed_from_last_result()


def test_log_rotation_keeps_tail(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(report, "MAX_LOG_BYTES", 200)
    monkeypatch.setattr(report, "KEEP_LOG_LINES", 3)

    log = tmp_path / "checkin.log"
    log.write_text("".join(f"line {i}\n" for i in range(50)), encoding="utf-8")
    report.append_log("最后一行")

    text = log.read_text(encoding="utf-8")
    assert "最后一行" in text
    assert "line 0\n" not in text
    assert len(text.splitlines()) <= 4


def test_log_file_is_private(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report.append_log("hello")
    if os.name == "posix":
        mode = stat.S_IMODE((tmp_path / "checkin.log").stat().st_mode)
        assert mode == 0o600


def test_reports_and_exit_codes_cover_every_result():
    from doubao_checkin.cli import EXIT_CODES

    assert set(report.REPORTS) == set(EXIT_CODES)
    assert set(report.NEEDS_ATTENTION) <= set(report.REPORTS)
    assert "success" not in report.NEEDS_ATTENTION
    assert "already" not in report.NEEDS_ATTENTION
    assert "FAILED" in report.NEEDS_ATTENTION
    assert EXIT_CODES["FAILED"] == 10


def test_json_log_format(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DOUBAO_LOG_FORMAT", "json")
    report.append_log("签到完成")
    line = (tmp_path / "checkin.log").read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["message"] == "签到完成"
    assert payload["ts"].startswith("20")
