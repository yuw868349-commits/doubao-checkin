# 参与贡献

先开 issue 描述问题，再提 PR。这个项目不大，不需要太重的流程。

## 本地开发

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
playwright install chromium
pytest
ruff check .
```

## 改代码时注意

- 页面文案集中在 `doubao_checkin/state.py`，豆包改版优先改那里，别把判断散到 runner 里。
- 凭据读取集中在 `credentials.py`，不要往仓库里加任何真实 Cookie。
- 新增结果码要同步 `report.py` 的 `REPORTS`、`cli.py` 的 `EXIT_CODES` 和 README 的结果码表。
- 提交前跑 `pytest` 和 `ruff check .`，CI 会再跑一遍。

## 提交信息

用中文或英文都行，说清楚改了什么、为什么改。不要一条提交里混进格式化、改文案和修 bug。
