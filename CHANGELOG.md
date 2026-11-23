# 更新记录

## 0.2.0 - 2026-11-23

按代码审计结果修了一轮，重点是「没签到却显示成功」的静默失败。

- 状态判断重写。`is_signed` 不再用「按钮不存在」证明「已经签过」，改成
  `card_state()` 五态机：失败文案、可点按钮、已签文案、只有标题、什么都没有。
  标题在但按钮还没渲染时返回 `unknown` 并留截图，不再猜成 `already`。
- 按钮文案只认「立即签到」的问题修了。`立即领取` / `今日签到` / `签到领额度`
  现在都会真的点，之前会直接判成已签，退出码还是 0。
- 新增 `FAILED` 结果码（退出码 10）。页面提示「已抢完」「活动结束」「签到失败」
  时不再报 `success`。
- `login` 写结果时带 `command=login`，不再污染当天的签到幂等判断。之前扫完码
  登录，当天 `silent` 会直接跳过。
- `silent-poll` 补上幂等检查，和 README 写的「同 silent」一致。
- `expires` 归一化：0 和负数当会话 Cookie，毫秒时间戳换算成秒。之前传 0 的
  Cookie 不会发送，传毫秒会让 Playwright 直接抛错。
- `_has_session` 改用 `SESSION_COOKIE_NAMES`，`sessionid_ss` / `sid_tt` 也能识别。
- 凭据降级链改成可回落：`AUTH_ERROR` 后跳过失效来源，用下一个凭据重试一次。
- 导航检查 HTTP 状态，4xx/5xx 归 `NETWORK`；用 `NavigationError` 替代字符串匹配。
- 时区显式成 UTC+8。`last-result.json` 同时写 `saved_at` 和 `saved_at_utc`，
  幂等判断按北京时间算「今天」。
- 失败才截图，保留最近 30 张；`data/` 和截图目录权限 700，结果和日志 600。
- `doctor` 实际写探针文件检查目录权限，`problems` 不再打成 Python list。
- `--notify-webhook`：只在需要关注的结果上 POST；`--block-resources`：拦截图片、
  字体、媒体。
- 新增 `deploy/` 三套定时任务配置和 `Dockerfile`。
- 结果码收成一份 `META` 表，`REPORTS` / `EXIT_CODES` / `NEEDS_ATTENTION` 自动派生。
- 工程化：mypy 清零，ruff 加 S/PTH/RET/SIM/C4/ARG 规则，`ruff format` 门禁，
  覆盖率门槛 75%，CI 加 3.13 和 Windows/macOS smoke，打包验证。
- 测试从 56 个加到 109 个，覆盖率从 45% 到 78%，新增 11 个真实浏览器场景。

## 0.1.1 - 2026-11-22

修客户端凭据的可用性问题，之前两条「零配置」路径解出来的 Cookie 都是脏的。

- Windows 的 v10 解密改成 `decrypt_and_verify`：之前把 16 字节 GCM tag 当密文一起解，
  每条 Cookie 尾部都多 16 字节垃圾；现在 tag 校验不过直接丢弃该条。
- macOS 的 v10 解密补上 PKCS#7 去填充：之前只截掉两个空格，填充字节会留在 Cookie 值里。
  老版本的空格填充仍然兼容。
- 客户端 Cookie 库连 `-wal` / `-shm` 一起复制，避免客户端在写时读到旧快照。
- 补了 `samesite` 列的读取和映射，不再一律写死 `Lax`；扩展导出的
  `no_restriction` / `unspecified` 也能识别，`None` 缺 `Secure` 时降级成 `Lax`。
- 客户端凭据全部不可用时，`NO_CREDENTIAL` 的 `detail` 会带上第一条真实原因（v20、密钥不对、没 sessionid）。
- `is_client_only` 判断提到 `needs_login` 前：客户端专用提示里通常也带「登录」二字，之前会误判成掉登录。
- `needs_login` 在签到卡片已渲染时不再触发。
- `run_checkin` 的 `page` 先初始化再进 try，修掉 `new_page()` 超时时的 `UnboundLocalError`。
- `last-result.json` 改成临时文件加 `os.replace` 原子写，外部定时任务不会读到半截 JSON；日志轮转同样改成原子替换。
- 删掉没用到的 `load_credential(prefer_profile=...)` 和 `cli.py` 里永远不执行的会话天数回填。
- `doctor` 单独检查 playwright 包与 Chromium 可执行文件是否存在，不再把两者混在一个 except 里。
- 新增 30 个测试，覆盖两端的加解密、去填充、读库、登录墙判断和结果落盘。

## 0.1.0 - 2026-11-22

首个公开版本。

- 凭据来源：环境变量、`data/cookies.json`、macOS 钥匙串、Windows DPAPI、`data/profile`。
- 命令：`run`、`silent`、`silent-poll`、`doctor`、`login`。
- 状态判断集中在 `state.py`，页面改版只改一处。
- 导航失败退避重试，整次运行有时间预算。
- 失败留截图，结果写 `data/last-result.json`，日志写 `data/checkin.log`。
- 附带 GitHub Actions 每日签到、CI 矩阵和 Dependabot。
