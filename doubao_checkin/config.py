"""运行参数集中在这里，尽量不散落在各处。

本仓库只走 HTTP API，不启动浏览器。
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# 数据目录允许被环境变量挪走，方便容器和 CI 挂载
DATA_DIR = pathlib.Path(os.environ.get("DOUBAO_DATA_DIR", REPO_ROOT / "data")).expanduser()
LOG_FILE = DATA_DIR / "checkin.log"
RESULT_FILE = DATA_DIR / "last-result.json"
COOKIE_FILE = DATA_DIR / "cookies.json"

SITE = "https://www.doubao.com"

# 活动与额度接口
CAMPAIGN_GROUP = "pc_incentive_task_mvp_v3"
DETAIL_PATH = "/alice/ug/campaign/v3/detail/"
LIST_PATH = "/alice/ug/campaign/list/"
RECORDS_PATH = "/alice/ug/campaign/quota/records/"
REPORT_PATH = "/alice/ug/campaign/landing/report"
CHECKIN_PATH = "/alice/ug/campaign/landing/checkin"
CLAIM_PATH = "/alice/ug/campaign/item/reward/claim/"

# 豆包的活动接口按客户端信封校验，缺了这些 query 参数会回 invalid param
CLIENT_PARAMS: dict[str, str] = {
    "version_code": "20800",
    "language": "zh",
    "device_platform": "web",
    "doubao_device_platform": "web",
    "aid": "497858",
    "real_aid": "497858",
    "pkg_type": "release_version",
    "pc_version": "2.33.3",
    "doubao_pc_version": "5.76.11",
    "region": "CN",
    "sys_region": "CN",
    "samantha_web": "1",
    "web_platform": "desktop",
    "use-olympus-account": "1",
    "runtime_version": "5.76.11",
    "runtime": "web",
}

# 活动接口认这个 UA 里的电脑版客户端标识
CLIENT_UA_VERSION = os.environ.get("DOUBAO_CLIENT_UA_VERSION", "2.33.3")
_UA_BASE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)
CLIENT_UA = f"{_UA_BASE} SamanthaDoubao/{CLIENT_UA_VERSION}"

# 豆包的签到周期按北京时间算，别跟系统时区走。
CN_TZ = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")

REFERER = "https://www.doubao.com/pc/agent-explore"

# 会话类 Cookie，用来估算登录态还剩多少天，也用来判断凭据里有没有登录态
SESSION_COOKIE_NAMES = (
    "sessionid",
    "sessionid_ss",
    "sid_tt",
    "sid_guard",
    "sid_ucp_v1",
    "uid_tt",
)


class Timing:
    """网络参数集中在这里改。"""

    TIMEOUT = float(os.environ.get("DOUBAO_TIMEOUT", "20"))
    RETRIES = int(os.environ.get("DOUBAO_RETRIES", "2"))
    BACKOFF = (1.0, 3.0)
    BUDGET_SECONDS = int(os.environ.get("DOUBAO_BUDGET_SECONDS", "120"))
    POLL_INTERVAL = int(os.environ.get("DOUBAO_POLL_INTERVAL", "600"))
    POLL_WINDOW = int(os.environ.get("DOUBAO_POLL_WINDOW", "21600"))


BUDGET_SECONDS = Timing.BUDGET_SECONDS
NOTIFY_TIMEOUT = 10


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        DATA_DIR.chmod(0o700)


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


def python_ok() -> bool:
    return sys.version_info >= (3, 10)
