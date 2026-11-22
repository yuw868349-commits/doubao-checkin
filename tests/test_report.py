import datetime as dt
import json

from doubao_checkin import report
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
    assert payload["mode"] == "env"
    assert payload["needs_attention"] is False
    assert payload["saved_at"]
    assert not list(tmp_path.glob("*.tmp"))


def test_already_signed_today(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report.write_result(CheckinResult("success"))
    assert report.already_signed_from_last_result()


def test_already_signed_ignores_old_record(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    payload = {"result": "success", "saved_at": "2020-01-01 08:00:00"}
    (tmp_path / "last-result.json").write_text(json.dumps(payload), encoding="utf-8")
    assert not report.already_signed_from_last_result()


def test_already_signed_ignores_failure(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    payload = {
        "result": "AUTH_ERROR",
        "saved_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    (tmp_path / "last-result.json").write_text(json.dumps(payload), encoding="utf-8")
    assert not report.already_signed_from_last_result()


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


def test_reports_and_exit_codes_cover_every_result():
    from doubao_checkin.cli import EXIT_CODES

    assert set(report.REPORTS) == set(EXIT_CODES)
    assert set(report.NEEDS_ATTENTION) <= set(report.REPORTS)
    assert "success" not in report.NEEDS_ATTENTION
    assert "already" not in report.NEEDS_ATTENTION
