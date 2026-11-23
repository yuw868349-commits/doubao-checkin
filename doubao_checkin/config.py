"""运行参数集中在这里，尽量不散落在代码里。"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# 数据目录允许被环境变量挪走，方便容器和 CI 挂载
DATA_DIR = pathlib.Path(os.environ.get("DOUBAO_DATA_DIR", REPO_ROOT / "data")).expanduser()
PROFILE_DIR = DATA_DIR / "profile"
SHOTS_DIR = DATA_DIR / "shots"
LOG_FILE = DATA_DIR / "checkin.log"
RESULT_FILE = DATA_DIR / "last-result.json"
COOKIE_FILE = DATA_DIR / "cookies.json"

START_URL = "https://www.doubao.com/"
ACTIVITY_URL = "https://www.doubao.com/pc/agent-explore"

# 豆包的签到周期按北京时间算，别跟系统时区走。
# 固定 UTC+8，不依赖 tzdata，Windows 上也能跑。
CN_TZ = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")

# 活动页前端会检查 navigator.userAgent 里有没有 SamanthaDoubao 标识。
# 这个版本号只是让页面把请求当成电脑版客户端；官方一改校验就会失效。
CLIENT_UA_VERSION = os.environ.get("DOUBAO_CLIENT_UA_VERSION", "2.33.3")

if sys.platform == "darwin":
    _UA_BASE = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    )
else:
    _UA_BASE = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    )

CLIENT_UA = f"{_UA_BASE} SamanthaDoubao/{CLIENT_UA_VERSION}"

VIEWPORT = {"width": 1280, "height": 900}


class Timing:
    """浏览器流程里的等待时间，单位毫秒，集中在这里改。"""

    PAGE_SETTLE = 2_500
    READY_TIMEOUT = 15.0
    READY_POLL = 500
    BODY_TIMEOUT = 3_000
    GOTO_TIMEOUT = 45_000
    CLICK_TIMEOUT = 5_000
    CLICK_SETTLE = 1_500
    ENTRY_SETTLE = 2_000
    BUTTON_RETRY = 3
    BUTTON_RETRY_WAIT = 3_000
    SCREENSHOT_KEEP = 30


# 整次运行的时间预算，单位秒。要小于定时任务自己的超时，别让人家把你强杀了。
BUDGET_SECONDS = int(os.environ.get("DOUBAO_BUDGET_SECONDS", "300"))

# 会话类 Cookie，用来估算登录态还剩多少天，也用来判断凭据里有没有登录态
SESSION_COOKIE_NAMES = (
    "sessionid",
    "sessionid_ss",
    "sid_tt",
    "sid_guard",
    "sid_ucp_v1",
    "uid_tt",
)

# 这些资源对签到判断没用，--block-resources 打开时直接拦掉
BLOCKED_RESOURCE_TYPES = {"image", "font", "media"}

NOTIFY_TIMEOUT = 10


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    for path in (DATA_DIR, SHOTS_DIR):
        # Windows 上 chmod 不生效
        with contextlib.suppress(OSError):
            path.chmod(0o700)


def writable(path: pathlib.Path) -> tuple[bool, str | None]:
    """实际写一个探针文件，确认目录可写。"""
    probe = path / ".write-probe"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, None
    except OSError as exc:
        return False, str(exc)
