"""浏览器流程：打开活动页、判断状态、点签到、留证据。"""

from __future__ import annotations

import datetime as dt
import time

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from . import config, state
from .credentials import Credential, load_credential, save_cookies, session_days_left
from .report import CheckinResult, append_log


class Budget:
    def __init__(self, seconds: int) -> None:
        self.deadline = time.monotonic() + seconds

    def left(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def check(self) -> None:
        if self.left() <= 0:
            raise TimeoutError("时间预算用完")


RETRY_DELAYS = (5, 10, 20)
GATEWAY_MARKERS = ("502 Bad Gateway", "503 Service Unavailable", "504 Gateway Time-out", "ERR_EMPTY_RESPONSE")
COOKIE_FIELDS = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")


def _body_text(page: Page) -> str:
    try:
        return page.inner_text("body", timeout=3_000)
    except PlaywrightError:
        return ""


def _goto(page: Page, url: str, budget: Budget) -> None:
    last_error = None
    for index, delay in enumerate((0, *RETRY_DELAYS), start=1):
        budget.check()
        if delay:
            page.wait_for_timeout(int(delay * 1000))
            budget.check()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=int(min(45_000, budget.left() * 1000)))
            page.wait_for_timeout(2_500)
            body = _body_text(page)
            if any(marker in body for marker in GATEWAY_MARKERS):
                last_error = RuntimeError("网关错误")
                append_log(f"第 {index} 次打开遇到网关错误，准备重试")
                continue
            return
        except PlaywrightTimeout as exc:
            last_error = exc
            append_log(f"第 {index} 次打开超时")
        except PlaywrightError as exc:
            last_error = exc
            append_log(f"第 {index} 次打开失败：{exc}")
    raise RuntimeError(f"页面打不开：{last_error}")


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
                    item.click(timeout=5_000)
                    page.wait_for_timeout(1_500)
                    return text
            except (PlaywrightError, PlaywrightTimeout):
                continue
    return None


def _click_entry_then_button(page: Page, budget: Budget) -> str | None:
    """活动入口有时藏在侧栏，先进去再找按钮。"""
    _click_one(page, state.ENTRY_TEXTS)
    page.wait_for_timeout(2_000)
    for _ in range(3):
        budget.check()
        clicked = _click_one(page, state.CLICK_TEXTS)
        if clicked:
            return clicked
        page.wait_for_timeout(3_000)
    return None


def _screenshot(page: Page | None, prefix: str = "result") -> str | None:
    if page is None:
        return None
    config.ensure_dirs()
    name = f"{prefix}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    path = config.SHOTS_DIR / name
    try:
        page.screenshot(path=str(path), full_page=True)
    except PlaywrightError:
        try:
            page.screenshot(path=str(path))
        except Exception:  # noqa: BLE001
            return None
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


def _open_session(
    playwright: Playwright,
    credential: Credential,
    headed: bool,
    ignore_https_errors: bool,
) -> tuple[Browser | None, BrowserContext]:
    if credential.mode == "profile" and credential.profile_dir:
        credential.profile_dir.mkdir(parents=True, exist_ok=True)
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(credential.profile_dir),
            headless=not headed,
            **_context_kwargs(ignore_https_errors),
        )
        return None, context

    browser = _launch(playwright, headed)
    context = browser.new_context(**_context_kwargs(ignore_https_errors))
    cookies = _playwright_cookies(credential.cookies)
    if cookies:
        context.add_cookies(cookies)
    return browser, context


def _finish(result: CheckinResult, detail: str | None = None) -> CheckinResult:
    if detail:
        result.detail = detail
    append_log(f"结果：{result.result}（{result.report}）" + (f"；{detail}" if detail else ""))
    return result


def run_checkin(
    headed: bool = False,
    ignore_https_errors: bool = False,
    timeout: int | None = None,
    budget_seconds: int | None = None,
    credential: Credential | None = None,
    url: str | None = None,
) -> CheckinResult:
    budget = Budget(budget_seconds or config.BUDGET_SECONDS)
    try:
        credential = credential or load_credential()
    except RuntimeError as exc:
        return _finish(CheckinResult("NO_CREDENTIAL", detail=str(exc)))

    append_log(f"启动，凭据来源 {credential.mode}，Cookie {len(credential.cookies)} 条")
    days = session_days_left(credential.cookies) if credential.cookies else None
    if days:
        append_log(f"会话剩余约 {days} 天")

    result = CheckinResult("ERROR", mode=credential.mode, session_days_left=days)
    try:
        with sync_playwright() as playwright:
            browser, context = _open_session(playwright, credential, headed, ignore_https_errors)
            page: Page | None = None
            try:
                if timeout:
                    context.set_default_timeout(timeout * 1000)
                page = context.new_page()

                _goto(page, url or config.ACTIVITY_URL, budget)
                body = _body_text(page)

                # 客户端专用提示要排在登录判断前面：那段文案里通常也带「登录」两个字
                if state.is_client_only(body):
                    result.result = "CLIENT_ONLY"
                    result.screenshot = _screenshot(page, "client-only")
                    return _finish(result, "页面提示仅支持电脑版客户端")

                if state.needs_login(body):
                    result.result = "AUTH_ERROR"
                    result.screenshot = _screenshot(page, "auth")
                    return _finish(result, "活动页要求登录，Cookie 可能已过期")

                clicked = None
                if not state.is_signed(body):
                    clicked = _click_entry_then_button(page, budget)
                    body = _body_text(page)

                verdict = state.classify(body, clicked is not None)
                result.result = verdict
                result.screenshot = _screenshot(page)
                if verdict == "unknown":
                    return _finish(result, "没有找到签到按钮，也没有识别出已签状态")
                return _finish(result, f"点击了「{clicked}」" if clicked else None)

            except PlaywrightTimeout as exc:
                result.result = "TIMEOUT"
                result.screenshot = _screenshot(page, "timeout")
                return _finish(result, f"页面操作超时：{exc}")
            except TimeoutError as exc:
                # Budget.check 抛的是内置 TimeoutError，不是 Playwright 的那个
                result.result = "TIMEOUT"
                return _finish(result, str(exc))
            except PlaywrightError as exc:
                result.result = "BROWSER"
                return _finish(result, f"浏览器错误：{exc}")
            except RuntimeError as exc:
                result.result = "NETWORK" if "页面打不开" in str(exc) else "BROWSER"
                return _finish(result, str(exc))
            except Exception as exc:  # noqa: BLE001
                result.result = "ERROR"
                return _finish(result, f"{type(exc).__name__}: {exc}")
            finally:
                try:
                    context.close()
                    if browser:
                        browser.close()
                except PlaywrightError:
                    pass

    except PlaywrightError as exc:
        result.result = "BROWSER"
        return _finish(result, f"浏览器错误：{exc}")
    except RuntimeError as exc:
        result.result = "BROWSER"
        return _finish(result, str(exc))
    except Exception as exc:  # noqa: BLE001
        result.result = "ERROR"
        return _finish(result, f"{type(exc).__name__}: {exc}")


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
            page.goto(config.START_URL, wait_until="domcontentloaded")

            deadline = time.monotonic() + config.BUDGET_SECONDS
            while time.monotonic() < deadline:
                cookies = context.cookies()
                if any(c.get("name") == "sessionid" and c.get("value") for c in cookies):
                    break
                page.wait_for_timeout(1_500)
            else:
                return _finish(
                    CheckinResult("TIMEOUT"),
                    f"等待扫码超过 {config.BUDGET_SECONDS} 秒",
                )

            cookies = [c for c in context.cookies() if "doubao.com" in (c.get("domain") or "")]
            path = save_cookies(cookies)
            return _finish(CheckinResult("success"), f"已保存 {len(cookies)} 条 Cookie 到 {path}")
        finally:
            context.close()
