# 参与贡献

先开 issue 描述问题，再提 PR。这个项目不大，不需要太重的流程。

## 本地开发

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy
pytest --cov=doubao_checkin --cov-fail-under=75
```

不需要浏览器，也不需要 Chromium。

## 改代码时注意

- 接口路径和客户端信封参数集中在 `config.py`；活动字段解析在 `campaign.py`，豆包改版优先改这两处。
- 凭据读取集中在 `credentials.py`，不要往仓库里加任何真实 Cookie。
- 新增结果码只改 `report.py` 的 `META`，退出码、日志和 README 表都从它派生。
- 领奖只认服务端下发的字符串 `task_id`；不要伪造任务完成状态。
- 提交前跑 `ruff check .`、`ruff format --check .`、`mypy`、`pytest`，CI 会再跑一遍。

## 提交信息

用中文或英文都行，说清楚改了什么、为什么改。不要一条提交里混进格式化、改文案和修 bug。
