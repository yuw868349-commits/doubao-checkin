"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request

from . import __version__, config, report
from .credentials import load_credential
from .report import EXIT_CODES, CheckinResult, Result, append_log, write_result
from .runner import login_and_export, run_checkin


def _emit(result: CheckinResult, json_output: bool, quiet: bool = False) -> int:
    if quiet:
        append_log(
            f"{result.result}: {result.report}" + (f"；{result.detail}" if result.detail else "")
        )
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
        if result.duration_ms is not None:
            print(f"  耗时：{result.duration_ms} ms")
    return EXIT_CODES.get(result.result, 9)


def _notify(result: CheckinResult, url: str | None) -> None:
    """只在需要关注的结果上发通知，成功不打扰。"""
    if not url or not result.needs_attention:
        return
    if not url.lower().startswith(("http://", "https://")):
        append_log("通知地址只支持 http/https，已跳过")
        return
    payload = json.dumps(
        {"text": f"[{result.result}] {result.report}", "result": result.to_dict()},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.NOTIFY_TIMEOUT):  # noqa: S310
            pass
        append_log(f"已发送通知到 {url}")
    except Exception as exc:  # noqa: BLE001
        append_log(f"通知发送失败：{type(exc).__name__}: {exc}")


def _doctor(as_json: bool) -> int:
    info: dict[str, object] = {
        "version": __version__,
        "python": sys.version.split()[0],
        "data_dir": str(config.DATA_DIR),
        "activity_url": config.ACTIVITY_URL,
        "client_ua": config.CLIENT_UA,
    }
    problems: list[str] = []

    for label, path in (("data_writable", config.DATA_DIR), ("shots_writable", config.SHOTS_DIR)):
        ok, error = config.writable(path)
        info[label] = ok
        if not ok:
            problems.append(f"{path} 不可写：{error}")

    try:
        credential = load_credential()
        info["credential_mode"] = credential.mode
        info["cookies"] = len(credential.cookies)
    except RuntimeError as exc:
        info["credential_mode"] = None
        info["cookies"] = 0
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

            with sync_playwright() as pw:
                executable = pathlib.Path(pw.chromium.executable_path)
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
            if key == "problems":
                continue
            print(f"{key:16} {value}")
        if problems:
            print("\n需要处理：")
            for item in problems:
                print(f"  - {item}")
        else:
            print("\n一切就绪。")
    return 0 if not problems else 1


def _warn_insecure() -> None:
    print("警告：--insecure 已关闭 HTTPS 证书校验，Cookie 可能被中间人读取。", file=sys.stderr)
    append_log("警告：本次运行关闭了 HTTPS 证书校验")


def _run(args: argparse.Namespace) -> int:
    if args.command == "doctor":
        return _doctor(args.json)

    if args.command == "login":
        try:
            result = login_and_export()
        except Exception as exc:  # noqa: BLE001
            result = CheckinResult(
                Result.BROWSER.value, command="login", detail=f"{type(exc).__name__}: {exc}"
            )
        result.command = "login"
        write_result(result)
        code = _emit(result, args.json)
        _notify(result, getattr(args, "notify_webhook", None))
        return code

    if getattr(args, "insecure", False):
        _warn_insecure()

    # silent 和 silent-poll 都要幂等，当天成功过就直接跳过
    if args.command in ("silent", "silent-poll") and report.already_signed_from_last_result():
        result = CheckinResult(
            Result.ALREADY.value, command=args.command, detail="今日已有成功记录，跳过"
        )
        write_result(result)
        return _emit(result, args.json, quiet=True)

    if args.command == "silent-poll":
        try:
            credential = load_credential()
        except RuntimeError as exc:
            result = CheckinResult(
                Result.NO_CREDENTIAL.value, command=args.command, detail=str(exc)
            )
            write_result(result)
            return _emit(result, args.json, quiet=True)
        result = _poll(args, credential)
        write_result(result)
        code = _emit(result, args.json, quiet=True)
        _notify(result, args.notify_webhook)
        return code

    # run 自己会处理没有凭据的情况，这里的 session_days_left 也由它填
    result = run_checkin(
        headed=args.headed,
        ignore_https_errors=args.insecure,
        timeout=args.timeout,
        budget_seconds=args.budget,
        url=args.url,
        block_resources=args.block_resources,
    )
    result.command = args.command
    write_result(result)
    code = _emit(result, args.json, quiet=args.command == "silent")
    _notify(result, args.notify_webhook)
    return code


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
            block_resources=args.block_resources,
        )
        last.command = args.command
        if last.result in (Result.SUCCESS.value, Result.ALREADY.value):
            return last
        if last.result not in (Result.NETWORK.value, Result.TIMEOUT.value, Result.UNKNOWN.value):
            return last
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return last
        append_log(f"轮询：{last.result}，{args.poll_interval} 秒后再试")
        time.sleep(min(args.poll_interval, remaining))


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
        p.add_argument(
            "--block-resources",
            action="store_true",
            help="拦截图片、字体、媒体，加快页面加载",
        )
        p.add_argument("--notify-webhook", default=None, help="失败时 POST 结果到这个地址")

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
        result = CheckinResult(Result.ERROR.value, detail=f"{type(exc).__name__}: {exc}")
        write_result(result)
        return _emit(result, getattr(args, "json", False))
