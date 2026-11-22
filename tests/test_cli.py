from doubao_checkin import config
from doubao_checkin.cli import EXIT_CODES, build_parser
from doubao_checkin.report import REPORTS


def test_run_defaults():
    args = build_parser().parse_args(["run"])
    assert args.url == config.ACTIVITY_URL
    assert args.headed is False
    assert args.budget == config.BUDGET_SECONDS


def test_url_and_flags_are_accepted():
    args = build_parser().parse_args(["run", "--url", "https://example.com", "--headed", "--json"])
    assert args.url == "https://example.com"
    assert args.headed is True
    assert args.json is True


def test_silent_poll_defaults():
    args = build_parser().parse_args(["silent-poll"])
    assert args.poll_window == 1800
    assert args.poll_interval == 120


def test_every_result_has_an_exit_code():
    assert set(REPORTS) <= set(EXIT_CODES)
