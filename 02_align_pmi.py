# -*- coding: utf-8 -*-
"""Step2: 多源数据时间对齐（merge_asof 防未来函数）"""
import os
import akshare as ak
import pandas as pd

os.makedirs("data", exist_ok=True)

df = pd.read_csv("data/hs300_daily.csv", parse_dates=["date"])

pmi = ak.macro_china_pmi()
pmi = pmi[["月份", "制造业-指数"]].copy()
pmi["date"] = pd.to_datetime(pmi["月份"], format="%Y年%m月份") + pd.offsets.MonthEnd(0)
pmi = pmi[["date", "制造业-指数"]].rename(columns={"制造业-指数": "pmi"})
pmi = pmi.sort_values("date").reset_index(drop=True)

df["date"] = df["date"].astype("datetime64[ns]")
pmi["date"] = pmi["date"].astype("datetime64[ns]")

merged = pd.merge_asof(df, pmi, on="date", direction="backward")
merged.to_csv("data/hs300_with_pmi.csv", index=False)
print(f"[Step2] 完成：对齐后 {len(merged)} 条，PMI 缺失 {merged['pmi'].isna().sum()} 条")
