"""命令行入口：签到、任务、领奖全部走 HTTP API。"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.request

from . import __version__, config, report
from .api import ApiError, AuthError
from .campaign import CampaignApi
from .credentials import load_credential, session_days_left
from .report import EXIT_CODES, CheckinResult, Result, append_log, write_result


def _emit(result: CheckinResult, as_json: bool, quiet: bool = False) -> int:
    if quiet:
        append_log(
            f"{result.result}: {result.report}" + (f"；{result.detail}" if result.detail else "")
        )
    elif as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"[{result.result}] {result.report}")
        if result.detail:
            print(f"  {result.detail}")
        if result.session_days_left:
            print(f"  会话剩余：{result.session_days_left:g} 天")
        if result.duration_ms is not None:
            print(f"  耗时：{result.duration_ms} ms")
    return EXIT_CODES.get(result.result, 9)


def _notify(result: CheckinResult, url: str | None) -> None:
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
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=config.NOTIFY_TIMEOUT):  # noqa: S310
            pass
        append_log("已发送通知")
    except Exception as exc:  # noqa: BLE001
        append_log(f"通知发送失败：{type(exc).__name__}: {exc}")


def _open_api() -> CampaignApi:
    return CampaignApi(load_credential())


def _checkin_once(command: str) -> CheckinResult:
    started = time.monotonic()
    try:
        api = _open_api()
    except RuntimeError as exc:
        return CheckinResult(
            Result.NO_CREDENTIAL.value, command=command, mode="api", detail=str(exc)
        )
    days = session_days_left(api.credential.cookies)
    try:
        outcome = api.signin()
    except AuthError as exc:
        return CheckinResult(
            Result.AUTH_ERROR.value,
            command=command,
            mode="api",
            detail=str(exc),
            session_days_left=days,
        )
    except ApiError as exc:
        return CheckinResult(
            Result.NETWORK.value,
            command=command,
            mode="api",
            detail=str(exc),
            session_days_left=days,
        )
    except Exception as exc:  # noqa: BLE001
        return CheckinResult(
            Result.ERROR.value,
            command=command,
            mode="api",
            detail=f"{type(exc).__name__}: {exc}",
            session_days_left=days,
        )

    mapping = {
        "success": Result.SUCCESS.value,
        "already": Result.ALREADY.value,
        "SIGNIN_CLOSED": Result.SIGNIN_CLOSED.value,
        "FAILED": Result.FAILED.value,
        "unknown": Result.UNKNOWN.value,
    }
    detail = outcome.detail
    if outcome.amount:
        detail = f"{detail}（+{outcome.amount} 额度）"
    if outcome.record_time:
        detail = f"{detail}，记录时间 {outcome.record_time:%Y-%m-%d %H:%M}"
    return CheckinResult(
        mapping.get(outcome.result, Result.UNKNOWN.value),
        command=command,
        mode="api",
        detail=detail,
        session_days_left=days,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def _poll(args: argparse.Namespace) -> CheckinResult:
    deadline = time.monotonic() + args.poll_window
    last: CheckinResult | None = None
    while True:
        last = _checkin_once(args.command)
        if last.result in (Result.SUCCESS.value, Result.ALREADY.value):
            return last
        if last.result not in (
            Result.NETWORK.value,
            Result.TIMEOUT.value,
            Result.UNKNOWN.value,
            Result.SIGNIN_CLOSED.value,
        ):
            return last
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return last
        append_log(f"轮询：{last.result}，{args.poll_interval} 秒后再试")
        time.sleep(min(args.poll_interval, remaining))


def _doctor(as_json: bool, online: bool) -> int:
    info: dict[str, object] = {
        "version": __version__,
        "python": sys.version.split()[0],
        "data_dir": str(config.DATA_DIR),
        "campaign_group": config.CAMPAIGN_GROUP,
        "client_ua": config.CLIENT_UA,
        "transport": "http-api",
    }
    problems: list[str] = []

    ok, error = config.writable(config.DATA_DIR)
    info["data_writable"] = ok
    if not ok:
        problems.append(f"{config.DATA_DIR} 不可写：{error}")

    try:
        credential = load_credential()
        info["credential_mode"] = credential.mode
        info["cookies"] = len(credential.cookies)
        info["session_days_left"] = session_days_left(credential.cookies)
        if online:
            campaign = CampaignApi(credential).detail()
            info["api_ok"] = True
            info["tasks"] = len(campaign.tasks)
            info["total_amount"] = campaign.total_amount
        else:
            info["api_ok"] = None
    except (RuntimeError, AuthError, ApiError) as exc:
        info["credential_mode"] = None
        info["api_ok"] = False
        problems.append(str(exc))

    info["problems"] = problems
    if as_json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        for key, value in info.items():
            if key == "problems":
                continue
            print(f"{key:18} {value}")
        if problems:
            print("\n需要处理：")
            for item in problems:
                print(f"  - {item}")
        else:
            print("\n一切就绪。")
    return 0 if not problems else 1


def _print_campaign(api: CampaignApi) -> None:
    campaign = api.detail()
    today = api.today_signin()
    print(f"活动组：{config.CAMPAIGN_GROUP}")
    print(f"累计额度：{campaign.total_amount if campaign.total_amount is not None else '未知'}")
    print(f"今日签到：{'已签' if today else '未签'}")
    print("任务：")
    for task in campaign.tasks:
        mark = "可领取" if task.claimable else "未完成"
        reward = f"+{task.reward}" if task.reward else ""
        print(
            f"  [{mark}] {task.title} {reward} (status={task.status}, task_id={task.task_id or '-'})"
        )
    if campaign.milestones:
        print("里程碑：")
        for milestone in campaign.milestones:
            print(f"  {milestone.get('title')}: {milestone.get('subtitle')}")


def _print_records(api: CampaignApi) -> None:
    for record in api.records()[:15]:
        stamp = record.get("create_time_ms")
        when = ""
        if isinstance(stamp, (int, float)):
            when = dt.datetime.fromtimestamp(stamp / 1000, config.CN_TZ).strftime("%Y-%m-%d %H:%M")
        print(
            f"{when}  {record.get('task_code')}  {record.get('task_name')}  +{record.get('amount')}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doubao-checkin", description="豆包每日签到（API 版）")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--json", action="store_true", help="输出 JSON")
        p.add_argument("--notify-webhook", default=None, help="需要关注的结果 POST 到这个地址")

    for name, help_text in (
        ("run", "签到一次"),
        ("silent", "静默签到，今天成功过就跳过"),
        ("silent-poll", "失败时在窗口内轮询"),
    ):
        p = sub.add_parser(name, help=help_text)
        common(p)
        if name == "silent-poll":
            p.add_argument("--poll-interval", type=int, default=config.Timing.POLL_INTERVAL)
            p.add_argument("--poll-window", type=int, default=config.Timing.POLL_WINDOW)

    tasks = sub.add_parser("tasks", help="查看活动任务")
    common(tasks)
    claim = sub.add_parser("claim", help="领取所有可领任务奖励")
    common(claim)
    records = sub.add_parser("records", help="查看额度流水")
    common(records)
    status = sub.add_parser("status", help="签到状态 + 任务概览")
    common(status)

    doctor = sub.add_parser("doctor", help="自检")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--online", action="store_true", help="额外请求一次活动详情接口")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command

    if command == "doctor":
        return _doctor(args.json, args.online)

    if command in ("tasks", "claim", "records", "status"):
        try:
            api = _open_api()
        except RuntimeError as exc:
            print(f"没有可用凭据：{exc}", file=sys.stderr)
            return EXIT_CODES[Result.NO_CREDENTIAL.value]
        try:
            if command in ("tasks", "status"):
                _print_campaign(api)
            elif command == "records":
                _print_records(api)
            else:
                success, failed, reasons = api.claim_all()
                print(f"领取成功 {success} 个，失败 {failed} 个")
                for reason in reasons:
                    print(f"  - {reason}")
        except AuthError as exc:
            print(f"登录态失效：{exc}", file=sys.stderr)
            return EXIT_CODES[Result.AUTH_ERROR.value]
        except ApiError as exc:
            print(f"接口失败：{exc}", file=sys.stderr)
            return EXIT_CODES[Result.NETWORK.value]
        return 0

    if command in ("silent", "silent-poll") and report.already_signed_from_last_result():
        result = CheckinResult(
            Result.ALREADY.value, command=command, mode="api", detail="今日已有成功记录，跳过"
        )
        write_result(result)
        return _emit(result, args.json, quiet=True)

    if command == "silent-poll":
        result = _poll(args)
        write_result(result)
        code = _emit(result, args.json, quiet=True)
        _notify(result, args.notify_webhook)
        return code

    result = _checkin_once(command)
    write_result(result)
    code = _emit(result, args.json, quiet=command == "silent")
    _notify(result, args.notify_webhook)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
