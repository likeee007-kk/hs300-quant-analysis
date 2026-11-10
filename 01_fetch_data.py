# -*- coding: utf-8 -*-
"""Step1: 获取沪深300行情，计算日收益率与净值"""
import os
import akshare as ak
import pandas as pd
import matplotlib.pyplot as plt

os.makedirs("data", exist_ok=True)
os.makedirs("figures", exist_ok=True)

df = ak.stock_zh_index_daily(symbol="sh000300")
df["date"] = pd.to_datetime(df["date"])
df = df.sort_values("date").reset_index(drop=True)
df = df[df["date"] >= "2021-01-01"].reset_index(drop=True)

df["ret"] = df["close"].pct_change().fillna(0)
df["nav"] = (1 + df["ret"]).cumprod()

plt.figure(figsize=(10, 5))
plt.plot(df["date"], df["nav"], label="NAV (start=1.0)")
plt.title("CSI 300 Net Value Curve (2021-)")
plt.xlabel("Date"); plt.ylabel("NAV")
plt.legend(); plt.grid(True, alpha=0.3); plt.tight_layout()
plt.savefig("figures/hs300_nav.png", dpi=150); plt.close()

df.to_csv("data/hs300_daily.csv", index=False)
print(f"[Step1] 完成：{len(df)}条，累计收益 {df['nav'].iloc[-1]-1:.2%}")
