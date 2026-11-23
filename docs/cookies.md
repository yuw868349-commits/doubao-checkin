# 取 Cookie

脚本需要的是豆包网页的登录态。三种方式，按省事程度排序。

## 一、电脑版客户端（推荐，零配置）

先在豆包电脑版里登录，然后直接运行：

```bash
python checkin.py doctor
python checkin.py run
```

脚本会自己去找客户端的 Cookie 库：

- macOS：`~/Library/Application Support/Doubao` 或容器目录下的 `Cookies`，用钥匙串里的 `Doubao Safe Storage` 解密。
- Windows：`%LOCALAPPDATA%\Doubao`（以及 `DoubaoUserData`、`Programs\Doubao`）下的 `Network/Cookies`，走 DPAPI + AES-GCM。

前提是客户端已经登录过，且系统允许当前用户读取钥匙串/DPAPI。第一次跑建议先 `doctor`。

## 二、浏览器扫码导出

在有图形界面的机器上：

```bash
python checkin.py login
```

会弹出一个浏览器窗口，扫码登录后自动把 `doubao.com` 的 Cookie 写到 `data/cookies.json`，权限 600；同时保留 `data/profile` 持久化登录态。脚本检测到会话 Cookie 就继续，不用回终端按键。`login` 的结果不会写进签到状态，登录完可以直接跑 `silent`。

## 三、自己粘贴 Cookie

浏览器登录豆包后，从开发者工具里复制 Cookie，存成 `data/cookies.json`。支持两种形状：

```json
[
  {"name": "sessionid", "value": "...", "domain": ".doubao.com", "path": "/"}
]
```

```json
{"cookies": [{"name": "sessionid", "value": "...", "domain": ".doubao.com"}]}
```

也可以直接给一整条 `Cookie` 请求头：

```json
{"cookie": "sessionid=...; sid_guard=..."}
```

最少需要 `sessionid`（或 `sessionid_ss`、`sid_tt`、`sid_guard` 之一）。`expires` 可以省略；传 0 或负数会当会话 Cookie，传毫秒时间戳会换算成秒。

## GitHub Actions

仓库 Secrets 里加一个 `DOUBAO_COOKIES_JSON`，值是上面任意一种 JSON 的压缩版。workflow 会把它注进环境变量，不落盘。

注意 GitHub 的定时任务不保证准点，高峰期可能延迟几十分钟；它是 UTC 时间，`10 0 * * *` 对应北京时间 08:10。

## 失效了怎么办

- `AUTH_ERROR`：Cookie 过期，重新登录后覆盖 `data/cookies.json`。
- `CLIENT_ONLY`：活动页不再认这个 UA，或者官方改了入口。
- `unknown`：页面改版，先看 `data/shots/` 里的截图，再改 `doubao_checkin/state.py` 里的文案表。
- 会话一般能撑 30 天左右，建议每两周检查一次。

## 安全

`data/` 整个目录都在 `.gitignore` 里。不要把 `cookies.json`、截图、日志发到 issue 或群里。Cookie 等同于账号，泄漏了请立刻退出登录。
