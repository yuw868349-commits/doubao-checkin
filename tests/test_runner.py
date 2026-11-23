import pytest

from doubao_checkin import runner


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.config, "LOG_FILE", tmp_path / "checkin.log")
    monkeypatch.setattr(runner.config, "SHOTS_DIR", tmp_path / "shots")
    monkeypatch.setattr(runner.config, "ensure_dirs", lambda: None)


def test_budget_expires():
    budget = runner.Budget(0)
    with pytest.raises(TimeoutError):
        budget.check()
    assert budget.left() == 0


def test_budget_counts_down():
    budget = runner.Budget(60)
    assert 0 < budget.left() <= 60
    budget.check()


def test_playwright_cookies_keep_known_fields():
    cleaned = runner._playwright_cookies(
        [
            {
                "name": "sessionid",
                "value": "v",
                "domain": ".doubao.com",
                "expires": -1,
                "sameSite": "Lax",
                "junk": "drop me",
            }
        ]
    )
    assert cleaned == [
        {
            "name": "sessionid",
            "value": "v",
            "domain": ".doubao.com",
            "expires": -1,
            "sameSite": "Lax",
            "path": "/",
        }
    ]


def test_playwright_cookies_drop_entries_without_domain():
    assert runner._playwright_cookies([{"name": "sessionid", "value": "v"}]) == []


def test_screenshot_without_page_is_none():
    assert runner._screenshot(None) is None


def test_run_checkin_reports_missing_credential(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)

    def boom():
        raise RuntimeError("没有找到登录凭据")

    monkeypatch.setattr(runner, "load_credential", boom)

    result = runner.run_checkin()
    assert result.result == "NO_CREDENTIAL"
    assert "没有找到登录凭据" in (result.detail or "")
    assert (tmp_path / "checkin.log").exists()


def test_prune_screenshots_keeps_newest(tmp_path, monkeypatch):
    import os

    monkeypatch.setattr(runner.config, "SHOTS_DIR", tmp_path)
    for i in range(40):
        path = tmp_path / f"{i:02d}.png"
        path.write_bytes(b"x")
        os.utime(path, (i, i))

    runner._prune_screenshots(keep=10)
    left = sorted(path.name for path in tmp_path.glob("*.png"))
    assert len(left) == 10
    assert left == [f"{i:02d}.png" for i in range(30, 40)]


def test_prune_screenshots_tolerates_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.config, "SHOTS_DIR", tmp_path / "missing")
    runner._prune_screenshots()


def test_navigation_error_is_runtime_error():
    assert issubclass(runner.NavigationError, RuntimeError)
