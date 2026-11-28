# doubao-checkin

[![CI](https://github.com/yuw868349-commits/doubao-checkin/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/yuw868349-commits/doubao-checkin/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

豆包「额度天天送」每日签到与活动奖励。**纯 HTTP API 实现，不启动浏览器，不依赖 Playwright。**

没有官方接口，全部是抓包实测出来的活动接口；页面改版、风控升级都可能让它失效。

## 它做什么

- 每日签到：先上报 click，再调 `landing/checkin`；额度流水出现当天记录即视为已签，避免重复点击
- 任务面板：拉取活动任务、完成状态、累计额度、里程碑
- 自动领奖：对已完成任务的 `task_id` 逐个调领奖接口
- 结构化结果：`data/last-result.json` 带结果码、明细、会话剩余天数
- 幂等：当天有成功记录就直接跳过；`silent-poll` 在窗口内轮询补签

## 它不做什么

- 不伪造用户行为：文档/表格/PPT/定时提醒这类任务需要你先真正用一次豆包，脚本只负责领奖
- 不做多账号
- 不保证可用：接口是逆向来的，随时可能变

## 快速开始

需要 Python 3.10+，无浏览器依赖。

```bash
git clone https://github.com/yuw868349-commits/doubao-checkin.git
cd doubao-checkin
python3 -m venv .venv
source .venv/bin/activate
pip install .

python checkin.py doctor --online
python checkin.py run
```

## 登录凭据

| 方式 | 适用 | 怎么配 |
| --- | --- | --- |
| Cookie 注入 | Linux、容器、GitHub Actions | `DOUBAO_COOKIES_JSON` 环境变量，或 `data/cookies.json` |
| macOS 客户端 | 装过豆包电脑版并登录 | 自动读钥匙串 + `~/Library/Application Support/Doubao` |
| Windows 客户端 | 装过豆包电脑版并登录 | 自动读 DPAPI + `%LOCALAPPDATA%\Doubao` |

优先级：环境变量 → `data/cookies.json` → 客户端凭据。

`data/cookies.json` 最少一条 `sessionid`：

```json
[{"name": "sessionid", "value": "<你的值>", "domain": ".doubao.com", "path": "/"}]
```

Cookie 等同于账号，别提交、别外发。

## 命令

| 命令 | 说明 |
| --- | --- |
| `python checkin.py run` | 签到一次 |
| `python checkin.py silent` | 静默签到，当天成功过就跳过 |
| `python checkin.py silent-poll` | 失败时在窗口内轮询补签 |
| `python checkin.py tasks` | 查看任务、状态、累计额度 |
| `python checkin.py claim` | 领取所有已完成任务奖励 |
| `python checkin.py records` | 查看额度流水 |
| `python checkin.py status` | 签到状态 + 任务概览 |
| `python checkin.py doctor [--online]` | 自检，`--online` 额外请求一次活动详情 |

通用参数：`--json`、`--notify-webhook`。`silent-poll` 另有 `--poll-interval`、`--poll-window`。

## 接口

所有活动接口必须带客户端信封 query 参数（`aid`、`version_code`、`pc_version`、`web_tab_id` 等，见 `doubao_checkin/config.py`），否则返回 `{"code":1,"message":"invalid param"}`。

| 路径 | 用途 |
| --- | --- |
| `POST /alice/ug/campaign/v3/detail/` | 任务清单、进度、里程碑 |
| `POST /alice/ug/campaign/quota/records/` | 额度流水（判断当天是否已签） |
| `POST /alice/ug/campaign/landing/report` | 上报签到卡点击（checkin 的前置） |
| `POST /alice/ug/campaign/landing/checkin` | 每日签到 |
| `POST /alice/ug/campaign/item/reward/claim/` | 领奖，body `{"task_id": "<字符串 id>"}` |

实测要点：

- `task_id` 只在任务完成后下发；领奖传数字会 400，必须传字符串
- 签到接口对部分账号/时段返回 `710010702 系统错误`（页面自己的签名请求也一样），说明入口被平台下线，不是参数问题
- 结果码 `SIGNIN_CLOSED` 就是这种情况，`silent-poll` 会在窗口内持续重试，等接口恢复

## 结果码

| result | 含义 | 退出码 |
| --- | --- | --- |
| `success` | 签到成功 | 0 |
| `already` | 今天已经签过 | 0 |
| `AUTH_ERROR` | 登录态失效 | 2 |
| `CLIENT_ONLY` | 被判定成网页端（历史结果码） | 3 |
| `NETWORK` | 接口打不开 | 4 |
| `TIMEOUT` | 时间预算或请求超时 | 5 |
| `BROWSER` | 历史结果码，保留兼容 | 6 |
| `NO_CREDENTIAL` | 没有可用凭据 | 7 |
| `unknown` | 状态没认出来 | 8 |
| `ERROR` | 未预期异常 | 9 |
| `FAILED` | 接口明确拒绝 | 10 |
| `SIGNIN_CLOSED` | 平台下线网页端签到入口 | 11 |

## 部署

systemd：

```bash
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now doubao-checkin.timer
```

Docker：

```bash
docker build -t doubao-checkin .
docker run --rm -e DOUBAO_COOKIES_JSON="$(cat cookies.json)" -v "$PWD/data:/data" doubao-checkin
```

GitHub Actions：`.github/workflows/checkin.yml` 每天 UTC 00:10 跑；在 fork 的 Secrets 里配 `DOUBAO_COOKIES_JSON`。

## 安全

- `data/` 在 `.gitignore`，权限 700；`cookies.json`、`last-result.json`、日志权限 600
- 结果文件和日志不打印 Cookie
- 会话约 30 天过期，建议每两周检查一次

## License

MIT
