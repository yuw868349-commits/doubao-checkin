"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import __version__, config, report
from .credentials import load_credential
from .report import CheckinResult, append_log, write_result
from .runner import login_and_export, run_checkin

EXIT_CODES = {
    "success": 0,
    "already": 0,
    "AUTH_ERROR": 2,
    "CLIENT_ONLY": 3,
    "NETWORK": 4,
    "TIMEOUT": 5,
    "BROWSER": 6,
    "NO_CREDENTIAL": 7,
    "unknown": 8,
    "ERROR": 9,
}


def _emit(result: CheckinResult, json_output: bool, quiet: bool = False) -> int:
    if quiet:
        append_log(f"{result.result}: {result.report}" + (f"；{result.detail}" if result.detail else ""))
    elif json_output:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"[{result.result}] {result.report}")
        if result.detail:
            print(f"  {result.detail}")
        if result.screenshot:
            print(f"  截图：{result.screenshot}")
        if result.session_days_left:
            print(f"  会话剩余：{result.session_days_left} 天")
    return EXIT_CODES.get(result.result, 9)


def _doctor(as_json: bool) -> int:
    config.ensure_dirs()
    info = {
        "version": __version__,
        "python": sys.version.split()[0],
        "data_dir": str(config.DATA_DIR),
        "activity_url": config.ACTIVITY_URL,
        "client_ua": config.CLIENT_UA,
    }
    problems = []
    try:
        credential = load_credential()
        info["credential_mode"] = credential.mode
        info["cookies"] = len(credential.cookies)
    except RuntimeError as exc:
        info["credential_mode"] = None
        problems.append(str(exc))

    try:
        import playwright  # noqa: F401

        info["playwright"] = "ok"
    except ImportError:
        info["playwright"] = "missing"
        problems.append("没有安装 playwright")

    info["chromium"] = None
    if info["playwright"] == "ok":
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                executable = pathlib.Path(playwright.chromium.executable_path)
            info["chromium"] = str(executable)
            if not executable.exists():
                info["chromium"] = None
                problems.append("Chromium 没装好：先运行 `playwright install chromium`")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"Chromium 不可用：{exc}")

    info["problems"] = problems
    if as_json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        for key, value in info.items():
            print(f"{key:16} {value}")
        print("\n一切就绪。" if not problems else "\n需要处理上面的问题。")
    return 0 if not problems else 1


def _run(args: argparse.Namespace) -> int:
    if args.command == "doctor":
        return _doctor(args.json)

    if args.command == "login":
        try:
            result = login_and_export()
        except Exception as exc:  # noqa: BLE001
            result = CheckinResult("BROWSER", detail=f"{type(exc).__name__}: {exc}")
        write_result(result)
        return _emit(result, args.json)

    if args.command == "silent" and report.already_signed_from_last_result():
        result = CheckinResult("already", detail="今日已有成功记录，跳过")
        write_result(result)
        return _emit(result, args.json, quiet=True)

    if args.command == "silent-poll":
        try:
            credential = load_credential()
        except RuntimeError as exc:
            result = CheckinResult("NO_CREDENTIAL", detail=str(exc))
            write_result(result)
            return _emit(result, args.json, quiet=True)
        result = _poll(args, credential)
        write_result(result)
        return _emit(result, args.json, quiet=True)

    # run 自己会处理没有凭据的情况，这里的 session_days_left 也由它填
    result = run_checkin(
        headed=args.headed,
        ignore_https_errors=args.insecure,
        timeout=args.timeout,
        budget_seconds=args.budget,
        url=args.url,
    )
    write_result(result)
    return _emit(result, args.json, quiet=args.command == "silent")


def _poll(args: argparse.Namespace, credential) -> CheckinResult:
    import time

    deadline = time.monotonic() + args.poll_window
    last = None
    while True:
        last = run_checkin(
            headed=args.headed,
            ignore_https_errors=args.insecure,
            timeout=args.timeout,
            budget_seconds=args.budget,
            credential=credential,
            url=args.url,
        )
        if last.result in ("success", "already"):
            return last
        if last.result not in ("NETWORK", "TIMEOUT", "unknown"):
            return last
        if time.monotonic() >= deadline:
            return last
        append_log(f"轮询：{last.result}，{args.poll_interval} 秒后再试")
        time.sleep(args.poll_interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doubao-checkin", description="豆包每日签到")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--url", default=config.ACTIVITY_URL, help="活动页地址")
        p.add_argument("--headed", action="store_true", help="显示浏览器窗口")
        p.add_argument("--insecure", action="store_true", help="忽略 HTTPS 证书错误")
        p.add_argument("--timeout", type=int, default=15, help="单步超时秒数，默认 15")
        p.add_argument("--budget", type=int, default=config.BUDGET_SECONDS, help="整次运行预算秒数")
        p.add_argument("--json", action="store_true", help="输出 JSON")

    for name, help_text in (
        ("run", "签到一次"),
        ("silent", "静默签到，今天成功过就跳过"),
        ("silent-poll", "失败时在窗口内轮询"),
    ):
        p = sub.add_parser(name, help=help_text)
        common(p)
        if name == "silent-poll":
            p.add_argument("--poll-window", type=int, default=1800, help="轮询总时长秒数")
            p.add_argument("--poll-interval", type=int, default=120, help="两次尝试间隔秒数")

    p = sub.add_parser("doctor", help="检查环境与凭据")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("login", help="打开浏览器扫码，导出 Cookie")
    p.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        append_log(f"未捕获异常：{type(exc).__name__}: {exc}")
        result = CheckinResult("ERROR", detail=f"{type(exc).__name__}: {exc}")
        write_result(result)
        return _emit(result, getattr(args, "json", False))
