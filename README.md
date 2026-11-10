# 沪深300多源金融数据分析与均线策略回测

完整的量化金融数据分析项目：真实数据获取 → 多源对齐 → 数据清洗 → 特征工程 → 策略回测。

## 技术栈
Python 3.x、Pandas、NumPy、Matplotlib、AKShare

## 项目流程
1. `01_fetch_data.py`：获取沪深300日线，计算日收益率与净值；
2. `02_align_pmi.py`：日频行情与月频PMI用 `merge_asof`(backward) 对齐，杜绝未来函数；
3. `03_clean_and_features.py`：停牌缺失 `ffill`、异常值 `3σ` 识别；构建 MA5/MA20、20日波动率、MACD；
4. `04_backtest.py`：均线趋势跟踪策略回测，输出年化收益、最大回撤、夏普比率。

## 回测结果（2021至今）
- 策略累计收益 **-16.8%**（同期买入持有基准 -23.0%）；
- 策略最大回撤 **29.7%**（基准 45.6%）；
- 结论：趋势跟踪通过"下跌时空仓"大幅降低回撤，代价是牺牲部分收益弹性。

## 运行
```bash
pip install -r requirements.txt
python 01_fetch_data.py
python 02_align_pmi.py
python 03_clean_and_features.py
python 04_backtest.py
