# 更新记录

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
