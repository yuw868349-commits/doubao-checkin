# 更新记录

## 0.1.0 - 2026-11-22

首个公开版本。

- 凭据来源：环境变量、`data/cookies.json`、macOS 钥匙串、Windows DPAPI、`data/profile`。
- 命令：`run`、`silent`、`silent-poll`、`doctor`、`login`。
- 状态判断集中在 `state.py`，页面改版只改一处。
- 导航失败退避重试，整次运行有时间预算。
- 失败留截图，结果写 `data/last-result.json`，日志写 `data/checkin.log`。
- 附带 GitHub Actions 每日签到、CI 矩阵和 Dependabot。
