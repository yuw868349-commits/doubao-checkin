# doubao-checkin

[![CI](https://github.com/yuw868349-commits/doubao-checkin/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/yuw868349-commits/doubao-checkin/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

豆包「额度天天送」每日签到。打开活动页，点一次签到，把结果写进 `data/last-result.json`，定时任务读这个文件就行。

这不是官方项目。页面改版、规则收紧、客户端校验升级，都会让它失效。

## 它做什么

- 每天签到一次，平时 200 额度、周一翻倍（以页面实际显示为准）
- 已签过就跳过，不重复点
- 结果落 JSON，退出码能区分登录失效、网络失败、被判定成网页端
- macOS / Windows 直接读豆包电脑版客户端凭据，零配置
- 服务器或容器可以用 Cookie 注入
- 自带 `doctor` 自检、日志、失败截图

## 它不做什么

- 不刷额度、不批量注册、不处理验证码
- 不保证一直可用。活动页前端校验一改，可能需要改 `state.py` 或 UA 版本号
- 不做多账号。一台机器只认一个登录态

## 快速开始

需要 Python 3.10+ 和能跑 Playwright 的环境。

```bash
git clone https://github.com/yuw868349-commits/doubao-checkin.git
cd doubao-checkin

python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
python -m playwright install chromium

python checkin.py doctor
python checkin.py run
```

`doctor` 检查 Playwright、Chromium、凭据和目录写入权限，不联网。

## 登录方式

| 方式 | 适用 | 怎么配 |
| --- | --- | --- |
| 客户端凭据 | macOS / Windows，装过豆包电脑版并登录 | 不用配，脚本自己读 |
| 手动登录 | 有桌面环境的机器 | `python checkin.py login` |
| Cookie 注入 | Linux、容器、GitHub Actions | `DOUBAO_COOKIES_JSON` 环境变量，或 `data/cookies.json` |

优先级是：环境变量 → `data/cookies.json` → 客户端凭据 → `data/profile`。

### macOS / Windows 客户端

macOS 从钥匙串取 `Doubao Safe Storage` 密钥，解 `~/Library/Application Support/Doubao/*/Cookies`。

Windows 从 `%LOCALAPPDATA%` 找豆包客户端目录，用 DPAPI 解 `Local State` 里的密钥，再解 Cookie 库。客户端开着的时候 Cookie 库会被占用，脚本先普通拷贝，失败再用 `esentutl` 备份拷贝；还失败就关掉客户端跑一次，或者直接走 Cookie 注入。

Windows 的 v20 / App-Bound 加密暂不支持，遇到会明确报错，不会拿空 Cookie 硬跑。

### 手动登录

```bash
python checkin.py login
```

会打开有头浏览器，脚本检测到 `sessionid` 后自动继续，不用回终端按键。登录态存在 `data/profile`，同时把 `doubao.com` 的 Cookie 导出到 `data/cookies.json`（权限 600）。下次运行优先用环境变量或这份 Cookie，没有再回落到 `data/profile`。

没有桌面的服务器跑不了这一步，用 Cookie 注入。

### Cookie 注入

最少需要这些字段：

```json
[
  {
    "name": "sessionid",
    "value": "<你的值>",
    "domain": ".doubao.com",
    "path": "/"
  }
]
```

也可以直接放浏览器扩展导出的完整数组，脚本认 `name` / `value` / `domain`（或 `host_key`）/ `path` / `expires` / `httpOnly` / `sameSite`。

```bash
export DOUBAO_COOKIES_JSON="$(cat cookies.json)"
python checkin.py run
```

细节和取值注意看 [docs/cookies.md](docs/cookies.md)。

## 命令

| 命令 | 说明 |
| --- | --- |
| `python checkin.py run` | 执行一次，结果打到 stdout |
| `python checkin.py silent` | 执行一次，不打印；结果写日志和 `last-result.json`，当天成功过就直接跳过 |
| `python checkin.py silent-poll` | 同 `silent`，遇到网络、超时或未知状态时在窗口内重试 |
| `python checkin.py doctor` | 离线自检 |
| `python checkin.py login` | 手动登录，写 `data/profile` 并导出 Cookie |

通用参数：`--url` 换活动页，`--headed` 显示浏览器，`--insecure` 忽略证书错误（抓包代理环境用），`--timeout` 单步超时，`--budget` 整次运行预算，`--json` 输出结构化结果。

## 结果码

| result | 含义 | 退出码 |
| --- | --- | --- |
| `success` | 签到成功 | 0 |
| `already` | 今天已经签过 | 0 |
| `AUTH_ERROR` | 登录态失效 | 2 |
| `CLIENT_ONLY` | 被判定成网页端 | 3 |
| `NETWORK` | 页面打不开 | 4 |
| `TIMEOUT` | 时间预算或页面操作超时 | 5 |
| `BROWSER` | Chromium / Playwright 异常 | 6 |
| `NO_CREDENTIAL` | 没有可用凭据 | 7 |
| `unknown` | 页面状态没认出来 | 8 |
| `ERROR` | 未预期异常 | 9 |

`data/last-result.json` 每次覆盖写，只有本次结果，不含 Cookie。

## GitHub Actions

仓库带了 `.github/workflows/checkin.yml`，每天 UTC 00:10（北京时间 08:10）跑一次，也可以手动触发。

1. Fork 这个仓库
2. 在 fork 的 Settings → Secrets and variables → Actions 里加 `DOUBAO_COOKIES_JSON`
3. 需要时在 Actions 页面点 `Run workflow`

别人 fork 你的仓库，用不了你仓库里的 Secret，每个 fork 要自己配。别把 Cookie 写进 workflow 文件。

## 项目结构

```text
checkin.py               兼容入口，python checkin.py <命令>
doubao_checkin/
  cli.py                 命令解析、doctor、退出码
  runner.py              浏览器流程：导航、点击、截图
  credentials.py         环境变量 / Cookie 文件 / macOS / Windows 凭据
  state.py               页面文案与状态判断
  report.py              结果码、日志、last-result.json
  config.py              路径、UA、时间预算
tests/                   状态判断、Cookie、命令行
docs/cookies.md          取 Cookie 的几种方式
```

## 为什么这么做

活动接口带签名参数，纯 `requests` 一开始能跑，改版就废。用浏览器点按钮笨，但坏得慢，出问题还能截图。

活动页会检查 UA 里有没有 `SamanthaDoubao`，脚本默认带 `2.33.3`。这是最脆弱的一环，官方一改校验就会返回 `CLIENT_ONLY`。遇到的话先改 `DOUBAO_CLIENT_UA_VERSION`，还不行就提 issue。

签到前会先判断卡片状态：`每日签到` 在、`立即签到` 不在，就算已签。这条比找「已签到」文案稳，因为任务卡片是常驻的。

整次运行有预算（默认 300 秒），避免定时任务被系统强杀之前还没写日志。

## 已知限制

- 页面文案改版要改 `doubao_checkin/state.py`，集中在一个文件里
- `sessionid` 大约 30 天过期，之后返回 `AUTH_ERROR`
- `data/profile` 模式在部分环境下会被识别成网页端，优先用客户端凭据或 Cookie
- Windows v20 / App-Bound 加密的 Cookie 解不了
- 无头浏览器不是 100% 隐形，官方要封就是会封

## 实测

| 日期 | 环境 | 结果 |
| --- | --- | --- |
| 2026-11-22 | Linux + Cookie 注入 | 签到成功，200 额度 |

客户端凭据路径沿用作者自用脚本的实现，macOS 真机跑过；Windows 的 DPAPI + AES-GCM 逻辑没有在本仓库环境复测。有 Windows 机器的话欢迎提 issue 反馈。

## 安全

Cookie 等同于账号登录凭证。别提交，别发给别人，别贴到 issue 里。

- `data/`、`cookies.json`、`*.log` 已在 `.gitignore`
- 导出的 `data/cookies.json` 权限是 600
- 日志和结果文件不打印 Cookie 值

## License

MIT
