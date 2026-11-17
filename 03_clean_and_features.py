# -*- coding: utf-8 -*-
"""Step3: 数据清洗（3σ 检查）与特征工程（趋势/动量/波动/量能），并通过 IC 检验预测力。

相较初版，本版做了三点优化：
1. 特征精简：ma5 与 ma20 的相关性高达 0.99，本质是同一价格序列的两种平滑。
   这里保留两列以兼容回测，但把趋势维度合并为平稳化的 ma_ratio = ma5/ma20 - 1；
   同时补充量能特征 vratio = volume / MA20(volume) - 1。
   候选特征再做一次冗余诊断，剔除与 ma_ratio 相关 0.82 的 dif（其 IC 也最弱），
   最终分析集为 ma_ratio / macd / vol20 / vratio / pmi，并用 VIF 复核。
2. 预测导向：初版热力图是“同日相关”，无法回答能否预测。这里改为计算
   特征在 t 日与未来 HORIZON 日收益的 IC（Pearson / Spearman）、滚动 IC 与 ICIR。
3. 稳健性：用 Spearman 对照 Pearson 以抑制极端值影响；对特征做滚动 z-score
   后再做十分位分组，避免价格水平随时间漂移导致的跨期不可比。
"""
import os
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

import statsmodels.api as sm
from statsmodels.stats.outliers_influence import variance_inflation_factor

# Windows 控制台默认 GBK，统一为 UTF-8 以便中文输出正常显示
import codecs

def _force_utf8(stream):
      """尽力把文本流切到 UTF-8；没有 reconfigure 的流（代理/测试捕获）直接跳过。"""
      if stream is None:
          return
      enc = getattr(stream, "encoding", None)
      if enc:
          try:
              if codecs.lookup(enc).name == "utf-8":
                  return
          except LookupError:
              pass
      reconfigure = getattr(stream, "reconfigure", None)
      if reconfigure is None:          # Python 3.6 或非 TextIOWrapper
          return
      try:
          reconfigure(encoding="utf-8", errors="replace")
      except (ValueError, OSError):
          pass

if __name__ == "__main__":
    _force_utf8(sys.stdout)
    _force_utf8(sys.stderr)

os.makedirs("figures", exist_ok=True)

HORIZON = 1          # 预测未来 1 个交易日收益
ROLL_WINDOW = 120    # 滚动 IC 窗口
Z_WINDOW = 252       # 滚动 z-score 窗口（约一年）
MIN_PERIODS = 60

# 候选特征：趋势 / 动量 / 波动 / 量能 / 宏观
CANDIDATES = ["ma_ratio", "dif", "macd", "vol20", "vratio", "pmi"]
# 冗余诊断后进入 IC 分析的特征集：dif 与 ma_ratio 同属趋势维度（r≈0.82），保留更平稳、更易解释的 ma_ratio
FEATURES = ["ma_ratio", "macd", "vol20", "vratio", "pmi"]


def vif_table(frame: pd.DataFrame) -> pd.Series:
    """计算方差膨胀因子（VIF），使用 statsmodels 标准库实现。"""
    x = frame.dropna()
    
    # statsmodels 要求必须手动加一列常数项（截距），否则算出来的第一列会不准
    X = sm.add_constant(x)
    
    # 一行代码直接计算所有特征的 VIF
    vif_data = pd.Series(
        [variance_inflation_factor(X.values, i) for i in range(X.shape[1])],
        index=X.columns
    )
    
    # 把常数项（const）的 VIF 删掉，只返回特征列的 VIF
    return vif_data.drop("const")


def rolling_zscore(s: pd.Series, window: int = Z_WINDOW, min_periods: int = MIN_PERIODS) -> pd.Series:
    """滚动标准化，使不同市场点位下的指标强度可比。"""
    mu = s.rolling(window, min_periods=min_periods).mean()
    sd = s.rolling(window, min_periods=min_periods).std()
    return (s - mu) / sd.replace(0, np.nan)


def ic_stats(x: pd.Series, y: pd.Series, roll_window: int = ROLL_WINDOW) -> dict:
    """单特征 vs 未来收益的 IC 统计：全样本 IC、Spearman、t 值、滚动 IC/ICIR。"""
    m = pd.concat([x.rename("x"), y.rename("y")], axis=1).dropna()
    n = len(m)
    if n < 4:
        return {}

    ic = m["x"].corr(m["y"])
    # Spearman = 秩上的 Pearson；用 pandas 内置 rank 实现，避免额外依赖 scipy
    ic_spear = m["x"].rank().corr(m["y"].rank())
    t_stat = ic * np.sqrt((n - 2) / (1 - ic ** 2)) if abs(ic) < 1 else np.nan

    roll_ic = m["x"].rolling(roll_window, min_periods=MIN_PERIODS).corr(m["y"]).dropna()
    icir = roll_ic.mean() / roll_ic.std() if roll_ic.std() > 0 else np.nan
    ic_win_rate = (roll_ic > 0).mean()

    return {
        "feature": x.name,
        "n": n,
        "ic": ic,
        "ic_spearman": ic_spear,
        "t_stat": t_stat,
        "icir": icir,
        "ic_win_rate": ic_win_rate,
        "rolling_ic": roll_ic,
    }


def decile_returns(z: pd.Series, fwd: pd.Series, q: int = 10):
    """按滚动 z-score 做 q 分位，返回各分位的平均未来收益（bps）及标准误。"""
    m = pd.concat([z.rename("z"), fwd.rename("fwd")], axis=1).dropna()
    if len(m) < q * 20:
        return None
    m["bucket"] = pd.qcut(m["z"], q, labels=False, duplicates="drop")
    g = m.groupby("bucket")["fwd"]
    return g.mean() * 1e4, g.std() / np.sqrt(g.count()) * 1e4


# ---------------------------------------------------------------- 读取与清洗
df = pd.read_csv("data/hs300_with_pmi.csv", parse_dates=["date"])

ret = df["ret"]
mean, std = ret.mean(), ret.std()
lower, upper = mean - 3 * std, mean + 3 * std
n_abnormal = ((ret > upper) | (ret < lower)).sum()
print(f"[Step3] 3σ 范围=[{lower:.4f}, {upper:.4f}]，异常日 {n_abnormal} 天（多为真实极端行情，保留）")

# 极少数缺失值前向填充（收盘价类），避免滚动窗口整段作废
df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].ffill()

# ---------------------------------------------------------------- 特征工程
df["ma5"] = df["close"].rolling(5).mean()
df["ma20"] = df["close"].rolling(20).mean()
df["vol20"] = df["ret"].rolling(20).std()

ema12 = df["close"].ewm(span=12, adjust=False).mean()
ema26 = df["close"].ewm(span=26, adjust=False).mean()
df["dif"] = ema12 - ema26
df["dea"] = df["dif"].ewm(span=9, adjust=False).mean()
df["macd"] = 2 * (df["dif"] - df["dea"])

# 趋势维度平稳化：均线偏离度替代共线的 ma5 / ma20
df["ma_ratio"] = df["ma5"] / df["ma20"] - 1
# 量能维度：相对 20 日均量的放缩倍数
df["vratio"] = df["volume"] / df["volume"].rolling(20).mean() - 1

# 预测目标：未来 HORIZON 日收益（t 日特征 → t+HORIZON 收益，避免前视偏差）
df["fwd_ret"] = df["ret"].shift(-HORIZON)

# ---------------------------------------------------------------- 1. 特征冗余诊断
print("\n[Step3] ===== 特征冗余诊断 =====")
cand_corr = df[CANDIDATES].corr()
print("  |r| >= 0.7 的候选特征对：")
for i, a in enumerate(CANDIDATES):
    for b in CANDIDATES[i + 1:]:
        r = cand_corr.loc[a, b]
        if abs(r) >= 0.7:
            print(f"    {a:<9s} ↔ {b:<8s} r={r:+.2f}")
print("  处理：趋势维度保留更平稳、更易解释的 ma_ratio，剔除 dif；")
print("        ma_ratio↔macd 的 VIF 约 2.5，未到需要处理的水平（>5）")
print(f"  最终分析集：{FEATURES}")
print("  VIF（>5 视为共线）：" + "  ".join(f"{k}={v:.1f}" for k, v in vif_table(df[FEATURES]).items()))

# ---------------------------------------------------------------- 2. 同期相关热力图
# 作用从“找信号”改为“查冗余”：精简后的特征之间是否还有高度共线
corr_input = ["ret"] + FEATURES
corr = df[corr_input].corr()

plt.figure(figsize=(8, 6.5))
sns.heatmap(corr, annot=True, fmt=".2f", cmap="coolwarm", vmin=-1, vmax=1,
            linewidths=0.5, square=True)
plt.title("Contemporaneous Correlation (redundancy check)")
plt.tight_layout()
plt.savefig("figures/hs300_corr.png", dpi=150)
plt.close()

# ---------------------------------------------------------------- 3. 滞后 IC（预测力）
print("\n[Step3] ===== 滞后 IC：feature(t) vs ret(t+1) =====")
stats = {}
for col in FEATURES:
    s = ic_stats(df[col], df["fwd_ret"])
    if s:
        stats[col] = s

ic_table = pd.DataFrame({k: {kk: vv for kk, vv in v.items() if kk != "rolling_ic"}
                         for k, v in stats.items()}).T
ic_table = ic_table.sort_values("ic", key=lambda s: s.abs(), ascending=False)
ic_table.to_csv("data/hs300_ic.csv", encoding="utf-8-sig")

print(ic_table[["n", "ic", "ic_spearman", "t_stat", "icir", "ic_win_rate"]]
      .round(3).to_string())

# t 值阈值：n≈1400 时 5% 双尾显著约 |r| ≈ 0.05
sig = ic_table[ic_table["t_stat"].abs() > 1.96]
if len(sig):
    print(f"  → 显著特征（|t|>1.96）：{', '.join(sig.index)}")
else:
    print("  → 没有特征达到 5% 显著性，当前特征对次日收益几乎没有线性预测力")

# 2a. IC 柱状图（Pearson vs Spearman）
order = ic_table.index[::-1]
y = np.arange(len(order))
height = 0.38
fig, ax = plt.subplots(figsize=(9, 5.5))
ax.barh(y + height / 2, ic_table.loc[order, "ic"], height=height,
        color=["#c0392b" if v > 0 else "#2c6fb0" for v in ic_table.loc[order, "ic"]],
        label="Pearson IC")
ax.barh(y - height / 2, ic_table.loc[order, "ic_spearman"], height=height,
        color="#999999", alpha=0.8, label="Spearman IC")
thr = 1.96 / np.sqrt(ic_table["n"].max())
for x in (-thr, thr):
    ax.axvline(x, color="k", linestyle="--", linewidth=0.8, alpha=0.6)
ax.axvline(0, color="k", linewidth=0.8)
ax.set_yticks(y, order)
ax.set_xlabel(f"IC with ret(t+{HORIZON})   (dashed = 5% significance)")
ax.set_title("Lagged Information Coefficient (predictive power)")
for yi, (v, t) in enumerate(zip(ic_table.loc[order, "ic"], ic_table.loc[order, "t_stat"])):
    ax.text(v + (0.004 if v >= 0 else -0.004), yi + height / 2, f"{v:.2f} (t={t:.1f})",
            va="center", ha="left" if v >= 0 else "right", fontsize=8)
ax.legend(loc="lower right")
ax.grid(axis="x", alpha=0.3)
plt.tight_layout()
plt.savefig("figures/hs300_ic.png", dpi=150)
plt.close()

# 2b. 滚动 IC 小图：看预测力是否稳定（还是只在某段行情成立）
ncol = 3
nrow = int(np.ceil(len(FEATURES) / ncol))
fig, axes = plt.subplots(nrow, ncol, figsize=(14, 3.2 * nrow), sharex=True)
axes = np.atleast_1d(axes).ravel()
band = 1.96 / np.sqrt(ROLL_WINDOW)
for ax, col in zip(axes, FEATURES):
    s = stats[col]
    ri = s["rolling_ic"]
    ax.plot(df.loc[ri.index, "date"], ri.values, color="#c0392b", linewidth=1.0)
    ax.axhline(0, color="k", linewidth=0.7)
    ax.axhspan(-band, band, color="grey", alpha=0.15)
    ax.set_title(f"{col}  mean IC={s['ic']:.2f}  ICIR={s['icir']:.2f}", fontsize=9)
    ax.set_ylim(-0.6, 0.6)
    ax.grid(alpha=0.3)
for ax in axes[len(FEATURES):]:
    ax.axis("off")
fig.suptitle(f"Rolling {ROLL_WINDOW}-day IC (grey band = 5% noise level)", fontsize=11)
fig.tight_layout()
plt.savefig("figures/hs300_ic_rolling.png", dpi=150)
plt.close()

# ---------------------------------------------------------------- 4. 十分位分组（看单调性/非线性）
top_feats = ic_table.index[:3].tolist()
fig, axes = plt.subplots(1, len(top_feats), figsize=(5 * len(top_feats), 4.2), sharey=False)
axes = np.atleast_1d(axes)
print("\n[Step3] ===== 十分位分组：滚动 z-score → 平均未来收益(bps) =====")
for ax, col in zip(axes, top_feats):
    z = rolling_zscore(df[col])
    res = decile_returns(z, df["fwd_ret"])
    if res is None:
        ax.axis("off")
        continue
    mean_bps, se_bps = res
    ax.bar(mean_bps.index + 1, mean_bps.values, yerr=se_bps.values,
           color="#c0392b", alpha=0.85, capsize=2)
    ax.axhline(0, color="k", linewidth=0.8)
    ax.set_xticks(range(1, len(mean_bps) + 1))
    ax.set_xlabel("decile of rolling z-score (1 = lowest)")
    ax.set_ylabel("avg next-day return (bps)")
    ax.set_title(f"{col}   (IC={stats[col]['ic']:.2f})")
    ax.grid(axis="y", alpha=0.3)
    spread = mean_bps.iloc[-1] - mean_bps.iloc[0]
    print(f"  {col:<9s} D10-D1 spread = {spread:6.2f} bps")
fig.suptitle("Decile analysis of top features", fontsize=11)
fig.tight_layout()
plt.savefig("figures/hs300_decile.png", dpi=150)
plt.close()

# ---------------------------------------------------------------- 落盘
# fwd_ret 只用于分析，不写入特征文件，避免后续误当作特征造成前视偏差
df.drop(columns=["fwd_ret"]).to_csv("data/hs300_features.csv", index=False)
print("\n[Step3] 完成：特征文件、IC 表与 4 张图已保存（figures/）")
