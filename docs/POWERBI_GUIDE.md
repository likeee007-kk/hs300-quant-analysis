# Power BI 看板 —— 逐步骤 Copy-Paste 指南

> 前提：Power BI Desktop 已连 MySQL `finance_project`，已加载 3 张视图。

---

## 第 1 步：打开度量值面板

左侧第三个图标「模型视图」→ 点顶部「新建度量值」

---

## 第 2 步：粘贴 6 个度量值

### ① 账户数量（KPI 用）

```dax
账户数量 = COUNTROWS(SUMMARIZE('finance_project v_account_daily_return', 'finance_project v_account_daily_return'[account_id]))
```

### ② 最新日期

```dax
最新日期 = MAX('finance_project v_account_daily_return'[trade_date])
```

### ③ 累计 TWR 展示（最新一天）

```dax
TWR 展示 = 
VAR d = MAX('finance_project v_account_daily_return'[trade_date])
RETURN
CALCULATE(
    AVERAGE('finance_project v_account_daily_return'[cum_twr_pct]),
    'finance_project v_account_daily_return'[trade_date] = d
)
```

### ④ 总市值展示（最新一天）

```dax
总市值展示 = 
VAR d = MAX('finance_project v_account_daily_return'[trade_date])
RETURN
CALCULATE(
    SUM('finance_project v_account_daily_return'[market_value]),
    'finance_project v_account_daily_return'[trade_date] = d
)
```

### ⑤ 当日盈亏展示（最新一天）

```dax
当日盈亏展示 = 
VAR d = MAX('finance_project v_account_daily_return'[trade_date])
RETURN
CALCULATE(
    SUM('finance_project v_account_daily_return'[daily_pnl]),
    'finance_project v_account_daily_return'[trade_date] = d
)
```

### ⑥ 对账差异数

```dax
对账差异数 = COUNTROWS('finance_project v_recon_breaks')
```

---

## 第 3 步：建第 1 页「日报」

左下角改名为「日报」

### 3.1 放 4 个卡片

1. 工具栏 → 「卡片」→ 拖到画布
2. 右侧「生成视觉对象」→ 「将数据字段拖放到此处」→ 从列表里把 TWR 展示 拖进去
3. 重复三次，分别拖入：总市值展示、当日盈亏展示、对账差异数

排成一行，像这样：

```
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│ TWR 展示 │ │ 总市值展示│ │当日盈亏展示│ │对账差异数 │
└──────────┘ └──────────┘ └──────────┘ └──────────┘
```

### 3.2 放折线图：累计 TWR 时序

1. 工具栏 → 「折线图」→ 拖到画布（占下面一大块）
2. 右侧侧栏里把字段拖入对应的槽：

| 槽 | 拖入 |
|----|------|
| X 轴 | `trade_date` |
| Y 轴 | `cum_twr_pct` |
| 图例 | `account_id` |

3. 点 Y 轴 `cum_twr_pct` 的 ▼ → 聚合改「不满」或「平均值」

### 3.3 格式化折线图（让它好看）

点折线图 → 右上角「视觉对象格式」（画笔图标）→ 展开 Y 轴 → **网格线**颜色：改成 `#444`

---

## 第 4 步：建第 2 页「异常」

左下角「+」新建一页，改名为「异常」

### 4.1 放对账差异表

1. 工具栏 → 「表」→ 拖到画布
2. 右侧，从 `finance_project v_recon_breaks` 拖入：

| 拖入 |
|------|
| `trade_date` |
| `account_id` |
| `symbol` |
| `diff` |
| `diff_type` |
| `diff_pattern` |
| `pre_qty` |
| `qty` |
| `net_trade` |
| `non_trade_qty` |
| `expected_qty` |

3. 排序：点 `trade_date` 列头 → 降序

### 4.2 对 `diff` 列加条件格式

1. 右侧「可视化」面板 → 「表」视觉对象 → 找到「条件格式」(有些版本叫「单元格元素」)
2. 选择 `diff` 字段 → 条件格式类型：字体颜色
3. 规则：
   - 最小值（负数）→ **蓝色**
   - 中点（0）→ **白色**
   - 最大值（正数）→ **红色**

> Power BI 条件格式操作：可视化 → 格式 → 条件格式 → diff → 颜色「按规则」→ 格式模式「渐变」。最小值 = `-500`（深蓝），中间 = `0`（浅灰），最大值 = `+500`（深红）。

### 4.3 对 `diff_pattern` 列加条件格式

给每行差异模式上色（identify 新发/反转/持续一目了然）

可视化 → 格式 → 条件格式 → `diff_pattern` → 格式模式「按字段值」→ 保持默认

### 4.4 放异常说明卡片

1. 工具 → 「卡片」→ 拖到画布，放在表下方
2. 从 `finance_project v_recon_breaks` 拖入 `diff` 字段
3. 下拉聚合改「计数」

---

## 第 5 步：交互联动（核心）

这一步让你点击异常表中的一行，卡片跟着变化

1. 选中异常表
2. 菜单栏 → 格式 → **编辑交互**（或右键表 → 编辑交互）
3. 每张卡片上方弹出 3 个小图标：漏斗、高亮、无反应
4. 点那张「异常说明卡片」→ 选第二个（**高亮**图标）
5. 点回菜单 → 格式 → 编辑交互 → 关闭

现在你点表里任意一行（比如 2026-09-15 那条），卡片就会筛选到那一天，显示那条异常。

---

## 第 6 步：看板美化

### 6.1 加日期切片器

1. 工具 → 「切片器」→ 拖到画布上方
2. 从 `finance_project v_account_daily_return` 拖 `trade_date` 进切片器
3. 点切片器右上角 ▼ → 改成「介于」（Between）
4. 页面里其他折线图和卡片都会随日期范围自动筛选

### 6.2 加账户筛选器

1. 工具 → 「切片器」→ 拖到画布
2. 从 `finance_project v_recon_breaks` 拖 `account_id` 进去
3. 同样可以多选查看

### 6.3 暗色主题（报纸感）

1. 顶部 →「视图」→「主题」→ 选「深色」或「Dark」

---

## 第 7 步：最后验证

复现「点开一条异常看原因」——在异常表里点 `2026-09-15 acc_001 600519 漏记 -100`，检查：

- 表中那条行突出显示
- 底部卡片聚集到这一行
- 日报页的折线图和卡片与异常页联动（因为你用了同源数据）

---

## 排错

| 现象 | 原因 | 解法 |
|------|------|------|
| 表名找不到 | Power BI 导入时加了前缀 | 用 DAX 度量值时拖字段进去让它自动生成表名 |
| 卡片显示「(空白)」 | 度量值引用了空的日期范围 | 切片器改成「所有日期」 |
| TWR 折线有空白段 | 某天无持仓 TWR 为 NULL | 正常，NULL 就是没持仓 |
| 度量值报错语法错误 | 公式里表名没加单引号 | 用 `'finance_project v_xxx'` |