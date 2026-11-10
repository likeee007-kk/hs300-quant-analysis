# -*- coding: utf-8 -*-
"""Step4: 均线策略回测（年化收益/最大回撤/夏普比率）"""
import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

os.makedirs("figures", exist_ok=True)

df = pd.read_csv("data/hs300_features.csv", parse_dates=["date"])

df["signal"] = (df["ma5"] > df["ma20"]).astype(int)
df["position"] = df["signal"].shift(1).fillna(0)
df["ret_strategy"] = df["position"] * df["ret"]

df["nav_strategy"]  = (1 + df["ret_strategy"]).cumprod()
df["nav_benchmark"] = (1 + df["ret"]).cumprod()

n = len(df)
total_s = df["nav_strategy"].iloc[-1] - 1
total_b = df["nav_benchmark"].iloc[-1] - 1
annual_s = (1 + total_s) ** (252/n) - 1
dd_s = (df["nav_strategy"] / df["nav_strategy"].cummax() - 1).min()
dd_b = (df["nav_benchmark"] / df["nav_benchmark"].cummax() - 1).min()
sharpe = df["ret_strategy"].mean() / df["ret_strategy"].std() * np.sqrt(252)

print(f"[Step4] 策略累计 {total_s:.2%} vs 基准 {total_b:.2%}")
print(f"[Step4] 最大回撤 策略 {dd_s:.2%} vs 基准 {dd_b:.2%}")
print(f"[Step4] 夏普比率 {sharpe:.2f}")

plt.figure(figsize=(12, 6))
plt.plot(df["date"], df["nav_strategy"], label="Strategy (MA5/MA20)")
plt.plot(df["date"], df["nav_benchmark"], label="Benchmark (Buy & Hold)", alpha=0.7)
plt.title("Strategy vs Benchmark")
plt.xlabel("Date"); plt.ylabel("NAV")
plt.legend(); plt.grid(True, alpha=0.3); plt.tight_layout()
plt.savefig("figures/hs300_backtest.png", dpi=150); plt.close()

df.to_csv("data/hs300_backtest.csv", index=False)
print("[Step4] 完成：回测结果已保存")
