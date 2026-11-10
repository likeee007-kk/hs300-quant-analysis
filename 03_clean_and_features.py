# -*- coding: utf-8 -*-
"""Step3: 数据清洗（ffill/3σ）与特征工程（rolling/ewm）"""
import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

os.makedirs("figures", exist_ok=True)

df = pd.read_csv("data/hs300_with_pmi.csv", parse_dates=["date"])

# 停牌缺失：ffill 演示
s = pd.Series([10.0, 10.5, np.nan, np.nan, 11.0])
print("[Step3] 停牌填充  fillna(0)=", s.fillna(0).tolist(), "  ffill()=", s.ffill().tolist())

# 3σ 识别异常收益率
ret = df["ret"]
mean, std = ret.mean(), ret.std()
lower, upper = mean - 3*std, mean + 3*std
abnormal = df[(ret > upper) | (ret < lower)]
print(f"[Step3] 3σ范围=[{lower:.4f},{upper:.4f}]，异常日 {len(abnormal)} 天（多为真实极端行情，保留）")

# 特征工程
df["ma5"]  = df["close"].rolling(5).mean()
df["ma20"] = df["close"].rolling(20).mean()
df["vol20"] = df["ret"].rolling(20).std()

ema12 = df["close"].ewm(span=12, adjust=False).mean()
ema26 = df["close"].ewm(span=26, adjust=False).mean()
df["dif"]  = ema12 - ema26
df["dea"]  = df["dif"].ewm(span=9, adjust=False).mean()
df["macd"] = 2 * (df["dif"] - df["dea"])

# 相关性热力图
features = df[["ret", "vol20", "ma5", "ma20", "dif", "macd"]].dropna()
corr = features.corr()
fig, ax = plt.subplots(figsize=(8, 6))
im = ax.imshow(corr, cmap="coolwarm", vmin=-1, vmax=1)
cols = corr.columns
ax.set_xticks(range(len(cols))); ax.set_yticks(range(len(cols)))
ax.set_xticklabels(cols, rotation=45, ha="right"); ax.set_yticklabels(cols)
for i in range(len(cols)):
    for j in range(len(cols)):
        ax.text(j, i, f"{corr.iloc[i,j]:.2f}", ha="center", va="center", fontsize=9)
plt.colorbar(im, ax=ax); plt.title("Correlation Heatmap")
plt.tight_layout(); plt.savefig("figures/hs300_corr.png", dpi=150); plt.close()

df.to_csv("data/hs300_features.csv", index=False)
print("[Step3] 完成：特征工程与热力图已保存")
