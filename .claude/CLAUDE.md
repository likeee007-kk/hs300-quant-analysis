# hs300-quant-analysis

## 项目概况

沪深 300 量化数据分析与回测。流程：`01_fetch_data.py` → `02_align_pmi.py` → `03_clean_and_features.py` → `04_backtest.py`，或 `run_all.py` 一键执行。

## 环境

- Python：`.venv`（Python 3.14）
- MySQL：`127.0.0.1:3306/finance_project`，用户 `hs300`，连接串在 `.env`
- 依赖：`pip install -r requirements.txt`

## 运行与测试

```bash
python run_all.py                              # 一键 01–04
python import_data.py --dry-run                # 校验数据不写库
python import_data.py                          # 增量 upsert 到 MySQL
python -m unittest discover -s tests -v        # 23 tests
```

## 互通协议

本仓库由 CLI Claude 和 VS Code Claude 共享。每次 session 启动时：

1. 先读 `C:\Users\like\.claude\projects\E--hs300-quant-analysis\memory\MEMORY.md` 了解最新项目状态
2. 用 ListAgents 发现对方会话，通过 SendMessage 同步当前任务上下文
3. 做完关键操作后，更新 memory 文件作为上下文检查点
4. 跨会话共享的 memory：`mini-trade-recon-project.md`

## 当前项目状态

- 主分支 main，已推送到 GitHub → `https://github.com/likeee007-kk/hs300-quant-analysis.git`
- `hs300` 表已建，1,420 行，主键 `trade_date`，upsert 幂等
- Git push 时网络可能不稳，需重试（`http.version=HTTP/1.1`、`http.postBuffer=524288000`）
- 未跟踪目录 `hs300-updates/`（本地 patch/bundle/zip 残留）