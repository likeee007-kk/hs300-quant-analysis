# DataGrip 操作指南 — 从建库到对账

> 适用于当前 mini 项目 `hs300-quant-analysis`

---

## 第一步：连接 MySQL

1. 打开 DataGrip → `+` → Data Source → MySQL
2. 填入连接信息：Host `127.0.0.1` / Port `3306` / User `hs300` / Database `finance_project`
3. 点 **Test Connection** 确认连通
4. 点 **OK** 保存

> 三张对账表和两个视图建在 `finance_project` 中，与主项目 `hs300` 表同库。

---

## 第二步：建表 + 造数据

回到终端，运行造数脚本（DDL + INSERT 一条龙）：

```bash
cd E:\hs300-quant-analysis
python 05_mini_recon_gen.py
```

执行完后 DataGrip 中刷新表列表，应看到四张表：
- `hs300` — 沪深 300 主表（1420 行）
- `trade` — 交易流水（~235 行）
- `position` — 日频持仓（~822 行）
- `market_data` — 日频行情（325 行）

> 验证: 右键每张表 → **Quick Documentation** 查看表结构和注释。

---

## 第三步：创建对账视图

在 DataGrip 中执行 `sql/02_reconciliation_queries.sql`，选中 **Part E + Part F** 的两个 `CREATE OR REPLACE VIEW` 语句执行（Ctrl+Enter）。

执行后在 DataGrip 左侧刷新 → 应出现两个视图：
- `v_recon_diff`
- `v_daily_return`

---

## 第四步：跑对账查询（逐段执行）

在 `sql/02_reconciliation_queries.sql` 中**逐段选中 + Ctrl+Enter 执行**：

| 顺序 | 段落 | 目的 |
|------|------|------|
| 1 | **Part A**（A1+A2） | 看对账差异清单 |
| 2 | **Part B** | 看每标的每日市值/盈亏/收益率 |
| 3 | **Part C** | 看账户级汇总盈亏 |
| 4 | **Part D** | 看浮动盈亏（成本 vs 市价） |

**期望结果**：Part A 应查出 6 条差异（3 颗雷 × 2 天，因账面差异通过 `pre_qty` 传播到次日）：
- 2026-09-15 | acc_001 | 600519 | diff=-100（漏记）
- 2026-10-20 | acc_002 | 000858 | diff=-200（漏记）
- 2026-10-28 | acc_003 | 601318 | diff=+500（多记）

---

## 第五步：Python 异常检测

```bash
python 06_anomaly_detection.py
```

输出到 `reports/` 目录下的 3 个 CSV：
- `anomaly_recon_diff_*.csv` — 对账差异
- `anomaly_return_jumps_*.csv` — 收益率跳变
- `anomaly_neg_position_*.csv` — 持仓为负

---

## 第六步：Power BI 连接

1. **Power BI Desktop** → 获取数据 → MySQL 数据库
2. 服务器: 填你的 MySQL 地址，数据库: `hs300_recon`
3. 选择视图:
   - `v_recon_diff` → 对账差异看板
   - `v_daily_return` → 收益率时序 & 异常监控
   - 或者直接选基表 `trade`, `position`, `market_data` 自定义建模

---

## 快速验证清单

| # | 操作 | 预期结果 |
|---|------|----------|
| 1 | `SELECT COUNT(*) FROM trade;` | ~200-500 行 |
| 2 | `SELECT COUNT(*) FROM position;` | ~500-1000 行 |
| 3 | `SELECT COUNT(*) FROM market_data;` | = 交易日数 × 5（约 325 行） |
| 4 | `SELECT COUNT(*) FROM v_recon_diff WHERE diff != 0;` | = 6 行（3 颗雷 × 2 天传播） |
| 5 | `SELECT DISTINCT account_id FROM position;` | acc_001, acc_002, acc_003 |
| 6 | `SELECT DISTINCT symbol FROM market_data;` | 5 只标的 |
| 7 | `python 06_anomaly_detection.py` | 检出 25 条异常（对账 6 + 跳变 18 + 负持仓 1） |

---

## 常用 DataGrip 快捷键

| 快捷键 | 作用 |
|--------|------|
| `Ctrl+Enter` | 执行当前语句 |
| `Ctrl+Shift+F10` | 执行整个脚本 |
| `F4` | 查看表数据 |
| `Ctrl+Q` | Quick Documentation |
| `Ctrl+F12` | 查看表/视图结构 |
| `Ctrl+D` | 复制当前行 |