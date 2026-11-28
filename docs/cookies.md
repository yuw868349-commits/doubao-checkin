# 取 Cookie

脚本需要的是豆包网页的登录态。不需要浏览器，Cookie 直接喂给 HTTP 客户端。

## 一、电脑版客户端（推荐，零配置）

先在豆包电脑版里登录，然后直接运行：

```bash
python checkin.py doctor --online
python checkin.py run
```

脚本会自己去找客户端的 Cookie 库：

- macOS：`~/Library/Application Support/Doubao` 或容器目录下的 `Cookies`，用钥匙串里的 `Doubao Safe Storage` 解密。
- Windows：`%LOCALAPPDATA%\Doubao`（以及 `DoubaoUserData`、`Programs\Doubao`）下的 `Network/Cookies`，走 DPAPI + AES-GCM。

前提是客户端已经登录过，且系统允许当前用户读取钥匙串/DPAPI。

## 二、注入 Cookie

Linux、容器和 GitHub Actions 用这种方式。

环境变量 `DOUBAO_COOKIES_JSON`，或文件 `data/cookies.json`，支持三种形状：

```json
[
  {"name": "sessionid", "value": "...", "domain": ".doubao.com", "path": "/"}
]
```

```json
{"cookies": [{"name": "sessionid", "value": "...", "domain": ".doubao.com"}]}
```

```json
{"cookie": "sessionid=...; sid_guard=..."}
```

最少需要 `sessionid`（或 `sessionid_ss`、`sid_tt`、`sid_guard` 之一）。`expires` 可以省略；传 0 或负数当会话 Cookie，传毫秒时间戳会换算成秒。

浏览器登录豆包后，从开发者工具里复制 Cookie 即可；只用一次，不需要跑任何浏览器自动化。

## GitHub Actions

仓库 Secrets 里加一个 `DOUBAO_COOKIES_JSON`，值是上面任意一种 JSON 的压缩版。workflow 会把它注进环境变量，不落盘。

GitHub 的定时任务不保证准点，高峰期可能延迟几十分钟；cron 是 UTC，`10 0 * * *` 对应北京时间 08:10。

## 失效了怎么办

- `AUTH_ERROR`：Cookie 过期，重新登录后覆盖 `data/cookies.json`。
- `SIGNIN_CLOSED`（`710010702`）：网页端签到入口被平台下线，页面自己的签名请求也一样被拒；`silent-poll` 会在窗口内继续重试。
- `NETWORK`：接口打不开，先查网络和代理。
- `unknown`：接口回了没见过的结构，把 `data/last-result.json` 的 `detail` 贴到 issue。

会话一般能撑 30 天左右，`doctor` 会显示 `session_days_left`，少于 7 天就重新登录。

## 安全

`data/` 整个目录都在 `.gitignore` 里。不要把 `cookies.json` 或日志发到 issue 或群里。Cookie 等同于账号，泄漏了请立刻退出登录。
