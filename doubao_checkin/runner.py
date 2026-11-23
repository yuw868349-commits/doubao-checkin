"""浏览器流程：打开活动页、判断状态、点签到、留证据。"""

from __future__ import annotations

import contextlib
import datetime as dt
import time
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from . import config, state
from .credentials import Credential, load_credential, save_cookies, session_days_left
from .report import CheckinResult, Result, append_log


class Budget:
    def __init__(self, seconds: int) -> None:
        self.deadline = time.monotonic() + seconds

    def left(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def check(self) -> None:
        if self.left() <= 0:
            raise TimeoutError("时间预算用完")


class NavigationError(RuntimeError):
    """页面本身打不开，区别于浏览器环境异常。"""


RETRY_DELAYS = (5, 10, 20)
GATEWAY_MARKERS = (
    "502 Bad Gateway",
    "503 Service Unavailable",
    "504 Gateway Time-out",
    "ERR_EMPTY_RESPONSE",
)
COOKIE_FIELDS = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")


def _body_text(page: Page) -> str:
    try:
        return page.inner_text("body", timeout=config.Timing.BODY_TIMEOUT)
    except PlaywrightError:
        return ""


def _goto(page: Page, url: str, budget: Budget) -> None:
    last_error: Exception | None = None
    for index, delay in enumerate((0, *RETRY_DELAYS), start=1):
        budget.check()
        if delay:
            page.wait_for_timeout(int(delay * 1000))
            budget.check()
        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=int(min(config.Timing.GOTO_TIMEOUT, budget.left() * 1000)),
            )
            if response is not None:
                if response.status >= 500:
                    last_error = NavigationError(f"HTTP {response.status}")
                    append_log(f"第 {index} 次打开遇到 HTTP {response.status}，准备重试")
                    continue
                if response.status >= 400:
                    raise NavigationError(f"HTTP {response.status}")
            page.wait_for_timeout(config.Timing.PAGE_SETTLE)
            body = _body_text(page)
            if any(marker in body for marker in GATEWAY_MARKERS):
                last_error = NavigationError("网关错误")
                append_log(f"第 {index} 次打开遇到网关错误，准备重试")
                continue
            return
        except PlaywrightTimeout as exc:
            last_error = exc
            append_log(f"第 {index} 次打开超时")
        except PlaywrightError as exc:
            last_error = exc
            append_log(f"第 {index} 次打开失败：{exc}")
    raise NavigationError(f"页面打不开：{last_error}")


def _wait_for_ready(page: Page, budget: Budget) -> str:
    """等页面进入可判断的状态：卡片出现、登录墙出现，或者超时。"""
    deadline = time.monotonic() + config.Timing.READY_TIMEOUT
    body = ""
    while True:
        body = _body_text(page)
        if state.is_client_only(body):
            return body
        if state.card_state(body) in ("clickable", "signed", "failed"):
            return body
        if state.needs_login(body):
            return body
        if time.monotonic() >= deadline or budget.left() <= 0:
            return body
        page.wait_for_timeout(config.Timing.READY_POLL)


def _click_one(page: Page, texts: tuple[str, ...]) -> str | None:
    for text in texts:
        locator = page.get_by_text(text, exact=True)
        try:
            count = locator.count()
        except PlaywrightError:
            continue
        for index in range(min(count, 3)):
            item = locator.nth(index)
            try:
                if item.is_visible():
                    item.click(timeout=config.Timing.CLICK_TIMEOUT)
                    page.wait_for_timeout(config.Timing.CLICK_SETTLE)
                    return text
            except (PlaywrightError, PlaywrightTimeout):
                continue
    return None


def _click_entry_then_button(page: Page, budget: Budget) -> str | None:
    """活动入口有时藏在侧栏，先进去再找按钮。"""
    _click_one(page, state.ENTRY_TEXTS)
    page.wait_for_timeout(config.Timing.ENTRY_SETTLE)
    for _ in range(config.Timing.BUTTON_RETRY):
        budget.check()
        clicked = _click_one(page, state.CLICK_TEXTS)
        if clicked:
            return clicked
        page.wait_for_timeout(config.Timing.BUTTON_RETRY_WAIT)
    return None


def _prune_screenshots(keep: int | None = None) -> None:
    keep = config.Timing.SCREENSHOT_KEEP if keep is None else keep
    try:
        shots = sorted(
            config.SHOTS_DIR.glob("*.png"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for path in shots[keep:]:
        with contextlib.suppress(OSError):
            path.unlink()


def _screenshot(page: Page | None, prefix: str = "result") -> str | None:
    if page is None:
        return None
    config.ensure_dirs()
    stamp = dt.datetime.now(config.CN_TZ).strftime("%Y%m%d-%H%M%S-%f")
    name = f"{prefix}-{stamp}.png"
    path = config.SHOTS_DIR / name
    try:
        page.screenshot(path=str(path), full_page=True)
    except PlaywrightError:
        try:
            page.screenshot(path=str(path))
        except Exception:  # noqa: BLE001
            return None
    _prune_screenshots()
    try:
        return str(path.relative_to(config.REPO_ROOT))
    except ValueError:
        return str(path)


def _playwright_cookies(cookies: list[dict]) -> list[dict]:
    cleaned = []
    for cookie in cookies:
        item = {key: cookie[key] for key in COOKIE_FIELDS if key in cookie}
        if not item.get("domain"):
            continue
        item.setdefault("path", "/")
        cleaned.append(item)
    return cleaned


def _context_kwargs(ignore_https_errors: bool) -> dict:
    return {
        "user_agent": config.CLIENT_UA,
        "viewport": config.VIEWPORT,
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "ignore_https_errors": ignore_https_errors,
    }


def _install_blocking(context: BrowserContext) -> None:
    def handler(route: Any) -> None:
        if route.request.resource_type in config.BLOCKED_RESOURCE_TYPES:
            route.abort()
        else:
            route.continue_()

    context.route("**/*", handler)


def _open_session(
    playwright: Playwright,
    credential: Credential,
    headed: bool,
    ignore_https_errors: bool,
    block_resources: bool,
) -> tuple[Browser | None, BrowserContext]:
    if credential.mode == "profile" and credential.profile_dir:
        credential.profile_dir.mkdir(parents=True, exist_ok=True)
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(credential.profile_dir),
            headless=not headed,
            **_context_kwargs(ignore_https_errors),
        )
        if block_resources:
            _install_blocking(context)
        return None, context

    browser = _launch(playwright, headed)
    context = browser.new_context(**_context_kwargs(ignore_https_errors))
    cookies = _playwright_cookies(credential.cookies)
    if cookies:
        context.add_cookies(cookies)  # type: ignore[arg-type]
    if block_resources:
        _install_blocking(context)
    return browser, context


def _finish(result: CheckinResult, started: float, detail: str | None = None) -> CheckinResult:
    if detail:
        result.detail = detail
    result.duration_ms = int((time.monotonic() - started) * 1000)
    append_log(f"结果：{result.result}（{result.report}）" + (f"；{detail}" if detail else ""))
    return result


def _run_once(
    credential: Credential,
    headed: bool,
    ignore_https_errors: bool,
    timeout: int | None,
    budget: Budget,
    url: str | None,
    block_resources: bool,
    started: float,
) -> CheckinResult:
    append_log(f"启动，凭据来源 {credential.mode}，Cookie {len(credential.cookies)} 条")
    days = session_days_left(credential.cookies) if credential.cookies else None
    if days is not None:
        append_log(f"会话剩余约 {days} 天")

    result = CheckinResult(Result.ERROR.value, mode=credential.mode, session_days_left=days)
    try:
        with sync_playwright() as playwright:
            browser, context = _open_session(
                playwright, credential, headed, ignore_https_errors, block_resources
            )
            page: Page | None = None
            try:
                if timeout:
                    context.set_default_timeout(timeout * 1000)
                page = context.new_page()

                _goto(page, url or config.ACTIVITY_URL, budget)
                body = _wait_for_ready(page, budget)

                # 客户端专用提示要排在登录判断前面：那段文案里通常也带「登录」两个字
                if state.is_client_only(body):
                    result.result = Result.CLIENT_ONLY.value
                    result.screenshot = _screenshot(page, "client-only")
                    return _finish(result, started, "页面提示仅支持电脑版客户端")

                if state.needs_login(body):
                    result.result = Result.AUTH_ERROR.value
                    result.screenshot = _screenshot(page, "auth")
                    return _finish(result, started, "活动页要求登录，Cookie 可能已过期")

                phase = state.card_state(body)
                clicked = None
                if phase == "clickable":
                    clicked = _click_entry_then_button(page, budget)
                    body = _body_text(page)
                    result.result = state.classify(body, clicked is not None)
                elif phase == "signed":
                    result.result = Result.ALREADY.value
                elif phase == "failed":
                    result.result = Result.FAILED.value
                else:
                    result.result = Result.UNKNOWN.value

                if result.needs_attention:
                    result.screenshot = _screenshot(page)

                detail = None
                if clicked:
                    detail = f"点击了「{clicked}」"
                elif result.result == Result.ALREADY.value:
                    detail = "页面显示今天已经签过"
                elif result.result == Result.FAILED.value:
                    detail = "页面提示签到失败"
                elif result.result == Result.UNKNOWN.value:
                    detail = "没有找到签到按钮，也没有识别出明确状态"
                return _finish(result, started, detail)

            except PlaywrightTimeout as exc:
                result.result = Result.TIMEOUT.value
                result.screenshot = _screenshot(page, "timeout")
                return _finish(result, started, f"页面操作超时：{exc}")
            except TimeoutError as exc:
                # Budget.check 抛的是内置 TimeoutError，不是 Playwright 的那个
                result.result = Result.TIMEOUT.value
                return _finish(result, started, str(exc))
            except NavigationError as exc:
                result.result = Result.NETWORK.value
                result.screenshot = _screenshot(page, "network")
                return _finish(result, started, str(exc))
            except PlaywrightError as exc:
                result.result = Result.BROWSER.value
                result.screenshot = _screenshot(page, "browser")
                return _finish(result, started, f"浏览器错误：{exc}")
            except RuntimeError as exc:
                result.result = Result.BROWSER.value
                return _finish(result, started, str(exc))
            except Exception as exc:  # noqa: BLE001
                result.result = Result.ERROR.value
                result.screenshot = _screenshot(page, "error")
                return _finish(result, started, f"{type(exc).__name__}: {exc}")
            finally:
                try:
                    context.close()
                    if browser:
                        browser.close()
                except PlaywrightError:
                    pass

    except PlaywrightError as exc:
        result.result = Result.BROWSER.value
        return _finish(result, started, f"浏览器错误：{exc}")
    except RuntimeError as exc:
        result.result = Result.BROWSER.value
        return _finish(result, started, str(exc))
    except Exception as exc:  # noqa: BLE001
        result.result = Result.ERROR.value
        return _finish(result, started, f"{type(exc).__name__}: {exc}")


def run_checkin(
    headed: bool = False,
    ignore_https_errors: bool = False,
    timeout: int | None = None,
    budget_seconds: int | None = None,
    credential: Credential | None = None,
    url: str | None = None,
    block_resources: bool = False,
    allow_fallback: bool = True,
) -> CheckinResult:
    started = time.monotonic()
    budget = Budget(budget_seconds or config.BUDGET_SECONDS)
    try:
        cred = credential or load_credential()
    except RuntimeError as exc:
        return _finish(CheckinResult(Result.NO_CREDENTIAL.value, detail=str(exc)), started)

    result = _run_once(
        cred, headed, ignore_https_errors, timeout, budget, url, block_resources, started
    )

    # 注入的 Cookie 失效时，回落到下一个凭据来源再试一次，只试一次。
    if (
        allow_fallback
        and credential is None
        and result.result == Result.AUTH_ERROR.value
        and budget.left() > 15
    ):
        try:
            fallback = load_credential(exclude={cred.mode})
        except RuntimeError:
            fallback = None
        if fallback and fallback.mode != cred.mode:
            append_log(f"凭据 {cred.mode} 失效，回落到 {fallback.mode} 重试")
            result = _run_once(
                fallback,
                headed,
                ignore_https_errors,
                timeout,
                budget,
                url,
                block_resources,
                started,
            )

    return result


def _launch(playwright: Playwright, headed: bool) -> Browser:
    try:
        return playwright.chromium.launch(headless=not headed)
    except PlaywrightError as exc:
        raise RuntimeError(
            "Chromium 启动失败。先运行 `playwright install chromium`，"
            f"或在无图形环境里去掉 --headed。原始错误：{exc}"
        ) from exc


def login_and_export() -> CheckinResult:
    """打开浏览器让人扫码，登录后把 Cookie 存到 data/cookies.json。"""
    started = time.monotonic()
    append_log("登录模式启动，等待扫码")
    config.ensure_dirs()
    config.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.PROFILE_DIR),
            headless=False,
            **_context_kwargs(False),
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                config.START_URL,
                wait_until="domcontentloaded",
                timeout=config.Timing.GOTO_TIMEOUT,
            )

            deadline = time.monotonic() + config.BUDGET_SECONDS
            while time.monotonic() < deadline:
                cookies = context.cookies()
                if any(
                    cookie.get("name") in config.SESSION_COOKIE_NAMES and cookie.get("value")
                    for cookie in cookies
                ):
                    break
                page.wait_for_timeout(1_500)
            else:
                return _finish(
                    CheckinResult(Result.TIMEOUT.value, command="login"),
                    started,
                    f"等待扫码超过 {config.BUDGET_SECONDS} 秒",
                )

            exported = [
                dict(cookie)
                for cookie in context.cookies()
                if "doubao.com" in (cookie.get("domain") or "")
            ]
            path = save_cookies(exported)
            return _finish(
                CheckinResult(Result.SUCCESS.value, command="login"),
                started,
                f"已保存 {len(exported)} 条 Cookie 到 {path}",
            )
        finally:
            context.close()
